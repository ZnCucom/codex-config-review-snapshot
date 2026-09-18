from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from review_sync.git import GitRunner
from review_sync.model import CapturePolicy, UnstableSourceError, UnsupportedWorkspaceError
from review_sync.workspace import capture_workspace, discover_workspace


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def _init_repo(parent: Path, name: str = "仓库 with spaces") -> Path:
    repo = parent / name
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "<REDACTED_EMAIL>")
    _git(repo, "config", "user.name", "Tests")
    (repo / "both.txt").write_bytes(b"base\r\n")
    (repo / "delete.txt").write_bytes(b"delete me\n")
    (repo / "script.sh").write_bytes(b"#!/bin/sh\necho ok\n")
    _git(repo, "add", "--", "both.txt", "delete.txt", "script.sh")
    _git(repo, "update-index", "--chmod=+x", "script.sh")
    _git(repo, "commit", "-m", "fixture")
    return repo


def _index_path(repo: Path) -> Path:
    raw = _git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index")
    return Path(raw.decode("utf-8").strip())


class MutatingRunner(GitRunner):
    def __init__(self, repo: Path, mutate_every_attempt: bool) -> None:
        super().__init__("git")
        self.repo = repo
        self.mutate_every_attempt = mutate_every_attempt
        self.head_calls = 0

    def source(self, repo: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        output = super().source(repo, *arguments, input_bytes=input_bytes)
        if arguments[:3] == ("rev-parse", "--verify", "HEAD"):
            self.head_calls += 1
            after_first_file_read = self.head_calls >= 3 and self.head_calls % 2 == 1
            if after_first_file_read and (self.mutate_every_attempt or self.head_calls == 3):
                target = self.repo / "both.txt"
                target.write_bytes(target.read_bytes() + b"changed\n")
        return output


class WorkspaceCaptureTests(unittest.TestCase):
    def test_descendant_cwd_resolves_exact_worktree_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = _init_repo(Path(temporary))
            nested = repo / "子 目录" / "deeper"
            nested.mkdir(parents=True)

            identity = discover_workspace(nested, GitRunner("git"))

            self.assertEqual(identity.root, repo.resolve())
            self.assertEqual(identity.head, _git(repo, "rev-parse", "HEAD").decode().strip())
            self.assertTrue(identity.git_dir.is_absolute())
            self.assertTrue(identity.common_dir.is_absolute())

    def test_staged_plus_unstaged_uses_worktree_bytes_and_preserves_source_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = _init_repo(Path(temporary))
            (repo / "both.txt").write_bytes(b"staged\r\n")
            _git(repo, "add", "--", "both.txt")
            (repo / "both.txt").write_bytes(b"working-copy\r\n")
            index = _index_path(repo)
            index_before = index.read_bytes()
            head_before = _git(repo, "rev-parse", "HEAD")
            branch_before = _git(repo, "branch", "--show-current")

            captured = capture_workspace(
                discover_workspace(repo, GitRunner("git")),
                CapturePolicy(max_file_bytes=1024 * 1024),
                GitRunner("git"),
            )

            self.assertEqual(captured.files["both.txt"].data, b"working-copy\r\n")
            self.assertEqual(captured.status["both.txt"].index, "M")
            self.assertEqual(captured.status["both.txt"].worktree, "M")
            self.assertEqual(
                captured.notes["both.txt"],
                "index-only version not separately archived",
            )
            self.assertEqual(index.read_bytes(), index_before)
            self.assertEqual(_git(repo, "rev-parse", "HEAD"), head_before)
            self.assertEqual(_git(repo, "branch", "--show-current"), branch_before)

    def test_deleted_untracked_unicode_and_executable_mode_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = _init_repo(Path(temporary))
            (repo / "delete.txt").unlink()
            (repo / "新 file.txt").write_bytes(b"new\n")

            captured = capture_workspace(
                discover_workspace(repo, GitRunner("git")),
                CapturePolicy(max_file_bytes=1024 * 1024),
                GitRunner("git"),
            )

            self.assertNotIn("delete.txt", captured.files)
            self.assertEqual(captured.status["delete.txt"].worktree, "D")
            self.assertEqual(captured.files["新 file.txt"].data, b"new\n")
            self.assertEqual(captured.status["新 file.txt"].index, "?")
            self.assertEqual(captured.files["script.sh"].mode, "100755")

    def test_one_mid_capture_change_retries_and_returns_the_later_stable_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = _init_repo(Path(temporary))
            runner = MutatingRunner(repo, mutate_every_attempt=False)

            captured = capture_workspace(
                discover_workspace(repo, runner),
                CapturePolicy(max_file_bytes=1024 * 1024),
                runner,
                retries=2,
            )

            self.assertEqual(captured.files["both.txt"].data, b"base\r\nchanged\n")
            self.assertGreaterEqual(captured.attempts, 2)

    def test_continuous_mid_capture_changes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = _init_repo(Path(temporary))
            runner = MutatingRunner(repo, mutate_every_attempt=True)

            with self.assertRaisesRegex(UnstableSourceError, "did not remain stable"):
                capture_workspace(
                    discover_workspace(repo, runner),
                    CapturePolicy(max_file_bytes=1024 * 1024),
                    runner,
                    retries=2,
                )

    @unittest.skipIf(os.name == "nt" and False, "kept explicit for platform diagnostics")
    def test_unmerged_index_is_reported_without_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = _init_repo(Path(temporary))
            blob_a = _git(repo, "hash-object", "-w", "--stdin", input_bytes=b"a\n").decode().strip()
            blob_b = _git(repo, "hash-object", "-w", "--stdin", input_bytes=b"b\n").decode().strip()
            index_before_path = _index_path(repo)
            index_info = (
                f"100644 {blob_a} 1\tconflict.txt\n"
                f"100644 {blob_a} 2\tconflict.txt\n"
                f"100644 {blob_b} 3\tconflict.txt\n"
            ).encode("utf-8")
            _git(repo, "update-index", "--index-info", input_bytes=index_info)
            index_before = index_before_path.read_bytes()

            with self.assertRaisesRegex(UnsupportedWorkspaceError, "unmerged index"):
                capture_workspace(
                    discover_workspace(repo, GitRunner("git")),
                    CapturePolicy(max_file_bytes=1024 * 1024),
                    GitRunner("git"),
                )

            self.assertEqual(index_before_path.read_bytes(), index_before)


if __name__ == "__main__":
    unittest.main()
