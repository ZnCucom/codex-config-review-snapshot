from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from review_sync.git import GitRunner
from review_sync.model import (
    CandidateBlocked,
    CapturedFile,
    CapturedWorkspace,
    SourceStatus,
    TaskStatus,
    WorkspaceIdentity,
)
from review_sync.snapshot import assemble_candidate, create_checkpoint, semantic_digest


T1 = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 16, 12, 10, tzinfo=timezone.utc)


def _capture(root: Path) -> CapturedWorkspace:
    identity = WorkspaceIdentity(
        root=root,
        git_dir=root / ".git",
        common_dir=root / ".git",
        branch="feature/review",
        head="1" * 40,
    )
    return CapturedWorkspace(
        identity=identity,
        files={
            "src/中文 file.txt": CapturedFile(
                path="src/中文 file.txt",
                mode="100644",
                data=b"line one\r\n",
                digest="7bca690071a7792e0d344075f0f2f07501cfd1cf149d279a47d2e7d9f4eea6f0",
                tracked=True,
            ),
            "script.sh": CapturedFile(
                path="script.sh",
                mode="100755",
                data=b"#!/bin/sh\necho ok\n",
                digest="8d3a7a2b5e6e2c5e73ad8a3819a915be9e942c4b307174f58120560386682460",
                tracked=True,
            ),
        },
        status={"src/中文 file.txt": SourceStatus(index="M", worktree="M")},
        notes={"src/中文 file.txt": "index-only version not separately archived"},
        source_fingerprint="a" * 64,
        attempts=1,
    )


def _task(**overrides: object) -> TaskStatus:
    values: dict[str, object] = {
        "task_id": "task-local",
        "task_state": "blocked",
        "objective": "create review checkpoint",
        "completed": ("capture",),
        "remaining": ("upload",),
        "blockers": ("no authorized target",),
        "next_action": "request review",
        "verification_command": "python -m unittest",
        "verification_result": "passed",
        "verification_source_digest": "a" * 64,
        "lifecycle_event": "Stop",
    }
    values.update(overrides)
    return TaskStatus(**values)


def _git_dir(store: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "--git-dir", str(store), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


class SnapshotTests(unittest.TestCase):
    def test_secret_in_generated_handoff_blocks_before_store_or_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            store = Path(temporary) / "snapshot.git"
            secret = "ghp_" + "ABCDEFGHIJKLMNOPQR" + "STUVWXYZ0123456789"
            candidate = assemble_candidate(
                _capture(root),
                _task(blockers=(secret,)),
                captured_at=T1,
            )

            with self.assertRaises(CandidateBlocked) as raised:
                create_checkpoint(store, candidate, None, GitRunner("git"))

            self.assertFalse(store.exists())
            self.assertNotIn(secret, str(raised.exception))

    def test_capture_time_and_lifecycle_event_do_not_change_semantic_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = assemble_candidate(_capture(root), _task(lifecycle_event="Stop"), captured_at=T1)
            second = assemble_candidate(
                _capture(root),
                _task(lifecycle_event="SessionEnd"),
                captured_at=T2,
            )

            self.assertEqual(semantic_digest(first), semantic_digest(second))
            self.assertNotEqual(first.files[".review-sync/manifest.json"].data, second.files[".review-sync/manifest.json"].data)

    def test_raw_bytes_modes_and_parent_chain_are_verified_in_independent_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            store = Path(temporary) / "snapshot store.git"
            runner = GitRunner("git")
            first_candidate = assemble_candidate(_capture(root), _task(), captured_at=T1)

            first = create_checkpoint(store, first_candidate, None, runner)
            duplicate = create_checkpoint(store, first_candidate, first.sha, runner)
            second_candidate = assemble_candidate(
                _capture(root),
                _task(task_state="review_wait", blockers=(), next_action="review"),
                captured_at=T2,
            )
            second = create_checkpoint(store, second_candidate, first.sha, runner)

            self.assertTrue(first.created)
            self.assertFalse(duplicate.created)
            self.assertEqual(duplicate.sha, first.sha)
            self.assertTrue(second.created)
            self.assertNotEqual(first.sha, second.sha)
            self.assertNotEqual(first.sha, first_candidate.source_head)
            self.assertEqual(
                _git_dir(store, "show", f"{first.sha}:src/中文 file.txt"),
                b"line one\r\n",
            )
            listing = _git_dir(store, "ls-tree", "-r", first.sha).decode("utf-8")
            self.assertIn("100644 blob", listing)
            self.assertIn("100755 blob", listing)
            parents = _git_dir(store, "show", "-s", "--format=%P", second.sha).decode().strip()
            self.assertEqual(parents, first.sha)
            self.assertEqual(_git_dir(store, "rev-parse", "refs/review-sync/safe").decode().strip(), second.sha)

    def test_old_verification_is_marked_stale_for_changed_source_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = assemble_candidate(
                _capture(Path(temporary)),
                _task(verification_source_digest="b" * 64, task_state="complete"),
                captured_at=T1,
            )

            handoff = candidate.files[".review-sync/HANDOFF.md"].data.decode("utf-8")
            self.assertIn("Verification evidence: stale", handoff)
            self.assertIn("Task state: unknown", handoff)


if __name__ == "__main__":
    unittest.main()
