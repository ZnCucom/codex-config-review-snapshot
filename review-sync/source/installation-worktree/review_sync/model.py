from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Any


class ReviewSyncError(RuntimeError):
    """Base error for deterministic review-sync operations."""


class PolicyError(ReviewSyncError):
    """An operation was rejected by a local authorization or safety gate."""


class GitError(ReviewSyncError):
    """A bounded Git subprocess failed."""


class UnsupportedWorkspaceError(PolicyError):
    """The source contains a Git/filesystem state not supported by v1."""


class UnstableSourceError(ReviewSyncError):
    """The source changed while a stable snapshot was being collected."""


class CandidateBlocked(PolicyError):
    """A candidate failed a mandatory local safety gate."""

    def __init__(self, report: "ScanReport") -> None:
        self.report = report
        super().__init__(report.render())


@dataclass(frozen=True)
class CapturePolicy:
    max_file_bytes: int = 5 * 1024 * 1024
    include_untracked: bool = True


@dataclass(frozen=True)
class WorkspaceIdentity:
    root: Path
    git_dir: Path
    common_dir: Path
    branch: str
    head: str


@dataclass(frozen=True)
class SourceStatus:
    index: str
    worktree: str
    original_path: str | None = None


@dataclass(frozen=True)
class CapturedFile:
    path: str
    mode: str
    data: bytes = field(repr=False)
    digest: str
    tracked: bool


@dataclass(frozen=True)
class CapturedWorkspace:
    identity: WorkspaceIdentity
    files: dict[str, CapturedFile]
    status: dict[str, SourceStatus]
    notes: dict[str, str]
    source_fingerprint: str
    attempts: int


@dataclass(frozen=True)
class CandidateFile:
    mode: str
    data: bytes = field(repr=False)
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.digest:
            object.__setattr__(self, "digest", hashlib.sha256(self.data).hexdigest())


@dataclass(frozen=True, order=True)
class ScanFinding:
    rule_id: str
    path: str
    message: str


@dataclass(frozen=True)
class ScanReport:
    findings: tuple[ScanFinding, ...] = ()

    @property
    def blocked(self) -> bool:
        return bool(self.findings)

    def render(self) -> str:
        return "\n".join(
            f"{finding.rule_id} {finding.path}: {finding.message}"
            for finding in self.findings
        )


@dataclass(frozen=True)
class TaskStatus:
    task_id: str
    task_state: str
    objective: str
    completed: tuple[str, ...] = ()
    remaining: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    next_action: str = "unknown"
    verification_command: str = "not_run"
    verification_result: str = "not_run"
    verification_source_digest: str | None = None
    lifecycle_event: str = "unknown"


@dataclass(frozen=True)
class CandidateSnapshot:
    files: dict[str, CandidateFile]
    semantic_digest: str
    source_head: str
    source_fingerprint: str
    captured_at: str


@dataclass(frozen=True)
class Checkpoint:
    sha: str
    tree_sha: str
    semantic_digest: str
    created: bool


@dataclass(frozen=True)
class SyncRequest:
    source_fingerprint: str
    task_state: str
    objective: str
    verification_result: str
    verification_source_digest: str | None
    reason: str
    lifecycle_event: str
    event_id: str
    created_at: str
    completed: tuple[str, ...] = ()
    remaining: tuple[str, ...] = ()
    next_action: str = "unknown"
    verification_command: str = "not_run"


@dataclass(frozen=True)
class UploadIntent:
    operation_id: str
    target_identity: str
    remote_ref: str
    previous_verified_sha: str | None
    expected_snapshot_sha: str
    safe_parent_sha: str | None
    status: str = "prepared"


@dataclass
class WorkspaceState:
    workspace_id: str
    paused: bool = False
    pause_reason: str | None = None
    disabled: bool = False
    public_task_state: str = "unknown"
    public_reason: str = "unknown"
    objective: str = "unknown"
    completed: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    next_action: str = "unknown"
    verification_command: str = "not_run"
    source_fingerprint: str | None = None
    verification_result: str = "not_run"
    verification_source_digest: str | None = None
    pending_requests: list[dict[str, Any]] = field(default_factory=list)
    safe_local_sha: str | None = None
    remote_verified_sha: str | None = None
    remote_ever_verified: bool = False
    upload_intent: UploadIntent | None = None
    cancel_requested: bool = False
    last_upload_outcome: str | None = None
    quarantine: dict[str, Any] | None = None
    reauthorized_digest: str | None = None


@dataclass(frozen=True)
class RemoteTarget:
    kind: str
    identity: str
    push_url: str
    remote_ref: str
    remote_automation_reviewed: bool = False


@dataclass(frozen=True)
class SyncResult:
    state: str
    snapshot_sha: str | None = None
    remote_sha: str | None = None
    operation_id: str | None = None
    message: str = ""
