from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .fs import atomic_write_json, canonical_json, read_json, workspace_lock
from .model import PolicyError, SyncRequest, UploadIntent, WorkspaceState


SCHEMA_VERSION = 1
RESUME_CHOICES = {"reauthorize", "abandon", "recapture"}


class WorkspaceStateStore:
    def __init__(self, path: Path, lock_path: Path, workspace_id: str) -> None:
        self.path = path
        self.lock_path = lock_path
        self.workspace_id = workspace_id

    def _initial(self) -> WorkspaceState:
        return WorkspaceState(workspace_id=self.workspace_id)

    def _load_unlocked(self) -> WorkspaceState:
        if not self.path.exists():
            return self._initial()
        raw = read_json(self.path)
        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
            raise PolicyError("workspace state schema is unsupported")
        if raw.get("workspace_id") != self.workspace_id:
            raise PolicyError("workspace state identity does not match registration")
        intent_raw = raw.get("upload_intent")
        intent = UploadIntent(**intent_raw) if isinstance(intent_raw, dict) else None
        return WorkspaceState(
            workspace_id=raw["workspace_id"],
            paused=bool(raw.get("paused", False)),
            pause_reason=raw.get("pause_reason"),
            disabled=bool(raw.get("disabled", False)),
            public_task_state=raw.get("public_task_state", "unknown"),
            public_reason=raw.get("public_reason", "unknown"),
            objective=raw.get("objective", "unknown"),
            completed=list(raw.get("completed", [])),
            remaining=list(raw.get("remaining", [])),
            next_action=raw.get("next_action", "unknown"),
            verification_command=raw.get("verification_command", "not_run"),
            source_fingerprint=raw.get("source_fingerprint"),
            verification_result=raw.get("verification_result", "not_run"),
            verification_source_digest=raw.get("verification_source_digest"),
            pending_requests=list(raw.get("pending_requests", [])),
            safe_local_sha=raw.get("safe_local_sha"),
            remote_verified_sha=raw.get("remote_verified_sha"),
            remote_ever_verified=bool(raw.get("remote_ever_verified", False)),
            upload_intent=intent,
            cancel_requested=bool(raw.get("cancel_requested", False)),
            last_upload_outcome=raw.get("last_upload_outcome"),
            quarantine=raw.get("quarantine"),
            reauthorized_digest=raw.get("reauthorized_digest"),
        )

    def _save_unlocked(self, state: WorkspaceState) -> None:
        if state.workspace_id != self.workspace_id:
            raise PolicyError("refusing to save another workspace identity")
        raw = asdict(state)
        raw["schema_version"] = SCHEMA_VERSION
        atomic_write_json(self.path, raw)

    def load(self) -> WorkspaceState:
        with workspace_lock(self.lock_path):
            return self._load_unlocked()

    def save(self, state: WorkspaceState) -> None:
        with workspace_lock(self.lock_path):
            self._save_unlocked(state)

    def update(self, operation: Callable[[WorkspaceState], Any]) -> Any:
        with workspace_lock(self.lock_path):
            state = self._load_unlocked()
            result = operation(state)
            self._save_unlocked(state)
            return result


def _semantic_request(request: SyncRequest) -> dict[str, Any]:
    return {
        "source_fingerprint": request.source_fingerprint,
        "task_state": request.task_state,
        "objective": request.objective,
        "verification_result": request.verification_result,
        "verification_source_digest": request.verification_source_digest,
        "reason": request.reason,
        "completed": list(request.completed),
        "remaining": list(request.remaining),
        "next_action": request.next_action,
        "verification_command": request.verification_command,
    }


def _request_key(request: SyncRequest) -> str:
    return hashlib.sha256(canonical_json(_semantic_request(request))).hexdigest()


def enqueue_request(store: WorkspaceStateStore, request: SyncRequest) -> str:
    key = _request_key(request)

    def operation(state: WorkspaceState) -> str:
        same_source_unknown = (
            request.task_state == "unknown"
            and state.source_fingerprint == request.source_fingerprint
            and bool(state.pending_requests)
        )
        if same_source_unknown:
            return state.pending_requests[-1]["semantic_key"]
        if any(item.get("semantic_key") == key for item in state.pending_requests):
            return key
        source_changed = (
            state.source_fingerprint is not None
            and state.source_fingerprint != request.source_fingerprint
        )
        if source_changed and request.task_state == "unknown":
            state.public_task_state = "unknown"
            state.public_reason = "unknown"
            state.verification_result = "stale"
            state.verification_source_digest = None
            state.completed = []
            state.remaining = []
            state.next_action = "unknown"
            state.verification_command = "not_run"
        elif request.task_state != "unknown":
            state.public_task_state = request.task_state
            state.public_reason = request.reason
            state.verification_result = request.verification_result
            state.verification_source_digest = request.verification_source_digest
            state.completed = list(request.completed)
            state.remaining = list(request.remaining)
            state.next_action = request.next_action
            state.verification_command = request.verification_command
        state.objective = request.objective
        state.source_fingerprint = request.source_fingerprint
        state.pending_requests.append(
            {
                "semantic_key": key,
                **_semantic_request(request),
                "lifecycle_event": request.lifecycle_event,
                "first_event_id": request.event_id,
                "first_created_at": request.created_at,
            }
        )
        return key

    return store.update(operation)


