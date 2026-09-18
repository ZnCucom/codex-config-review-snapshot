from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from .engine import SyncEngine
from .fs import atomic_write_json, read_json, workspace_lock
from .git import GitRunner
from .github import GitHubTargetVerifier
from .install import WindowsTaskScheduler, install_user, uninstall_user, verify_installation
from .model import (
    CapturePolicy,
    GitError,
    PolicyError,
    RemoteTarget,
    ReviewSyncError,
    SyncRequest,
    SyncResult,
    TaskStatus,
    WorkspaceIdentity,
    WorkspaceState,
)
from .queue import (
    WorkspaceStateStore,
    disable_workspace,
    enqueue_request,
    pause_workspace,
    quarantine_chain,
    record_local_checkpoint,
    resume_workspace,
)
from .security import scan_reachable_history, workflow_digest
from .snapshot import assemble_candidate, create_checkpoint
from .workspace import capture_workspace, discover_workspace


from .registration import match_project, registered_worktrees, runtime_project, workspace_id


CONFIG_SCHEMA = 1


def _empty_config() -> dict[str, object]:
    return {"schema_version": CONFIG_SCHEMA, "device_id": uuid.uuid4().hex[:12], "projects": []}


def _load_config(data_root: Path, *, create: bool = False) -> dict[str, object] | None:
    path = data_root / "config.json"
    if not path.exists():
        if not create:
            return None
        return _empty_config()
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != CONFIG_SCHEMA or not isinstance(payload.get("projects"), list):
        raise PolicyError("review-sync registry schema is invalid")
    return payload


def _save_config(data_root: Path, config: dict[str, object]) -> None:
    atomic_write_json(data_root / "config.json", config)


def register_project(
    identity: WorkspaceIdentity,
    data_root: Path,
    target: RemoteTarget,
    verifier,
    *,
    reviewed_workflow_digest: str | None,
) -> dict[str, object]:
    config = _load_config(data_root, create=True)
    assert config is not None
    identifier = workspace_id(identity)
    state_path = data_root / "state" / f"{identifier}.json"
    lock_path = data_root / "locks" / f"{identifier}.lock"
    store_path = data_root / "stores" / f"{identifier}.git"
    verifier.verify(target, store_path if store_path.exists() else None)
    project = {
        "workspace_id": identifier,
        "root": str(identity.root),
        "git_dir": str(identity.git_dir),
        "common_dir": str(identity.common_dir),
        "state_path": str(state_path),
        "lock_path": str(lock_path),
        "store_path": str(store_path),
        "receipt_dir": str(data_root / "receipts" / identifier),
        "reviewed_workflow_digest": reviewed_workflow_digest,
        "target": asdict(target),
    }
    projects = config["projects"]
    assert isinstance(projects, list)
    for existing in projects:
        if existing.get("workspace_id") == identifier:
            if existing != project:
                raise PolicyError("workspace is already registered with different settings")
            return existing
    projects.append(project)
    _save_config(data_root, config)
    return project


def _project_for_cwd(
    cwd: Path,
    data_root: Path,
    runner: GitRunner,
) -> tuple[dict[str, object], WorkspaceIdentity] | None:
    config = _load_config(data_root)
    if config is None:
        return None
    try:
        identity = discover_workspace(cwd, runner)
    except (OSError, GitError, PolicyError):
        return None
    project = match_project(config["projects"], identity, data_root, runner)
    return (project, identity) if project is not None else None


def _state_store(project: dict[str, object]) -> WorkspaceStateStore:
    return WorkspaceStateStore(
        Path(project["state_path"]),
        Path(project["lock_path"]),
        str(project["workspace_id"]),
    )


def _outcomes(github: str, installation: object) -> dict[str, object]:
    return {
        "IMPLEMENTATION": "AVAILABLE",
        "INSTALLATION": installation,
        "GITHUB": github,
        "CHATGPT_ACCESS": "NOT_VERIFIED",
    }


def _task_from_state(state: WorkspaceState) -> TaskStatus:
    has_blocker = state.public_task_state in {"blocked", "failed_test", "review_wait"}
    blockers = (state.public_reason,) if has_blocker and state.public_reason != "unknown" else ()
    return TaskStatus(
        task_id=state.workspace_id,
        task_state=state.public_task_state,
        objective=state.objective,
        completed=tuple(state.completed),
        remaining=tuple(state.remaining),
        blockers=blockers,
        next_action=state.next_action,
        verification_command=state.verification_command,
        verification_result=state.verification_result,
        verification_source_digest=state.verification_source_digest,
    )


