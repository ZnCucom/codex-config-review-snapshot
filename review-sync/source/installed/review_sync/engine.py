from __future__ import annotations

from pathlib import Path

from .fs import atomic_write_json
from .git import GitRunner
from .github import (
    TargetVerifier,
    push_exact_ref,
    read_remote_ref,
    verify_remote_checkpoint,
)
from .model import GitError, PolicyError, RemoteTarget, SyncResult
from .queue import (
    WorkspaceStateStore,
    begin_upload_intent,
    claim_upload_start,
    complete_upload_intent,
    pause_workspace,
)


class SyncEngine:
    def __init__(
        self,
        runner: GitRunner,
        verifier: TargetVerifier,
        *,
        receipt_dir: Path,
    ) -> None:
        self.runner = runner
        self.verifier = verifier
        self.receipt_dir = receipt_dir

    def _pause_result(
        self,
        store: WorkspaceStateStore,
        state: str,
        snapshot_sha: str,
        operation_id: str,
        reason: str,
    ) -> SyncResult:
        pause_workspace(store, reason)
        return SyncResult(
            state=state,
            snapshot_sha=snapshot_sha,
            operation_id=operation_id,
            message=reason,
        )

    def _verify_and_complete(
        self,
        snapshot_store: Path,
        state_store: WorkspaceStateStore,
        target_url: str,
        target: RemoteTarget,
        expected_sha: str,
        operation_id: str,
        *,
        outcome: str,
    ) -> SyncResult:
        receipt = verify_remote_checkpoint(
            self.runner,
            snapshot_store,
            target_url,
            target.remote_ref,
            expected_sha,
        )
        complete_upload_intent(
            state_store,
            operation_id,
            expected_sha,
            outcome=outcome,
        )
        receipt.update(
            {
                "schema_version": 1,
                "operation_id": operation_id,
                "target_identity": target.identity,
                "remote_ref": target.remote_ref,
                "outcome": outcome,
            }
        )
        atomic_write_json(self.receipt_dir / f"{operation_id}.json", receipt)
        return SyncResult(
            state="REMOTE_VERIFIED",
            snapshot_sha=expected_sha,
            remote_sha=expected_sha,
            operation_id=operation_id,
        )

    def upload_checkpoint(
        self,
        snapshot_store: Path,
        state_store: WorkspaceStateStore,
        target: RemoteTarget,
        snapshot_sha: str,
        *,
        timeout: float = 30.0,
    ) -> SyncResult:
        target_url = self.verifier.verify(target, snapshot_store)
        current = state_store.load()
        intent = current.upload_intent
        if intent is None:
            intent = begin_upload_intent(
                state_store,
                target.identity,
                target.remote_ref,
                snapshot_sha,
            )
        elif (
            intent.target_identity != target.identity
            or intent.remote_ref != target.remote_ref
            or intent.expected_snapshot_sha != snapshot_sha
        ):
            return self._pause_result(
                state_store,
                "REMOTE_DIVERGENCE",
                snapshot_sha,
                intent.operation_id,
                "persisted upload intent does not match the requested target/ref/SHA",
            )
        remote = read_remote_ref(self.runner, snapshot_store, target_url, target.remote_ref)
        if remote == intent.expected_snapshot_sha:
            return self._verify_and_complete(
                snapshot_store,
                state_store,
                target_url,
                target,
                intent.expected_snapshot_sha,
                intent.operation_id,
                outcome="reconciled_pending_remote",
            )
        if remote is None and intent.previous_verified_sha is not None:
            return self._pause_result(
                state_store,
                "REMOTE_REF_DELETED",
                snapshot_sha,
                intent.operation_id,
                "previously verified remote ref is now absent",
            )
        if remote != intent.previous_verified_sha:
            return self._pause_result(
                state_store,
                "REMOTE_DIVERGENCE",
                snapshot_sha,
                intent.operation_id,
                "remote ref is not explained by the persisted upload intent",
            )
        if not claim_upload_start(state_store, intent.operation_id):
            return SyncResult(
                state="PAUSED",
                snapshot_sha=snapshot_sha,
                operation_id=intent.operation_id,
                message="upload did not start because pause/disable/cancellation won the coordination lock",
            )
        try:
            push_exact_ref(
                self.runner,
                snapshot_store,
                target_url,
                target.remote_ref,
                snapshot_sha,
                timeout=timeout,
                cancel_check=lambda: state_store.load().cancel_requested,
            )
        except GitError:
            remote_after_error = read_remote_ref(
                self.runner,
                snapshot_store,
                target_url,
                target.remote_ref,
            )
            if remote_after_error != snapshot_sha:
                return SyncResult(
                    state="PENDING",
                    snapshot_sha=snapshot_sha,
                    remote_sha=remote_after_error,
                    operation_id=intent.operation_id,
                    message="push result is not verified; intent and queue are retained",
                )
            return self._verify_and_complete(
                snapshot_store,
                state_store,
                target_url,
                target,
                snapshot_sha,
                intent.operation_id,
                outcome="remote_verified_after_transport_error",
            )
        return self._verify_and_complete(
            snapshot_store,
            state_store,
            target_url,
            target,
            snapshot_sha,
            intent.operation_id,
            outcome="push_and_verify",
        )
