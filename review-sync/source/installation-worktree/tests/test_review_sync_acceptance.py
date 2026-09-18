from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from review_sync.engine import SyncEngine
from review_sync.git import GitRunner
from review_sync.github import LocalTargetVerifier
from review_sync.model import CapturePolicy, RemoteTarget, TaskStatus
from review_sync.queue import WorkspaceStateStore, record_local_checkpoint
from review_sync.snapshot import assemble_candidate, create_checkpoint
from review_sync.workspace import capture_workspace, discover_workspace


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


class AcceptanceTests(unittest.TestCase):
    def test_full_local_checkpoint_pipeline_preserves_source_worktree_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source with spaces"
            source.mkdir()
            _git(source, "init")
            _git(source, "config", "user.email", "<REDACTED_EMAIL>")
            _git(source, "config", "user.name", "Tests")
            (source / "both.txt").write_bytes(b"base\r\n")
            (source / "delete.txt").write_bytes(b"delete\n")
            _git(source, "add", "both.txt", "delete.txt")
            _git(source, "commit", "-m", "fixture")
            (source / "both.txt").write_bytes(b"staged\r\n")
            _git(source, "add", "both.txt")
            (source / "both.txt").write_bytes(b"working\r\n")
            (source / "delete.txt").unlink()
            (source / "新 file.py").write_bytes(b"print('new')\n")
            git_dir = Path(_git(source, "rev-parse", "--path-format=absolute", "--git-dir").decode().strip())
            index = Path(_git(source, "rev-parse", "--path-format=absolute", "--git-path", "index").decode().strip())
            before = {
                "head": _git(source, "rev-parse", "HEAD"),
                "branch": _git(source, "branch", "--show-current"),
                "status": _git(source, "status", "--porcelain=v2", "-z"),
                "index": index.read_bytes(),
                "both": (source / "both.txt").read_bytes(),
                "new": (source / "新 file.py").read_bytes(),
            }
            runner = GitRunner("git")
            capture = capture_workspace(
                discover_workspace(source / ".", runner),
                CapturePolicy(max_file_bytes=1024 * 1024),
                runner,
            )
            task = TaskStatus(
                task_id="acceptance",
                task_state="blocked",
                objective="review",
                blockers=("business blocker",),
                verification_result="failed",
                verification_source_digest=capture.source_fingerprint,
            )
            candidate = assemble_candidate(capture, task, captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
            store = root / "snapshot.git"
            checkpoint = create_checkpoint(store, candidate, None, runner)
            remote = root / "remote.git"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            state = WorkspaceStateStore(root / "state.json", root / "state.lock", "workspace")
            record_local_checkpoint(state, checkpoint.sha, checkpoint.semantic_digest)
            target = RemoteTarget(
                kind="local-test",
                identity="local/test",
                push_url=str(remote),
                remote_ref="refs/heads/codex-sync/device/worktree",
            )

            result = SyncEngine(runner, LocalTargetVerifier(), receipt_dir=root / "receipts").upload_checkpoint(
                store, state, target, checkpoint.sha
            )

            self.assertEqual(result.state, "REMOTE_VERIFIED")
            self.assertEqual(_git(source, "rev-parse", "HEAD"), before["head"])
            self.assertEqual(_git(source, "branch", "--show-current"), before["branch"])
            self.assertEqual(_git(source, "status", "--porcelain=v2", "-z"), before["status"])
            self.assertEqual(index.read_bytes(), before["index"])
            self.assertEqual((source / "both.txt").read_bytes(), before["both"])
            self.assertEqual((source / "新 file.py").read_bytes(), before["new"])
            self.assertFalse((source / ".review-sync").exists())
            self.assertNotEqual(checkpoint.sha.encode(), before["head"].strip())

    def test_two_worktrees_get_distinct_identity_and_do_not_share_snapshot_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "<REDACTED_EMAIL>")
            _git(repo, "config", "user.name", "Tests")
            (repo / "file.txt").write_text("one\n", encoding="utf-8")
            _git(repo, "add", "file.txt")
            _git(repo, "commit", "-m", "fixture")
            linked = root / "linked"
            _git(repo, "worktree", "add", "-b", "linked-branch", str(linked))
            first = discover_workspace(repo, GitRunner("git"))
            second = discover_workspace(linked, GitRunner("git"))

            from review_sync.cli import workspace_id

            self.assertEqual(first.common_dir, second.common_dir)
            self.assertNotEqual(first.git_dir, second.git_dir)
            self.assertNotEqual(workspace_id(first), workspace_id(second))


if __name__ == "__main__":
    unittest.main()
