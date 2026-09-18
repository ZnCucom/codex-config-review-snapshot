from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from review_sync.cli import (
    _parser,
    _sync_project,
    _sync_project_bounded,
    _task_from_state,
    _validate_resume_source,
    main,
    register_project,
    workspace_id,
)
from review_sync.fs import atomic_write_json, workspace_lock
from review_sync.git import GitRunner
from review_sync.model import (
    PolicyError,
    RemoteTarget,
    ReviewSyncError,
    SyncRequest,
    WorkspaceIdentity,
    WorkspaceState,
)
from review_sync.queue import WorkspaceStateStore, enqueue_request
from review_sync.workspace import discover_workspace


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.strip()


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "<REDACTED_EMAIL>")
    _git(repo, "config", "user.name", "Tests")
    (repo / "source.py").write_text("print('ok')\n", encoding="utf-8")
    _git(repo, "add", "source.py")
    _git(repo, "commit", "-m", "fixture")
    return repo


class RecordingVerifier:
    def __init__(self) -> None:
        self.targets: list[RemoteTarget] = []

    def verify(self, target: RemoteTarget, git_dir: Path | None = None) -> str:
        self.targets.append(target)
        return target.push_url


class CliTests(unittest.TestCase):
    def test_request_parser_accepts_structured_handoff_fields(self) -> None:
        _args, unknown = _parser().parse_known_args(
            [
                "request",
                "--data-root",
                "data",
                "--codex-home",
                "codex",
                "--task-state",
                "review_wait",
                "--objective",
                "review",
                "--completed",
                "done",
                "--remaining",
                "manual trust",
                "--next-action",
                "review hooks",
                "--verification-command",
                "python -B -m unittest affected.tests -v",
            ]
        )

        self.assertEqual(unknown, [])

    def test_task_handoff_preserves_structured_status_fields(self) -> None:
        state = WorkspaceState(workspace_id="workspace-1")
        state.completed = ["SSH upload verified", "installation integrity restored"]
        state.remaining = ["user hook trust"]
        state.next_action = "trust hooks and observe a host event"
        state.verification_command = "python -B -m unittest affected.tests -v"

        task = _task_from_state(state)

        self.assertEqual(
            task.completed,
            ("SSH upload verified", "installation integrity restored"),
        )
        self.assertEqual(task.remaining, ("user hook trust",))
        self.assertEqual(task.next_action, "trust hooks and observe a host event")
        self.assertEqual(task.verification_command, "python -B -m unittest affected.tests -v")

    def test_request_preserves_structured_handoff_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = _repo(root)
            data = root / "data"
            codex_home = root / "codex"
            codex_home.mkdir()
            identity = discover_workspace(repo, GitRunner("git"))
            project = register_project(
                identity,
                data,
                RemoteTarget(
                    kind="github",
                    identity="owner/private-repo",
                    push_url="https://github.com/owner/private-repo.git",
                    remote_ref=f"refs/heads/codex-sync/device/{workspace_id(identity)}",
                ),
                RecordingVerifier(),
                reviewed_workflow_digest=None,
            )
            output = io.StringIO()

            code = main(
                [
                    "request",
                    "--cwd",
                    str(repo),
                    "--data-root",
                    str(data),
                    "--codex-home",
                    str(codex_home),
                    "--task-state",
                    "review_wait",
                    "--objective",
                    "review final checkpoint",
                    "--completed",
                    "SSH upload verified",
                    "--completed",
                    "installation integrity restored",
                    "--remaining",
                    "user hook trust",
                    "--reason",
                    "waiting for hook trust",
                    "--next-action",
                    "trust hooks and observe a host event",
                    "--verification-command",
                    "python -B -m unittest tests.test_review_sync_install tests.test_review_sync_cli -v",
                    "--verification-result",
                    "passed",
                    "--json",
                ],
                stdout=output,
            )

            self.assertEqual(code, 0)
            store = WorkspaceStateStore(
                Path(project["state_path"]),
                Path(project["lock_path"]),
                project["workspace_id"],
            )
            task = _task_from_state(store.load())
            self.assertEqual(
                task.completed,
                ("SSH upload verified", "installation integrity restored"),
            )
            self.assertEqual(task.remaining, ("user hook trust",))
            self.assertEqual(task.next_action, "trust hooks and observe a host event")
            self.assertEqual(
                task.verification_command,
                "python -B -m unittest tests.test_review_sync_install tests.test_review_sync_cli -v",
            )

    def test_task_handoff_semantics_do_not_depend_on_transport_queue_entries(self) -> None:
        state = WorkspaceState(
            workspace_id="workspace-1",
            public_task_state="blocked",
            public_reason="waiting for reviewer",
            objective="ship checkpoint",
            verification_result="failed",
            verification_source_digest="a" * 64,
            pending_requests=[{"semantic_key": "event-key", "reason": "waiting for reviewer"}],
        )

        before = _task_from_state(state)
        state.pending_requests.clear()
        after = _task_from_state(state)

        self.assertEqual(before, after)
        self.assertEqual(after.task_id, "workspace-1")
        self.assertEqual(after.blockers, ("waiting for reviewer",))

    def test_reauthorize_refuses_when_current_worktree_differs_from_paused_request(self) -> None:
        state = WorkspaceState(
            workspace_id="workspace-1",
            paused=True,
            pending_requests=[{"source_fingerprint": "a" * 64, "semantic_key": "b" * 64}],
        )

        with self.assertRaisesRegex(PolicyError, "changed while paused"):
            _validate_resume_source(state, "c" * 64, "reauthorize")

    def test_whole_sync_operation_uses_a_distinct_workspace_mutex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / "state.lock"
            project = {
                "workspace_id": "workspace-1",
                "state_path": str(root / "state.json"),
                "lock_path": str(lock_path),
            }
            identity = WorkspaceIdentity(root, root / ".git", root / ".git", "main", "a" * 40)
            sync_lock = Path(str(lock_path) + ".sync")

            with workspace_lock(sync_lock):
                with self.assertRaisesRegex(ReviewSyncError, "workspace lock timeout"):
                    _sync_project(
                        project,
                        identity,
                        GitRunner("git"),
                        Path("gh"),
                        timeout=1.0,
                        lock_timeout=0.05,
                    )

    def test_sync_now_total_timeout_returns_pending_and_leaves_worker_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / "state.lock"
            project = {
                "workspace_id": "workspace-1",
                "state_path": str(root / "state.json"),
                "lock_path": str(lock_path),
            }
            identity = WorkspaceIdentity(root, root / ".git", root / ".git", "main", "a" * 40)
            sync_lock = Path(str(lock_path) + ".sync")

            with workspace_lock(sync_lock):
                started = time.monotonic()
                result = _sync_project_bounded(
                    project,
                    identity,
                    Path("git"),
                    Path("gh"),
                    total_timeout=0.2,
                )

            self.assertLess(time.monotonic() - started, 3.0)
            self.assertEqual(result.state, "PENDING")
            self.assertIn("total timeout", result.message)

    def test_policy_error_returns_structured_nonzero_result_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = _repo(root)
            data = root / "data"
            codex_home = root / "codex"
            codex_home.mkdir()
            atomic_write_json(data / "config.json", {"schema_version": 999, "projects": []})
            output = io.StringIO()

            code = main(
                [
                    "status",
                    "--cwd",
                    str(repo),
                    "--data-root",
                    str(data),
                    "--codex-home",
                    str(codex_home),
                    "--json",
                ],
                stdout=output,
            )

            self.assertEqual(code, 2)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["GITHUB"], "POLICY_BLOCKED")
            self.assertEqual(payload["error_type"], "PolicyError")
            self.assertNotIn("Traceback", output.getvalue())

    def test_unregistered_status_and_mutating_commands_report_needs_setup_without_gh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = _repo(root)
            data = root / "data"
            codex_home = root / "codex"
            codex_home.mkdir()
            for command in (
                ["status"],
                ["sync-now"],
                ["pause", "--reason", "no upload"],
                ["resume", "--choice", "abandon"],
                ["disable", "--reason", "disabled"],
            ):
                output = io.StringIO()
                code = main(
                    [
                        *command,
                        "--cwd",
                        str(repo),
                        "--data-root",
                        str(data),
                        "--codex-home",
                        str(codex_home),
                        "--json",
                    ],
                    stdout=output,
                )
                payload = json.loads(output.getvalue())
                self.assertEqual(code, 0)
                self.assertEqual(payload["GITHUB"], "NEEDS_SETUP")
                self.assertEqual(payload["CHATGPT_ACCESS"], "NOT_VERIFIED")
            self.assertFalse((data / "config.json").exists())
            self.assertEqual(_git(repo, "remote"), "")

    def test_registration_uses_explicit_target_without_editing_source_remotes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = _repo(root)
            data = root / "data"
            identity = discover_workspace(repo, GitRunner("git"))
            verifier = RecordingVerifier()
            before = (repo / ".git" / "config").read_bytes()

            project = register_project(
                identity,
                data,
                RemoteTarget(
                    kind="github",
                    identity="owner/private-repo",
                    push_url="https://github.com/owner/private-repo.git",
                    remote_ref=f"refs/heads/codex-sync/device/{workspace_id(identity)}",
                ),
                verifier,
                reviewed_workflow_digest=None,
            )

            self.assertEqual(project["target"]["identity"], "owner/private-repo")
            self.assertEqual(len(verifier.targets), 1)
            self.assertEqual((repo / ".git" / "config").read_bytes(), before)
            self.assertEqual(_git(repo, "remote"), "")

    def test_registered_status_exposes_only_semantic_keys_needed_for_reauthorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = _repo(root)
            data = root / "data"
            codex_home = root / "codex"
            codex_home.mkdir()
            identity = discover_workspace(repo, GitRunner("git"))
            project = register_project(
                identity,
                data,
                RemoteTarget(
                    kind="github",
                    identity="owner/private-repo",
                    push_url="https://github.com/owner/private-repo.git",
                    remote_ref=f"refs/heads/codex-sync/device/{workspace_id(identity)}",
                ),
                RecordingVerifier(),
                reviewed_workflow_digest=None,
            )
            store = WorkspaceStateStore(
                Path(project["state_path"]),
                Path(project["lock_path"]),
                project["workspace_id"],
            )
            key = enqueue_request(
                store,
                SyncRequest(
                    source_fingerprint="a" * 64,
                    task_state="blocked",
                    objective="review",
                    verification_result="failed",
                    verification_source_digest="a" * 64,
                    reason="blocked",
                    lifecycle_event="manual_request",
                    event_id="event",
                    created_at="time",
                ),
            )
            output = io.StringIO()

            self.assertEqual(
                main(
                    [
                        "status",
                        "--cwd",
                        str(repo),
                        "--data-root",
                        str(data),
                        "--codex-home",
                        str(codex_home),
                        "--json",
                    ],
                    stdout=output,
                ),
                0,
            )

            payload = json.loads(output.getvalue())
            self.assertEqual(payload["pending"], 1)
            self.assertEqual(payload["pending_semantic_keys"], [key])


if __name__ == "__main__":
    unittest.main()
