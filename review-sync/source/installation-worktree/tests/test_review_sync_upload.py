from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from review_sync.engine import SyncEngine
from review_sync.git import GitRunner
from review_sync.github import GitHubTargetVerifier, LocalTargetVerifier, push_exact_ref
from review_sync.model import (
    CapturedFile,
    CapturedWorkspace,
    GitError,
    PolicyError,
    RemoteTarget,
    SourceStatus,
    TaskStatus,
    WorkspaceIdentity,
)
from review_sync.queue import WorkspaceStateStore, begin_upload_intent, claim_upload_start
from review_sync.snapshot import assemble_candidate, create_checkpoint


def _git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def _candidate(root: Path, task_state: str = "blocked"):
    data = b"print('checkpoint')\r\n"
    capture = CapturedWorkspace(
        identity=WorkspaceIdentity(root, root / ".git", root / ".git", "feature", "1" * 40),
        files={
            "src/main.py": CapturedFile(
                path="src/main.py",
                mode="100644",
                data=data,
                digest="ignored",
                tracked=True,
            )
        },
        status={"src/main.py": SourceStatus("M", " ")},
        notes={},
        source_fingerprint=("a" if task_state == "blocked" else "b") * 64,
        attempts=1,
    )
    task = TaskStatus(
        task_id="task",
        task_state=task_state,
        objective="checkpoint",
        blockers=("review",) if task_state == "blocked" else (),
        verification_source_digest=capture.source_fingerprint,
    )
    return assemble_candidate(capture, task, captured_at=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc))


class ResponseLostRunner(GitRunner):
    def __init__(self) -> None:
        super().__init__("git")
        self.lost = False

    def network(self, git_dir: Path, *arguments: str, **kwargs: object) -> bytes:
        output = super().network(git_dir, *arguments, **kwargs)
        if arguments and arguments[0] == "push" and not self.lost:
            self.lost = True
            raise GitError("simulated response loss")
        return output


class CancellablePushRunner(GitRunner):
    def __init__(self) -> None:
        super().__init__("git")
        self.started = threading.Event()
        self.cancel_observed = False

    def network(self, git_dir: Path, *arguments: str, **kwargs: object) -> bytes:
        if arguments and arguments[0] == "push":
            self.started.set()
            cancel_check = kwargs.get("cancel_check")
            if not callable(cancel_check):
                raise AssertionError("push did not receive a cancellation check")
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if cancel_check():
                    self.cancel_observed = True
                    raise GitError("simulated cancellable push stopped")
                time.sleep(0.01)
            raise AssertionError("push did not observe the persisted cancellation request")
        return super().network(git_dir, *arguments, **kwargs)


class FakeGhExecutor:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0, json.dumps(self.response).encode(), b"")


