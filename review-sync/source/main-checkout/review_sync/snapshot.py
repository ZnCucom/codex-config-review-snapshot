from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .fs import canonical_json
from .git import GitRunner
from .model import (
    CandidateBlocked,
    CandidateFile,
    CandidateSnapshot,
    CapturedWorkspace,
    Checkpoint,
    PolicyError,
    TaskStatus,
)
from .security import enforce_workflow_gate, scan_candidate


SAFE_REF = "refs/review-sync/safe"
ZERO_SHA = "0" * 40


def _effective_task(capture: CapturedWorkspace, task: TaskStatus) -> dict[str, Any]:
    stale = task.verification_source_digest not in {None, capture.source_fingerprint}
    task_state = task.task_state
    if stale and task_state in {"complete", "review_wait"}:
        task_state = "unknown"
    return {
        "task_id": task.task_id,
        "task_state": task_state,
        "objective": task.objective,
        "completed": list(task.completed),
        "remaining": list(task.remaining),
        "blockers": list(task.blockers),
        "next_action": task.next_action,
        "verification_command": task.verification_command,
        "verification_result": "stale" if stale else task.verification_result,
        "verification_source_digest": task.verification_source_digest,
    }


def _semantic_material(capture: CapturedWorkspace, task: TaskStatus) -> dict[str, Any]:
    return {
        "source": {
            "branch": capture.identity.branch,
            "head": capture.identity.head,
            "fingerprint": capture.source_fingerprint,
            "status": {
                path: {
                    "index": item.index,
                    "worktree": item.worktree,
                    "original_path": item.original_path,
                }
                for path, item in sorted(capture.status.items())
            },
            "notes": dict(sorted(capture.notes.items())),
        },
        "files": [
            {
                "path": path,
                "mode": item.mode,
                "digest": hashlib.sha256(item.data).hexdigest(),
                "tracked": item.tracked,
            }
            for path, item in sorted(capture.files.items())
        ],
        "task": _effective_task(capture, task),
    }


def _render_handoff(capture: CapturedWorkspace, task: dict[str, Any], digest: str) -> bytes:
    def bullets(values: list[str]) -> str:
        return "\n".join(f"- {value}" for value in values) if values else "- none"

    text = f"""# Review Sync Handoff

Project: {capture.identity.root.name}
Source branch: {capture.identity.branch or 'detached'}
Source HEAD: {capture.identity.head}
Source fingerprint: {capture.source_fingerprint}
Semantic digest: {digest}
Task ID: {task['task_id']}
Task state: {task['task_state']}
Objective: {task['objective']}
Verification command: {task['verification_command']}
Verification evidence: {task['verification_result']}

## Completed

{bullets(task['completed'])}

## Remaining

{bullets(task['remaining'])}

## Blockers

{bullets(task['blockers'])}

## Next action

{task['next_action']}

## Limitations

- This checkpoint stores allowed current worktree bytes, not a separate index-only file version.
- Source HEAD and snapshot commit are different identities.
- Upload and remote verification are recorded outside this commit.
"""
    return text.encode("utf-8")


def assemble_candidate(
    capture: CapturedWorkspace,
    task: TaskStatus,
    *,
    captured_at: datetime,
) -> CandidateSnapshot:
    material = _semantic_material(capture, task)
    digest = hashlib.sha256(canonical_json(material)).hexdigest()
    effective_task = material["task"]
    files = {
        path: CandidateFile(mode=item.mode, data=item.data)
        for path, item in capture.files.items()
    }
    handoff = _render_handoff(capture, effective_task, digest)
    manifest = {
        "schema_version": 1,
        "captured_at": captured_at.isoformat(),
        "project": capture.identity.root.name,
        "source_branch": capture.identity.branch,
        "source_head": capture.identity.head,
        "source_fingerprint": capture.source_fingerprint,
        "semantic_digest": digest,
        "task": effective_task,
        "source_status": material["source"]["status"],
        "notes": material["source"]["notes"],
        "files": material["files"],
        "limitations": [
            "current allowed worktree bytes only",
            "index-only variants are not separately archived",
            "snapshot SHA and upload receipt are external",
        ],
    }
    files[".review-sync/HANDOFF.md"] = CandidateFile(mode="100644", data=handoff)
    files[".review-sync/manifest.json"] = CandidateFile(
        mode="100644",
        data=canonical_json(manifest),
    )
    return CandidateSnapshot(
        files=files,
        semantic_digest=digest,
        source_head=capture.identity.head,
        source_fingerprint=capture.source_fingerprint,
        captured_at=captured_at.isoformat(),
    )


def semantic_digest(candidate: CandidateSnapshot) -> str:
    return candidate.semantic_digest


