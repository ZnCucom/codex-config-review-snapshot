from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from review_sync.fs import atomic_write_json
from review_sync.git import GitRunner
from review_sync.hook import handle_hook
from review_sync.queue import WorkspaceStateStore


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.strip()


class HookTests(unittest.TestCase):
    def _fixture(self, root: Path):
        repo = root / "repo"
        nested = repo / "sub" / "dir"
        nested.mkdir(parents=True)
        _git(repo, "init")
        _git(repo, "config", "user.email", "<REDACTED_EMAIL>")
        _git(repo, "config", "user.name", "Tests")
        (repo / "source.txt").write_text("source\n", encoding="utf-8")
        _git(repo, "add", "source.txt")
        _git(repo, "commit", "-m", "fixture")
        git_dir = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
        common_dir = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        data = root / "data"
        state_path = data / "state" / "workspace.json"
        lock_path = data / "locks" / "workspace.lock"
        atomic_write_json(
            data / "config.json",
            {
                "schema_version": 1,
                "projects": [
                    {
                        "workspace_id": "workspace",
                        "root": str(repo.resolve()),
                        "git_dir": str(git_dir.resolve()),
                        "common_dir": str(common_dir.resolve()),
                        "state_path": str(state_path),
                        "lock_path": str(lock_path),
                    }
                ],
            },
        )
        return repo, nested, data, WorkspaceStateStore(state_path, lock_path, "workspace")

    def test_stop_from_descendant_cwd_enqueues_without_reading_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, nested, data, store = self._fixture(Path(temporary))
            transcript = Path(temporary) / "transcript.jsonl"
            secret = "ghp_" + "ABCDEFGHIJKLMNOPQR" + "STUVWXYZ0123456789"
            transcript.write_text(secret, encoding="utf-8")
            payload = {
                "hook_event_name": "Stop",
                "cwd": str(nested),
                "session_id": "session",
                "last_assistant_message": "done",
                "transcript_path": str(transcript),
            }

            output = handle_hook("Stop", json.dumps(payload).encode(), data, GitRunner("git"))

            self.assertEqual(json.loads(output), {"continue": True, "suppressOutput": True})
            serialized = (data / "state" / "workspace.json").read_text(encoding="utf-8")
            self.assertNotIn(secret, serialized)
            self.assertEqual(len(store.load().pending_requests), 1)
            self.assertEqual(store.load().pending_requests[0]["lifecycle_event"], "Stop")
            self.assertEqual(repo.resolve(), Path(json.loads((data / "config.json").read_text())["projects"][0]["root"]))

    def test_interrupt_and_session_end_do_not_require_turn_id_or_emit_steering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _repo, nested, data, store = self._fixture(Path(temporary))
            for event in ("Interrupt", "SessionEnd"):
                payload = {"hook_event_name": event, "cwd": str(nested), "reason": "other"}
                self.assertEqual(handle_hook(event, json.dumps(payload).encode(), data, GitRunner("git")), b"")
            self.assertEqual(len(store.load().pending_requests), 1)

    def test_unregistered_cwd_is_noop_but_stop_output_remains_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _repo, _nested, data, store = self._fixture(root)
            outside = root / "outside"
            outside.mkdir()
            payload = {"hook_event_name": "Stop", "cwd": str(outside)}

            output = handle_hook("Stop", json.dumps(payload).encode(), data, GitRunner("git"))

            self.assertEqual(json.loads(output), {"continue": True, "suppressOutput": True})
            self.assertEqual(store.load().pending_requests, [])


if __name__ == "__main__":
    unittest.main()