class SequencedGhExecutor:
    def __init__(self, *responses: dict[str, object]) -> None:
        self.responses = list(responses)
        self.commands: list[list[str]] = []
        self.keyword_arguments: list[dict[str, object]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(command)
        self.keyword_arguments.append(kwargs)
        response = self.responses.pop(0)
        return subprocess.CompletedProcess(command, 0, json.dumps(response).encode(), b"")


class UploadIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows console behavior")
    def test_github_cli_suppresses_child_console_windows(self) -> None:
        repo = {"nameWithOwner": "authorized/repo", "visibility": "PRIVATE", "viewerPermission": "WRITE"}
        workflows = {"total_count": 0, "workflows": []}
        executor = SequencedGhExecutor(repo, workflows)
        target = RemoteTarget(
            kind="github",
            identity="authorized/repo",
            push_url="https://github.com/authorized/repo.git",
            remote_ref="refs/heads/codex-sync/device/worktree",
        )

        GitHubTargetVerifier(GitRunner("git"), gh_executable="gh", gh_executor=executor).verify(target)

        self.assertEqual(
            [arguments.get("creationflags") for arguments in executor.keyword_arguments],
            [subprocess.CREATE_NO_WINDOW, subprocess.CREATE_NO_WINDOW],
        )

    def _fixture(self, root: Path, runner: GitRunner | None = None):
        runner = runner or GitRunner("git")
        source = root / "source"
        source.mkdir()
        store = root / "store.git"
        remote = root / "remote.git"
        _git("init", "--bare", str(remote))
        checkpoint = create_checkpoint(store, _candidate(source), None, runner)
        state = WorkspaceStateStore(root / "state.json", root / "state.lock", "workspace")
        target = RemoteTarget(
            kind="local-test",
            identity="local/test",
            push_url=str(remote),
            remote_ref="refs/heads/codex-sync/device/worktree",
        )
        engine = SyncEngine(runner, LocalTargetVerifier(), receipt_dir=root / "receipts")
        return runner, source, store, remote, checkpoint, state, target, engine

    def test_exact_ref_push_is_verified_by_sha_handoff_and_source_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner, _source, store, remote, checkpoint, state, target, engine = self._fixture(Path(temporary))

            result = engine.upload_checkpoint(store, state, target, checkpoint.sha)

            self.assertEqual(result.state, "REMOTE_VERIFIED")
            self.assertEqual(result.remote_sha, checkpoint.sha)
            self.assertEqual(
                _git("--git-dir", str(remote), "rev-parse", target.remote_ref).decode().strip(),
                checkpoint.sha,
            )
            self.assertIn(b"Review Sync Handoff", _git("--git-dir", str(remote), "show", f"{checkpoint.sha}:.review-sync/HANDOFF.md"))
            self.assertEqual(_git("--git-dir", str(remote), "show", f"{checkpoint.sha}:src/main.py"), b"print('checkpoint')\r\n")
            receipt = json.loads((Path(temporary) / "receipts" / f"{result.operation_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["remote_verified_sha"], checkpoint.sha)

    def test_response_loss_after_remote_update_is_reconciled_without_duplicate_push(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = ResponseLostRunner()
            _runner, _source, store, _remote, checkpoint, state, target, _engine = self._fixture(root, runner)
            engine = SyncEngine(runner, LocalTargetVerifier(), receipt_dir=root / "receipts")

            result = engine.upload_checkpoint(store, state, target, checkpoint.sha)

            self.assertEqual(result.state, "REMOTE_VERIFIED")
            self.assertTrue(runner.lost)
            self.assertIsNone(state.load().upload_intent)

    def test_pause_during_started_push_requests_abort_and_reports_observed_remote_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = CancellablePushRunner()
            _runner, _source, store, _remote, checkpoint, state, target, _engine = self._fixture(root, runner)
            engine = SyncEngine(runner, LocalTargetVerifier(), receipt_dir=root / "receipts")
            outcome: list[object] = []

            worker = threading.Thread(
                target=lambda: outcome.append(engine.upload_checkpoint(store, state, target, checkpoint.sha)),
                daemon=True,
            )
            worker.start()
            self.assertTrue(runner.started.wait(2.0))
            from review_sync.queue import pause_workspace

            pause_workspace(state, "user requested stop during transfer")
            worker.join(3.0)

            self.assertFalse(worker.is_alive())
            self.assertTrue(runner.cancel_observed)
            self.assertEqual(len(outcome), 1)
            self.assertEqual(outcome[0].state, "PENDING")
            self.assertIsNone(outcome[0].remote_sha)
            self.assertTrue(state.load().paused)
            self.assertIsNotNone(state.load().upload_intent)

    def test_process_exit_after_push_before_receipt_recovers_existing_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner, _source, store, _remote, checkpoint, state, target, engine = self._fixture(Path(temporary))
            intent = begin_upload_intent(state, target.identity, target.remote_ref, checkpoint.sha)
            self.assertTrue(claim_upload_start(state, intent.operation_id))
            push_exact_ref(runner, store, target.push_url, target.remote_ref, checkpoint.sha)

            result = engine.upload_checkpoint(store, state, target, checkpoint.sha)

            self.assertEqual(result.state, "REMOTE_VERIFIED")
            self.assertEqual(result.operation_id, intent.operation_id)
            self.assertIsNone(state.load().upload_intent)

    def test_unexplained_remote_sha_pauses_without_accepting_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner, source, store, remote, checkpoint, state, target, engine = self._fixture(Path(temporary))
            result = engine.upload_checkpoint(store, state, target, checkpoint.sha)
            self.assertEqual(result.state, "REMOTE_VERIFIED")
            second = create_checkpoint(store, _candidate(source, "review_wait"), checkpoint.sha, runner)
            rogue_store = Path(temporary) / "rogue.git"
            rogue = create_checkpoint(rogue_store, _candidate(source, "complete"), None, runner)
            _git("--git-dir", str(remote), "update-ref", "-d", target.remote_ref)
            push_exact_ref(runner, rogue_store, str(remote), target.remote_ref, rogue.sha)

            diverged = engine.upload_checkpoint(store, state, target, second.sha)

            self.assertEqual(diverged.state, "REMOTE_DIVERGENCE")
            self.assertTrue(state.load().paused)
            self.assertNotEqual(rogue.sha, second.sha)

    def test_previously_verified_remote_ref_deletion_is_not_recreated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner, source, store, remote, checkpoint, state, target, engine = self._fixture(Path(temporary))
            self.assertEqual(engine.upload_checkpoint(store, state, target, checkpoint.sha).state, "REMOTE_VERIFIED")
            second = create_checkpoint(store, _candidate(source, "review_wait"), checkpoint.sha, runner)
            _git("--git-dir", str(remote), "update-ref", "-d", target.remote_ref)

            deleted = engine.upload_checkpoint(store, state, target, second.sha)

            self.assertEqual(deleted.state, "REMOTE_REF_DELETED")
            self.assertTrue(state.load().paused)
            self.assertNotEqual(
                subprocess.run(
                    ["git", "--git-dir", str(remote), "rev-parse", "--verify", target.remote_ref],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                ).returncode,
                0,
            )

    def test_github_verifier_rejects_effective_url_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "gitconfig"
            config.write_text(
                "[url \"https://github.com/evil/\"]\n\tpushInsteadOf = https://github.com/authorized/\n",
                encoding="utf-8",
            )
            git = GitRunner("git", base_env={**os.environ, "GIT_CONFIG_GLOBAL": str(config)})
            verifier = GitHubTargetVerifier(
                git,
                gh_executable="gh",
                gh_executor=FakeGhExecutor(
                    {"nameWithOwner": "authorized/repo", "visibility": "PRIVATE", "viewerPermission": "WRITE"}
                ),
            )
            target = RemoteTarget(
                kind="github",
                identity="authorized/repo",
                push_url="https://github.com/authorized/repo.git",
                remote_ref="refs/heads/codex-sync/device/worktree",
            )

            with self.assertRaisesRegex(PolicyError, "effective push URL"):
                verifier.verify(target)

    def test_github_verifier_rejects_public_or_nonwritable_target(self) -> None:
        target = RemoteTarget(
            kind="github",
            identity="authorized/repo",
            push_url="https://github.com/authorized/repo.git",
            remote_ref="refs/heads/codex-sync/device/worktree",
        )
        for response, message in (
            (
                {"nameWithOwner": "authorized/repo", "visibility": "PUBLIC", "viewerPermission": "WRITE"},
                "not private",
            ),
            (
                {"nameWithOwner": "authorized/repo", "visibility": "PRIVATE", "viewerPermission": "READ"},
                "does not grant push permission",
            ),
        ):
            with self.subTest(message=message):
                executor = SequencedGhExecutor(response)
                verifier = GitHubTargetVerifier(GitRunner("git"), gh_executable="gh", gh_executor=executor)
                with self.assertRaisesRegex(PolicyError, message):
                    verifier.verify(target)
                self.assertEqual(len(executor.commands), 1)

    def test_github_verifier_requires_explicit_review_when_remote_actions_exist(self) -> None:
        repo = {"nameWithOwner": "authorized/repo", "visibility": "PRIVATE", "viewerPermission": "WRITE"}
        workflows = {"total_count": 1, "workflows": [{"id": 1, "name": "CI"}]}
        target = RemoteTarget(
            kind="github",
            identity="authorized/repo",
            push_url="https://github.com/authorized/repo.git",
            remote_ref="refs/heads/codex-sync/device/worktree",
        )
        blocked = SequencedGhExecutor(repo, workflows)
        with self.assertRaisesRegex(PolicyError, "automation exists"):
            GitHubTargetVerifier(GitRunner("git"), gh_executable="gh", gh_executor=blocked).verify(target)

        reviewed = RemoteTarget(**{**target.__dict__, "remote_automation_reviewed": True})
        allowed = SequencedGhExecutor(repo, workflows)
        self.assertEqual(
            GitHubTargetVerifier(GitRunner("git"), gh_executable="gh", gh_executor=allowed).verify(reviewed),
            target.push_url,
        )
        self.assertIn("actions/workflows", " ".join(allowed.commands[1]))


if __name__ == "__main__":
    unittest.main()