def _blob_objects(
    store: Path,
    files: dict[str, CandidateFile],
    runner: GitRunner,
) -> dict[str, tuple[str, str]]:
    blobs: dict[str, tuple[str, str]] = {}
    for path, candidate in sorted(files.items()):
        blob = runner.snapshot(store, "hash-object", "-w", "--stdin", input_bytes=candidate.data)
        blobs[path] = (candidate.mode, blob.decode("ascii").strip())
    return blobs


def _tree_node(blobs: dict[str, tuple[str, str]]) -> dict[str, Any]:
    root: dict[str, Any] = {}
    for path, value in sorted(blobs.items()):
        parts = PurePosixPath(path).parts
        node = root
        for part in parts[:-1]:
            existing = node.setdefault(part, {})
            if not isinstance(existing, dict):
                raise PolicyError(f"candidate path collides with a file: {path}")
            node = existing
        if parts[-1] in node:
            raise PolicyError(f"duplicate candidate path: {path}")
        node[parts[-1]] = value
    return root


def _write_tree(store: Path, node: dict[str, Any], runner: GitRunner) -> str:
    records: list[bytes] = []
    for name, value in sorted(node.items(), key=lambda item: item[0].encode("utf-8")):
        if isinstance(value, dict):
            mode, object_type, object_id = "040000", "tree", _write_tree(store, value, runner)
        else:
            mode, object_id = value
            object_type = "blob"
        records.append(
            mode.encode("ascii")
            + b" "
            + object_type.encode("ascii")
            + b" "
            + object_id.encode("ascii")
            + b"\t"
            + name.encode("utf-8")
            + b"\0"
        )
    return runner.snapshot(store, "mktree", "-z", input_bytes=b"".join(records)).decode("ascii").strip()


def _commit_semantic(store: Path, commit: str, runner: GitRunner) -> str | None:
    body = runner.snapshot(store, "cat-file", "commit", commit).decode("utf-8", errors="replace")
    marker = "Review-Sync-Semantic: "
    for line in body.splitlines():
        if line.startswith(marker):
            return line[len(marker) :].strip()
    return None


def create_checkpoint(
    store: Path,
    candidate: CandidateSnapshot,
    safe_parent: str | None,
    runner: GitRunner,
    *,
    reviewed_workflow_digest: str | None = None,
) -> Checkpoint:
    report = scan_candidate(candidate.files)
    if report.blocked:
        raise CandidateBlocked(report)
    enforce_workflow_gate(candidate.files, reviewed_workflow_digest)
    if safe_parent is not None and store.exists():
        if _commit_semantic(store, safe_parent, runner) == candidate.semantic_digest:
            tree = runner.snapshot(store, "show", "-s", "--format=%T", safe_parent).decode().strip()
            return Checkpoint(
                sha=safe_parent,
                tree_sha=tree,
                semantic_digest=candidate.semantic_digest,
                created=False,
            )
    if not store.exists():
        runner.init_bare(store)
    blobs = _blob_objects(store, candidate.files, runner)
    for path, (_mode, object_id) in blobs.items():
        actual = runner.snapshot(store, "cat-file", "blob", object_id)
        if actual != candidate.files[path].data:
            raise PolicyError(f"snapshot blob differs from scanned bytes: {path}")
    tree = _write_tree(store, _tree_node(blobs), runner)
    message = (
        "Review checkpoint\n\n"
        f"Source-HEAD: {candidate.source_head}\n"
        f"Review-Sync-Semantic: {candidate.semantic_digest}\n"
    ).encode("utf-8")
    timestamp = int(datetime.fromisoformat(candidate.captured_at).timestamp())
    environment = {
        "GIT_AUTHOR_NAME": "Codex Review Sync",
        "GIT_AUTHOR_EMAIL": "<REDACTED_EMAIL>",
        "GIT_COMMITTER_NAME": "Codex Review Sync",
        "GIT_COMMITTER_EMAIL": "<REDACTED_EMAIL>",
        "GIT_AUTHOR_DATE": f"@{timestamp} +0000",
        "GIT_COMMITTER_DATE": f"@{timestamp} +0000",
    }
    arguments = [
        "-c",
        "user.name=Codex Review Sync",
        "-c",
        "user.email=<REDACTED_EMAIL>",
        "commit-tree",
        tree,
    ]
    if safe_parent is not None:
        arguments.extend(["-p", safe_parent])
    commit = runner.snapshot(
        store,
        *arguments,
        input_bytes=message,
        environment_overrides=environment,
    ).decode("ascii").strip()
    expected = safe_parent or ZERO_SHA
    runner.snapshot(store, "update-ref", SAFE_REF, commit, expected)
    return Checkpoint(
        sha=commit,
        tree_sha=tree,
        semantic_digest=candidate.semantic_digest,
        created=True,
    )