def _validate_resume_source(
    state: WorkspaceState,
    current_source_fingerprint: str,
    choice: str,
) -> None:
    if choice != "reauthorize":
        return
    if not state.pending_requests:
        raise PolicyError("there is no pending request to reauthorize")
    expected_source = state.pending_requests[-1].get("source_fingerprint")
    if expected_source != current_source_fingerprint:
        raise PolicyError("current worktree changed while paused; use recapture or abandon")


def _sync_project_locked(
    project: dict[str, object],
    identity: WorkspaceIdentity,
    runner: GitRunner,
    gh_executable: Path,
    *,
    timeout: float,
) -> SyncResult:
    state_store = _state_store(project)
    state = state_store.load()
    if state.disabled:
        return SyncResult(state="DISABLED", message=state.pause_reason or "disabled")
    if state.paused:
        return SyncResult(state="PAUSED", message=state.pause_reason or "paused")
    capture = capture_workspace(identity, CapturePolicy(), runner)
    task = _task_from_state(state)
    candidate = assemble_candidate(capture, task, captured_at=datetime.now(timezone.utc))
    checkpoint = create_checkpoint(
        Path(project["store_path"]),
        candidate,
        state.safe_local_sha,
        runner,
        reviewed_workflow_digest=project.get("reviewed_workflow_digest"),
    )
    record_local_checkpoint(state_store, checkpoint.sha, checkpoint.semantic_digest)
    history_report = scan_reachable_history(
        Path(project["store_path"]),
        checkpoint.sha,
        state.remote_verified_sha,
        runner,
    )
    if history_report.blocked:
        quarantine_chain(
            state_store,
            checkpoint.sha,
            tuple(item.rule_id for item in history_report.findings),
            published=checkpoint.sha == state.remote_verified_sha,
        )
        return SyncResult(state="QUARANTINED", snapshot_sha=checkpoint.sha, message=history_report.render())
    refreshed = state_store.load()
    if not checkpoint.created and refreshed.remote_verified_sha == checkpoint.sha and not refreshed.pending_requests:
        return SyncResult(state="NO_CHANGE", snapshot_sha=checkpoint.sha, remote_sha=checkpoint.sha)
    target = RemoteTarget(**project["target"])
    verifier = GitHubTargetVerifier(runner, gh_executable=gh_executable)
    engine = SyncEngine(runner, verifier, receipt_dir=Path(project["receipt_dir"]))
    return engine.upload_checkpoint(Path(project["store_path"]), state_store, target, checkpoint.sha, timeout=timeout)


def _sync_project(
    project: dict[str, object],
    identity: WorkspaceIdentity,
    runner: GitRunner,
    gh_executable: Path,
    *,
    timeout: float,
    lock_timeout: float = 10.0,
) -> SyncResult:
    sync_lock = Path(str(project["lock_path"]) + ".sync")
    with workspace_lock(sync_lock, timeout=lock_timeout):
        return _sync_project_locked(
            project,
            identity,
            runner,
            gh_executable,
            timeout=timeout,
        )


def _sync_process_entry(
    sender,
    project: dict[str, object],
    identity: WorkspaceIdentity,
    git_executable: Path,
    gh_executable: Path,
    operation_timeout: float,
) -> None:
    try:
        result = _sync_project(
            project,
            identity,
            GitRunner(git_executable, timeout=max(0.1, operation_timeout)),
            gh_executable,
            timeout=operation_timeout,
        )
        sender.send(("ok", result))
    except ReviewSyncError as error:
        sender.send(("error", type(error).__name__, str(error)))
    except Exception:
        sender.send(("error", "ReviewSyncError", "sync worker failed without a verified outcome"))
    finally:
        sender.close()


def _sync_project_bounded(
    project: dict[str, object],
    identity: WorkspaceIdentity,
    git_executable: Path,
    gh_executable: Path,
    *,
    total_timeout: float,
) -> SyncResult:
    if total_timeout <= 0:
        raise PolicyError("sync-now total timeout must be positive")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_sync_process_entry,
        args=(sender, project, identity, git_executable, gh_executable, total_timeout),
        name="review-sync-now",
    )
    started = time.monotonic()
    try:
        process.start()
        sender.close()
        remaining = max(0.0, total_timeout - (time.monotonic() - started))
        process.join(remaining)
        if process.is_alive():
            process.terminate()
            process.join(1.0)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(1.0)
            return SyncResult(
                state="PENDING",
                message="sync-now total timeout expired; worker stopped and persisted state requires recovery",
            )
        if not receiver.poll(0.1):
            raise ReviewSyncError("sync worker exited without a verified outcome")
        outcome = receiver.recv()
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
        process.close()
    if outcome[0] == "ok":
        return outcome[1]
    _kind, error_type, message = outcome
    if error_type in {"PolicyError", "CandidateBlocked", "UnsupportedWorkspaceError"}:
        raise PolicyError(message)
    raise ReviewSyncError(message)


