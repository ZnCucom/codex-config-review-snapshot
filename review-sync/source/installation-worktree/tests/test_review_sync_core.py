from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from review_sync.fs import atomic_write_json, read_json
from review_sync.git import GitRunner
from review_sync.model import PolicyError


PYTHON = Path(os.environ.get("PYTHON", os.sys.executable))


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str], bytes | None]] = []
        self.creation_flags: list[int] = []

    def __call__(
        self,
        command: list[str],
        *,
        env: dict[str, str],
        input: bytes | None,
        stdout: int,
        stderr: int,
        timeout: float,
        check: bool,
        creationflags: int = 0,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((command, env, input))
        self.creation_flags.append(creationflags)
        return subprocess.CompletedProcess(command, 0, b"ok\n", b"")


class ReviewSyncCoreTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows console behavior")
    def test_git_runner_suppresses_child_console_windows(self) -> None:
        recorder = RecordingExecutor()

        GitRunner("git", executor=recorder).source(Path("C:/repo"), "status", "--porcelain=v2")

        self.assertEqual(recorder.creation_flags, [subprocess.CREATE_NO_WINDOW])

    def test_atomic_json_is_canonical_utf8_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "state" / "状态.json"
            atomic_write_json(target, {"z": 1, "name": "中文", "items": [2, 1]})

            self.assertEqual(
                target.read_bytes(),
                b'{"items":[2,1],"name":"\xe4\xb8\xad\xe6\x96\x87","z":1}\n',
            )
            self.assertEqual(read_json(target)["name"], "中文")

    def test_source_runner_removes_repository_environment_and_suppresses_optional_locks(self) -> None:
        recorder = RecordingExecutor()
        runner = GitRunner(
            "git",
            base_env={
                "PATH": os.environ.get("PATH", ""),
                "GIT_DIR": "wrong",
                "GIT_WORK_TREE": "wrong",
                "GIT_INDEX_FILE": "wrong",
                "GIT_OBJECT_DIRECTORY": "wrong",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": "wrong",
            },
            executor=recorder,
        )

        self.assertEqual(runner.source(Path("C:/repo path"), "status", "--porcelain=v2"), b"ok\n")

        command, environment, _ = recorder.calls[-1]
        self.assertEqual(command, ["git", "-C", "C:\\repo path", "status", "--porcelain=v2"])
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        ):
            self.assertNotIn(name, environment)

    def test_source_runner_rejects_mutation_before_process_start(self) -> None:
        recorder = RecordingExecutor()
        runner = GitRunner("git", executor=recorder)

        with self.assertRaisesRegex(PolicyError, "source Git command is not read-only"):
            runner.source(Path("."), "add", "-A")

        self.assertEqual(recorder.calls, [])

    def test_snapshot_runner_ignores_global_configuration_and_inherited_object_store(self) -> None:
        recorder = RecordingExecutor()
        runner = GitRunner(
            "git",
            base_env={"PATH": os.environ.get("PATH", ""), "GIT_OBJECT_DIRECTORY": "wrong"},
            executor=recorder,
        )

        runner.snapshot(Path("C:/store path.git"), "hash-object", "-w", "--stdin", input_bytes=b"a\r\n")

        command, environment, input_bytes = recorder.calls[-1]
        self.assertEqual(
            command,
            ["git", "--git-dir", "C:\\store path.git", "-c", "core.hooksPath=NUL", "hash-object", "-w", "--stdin"],
        )
        self.assertEqual(input_bytes, b"a\r\n")
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertNotIn("GIT_OBJECT_DIRECTORY", environment)

    def test_real_source_query_handles_unicode_and_spaces_without_index_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "仓库 with spaces"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "<REDACTED_EMAIL>")
            _git(repo, "config", "user.name", "Tests")
            source = repo / "中文 file.txt"
            source.write_bytes(b"line one\r\n")
            _git(repo, "add", "--", source.name)
            _git(repo, "commit", "-m", "fixture")
            index_path = Path(_git(repo, "rev-parse", "--git-path", "index").decode().strip())
            if not index_path.is_absolute():
                index_path = repo / index_path
            before = index_path.read_bytes()

            output = GitRunner("git").source(repo, "ls-files", "-z")

            self.assertEqual(output, "中文 file.txt\0".encode("utf-8"))
            self.assertEqual(index_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