def pause_workspace(store: WorkspaceStateStore, reason: str) -> None:
    if not reason.strip():
        raise PolicyError("pause reason must be nonempty")

    def operation(state: WorkspaceState) -> None:
        state.paused = True
        state.pause_reason = reason.strip()
        if state.upload_intent is not None:
            state.cancel_requested = True

    store.update(operation)


def disable_workspace(store: WorkspaceStateStore, reason: str) -> None:
    def operation(state: WorkspaceState) -> None:
        state.disabled = True
        state.paused = True
        state.pause_reason = reason.strip() or "disabled"
        if state.upload_intent is not None:
            state.cancel_requested = True

    store.update(operation)


def begin_upload_intent(
    store: WorkspaceStateStore,
    target_identity: str,
    remote_ref: str,
    expected_snapshot_sha: str,
) -> UploadIntent:
    def operation(state: WorkspaceState) -> UploadIntent:
        if state.disabled:
            raise PolicyError("workspace synchronization is disabled")
        if state.paused:
            raise PolicyError("workspace synchronization is paused")
        if state.upload_intent is not None:
            existing = state.upload_intent
            if (
                existing.target_identity == target_identity
                and existing.remote_ref == remote_ref
                and existing.expected_snapshot_sha == expected_snapshot_sha
            ):
                return existing
            raise PolicyError("another upload intent is already pending")
        intent = UploadIntent(
            operation_id=str(uuid.uuid4()),
            target_identity=target_identity,
            remote_ref=remote_ref,
            previous_verified_sha=state.remote_verified_sha,
            expected_snapshot_sha=expected_snapshot_sha,
            safe_parent_sha=state.safe_local_sha,
        )
        state.upload_intent = intent
        state.cancel_requested = False
        return intent

    return store.update(operation)


def claim_upload_start(store: WorkspaceStateStore, operation_id: str) -> bool:
    def operation(state: WorkspaceState) -> bool:
        intent = state.upload_intent
        if intent is None or intent.operation_id != operation_id:
            return False
        if state.paused or state.disabled or state.cancel_requested:
            return False
        state.upload_intent = UploadIntent(**{**asdict(intent), "status": "started"})
        return True

    return store.update(operation)


def complete_upload_intent(
    store: WorkspaceStateStore,
    operation_id: str,
    observed_remote_sha: str,
    *,
    outcome: str,
) -> None:
    def operation(state: WorkspaceState) -> None:
        intent = state.upload_intent
        if intent is None or intent.operation_id != operation_id:
            raise PolicyError("upload completion does not match the persisted intent")
        if observed_remote_sha != intent.expected_snapshot_sha:
            raise PolicyError("observed remote SHA does not match the upload intent")
        state.remote_verified_sha = observed_remote_sha
        state.remote_ever_verified = True
        state.safe_local_sha = observed_remote_sha
        state.last_upload_outcome = outcome
        state.upload_intent = None
        state.cancel_requested = False
        state.pending_requests.clear()

    store.update(operation)


def resume_workspace(
    store: WorkspaceStateStore,
    choice: str,
    *,
    authorized_digest: str | None = None,
) -> None:
    if choice not in RESUME_CHOICES:
        raise PolicyError("resume choice must be reauthorize, abandon, or recapture")

    def operation(state: WorkspaceState) -> None:
        if choice == "reauthorize":
            if not state.pending_requests:
                raise PolicyError("there is no pending request to reauthorize")
            expected = state.pending_requests[-1]["semantic_key"]
            if authorized_digest != expected:
                raise PolicyError("authorized digest does not match pending content")
            state.reauthorized_digest = expected
        elif choice == "abandon":
            state.pending_requests.clear()
            state.upload_intent = None
            state.cancel_requested = False
            state.reauthorized_digest = None
            state.paused = True
            state.disabled = False
            state.pause_reason = "backlog abandoned; current content remains paused"
            return
        else:
            state.pending_requests.clear()
            state.upload_intent = None
            state.cancel_requested = False
            state.safe_local_sha = state.remote_verified_sha
            state.reauthorized_digest = None
            if state.quarantine is not None:
                state.quarantine = {**state.quarantine, "recovery": "authorized_recapture"}
        state.paused = False
        state.disabled = False
        state.pause_reason = None

    store.update(operation)


def quarantine_chain(
    store: WorkspaceStateStore,
    suspicious_sha: str,
    rule_ids: tuple[str, ...],
    *,
    published: bool,
) -> None:
    def operation(state: WorkspaceState) -> None:
        state.paused = True
        state.pause_reason = "published sensitive history" if published else "unpublished candidate quarantined"
        state.quarantine = {
            "state": "PUBLISHED_SENSITIVE_HISTORY" if published else "UNPUBLISHED_QUARANTINE",
            "sha": suspicious_sha,
            "rule_ids": sorted(set(rule_ids)),
        }
        if not published:
            state.safe_local_sha = state.remote_verified_sha

    store.update(operation)


def recapture_from_verified(store: WorkspaceStateStore) -> None:
    resume_workspace(store, "recapture")


def record_local_checkpoint(
    store: WorkspaceStateStore,
    checkpoint_sha: str,
    semantic_digest: str,
) -> None:
    def operation(state: WorkspaceState) -> None:
        state.safe_local_sha = checkpoint_sha
        state.last_upload_outcome = f"LOCAL_CHECKPOINT:{semantic_digest}"

    store.update(operation)