def tick_registered(data_root: Path, git_executable: Path, gh_executable: Path) -> list[SyncResult]:
    config = _load_config(data_root)
    if config is None:
        return []
    runner = GitRunner(git_executable)
    results: list[SyncResult] = []
    seen = set()
    for registration in config["projects"]:
        try:
            identities = list(registered_worktrees(registration, runner))
        except FileNotFoundError:
            continue
        except Exception as error:
            results.append(SyncResult(state="ERROR", message=str(error)))
            continue
        for identity in identities:
            key = workspace_id(identity)
            if key in seen:
                continue
            seen.add(key)
            try:
                owner = next((p for p in config["projects"]
                              if all(Path(p[k]).resolve(strict=False) == getattr(identity, k)
                                     for k in ("root", "git_dir", "common_dir"))), registration)
                project = runtime_project(owner, identity, data_root)
                results.append(_sync_project(project, identity, runner, gh_executable, timeout=60.0))
            except Exception as error:
                results.append(SyncResult(state="ERROR", message=str(error)))
    return results


def _common(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--cwd", type=Path, default=Path.cwd())
    subparser.add_argument("--data-root", type=Path, required=True)
    subparser.add_argument("--codex-home", type=Path, required=True)
    subparser.add_argument("--git", type=Path, default=Path("git"))
    subparser.add_argument("--gh", type=Path, default=Path("gh"))
    subparser.add_argument("--json", action="store_true")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review-sync")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "sync-now"):
        child = commands.add_parser(name)
        _common(child)
    sync = commands.choices["sync-now"]
    sync.add_argument("--timeout-seconds", type=float, default=60.0)
    pause = commands.add_parser("pause")
    _common(pause)
    pause.add_argument("--reason", required=True)
    disable = commands.add_parser("disable")
    _common(disable)
    disable.add_argument("--reason", required=True)
    resume = commands.add_parser("resume")
    _common(resume)
    resume.add_argument("--choice", choices=("reauthorize", "abandon", "recapture"), required=True)
    resume.add_argument("--authorized-digest")
    request = commands.add_parser("request")
    _common(request)
    request.add_argument("--task-state", choices=("unknown", "complete", "blocked", "failed_test", "review_wait"), required=True)
    request.add_argument("--objective", required=True)
    request.add_argument("--reason", default="unknown")
    request.add_argument("--completed", action="append", default=[])
    request.add_argument("--remaining", action="append", default=[])
    request.add_argument("--next-action", default="unknown")
    request.add_argument("--verification-command", default="not_run")
    request.add_argument("--verification-result", default="not_run")
    register = commands.add_parser("register")
    _common(register)
    register.add_argument("--repo", required=True)
    register.add_argument("--push-url", required=True)
    register.add_argument("--reviewed-workflow-digest")
    register.add_argument("--acknowledge-remote-automation", action="store_true")
    install = commands.add_parser("install")
    _common(install)
    install.add_argument("--source-root", type=Path, required=True)
    install.add_argument("--python", type=Path, required=True)
    uninstall = commands.add_parser("uninstall")
    _common(uninstall)
    uninstall.add_argument("--dry-run", action="store_true")
    return parser


def _emit(stdout: TextIO, payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        for key, value in payload.items():
            stdout.write(f"{key}={value}\n")


def _main(argv: list[str] | None = None, *, stdout: TextIO | None = None) -> int:
    args = _parser().parse_args(argv)
    stdout = sys.stdout if stdout is None else stdout
    runner = GitRunner(args.git)
    scheduler = WindowsTaskScheduler()
    if args.command == "install":
        result = install_user(
            source_root=args.source_root,
            codex_home=args.codex_home,
            data_root=args.data_root,
            python_executable=args.python,
            git_executable=args.git,
            gh_executable=args.gh,
            scheduler=scheduler,
        )
        payload = _outcomes("NEEDS_SETUP", verify_installation(args.codex_home, args.data_root, scheduler))
        payload["result"] = result
        _emit(stdout, payload, args.json)
        return 0
    if args.command == "uninstall":
        if args.dry_run:
            payload = _outcomes("NEEDS_SETUP", verify_installation(args.codex_home, args.data_root, scheduler))
            payload["result"] = "DRY_RUN_ONLY"
        else:
            payload = _outcomes("NEEDS_SETUP", uninstall_user(args.codex_home, args.data_root, scheduler))
        _emit(stdout, payload, args.json)
        return 0
    matched = _project_for_cwd(args.cwd, args.data_root, runner)
    if args.command == "register":
        identity = discover_workspace(args.cwd, runner)
        config = _load_config(args.data_root, create=True)
        assert config is not None
        identifier = workspace_id(identity)
        capture = capture_workspace(identity, CapturePolicy(), runner)
        candidate_workflow = workflow_digest(
            {path: __import__("review_sync.model", fromlist=["CandidateFile"]).CandidateFile(item.mode, item.data) for path, item in capture.files.items()}
        )
        if candidate_workflow != args.reviewed_workflow_digest:
            raise PolicyError("current workflow digest is absent or does not match explicit review")
        target = RemoteTarget(
            kind="github",
            identity=args.repo,
            push_url=args.push_url,
            remote_ref=f"refs/heads/codex-sync/{config['device_id']}/{identifier}",
            remote_automation_reviewed=args.acknowledge_remote_automation,
        )
        verifier = GitHubTargetVerifier(runner, gh_executable=args.gh)
        project = register_project(identity, args.data_root, target, verifier, reviewed_workflow_digest=candidate_workflow)
        payload = _outcomes("REGISTERED_NOT_SYNCED", verify_installation(args.codex_home, args.data_root, scheduler))
        payload["project"] = project["workspace_id"]
        _emit(stdout, payload, args.json)
        return 0
    if matched is None:
        payload = _outcomes("NEEDS_SETUP", verify_installation(args.codex_home, args.data_root, scheduler))
        payload["result"] = "workspace is not registered"
        _emit(stdout, payload, args.json)
        return 0
    project, identity = matched
    store = _state_store(project)
    if args.command == "status":
        state = store.load()
        payload = _outcomes(
            "REMOTE_VERIFIED" if state.remote_verified_sha else "REGISTERED_NOT_VERIFIED",
            verify_installation(args.codex_home, args.data_root, scheduler),
        )
        payload["workspace"] = project["workspace_id"]
        payload["paused"] = state.paused
        payload["disabled"] = state.disabled
        payload["local_checkpoint"] = state.safe_local_sha
        payload["remote_verified_sha"] = state.remote_verified_sha
        payload["pending"] = len(state.pending_requests)
        payload["pending_semantic_keys"] = [
            item["semantic_key"]
            for item in state.pending_requests
            if isinstance(item.get("semantic_key"), str)
        ]
    elif args.command == "sync-now":
        result = _sync_project_bounded(
            project,
            identity,
            args.git,
            args.gh,
            total_timeout=args.timeout_seconds,
        )
        payload = _outcomes(result.state, verify_installation(args.codex_home, args.data_root, scheduler))
        payload["result"] = asdict(result)
    elif args.command == "pause":
        pause_workspace(store, args.reason)
        payload = _outcomes("PAUSED", verify_installation(args.codex_home, args.data_root, scheduler))
        payload["result"] = "PAUSE_CONFIRMED"
    elif args.command == "disable":
        disable_workspace(store, args.reason)
        payload = _outcomes("DISABLED", verify_installation(args.codex_home, args.data_root, scheduler))
        payload["result"] = "DISABLE_CONFIRMED"
    elif args.command == "resume":
        capture = capture_workspace(identity, CapturePolicy(), runner)
        _validate_resume_source(store.load(), capture.source_fingerprint, args.choice)
        resume_workspace(store, args.choice, authorized_digest=args.authorized_digest)
        resumed = not store.load().paused
        payload = _outcomes(
            "RESUMED_RECHECK_REQUIRED" if resumed else "PAUSED",
            verify_installation(args.codex_home, args.data_root, scheduler),
        )
        payload["result"] = "RESUME_CONFIRMED" if resumed else "BACKLOG_ABANDONED_PAUSE_RETAINED"
    else:
        capture = capture_workspace(identity, CapturePolicy(), runner)
        request = SyncRequest(
            source_fingerprint=capture.source_fingerprint,
            task_state=args.task_state,
            objective=args.objective,
            verification_result=args.verification_result,
            verification_source_digest=capture.source_fingerprint,
            reason=args.reason,
            lifecycle_event="manual_request",
            event_id=uuid.uuid4().hex,
            created_at=datetime.now(timezone.utc).isoformat(),
            completed=tuple(args.completed),
            remaining=tuple(args.remaining),
            next_action=args.next_action,
            verification_command=args.verification_command,
        )
        key = enqueue_request(store, request)
        payload = _outcomes("PENDING", verify_installation(args.codex_home, args.data_root, scheduler))
        payload["request"] = key
    _emit(stdout, payload, args.json)
    return 0


def main(argv: list[str] | None = None, *, stdout: TextIO | None = None) -> int:
    destination = sys.stdout if stdout is None else stdout
    try:
        return _main(argv, stdout=destination)
    except ReviewSyncError as error:
        payload = _outcomes(
            "POLICY_BLOCKED" if isinstance(error, PolicyError) else "ERROR",
            "UNKNOWN",
        )
        payload["error_type"] = type(error).__name__
        payload["error"] = str(error)
        arguments = sys.argv[1:] if argv is None else argv
        _emit(destination, payload, "--json" in arguments)
        return 2
