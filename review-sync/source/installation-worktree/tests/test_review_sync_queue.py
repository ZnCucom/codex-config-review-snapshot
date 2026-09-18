from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from review_sync.model import PolicyError, SyncRequest
from review_sync.queue import (
    WorkspaceStateStore,
    begin_upload_intent,
    claim_upload_start,
    complete_upload_intent,
    disable_workspace,
    enqueue_request,
    pause_workspace,
    quarantine_chain,
    resume_workspace,
)


def _request(**overrides: object) -> SyncRequest:
    values: dict[str, object] = {
        "source_fingerprint": "a" * 64,
        "task_state": "blocked",
        "objective": "sync review",
        "verification_result": "failed",
        "verification_source_digest": "a" * 64,
        "reason": "blocker",
        "lifecycle_event": "Stop",
        "event_id": "event-1",
        "created_at": "2026-09-16T12:00:00Z",
    }
    values.update(overrides)
    return SyncRequest(**values)


class QueueStateTests(unittest.TestCase):
    def _store(self, root: Path) -> WorkspaceStateStore:
        return WorkspaceStateStore(root / "state.json", root / "state.lock", "workspace-1")

    def test_pause_and_disable_persist_and_block_new_upload_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store(root)
            pause_workspace(store, "user prohibited upload")
            reloaded = self._store(root).load()
            self.assertTrue(reloaded.paused)
            self.assertEqual(reloaded.pause_reason, "user prohibited upload")
            with self.assertRaisesRegex(PolicyError, "paused"):
                begin_upload_intent(store, "owner/repo", "refs/heads/codex-sync/d/w", "b" * 40)

            disable_workspace(store, "disabled by user")
            self.assertTrue(self._store(root).load().disabled)

    def test_unknown_session_end_does_not_replace_blocked_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            enqueue_request(store, _request())
            enqueue_request(
                store,
                _request(
                    task_state="unknown",
                    reason="unknown",
                    lifecycle_event="SessionEnd",
                    event_id="event-2",
                    created_at="2026-09-16T12:10:00Z",
                ),
            )

            state = store.load()
            self.assertEqual(state.public_task_state, "blocked")
            self.assertEqual(len(state.pending_requests), 1)

    def test_new_source_with_unknown_event_stales_completion_and_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            enqueue_request(
                store,
                _request(
                    task_state="complete",
                    verification_result="passed",
                    completed=("old source verified",),
                    remaining=("publish",),
                    next_action="publish old source",
                    verification_command="python -B -m unittest old.tests -v",
                ),
            )
            enqueue_request(
                store,
                _request(
                    source_fingerprint="b" * 64,
                    task_state="unknown",
                    reason="unknown",
                    lifecycle_event="SessionEnd",
                    event_id="event-2",
                    created_at="2026-09-16T12:10:00Z",
                ),
            )

            state = store.load()
            self.assertEqual(state.public_task_state, "unknown")
            self.assertEqual(state.verification_result, "stale")
            self.assertEqual(state.completed, [])
            self.assertEqual(state.remaining, [])
            self.assertEqual(state.next_action, "unknown")
            self.assertEqual(state.verification_command, "not_run")
            self.assertEqual(len(state.pending_requests), 2)

    def test_duplicate_hook_time_and_event_id_do_not_expand_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            first = enqueue_request(store, _request())
            second = enqueue_request(
                store,
                _request(event_id="event-999", created_at="2026-09-16T13:00:00Z", lifecycle_event="SessionEnd"),
            )

            self.assertEqual(first, second)
            self.assertEqual(len(store.load().pending_requests), 1)

    def test_confirmed_pause_prevents_prepared_intent_from_starting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            intent = begin_upload_intent(
                store,
                "owner/repo",
                "refs/heads/codex-sync/device/worktree",
                "b" * 40,
            )
            pause_workspace(store, "task became no-upload")

            self.assertFalse(claim_upload_start(store, intent.operation_id))
            state = store.load()
            self.assertTrue(state.cancel_requested)
            self.assertEqual(state.upload_intent.operation_id, intent.operation_id)

    def test_started_intent_pause_requests_cancellation_and_completion_records_reality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            intent = begin_upload_intent(
                store,
                "owner/repo",
                "refs/heads/codex-sync/device/worktree",
                "b" * 40,
            )
            self.assertTrue(claim_upload_start(store, intent.operation_id))
            pause_workspace(store, "stop now")
            self.assertTrue(store.load().cancel_requested)

            complete_upload_intent(store, intent.operation_id, "b" * 40, outcome="remote_verified_after_cancel")

            state = store.load()
            self.assertEqual(state.remote_verified_sha, "b" * 40)
            self.assertEqual(state.last_upload_outcome, "remote_verified_after_cancel")
            self.assertTrue(state.paused)

    def test_resume_requires_explicit_choice_and_matching_digest_for_reauthorize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            enqueue_request(store, _request())
            pause_workspace(store, "review")
            with self.assertRaisesRegex(PolicyError, "resume choice"):
                resume_workspace(store, "continue")
            with self.assertRaisesRegex(PolicyError, "authorized digest"):
                resume_workspace(store, "reauthorize", authorized_digest="0" * 64)

            pending_digest = store.load().pending_requests[-1]["semantic_key"]
            resume_workspace(store, "reauthorize", authorized_digest=pending_digest)
            self.assertFalse(store.load().paused)

    def test_abandon_clears_backlog_but_does_not_authorize_paused_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            enqueue_request(store, _request())
            pause_workspace(store, "content must not upload")

            resume_workspace(store, "abandon")

            state = store.load()
            self.assertEqual(state.pending_requests, [])
            self.assertTrue(state.paused)
            self.assertEqual(state.pause_reason, "backlog abandoned; current content remains paused")

    def test_quarantine_unpublished_resets_safe_parent_but_published_history_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store(root)
            state = store.load()
            state.remote_verified_sha = "1" * 40
            state.safe_local_sha = "2" * 40
            store.save(state)

            quarantine_chain(store, "2" * 40, ("GITHUB_TOKEN",), published=False)
            unpublished = store.load()
            self.assertEqual(unpublished.safe_local_sha, "1" * 40)
            self.assertTrue(unpublished.paused)

            resume_workspace(store, "recapture")
            state = store.load()
            state.safe_local_sha = "3" * 40
            store.save(state)
            quarantine_chain(store, "3" * 40, ("PRIVATE_KEY",), published=True)
            published = store.load()
            self.assertEqual(published.safe_local_sha, "3" * 40)
            self.assertEqual(published.quarantine["state"], "PUBLISHED_SENSITIVE_HISTORY")

    def test_upload_receipt_clears_transport_queue_without_erasing_public_task_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            enqueue_request(store, _request(task_state="blocked", reason="waiting for reviewer"))
            intent = begin_upload_intent(
                store,
                "owner/repo",
                "refs/heads/codex-sync/device/worktree",
                "b" * 40,
            )
            complete_upload_intent(store, intent.operation_id, "b" * 40, outcome="verified")

            state = store.load()
            self.assertEqual(state.pending_requests, [])
            self.assertEqual(state.public_task_state, "blocked")
            self.assertEqual(state.public_reason, "waiting for reviewer")


if __name__ == "__main__":
    unittest.main()
