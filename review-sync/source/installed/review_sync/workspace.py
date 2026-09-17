from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .git import GitRunner
from .model import (
    CapturePolicy,
    CapturedFile,
    CapturedWorkspace,
    SourceStatus,
    UnstableSourceError,
    UnsupportedWorkspaceError,
    WorkspaceIdentity,
)


def _text(output: bytes) -> str:
    return output.decode("utf-8", errors="surrogateescape").strip()


def discover_workspace(cwd: Path, runner: GitRunner) -> WorkspaceIdentity:
    cwd = cwd.resolve(strict=True)
    root = Path(_text(runner.source(cwd, "rev-parse", "--show-toplevel"))).resolve(strict=True)
    git_dir = Path(
        _text(runner.source(cwd, "rev-parse", "--path-format=absolute", "--git-dir"))
    ).resolve(strict=True)
    common_dir = Path(
        _text(runner.source(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    ).resolve(strict=True)
    branch = _text(runner.source(root, "branch", "--show-current"))
    head = _text(runner.source(root, "rev-parse", "--verify", "HEAD"))
    try:
        cwd.relative_to(root)
    except ValueError as error:
        raise UnsupportedWorkspaceError("hook cwd is not inside the resolved Git worktree") from error
    return WorkspaceIdentity(root=root, git_dir=git_dir, common_dir=common_dir, branch=branch, head=head)


@dataclass(frozen=True)
class _ObservedSource:
    head: str
    branch: str
    status_raw: bytes
    status: dict[str, SourceStatus]
    tracked: dict[str, str]
    untracked: tuple[str, ...]
    index_digest: str

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for value in (self.head, self.branch, self.index_digest):
            digest.update(value.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
        digest.update(self.status_raw)
        digest.update(b"\0")
        for path, mode in sorted(self.tracked.items()):
            digest.update(mode.encode("ascii") + b"\0")
            digest.update(path.encode("utf-8", errors="surrogateescape") + b"\0")
        for path in self.untracked:
            digest.update(b"?\0" + path.encode("utf-8", errors="surrogateescape") + b"\0")
        return digest.hexdigest()


def _decode_path(value: bytes) -> str:
    return value.decode("utf-8", errors="surrogateescape")


def _parse_index(output: bytes) -> dict[str, str]:
    tracked: dict[str, str] = {}
    unmerged: set[str] = set()
    for record in output.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            raw_mode, _object_id, raw_stage = header.split(b" ", 2)
            stage = int(raw_stage)
        except (ValueError, TypeError) as error:
            raise UnsupportedWorkspaceError("unexpected ls-files --stage record") from error
        path = _decode_path(raw_path)
        if stage != 0:
            unmerged.add(path)
            continue
        tracked[path] = raw_mode.decode("ascii")
    if unmerged:
        names = ", ".join(sorted(unmerged)[:5])
        raise UnsupportedWorkspaceError(f"unmerged index is unsupported: {names}")
    return tracked


def _parse_status(output: bytes) -> dict[str, SourceStatus]:
    records = output.split(b"\0")
    result: dict[str, SourceStatus] = {}
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise UnsupportedWorkspaceError("unexpected Git status record")
        pair = record[:2].decode("ascii", errors="strict")
        path = _decode_path(record[3:])
        original: str | None = None
        if pair[0] in "RC" or pair[1] in "RC":
            if index >= len(records):
                raise UnsupportedWorkspaceError("incomplete Git rename/copy status record")
            original = _decode_path(records[index])
            index += 1
        result[path] = SourceStatus(index=pair[0], worktree=pair[1], original_path=original)
    return result


def _observe(identity: WorkspaceIdentity, policy: CapturePolicy, runner: GitRunner) -> _ObservedSource:
    root = identity.root
    head = _text(runner.source(root, "rev-parse", "--verify", "HEAD"))
    branch = _text(runner.source(root, "branch", "--show-current"))
    status_raw = runner.source(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    status = _parse_status(status_raw)
    tracked = _parse_index(runner.source(root, "ls-files", "--stage", "-z"))
    untracked: tuple[str, ...] = ()
    if policy.include_untracked:
        raw_untracked = runner.source(root, "ls-files", "--others", "--exclude-standard", "-z")
        untracked = tuple(sorted(_decode_path(item) for item in raw_untracked.split(b"\0") if item))
    index_path = Path(
        _text(runner.source(root, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    )
    index_digest = hashlib.sha256(index_path.read_bytes()).hexdigest()
    return _ObservedSource(
        head=head,
        branch=branch,
        status_raw=status_raw,
        status=status,
        tracked=tracked,
        untracked=untracked,
        index_digest=index_digest,
    )


def _has_reparse_flag(metadata: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & flag)


def _source_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in ("", ".", "..") for part in pure.parts):
        raise UnsupportedWorkspaceError(f"invalid repository path: {relative!r}")
    candidate = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        if not current.exists() and current == candidate:
            break
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or _has_reparse_flag(metadata):
            raise UnsupportedWorkspaceError(f"symbolic link or reparse point is unsupported: {relative}")
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise UnsupportedWorkspaceError(f"repository path escapes worktree: {relative}") from error
    return candidate


def _read_files(
    root: Path,
    observed: _ObservedSource,
    policy: CapturePolicy,
) -> dict[str, CapturedFile]:
    files: dict[str, CapturedFile] = {}
    sources: list[tuple[str, str, bool]] = [
        (path, mode, True) for path, mode in sorted(observed.tracked.items())
    ]
    sources.extend((path, "100644", False) for path in observed.untracked)
    for relative, mode, tracked in sources:
        if mode not in {"100644", "100755"}:
            if mode == "160000":
                raise UnsupportedWorkspaceError(f"submodule is unsupported: {relative}")
            if mode == "120000":
                raise UnsupportedWorkspaceError(f"Git symbolic link is unsupported: {relative}")
            raise UnsupportedWorkspaceError(f"unsupported Git mode {mode}: {relative}")
        candidate = _source_path(root, relative)
        if not candidate.exists():
            if tracked:
                continue
            raise UnstableSourceError(f"untracked file disappeared during capture: {relative}")
        metadata = candidate.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsupportedWorkspaceError(f"non-regular file is unsupported: {relative}")
        if metadata.st_size > policy.max_file_bytes:
            raise UnsupportedWorkspaceError(f"file exceeds configured capture limit: {relative}")
        data = candidate.read_bytes()
        if len(data) > policy.max_file_bytes:
            raise UnsupportedWorkspaceError(f"file exceeds configured capture limit: {relative}")
        if not tracked and os.name != "nt" and metadata.st_mode & 0o111:
            mode = "100755"
        files[relative] = CapturedFile(
            path=relative,
            mode=mode,
            data=data,
            digest=hashlib.sha256(data).hexdigest(),
            tracked=tracked,
        )
    return files


def _file_fingerprint(files: dict[str, CapturedFile]) -> tuple[tuple[str, str, str], ...]:
    return tuple((path, item.mode, item.digest) for path, item in sorted(files.items()))


def capture_workspace(
    identity: WorkspaceIdentity,
    policy: CapturePolicy,
    runner: GitRunner,
    *,
    retries: int = 2,
) -> CapturedWorkspace:
    if retries < 1:
        raise ValueError("retries must be at least one")
    for attempt in range(1, retries + 1):
        before = _observe(identity, policy, runner)
        if before.head != identity.head or before.branch != identity.branch:
            continue
        files = _read_files(identity.root, before, policy)
        after = _observe(identity, policy, runner)
        after_files = _read_files(identity.root, after, policy)
        if before.fingerprint() != after.fingerprint():
            continue
        if _file_fingerprint(files) != _file_fingerprint(after_files):
            continue
        notes = {
            path: "index-only version not separately archived"
            for path, source_status in before.status.items()
            if source_status.index not in {" ", "?"}
            and source_status.worktree not in {" ", "?"}
        }
        combined = hashlib.sha256()
        combined.update(before.fingerprint().encode("ascii"))
        for path, mode, digest in _file_fingerprint(files):
            combined.update(path.encode("utf-8", errors="surrogateescape") + b"\0")
            combined.update(mode.encode("ascii") + b"\0" + digest.encode("ascii") + b"\0")
        return CapturedWorkspace(
            identity=identity,
            files=files,
            status=before.status,
            notes=notes,
            source_fingerprint=combined.hexdigest(),
            attempts=attempt,
        )
    raise UnstableSourceError(f"source did not remain stable after {retries} capture attempts")
