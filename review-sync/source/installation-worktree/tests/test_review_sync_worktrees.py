from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_sync.cli import _project_for_cwd, _state_store, register_project, tick_registered
from review_sync.git import GitRunner
from review_sync.github import LocalTargetVerifier
from review_sync.hook import handle_hook
from review_sync.model import PolicyError, RemoteTarget
from review_sync.queue import pause_workspace
from review_sync.registration import registered_worktrees
from review_sync.workspace import discover_workspace
from tests.test_review_sync_cli import _git, _repo, RecordingVerifier


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = _repo(self.root)
        self.data = self.root / "data"
        self.runner = GitRunner("git")
        self.remote = self.root / "remote.git"
        self.remote.mkdir()
        _git(self.remote, "init", "--bare")
        self.registration = register_project(
            discover_workspace(self.repo, self.runner), self.data,
            RemoteTarget(kind="local-test", identity="fixture", push_url=str(self.remote),
                         remote_ref="refs/heads/codex-sync/device/main"),
            RecordingVerifier(), reviewed_workflow_digest=None)
        self.registry = (self.data / "config.json").read_bytes()
        self.a, self.b = self.root / "worktree A", self.root / "worktree B"
        for path, branch in ((self.a, "feature-a"), (self.b, "feature-b")):
            _git(self.repo, "worktree", "add", "-b", branch, str(path))

    def hook(self, path, event="Stop"):
        return handle_hook(event, json.dumps({"cwd": str(path), "hook_event_name": event,
                                              "turn_id": "same-turn"}).encode(), self.data, self.runner)

    def project(self, path):
        result = _project_for_cwd(path, self.data, self.runner)
        self.assertIsNotNone(result)
        return result[0]

    def tick(self):
        with patch("review_sync.cli.GitHubTargetVerifier", return_value=LocalTargetVerifier()):
            return tick_registered(self.data, Path("git"), Path("gh"))

    def test_matching_isolation_hooks_and_schema_one_compatibility(self):
        (self.a / "source.py").write_text("print('A')\n")
        (self.b / "source.py").write_text("print('B')\n")
        self.hook(self.a)
        pa, pb = self.project(self.a), self.project(self.b)
        sa, sb = _state_store(pa), _state_store(pb)
        before = Path(pa["state_path"]).read_bytes()
        self.hook(self.b, "Interrupt")
        self.assertEqual(Path(pa["state_path"]).read_bytes(), before)
        self.assertEqual(len(sa.load().pending_requests), 1)
        self.assertEqual(len(sb.load().pending_requests), 1)
        for key in ("workspace_id", "state_path", "lock_path", "store_path", "receipt_dir"):
            self.assertNotEqual(pa[key], pb[key])
        self.assertNotEqual(pa["target"]["remote_ref"], pb["target"]["remote_ref"])
        self.assertNotEqual(sa.load().source_fingerprint, sb.load().source_fingerprint)
        self.assertEqual(self.project(self.repo), self.registration)
        self.assertEqual((self.data / "config.json").read_bytes(), self.registry)

    def test_stop_session_end_single_semantic_upload_and_unchanged_tick(self):
        self.hook(self.a)
        self.hook(self.a, "SessionEnd")
        pa = self.project(self.a)
        self.assertEqual(len(_state_store(pa).load().pending_requests), 1)
        first = self.tick()
        self.assertEqual([x.state for x in first], ["REMOTE_VERIFIED"] * 3)
        self.assertEqual([x.state for x in self.tick()], ["NO_CHANGE"] * 3)
        receipts = list(Path(pa["receipt_dir"]).glob("*.json"))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(_state_store(pa).load().pending_requests, [])
        self.assertEqual(len({x.snapshot_sha for x in first}), 3)

    def test_similar_unregistered_and_spoofed_gitfile_are_ignored(self):
        other = self.root / "other"
        other.mkdir()
        repo = _repo(other)
        _git(repo, "branch", "-m", "feature-a")
        self.assertIsNone(_project_for_cwd(repo, self.data, self.runner))
        fake = self.root / "fake-worktree"
        fake.mkdir()
        (fake / ".git").write_bytes((self.a / ".git").read_bytes())
        self.assertIsNone(_project_for_cwd(fake, self.data, self.runner))
        self.hook(fake)
        self.assertFalse((self.data / "state").exists())

    def test_removed_worktree_skipped_without_losing_other_worktrees(self):
        shutil.rmtree(self.a)  # Stale Git administrative entry deliberately retained.
        identities = list(registered_worktrees(self.registration, self.runner))
        self.assertEqual({i.root for i in identities}, {self.repo.resolve(), self.b.resolve()})
        self.assertEqual([x.state for x in self.tick()], ["REMOTE_VERIFIED"] * 2)
        self.assertEqual([x.state for x in self.tick()], ["NO_CHANGE"] * 2)

    def test_pause_is_independent_and_blocks_affected_worktree(self):
        pa = self.project(self.a)
        pause_workspace(_state_store(pa), "fixture pause")
        states = [x.state for x in self.tick()]
        self.assertEqual(states.count("PAUSED"), 1)
        self.assertEqual(states.count("REMOTE_VERIFIED"), 2)

    def test_source_runner_rejects_worktree_mutation(self):
        with self.assertRaises(PolicyError):
            self.runner.source(self.repo, "worktree", "remove", str(self.a))
