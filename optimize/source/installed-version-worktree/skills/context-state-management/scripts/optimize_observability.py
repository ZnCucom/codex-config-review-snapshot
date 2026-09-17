from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


TASK_SCHEMA_VERSION = 1
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
TASK_FIELDS = {
    "schema_version",
    "archive_id",
    "title",
    "identities",
    "runtime",
    "mode",
    "timing",
    "git",
    "outcome",
    "change_scope",
    "tests",
    "reviews",
    "counters",
    "evidence",
    "anomalies",
    "missing_evidence",
    "next_action",
    "rollout",
}
REVIEW_FIELDS = {
    "schema_version",
    "review_id",
    "source_tool",
    "canonical_task_identity",
    "agent_id",
    "baseline",
    "head",
    "observed_repo_head",
    "received_at",
    "source_locator",
    "completeness",
    "fidelity",
    "review_round",
    "summary",
}
SUPPORTED_RESPONSE_PAYLOADS = {
    "agent_message",
    "message",
    "reasoning",
    "function_call",
    "custom_tool_call",
    "function_call_output",
    "custom_tool_call_output",
}
SUPPORTED_EVENT_PAYLOADS = {
    "item_completed",
    "task_complete",
    "task_started",
    "token_count",
}
SUPPORTED_ITEM_COMPLETIONS = {"AgentMessage", "CommandExecution", "Reasoning"}
TOKEN_USAGE_FIELDS = {
    "cache_write_input_tokens",
    "cached_input_tokens",
    "input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
}
LEGACY_TOKEN_USAGE_FIELDS = TOKEN_USAGE_FIELDS - {"cache_write_input_tokens"}
SUPPORTED_RECORD_TYPES = {
    "compacted",
    "event_msg",
    "inter_agent_communication_metadata",
    "response_item",
    "session_meta",
    "token_usage_record",
    "turn_context",
    "world_state",
}
ABSOLUTE_WINDOWS_OR_UNC = re.compile(
    r"(?i)(?<![A-Za-z0-9:])(?:[A-Z]:[\\/]|\\\\|//)"
)
ABSOLUTE_POSIX = re.compile(r"(?<![A-Za-z0-9:/\\])/(?!/)[^\s,;|<>()\[\]{}\"']+")
STORED_REVIEW_FIELDS = REVIEW_FIELDS | {
    "archive_id",
    "project",
    "body_sha256",
}
PROJECT_FIELDS = {
    "project_id",
    "name",
    "repo_root",
    "git_common_dir",
    "identity_source",
}


class ObservabilityError(ValueError):
    """Raised when a local observation cannot be stored without ambiguity."""


@dataclass(frozen=True)
class ArchiveResult:
    project_id: str
    archive_id: str
    json_path: Path
    markdown_path: Path
    status: str


@dataclass(frozen=True)
class ReviewArchiveResult:
    project_id: str
    archive_id: str
    review_id: str
    metadata_path: Path
    body_path: Path | None
    status: str


@dataclass(frozen=True)
class ExportQuery:
    project_id: str | None
    archive_id: str | None = None
    latest: int | None = None
    since: str | None = None
    until: str | None = None


def default_observability_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Optimize" / "observability"
    data = os.environ.get("XDG_DATA_HOME")
    if data:
        return Path(data) / "Optimize" / "observability"
    return Path.home() / ".local" / "share" / "Optimize" / "observability"


def _absolute_safe_root(path: Path, label: str) -> Path:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            details = current.lstat()
        except FileNotFoundError:
            break
        except OSError as error:
            raise ObservabilityError(f"cannot inspect {label} path {current}: {error}") from error
        attributes = getattr(details, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if stat.S_ISLNK(details.st_mode):
            raise ObservabilityError(f"{label} crosses a symbolic link: {current}")
        if reparse_flag and attributes & reparse_flag:
            raise ObservabilityError(f"{label} crosses a filesystem reparse point: {current}")
    return absolute


def _exists_lexically(path: Path) -> bool:
    return os.path.lexists(path)


def _is_reparse_point(path: Path) -> bool:
    if not _exists_lexically(path):
        return False
    try:
        details = path.lstat()
    except OSError:
        return True
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _assert_safe_chain(root: Path, target: Path, label: str) -> None:
    """Reject every existing lexical reparse component below a safe root."""

    safe_root = _absolute_safe_root(root, label)
    absolute_target = Path(os.path.abspath(target))
    try:
        relative = absolute_target.relative_to(safe_root)
    except ValueError as error:
        raise ObservabilityError(
            f"{label} must stay within {safe_root}: {absolute_target}"
        ) from error
    current = safe_root
    if _is_reparse_point(current):
        raise ObservabilityError(
            f"{label} crosses a filesystem reparse point: {current}"
        )
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
            raise ObservabilityError(
                f"{label} crosses a filesystem reparse point: {current}"
            )


def _safe_mkdirs(root: Path, target: Path, label: str) -> None:
    safe_root = _absolute_safe_root(root, label)
    safe_root.mkdir(parents=True, exist_ok=True)
    _assert_safe_chain(safe_root, safe_root, label)
    absolute_target = Path(os.path.abspath(target))
    try:
        relative = absolute_target.relative_to(safe_root)
    except ValueError as error:
        raise ObservabilityError(
            f"{label} must stay within {safe_root}: {absolute_target}"
        ) from error
    current = safe_root
    for part in relative.parts:
        current /= part
        _assert_safe_chain(safe_root, current, label)
        current.mkdir(exist_ok=True)
        _assert_safe_chain(safe_root, current, label)


def _regular_file_bytes(root: Path, path: Path, label: str) -> bytes:
    _assert_safe_chain(root, path, label)
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise ObservabilityError(f"{label} is missing: {path}") from error
    except OSError as error:
        raise ObservabilityError(f"cannot inspect {label} {path}: {error}") from error
    if _is_reparse_point(path) or not stat.S_ISREG(details.st_mode):
        raise ObservabilityError(f"{label} must be a regular non-reparse file: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ObservabilityError(f"cannot read {label} {path}: {error}") from error


def _directory_names(root: Path, path: Path, label: str) -> set[str]:
    _assert_safe_chain(root, path, label)
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise ObservabilityError(f"{label} is missing: {path}") from error
    except OSError as error:
        raise ObservabilityError(f"cannot inspect {label} {path}: {error}") from error
    if _is_reparse_point(path) or not stat.S_ISDIR(details.st_mode):
        raise ObservabilityError(f"{label} must be a directory without reparses: {path}")
    try:
        return {child.name for child in path.iterdir()}
    except OSError as error:
        raise ObservabilityError(f"cannot list {label} {path}: {error}") from error


def _git(repo: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ObservabilityError(
            f"cannot establish Git identity for repository {repo}"
        ) from error
    return result.stdout.strip()


def _safe_component(value: object, label: str) -> str:
    if not isinstance(value, str) or not SAFE_COMPONENT.fullmatch(value):
        raise ObservabilityError(
            f"{label} must be one safe path component of at most 128 characters"
        )
    return value


def _project_identity(repo: Path) -> dict[str, str]:
    root = Path(_git(repo, "rev-parse", "--show-toplevel")).resolve()
    common_raw = Path(_git(root, "rev-parse", "--git-common-dir"))
    common = (root / common_raw).resolve() if not common_raw.is_absolute() else common_raw.resolve()
    identity = json.dumps(
        {
            "repo_root": os.path.normcase(str(root)),
            "git_common_dir": os.path.normcase(str(common)),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:16]
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-.") or "repository"
    name = name[:48]
    return {
        "project_id": f"{name}-{suffix}",
        "name": root.name,
        "repo_root": str(root),
        "git_common_dir": str(common),
        "identity_source": "git-and-filesystem",
    }


def project_id_for_repo(repo: Path) -> str:
    return _project_identity(repo)["project_id"]


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _validate_fact(value: object, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"value", "source"}:
        raise ObservabilityError(f"{label} must contain exactly value and source")
    if not isinstance(value["source"], str) or not value["source"].strip():
        raise ObservabilityError(f"{label}.source must be a nonempty string")


def _validate_task_record(record: object) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != TASK_FIELDS:
        raise ObservabilityError("task record has an unknown or incomplete schema")
    if record.get("schema_version") != TASK_SCHEMA_VERSION:
        raise ObservabilityError("task record schema_version must be 1")
    if not isinstance(record.get("title"), str) or not record["title"].strip():
        raise ObservabilityError("task record title must be a nonempty string")
    identities = record.get("identities")
    if not isinstance(identities, dict) or set(identities) != {"work", "task", "session"}:
        raise ObservabilityError("identities must contain work, task, and session")
    for name, value in identities.items():
        _validate_fact(value, f"identities.{name}")
    runtime = record.get("runtime")
    expected_runtime = {
        "optimize_version",
        "loaded_path",
        "model",
        "reasoning_effort",
        "run_version",
    }
    if not isinstance(runtime, dict) or set(runtime) != expected_runtime:
        raise ObservabilityError("runtime has an unknown or incomplete schema")
    for name, value in runtime.items():
        _validate_fact(value, f"runtime.{name}")
    counters = record.get("counters")
    if not isinstance(counters, dict):
        raise ObservabilityError("counters must be an object")
    for name, counter in counters.items():
        if not isinstance(counter, dict) or set(counter) != {"value", "source", "scope"}:
            raise ObservabilityError(
                f"counters.{name} must contain exactly value, source, and scope"
            )
        if counter["value"] is not None and (
            not isinstance(counter["value"], int) or isinstance(counter["value"], bool)
        ):
            raise ObservabilityError(f"counters.{name}.value must be an integer or null")
        if any(
            not isinstance(counter[field], str) or not counter[field].strip()
            for field in ("source", "scope")
        ):
            raise ObservabilityError(
                f"counters.{name}.source and scope must be nonempty strings"
            )
    for name in ("change_scope", "tests", "reviews", "evidence", "anomalies", "missing_evidence"):
        if not isinstance(record.get(name), list):
            raise ObservabilityError(f"{name} must be a list")
    for name in ("mode", "timing", "git", "outcome"):
        if not isinstance(record.get(name), dict):
            raise ObservabilityError(f"{name} must be an object")
    rollout = record.get("rollout")
    if rollout is not None:
        if not isinstance(rollout, dict) or set(rollout) != {"path", "source", "scope"}:
            raise ObservabilityError("rollout must be null or contain path, source, and scope")
        if not isinstance(rollout["path"], str) or not Path(rollout["path"]).is_absolute():
            raise ObservabilityError("rollout.path must be an explicit absolute path")
        if any(
            not isinstance(rollout[field], str) or not rollout[field].strip()
            for field in ("source", "scope")
        ):
            raise ObservabilityError("rollout source and scope must be nonempty strings")
    archive_id = record.get("archive_id")
    if archive_id is not None:
        _safe_component(archive_id, "archive_id")
    return json.loads(json.dumps(record, ensure_ascii=False))


def _derived_archive_id(project_id: str, record: dict[str, Any]) -> str:
    supplied = record.get("archive_id")
    if supplied is not None:
        return _safe_component(supplied, "archive_id")
    basis = {
        "project_id": project_id,
        "identities": record["identities"],
        "started_at": record["timing"].get("started_at"),
        "title": record["title"],
    }
    digest = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return f"local-{digest}"


def _display(value: object) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("\r", " ").replace("\n", " ")


def _render_summary(record: dict[str, Any]) -> str:
    project = record["project"]
    identities = record["identities"]
    runtime = record["runtime"]
    mode = record["mode"]
    timing = record["timing"]
    git = record["git"]
    outcome = record["outcome"]
    lines = [
        f"# Task summary: {_display(record['title'])}",
        "",
        f"- Archive ID: `{record['archive_id']}`",
        f"- Project: `{project['name']}` (`{project['project_id']}`)",
        f"- Work identity: {_display(identities['work']['value'])} ({identities['work']['source']})",
        f"- Task identity: {_display(identities['task']['value'])} ({identities['task']['source']})",
        f"- Session identity: {_display(identities['session']['value'])} ({identities['session']['source']})",
        f"- Mode: {_display(mode.get('observed'))}; upgraded: {_display(mode.get('upgraded'))} ({_display(mode.get('source'))})",
        f"- Time: {_display(timing.get('started_at'))} to {_display(timing.get('ended_at'))} ({_display(timing.get('source'))})",
        f"- Git: branch {_display(git.get('branch'))}, baseline {_display(git.get('baseline'))}, head {_display(git.get('head'))} ({_display(git.get('source'))})",
        f"- Outcome: {_display(outcome.get('status'))} ({_display(outcome.get('source'))})",
        f"- Optimize: {_display(runtime['optimize_version']['value'])} ({runtime['optimize_version']['source']})",
        "",
        "## Change scope",
        "",
    ]
    lines.extend(f"- {_display(value)}" for value in record["change_scope"])
    if not record["change_scope"]:
        lines.append("- unknown")
    lines.extend(["", "## Tests and reviews", ""])
    if record["tests"]:
        for test in record["tests"]:
            if isinstance(test, dict):
                lines.append(
                    f"- Test {_display(test.get('name'))}: {_display(test.get('status'))}; evidence: {_display(test.get('evidence'))}"
                )
            else:
                lines.append(f"- Test: {_display(test)}")
    else:
        lines.append("- Tests: unknown")
    if record["reviews"]:
        lines.extend(f"- Review: {_display(review)}" for review in record["reviews"])
    else:
        lines.append("- Reviews: unknown")
    lines.extend(["", "## Counters", ""])
    if record["counters"]:
        for name, counter in sorted(record["counters"].items()):
            lines.append(
                f"- {name}: {_display(counter['value'])} ({counter['source']}; {counter['scope']})"
            )
    else:
        lines.append("- unknown")
    for heading, field in (
        ("Evidence", "evidence"),
        ("Anomalies", "anomalies"),
        ("Missing evidence", "missing_evidence"),
    ):
        lines.extend(["", f"## {heading}", ""])
        values = record[field]
        lines.extend(f"- {_display(value)}" for value in values)
        if not values:
            lines.append("- none reported")
    lines.extend(["", "## Selected log observation", ""])
    observation = record.get("rollout_observation")
    if isinstance(observation, dict):
        lines.extend(
            [
                f"- Completeness: {_display(observation.get('completeness'))}",
                f"- Completeness scope: {_display(observation.get('completeness_scope'))}",
                f"- Counter scope: {_display(observation.get('counter_scope'))}",
                f"- Actor: {_display(observation.get('actor', {}).get('role') if isinstance(observation.get('actor'), dict) else None)}",
                f"- Wait calls: {_display(observation.get('wait_calls'))}",
                f"- Actual wait duration ms: {_display(observation.get('actual_wait_duration_ms'))}",
                f"- Token scope: {_display(observation.get('token_scope'))}",
                f"- Selection: {_display(observation.get('selection_source'))}; {_display(observation.get('selection_scope'))}",
            ]
        )
    else:
        lines.append("- unknown; no explicit rollout was selected")
    lines.extend(["", "## Next action", "", f"- {_display(record['next_action'])}", ""])
    return "\n".join(lines)


def _store_task_directory(
    storage_root: Path,
    tasks_root: Path,
    archive_id: str,
    json_bytes: bytes,
    markdown_bytes: bytes,
) -> tuple[Path, Path, str]:
    task_root = tasks_root / archive_id
    target = task_root / "summary"
    json_path = target / "summary.json"
    markdown_path = target / "summary.md"

    def inspect_existing() -> str | None:
        if not _exists_lexically(target):
            return None
        names = _directory_names(storage_root, target, "task summary directory")
        if names != {"summary.json", "summary.md"}:
            raise ObservabilityError(f"task archive conflict at {target}")
        existing_json = _regular_file_bytes(
            storage_root, json_path, "task summary JSON"
        )
        existing_markdown = _regular_file_bytes(
            storage_root, markdown_path, "task summary Markdown"
        )
        if existing_json == json_bytes and existing_markdown == markdown_bytes:
            return "unchanged"
        raise ObservabilityError(f"task archive conflict at {target}; refusing overwrite")

    existing = inspect_existing()
    if existing is not None:
        return json_path, markdown_path, existing
    _safe_mkdirs(storage_root, task_root, "task archive path")
    _assert_safe_chain(storage_root, target, "task summary path")
    temporary = Path(tempfile.mkdtemp(prefix=".summary.", dir=task_root))
    try:
        (temporary / "summary.json").write_bytes(json_bytes)
        (temporary / "summary.md").write_bytes(markdown_bytes)
        _assert_safe_chain(storage_root, target, "task summary path")
        os.rename(temporary, target)
    except OSError as error:
        shutil.rmtree(temporary, ignore_errors=True)
        try:
            existing = inspect_existing()
        except ObservabilityError:
            raise
        if existing is not None:
            return json_path, markdown_path, existing
        raise ObservabilityError(f"could not create task archive {target}: {error}") from error
    return json_path, markdown_path, "created"


def save_task_summary(
    repo: Path,
    record: dict[str, object],
    root: Path | None = None,
) -> ArchiveResult:
    normalized = _validate_task_record(record)
    project = _project_identity(Path(repo))
    project_id = _safe_component(project["project_id"], "project_id")
    archive_id = _derived_archive_id(project_id, normalized)
    normalized["archive_id"] = archive_id
    normalized["project"] = project
    rollout = normalized.get("rollout")
    if isinstance(rollout, dict):
        observation = inspect_rollout(Path(rollout["path"]))
        observation["selection_source"] = rollout["source"]
        observation["selection_scope"] = rollout["scope"]
        normalized["rollout_observation"] = observation
    else:
        normalized["rollout_observation"] = None
    storage_root = _absolute_safe_root(
        Path(root) if root is not None else default_observability_root(),
        "observability root",
    )
    tasks_root = storage_root / "projects" / project_id / "tasks"
    json_bytes = _json_bytes(normalized)
    markdown_bytes = _render_summary(normalized).encode("utf-8")
    json_path, markdown_path, status = _store_task_directory(
        storage_root, tasks_root, archive_id, json_bytes, markdown_bytes
    )
    return ArchiveResult(
        project_id=project_id,
        archive_id=archive_id,
        json_path=json_path,
        markdown_path=markdown_path,
        status=status,
    )


def _validate_review_metadata(metadata: object, body: bytes | None) -> dict[str, Any]:
    if not isinstance(metadata, dict) or set(metadata) != REVIEW_FIELDS:
        raise ObservabilityError("review metadata has an unknown or incomplete schema")
    if metadata.get("schema_version") != 1:
        raise ObservabilityError("review metadata schema_version must be 1")
    review_id = metadata.get("review_id")
    if review_id is not None:
        _safe_component(review_id, "review_id")
    for field in (
        "source_tool",
        "canonical_task_identity",
        "baseline",
        "head",
        "observed_repo_head",
        "received_at",
        "source_locator",
        "review_round",
    ):
        if not isinstance(metadata.get(field), str) or not metadata[field].strip():
            raise ObservabilityError(f"review metadata {field} must be a nonempty string")
    agent_id = metadata.get("agent_id")
    if agent_id is not None and (not isinstance(agent_id, str) or not agent_id.strip()):
        raise ObservabilityError("review metadata agent_id must be null or nonempty")
    summary = metadata.get("summary")
    if summary is not None and (not isinstance(summary, str) or not summary.strip()):
        raise ObservabilityError("review metadata summary must be null or nonempty")
    completeness = metadata.get("completeness")
    if completeness not in {"complete", "partial", "summary-only"}:
        raise ObservabilityError("review completeness must be complete, partial, or summary-only")
    fidelity = metadata.get("fidelity")
    if fidelity not in {
        "captured-tool-return",
        "unverified-transcription",
        "summary-only",
    }:
        raise ObservabilityError(
            "review fidelity must not claim source verification that the archive cannot prove"
        )
    if completeness in {"complete", "partial"} and body is None:
        raise ObservabilityError(f"{completeness} review metadata requires a body")
    if completeness == "summary-only" and body is not None:
        raise ObservabilityError("summary-only review metadata must not include a body")
    if completeness == "summary-only" and summary is None:
        raise ObservabilityError("summary-only review metadata requires a summary")
    return json.loads(json.dumps(metadata, ensure_ascii=False))


def _derived_review_id(metadata: dict[str, Any]) -> str:
    supplied = metadata.get("review_id")
    if supplied is not None:
        return _safe_component(supplied, "review_id")
    basis = {key: value for key, value in metadata.items() if key != "review_id"}
    digest = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return f"review-{digest}"


def _store_review_directory(
    storage_root: Path,
    reviews_root: Path,
    review_id: str,
    metadata_bytes: bytes,
    body: bytes | None,
) -> tuple[Path, Path | None, str]:
    target = reviews_root / review_id
    metadata_path = target / "metadata.json"
    body_path = target / "body.txt" if body is not None else None
    if _exists_lexically(target):
        expected_names = {"metadata.json"} | ({"body.txt"} if body is not None else set())
        if _directory_names(storage_root, target, "review archive directory") != expected_names:
            raise ObservabilityError(f"review archive conflict at {target}")
        existing_metadata = _regular_file_bytes(
            storage_root, metadata_path, "review metadata"
        )
        existing_body = (
            None
            if body_path is None
            else _regular_file_bytes(storage_root, body_path, "review body")
        )
        if existing_metadata == metadata_bytes and existing_body == body:
            return metadata_path, body_path, "unchanged"
        raise ObservabilityError(f"review archive conflict at {target}; refusing overwrite")
    _safe_mkdirs(storage_root, reviews_root, "review archive path")
    _assert_safe_chain(storage_root, target, "review archive path")
    temporary = Path(tempfile.mkdtemp(prefix=f".{review_id}.", dir=reviews_root))
    try:
        (temporary / "metadata.json").write_bytes(metadata_bytes)
        if body is not None:
            (temporary / "body.txt").write_bytes(body)
        _assert_safe_chain(storage_root, target, "review archive path")
        os.rename(temporary, target)
    except OSError as error:
        shutil.rmtree(temporary, ignore_errors=True)
        if _exists_lexically(target):
            expected_names = {"metadata.json"} | (
                {"body.txt"} if body is not None else set()
            )
            names = _directory_names(storage_root, target, "review archive directory")
            existing_metadata = (
                _regular_file_bytes(storage_root, metadata_path, "review metadata")
                if names == expected_names
                else None
            )
            existing_body = (
                None
                if body_path is None or names != expected_names
                else _regular_file_bytes(storage_root, body_path, "review body")
            )
            if names == expected_names and existing_metadata == metadata_bytes and existing_body == body:
                return metadata_path, body_path, "unchanged"
            raise ObservabilityError(
                f"review archive conflict at {target}; refusing overwrite"
            ) from error
        raise ObservabilityError(f"could not create review archive {target}: {error}") from error
    return metadata_path, body_path, "created"


def archive_review(
    repo: Path,
    archive_id: str,
    metadata: dict[str, object],
    body: bytes | None,
    root: Path | None = None,
) -> ReviewArchiveResult:
    safe_archive_id = _safe_component(archive_id, "archive_id")
    normalized = _validate_review_metadata(metadata, body)
    project = _project_identity(Path(repo))
    project_id = _safe_component(project["project_id"], "project_id")
    review_id = _derived_review_id(normalized)
    normalized["review_id"] = review_id
    normalized["archive_id"] = safe_archive_id
    normalized["project"] = project
    normalized["body_sha256"] = hashlib.sha256(body).hexdigest() if body is not None else None
    storage_root = _absolute_safe_root(
        Path(root) if root is not None else default_observability_root(),
        "observability root",
    )
    reviews_root = (
        storage_root
        / "projects"
        / project_id
        / "tasks"
        / safe_archive_id
        / "reviews"
    )
    metadata_path, body_path, status = _store_review_directory(
        storage_root, reviews_root, review_id, _json_bytes(normalized), body
    )
    return ReviewArchiveResult(
        project_id=project_id,
        archive_id=safe_archive_id,
        review_id=review_id,
        metadata_path=metadata_path,
        body_path=body_path,
        status=status,
    )


def _tool_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("arguments", payload.get("input", {}))
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _unsupported_rollout(selected: Path, diagnostics: list[str]) -> dict[str, object]:
    return {
        "rollout_path": str(selected),
        "completeness": "unsupported",
        "completeness_scope": "selected-log-parse-only",
        "counter_scope": "unknown",
        "diagnostics": diagnostics,
        "actor": {"role": "unknown", "agent_id": None, "source": "not-exposed"},
        "observed_first_timestamp": None,
        "observed_last_timestamp": None,
        "wait_calls": None,
        "requested_wait_timeout_ms": None,
        "actual_wait_duration_ms": None,
        "actual_wait_samples": None,
        "list_agents_calls": None,
        "compactions": None,
        "tool_outputs": None,
        "large_tool_outputs": None,
        "large_output_threshold_bytes": 30 * 1024,
        "tokens": None,
        "token_scope": "unknown",
    }


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _normalized_token_snapshot(
    value: object, *, allow_legacy: bool = False
) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    accepted_fields = (TOKEN_USAGE_FIELDS, LEGACY_TOKEN_USAGE_FIELDS) if allow_legacy else (
        TOKEN_USAGE_FIELDS,
    )
    if set(value) not in accepted_fields:
        return None
    if not all(_plain_int(item) and item >= 0 for item in value.values()):
        return None
    return dict(value)


def _agent_message_shape(payload: dict[str, Any]) -> bool:
    content = payload.get("content")
    if not (
        _nonempty_string(payload.get("id"))
        and _nonempty_string(payload.get("author"))
        and _nonempty_string(payload.get("recipient"))
        and isinstance(content, list)
        and isinstance(payload.get("internal_chat_message_metadata_passthrough"), dict)
    ):
        return False
    for item in content:
        if not isinstance(item, dict):
            return False
        item_type = item.get("type")
        if item_type == "input_text" and isinstance(item.get("text"), str):
            continue
        if item_type == "encrypted_content" and isinstance(
            item.get("encrypted_content"), str
        ):
            continue
        return False
    metadata = payload["internal_chat_message_metadata_passthrough"]
    create_time = metadata.get("create_time")
    return (
        isinstance(create_time, (int, float))
        and not isinstance(create_time, bool)
        and _nonempty_string(metadata.get("turn_id"))
    )


def _item_completed_shape(payload: dict[str, Any]) -> bool:
    item = payload.get("item")
    return (
        _nonempty_string(payload.get("thread_id"))
        and _nonempty_string(payload.get("turn_id"))
        and _plain_int(payload.get("started_at_ms"))
        and _plain_int(payload.get("completed_at_ms"))
        and isinstance(item, dict)
        and item.get("type") in SUPPORTED_ITEM_COMPLETIONS
        and _nonempty_string(item.get("id"))
    )


def _supported_payload_shape(record_type: object, payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    payload_type = payload.get("type")
    if record_type == "response_item":
        if payload_type == "agent_message":
            return _agent_message_shape(payload)
        return payload_type in SUPPORTED_RESPONSE_PAYLOADS
    if record_type == "event_msg":
        if payload_type not in SUPPORTED_EVENT_PAYLOADS:
            return False
        if payload_type == "item_completed":
            return _item_completed_shape(payload)
        if payload_type == "task_started":
            return (
                _nonempty_string(payload.get("turn_id"))
                and _plain_int(payload.get("started_at"))
                and _plain_int(payload.get("model_context_window"))
                and _nonempty_string(payload.get("collaboration_mode_kind"))
            )
        if payload_type == "task_complete":
            return (
                _nonempty_string(payload.get("turn_id"))
                and _plain_int(payload.get("started_at"))
                and _plain_int(payload.get("completed_at"))
                and _plain_int(payload.get("duration_ms"))
                and _plain_int(payload.get("time_to_first_token_ms"))
                and isinstance(payload.get("last_agent_message"), str)
            )
        info = payload.get("info")
        return isinstance(info, dict) and _normalized_token_snapshot(
            info.get("total_token_usage"), allow_legacy=True
        ) is not None
    if record_type == "session_meta":
        return payload_type in {None, "session_meta"}
    if record_type == "turn_context":
        return payload_type in {None, "turn_context"}
    if record_type == "compacted":
        return payload_type in {None, "compacted"}
    if record_type == "inter_agent_communication_metadata":
        return payload_type is None and isinstance(payload.get("trigger_turn"), bool)
    if record_type == "token_usage_record":
        return (
            payload_type is None
            and all(
                _nonempty_string(payload.get(field))
                for field in (
                    "response_id",
                    "root_turn_id",
                    "session_id",
                    "thread_id",
                    "turn_id",
                )
            )
            and _normalized_token_snapshot(payload.get("usage")) is not None
            and _normalized_token_snapshot(payload.get("turn_token_usage")) is not None
            and _normalized_token_snapshot(payload.get("thread_token_usage")) is not None
        )
    if record_type == "world_state":
        return (
            payload_type is None
            and isinstance(payload.get("full"), bool)
            and isinstance(payload.get("state"), dict)
        )
    return False


def inspect_rollout(path: Path) -> dict[str, object]:
    """Inspect one explicitly selected Codex JSONL without modifying or broadening it."""

    selected = Path(path)
    try:
        lines = selected.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        return _unsupported_rollout(
            selected, [f"could not read selected UTF-8 JSONL: {error}"]
        )
    nonempty = [index for index, line in enumerate(lines) if line.strip()]
    if not nonempty:
        return _unsupported_rollout(
            selected, ["selected JSONL is empty; no supported format was established"]
        )
    records: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    completeness = "complete"
    last_nonempty = nonempty[-1] if nonempty else None
    for index in nonempty:
        try:
            record = json.loads(lines[index])
        except json.JSONDecodeError as error:
            if index == last_nonempty:
                completeness = "partial"
                diagnostics.append(
                    f"final nonempty line {index + 1} is incomplete or malformed: {error.msg}"
                )
                break
            return _unsupported_rollout(
                selected,
                [f"non-final line {index + 1} is malformed JSON: {error.msg}"],
            )
        if not isinstance(record, dict):
            return _unsupported_rollout(
                selected, [f"line {index + 1} is not a JSON object"]
            )
        records.append(record)

    wait_timeouts: Counter[str] = Counter()
    wait_calls = 0
    list_agents_calls = 0
    compactions = 0
    tool_outputs = 0
    large_tool_outputs = 0
    event_token_snapshot: dict[str, int] | None = None
    event_token_record_index = -1
    thread_token_snapshot: dict[str, int] | None = None
    thread_token_record_index = -1
    invalid_token_record_index = -1
    token_session_ids: set[str] = set()
    token_thread_ids: set[str] = set()
    actor = {"role": "unknown", "agent_id": None, "source": "not-exposed"}
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    pending_waits: dict[str, datetime] = {}
    actual_waits: list[int] = []
    recognized_records = 0
    unknown_records = 0
    for record_index, record in enumerate(records, start=1):
        timestamp_raw = record.get("timestamp")
        if isinstance(timestamp_raw, str):
            if first_timestamp is None:
                first_timestamp = timestamp_raw
            last_timestamp = timestamp_raw
        record_type = record.get("type")
        payload = record.get("payload")
        is_token_snapshot_record = record_type == "token_usage_record" or (
            record_type == "event_msg"
            and isinstance(payload, dict)
            and payload.get("type") == "token_count"
        )
        schema_version = record.get("schema_version")
        if schema_version is not None and (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != 1
        ):
            if is_token_snapshot_record:
                invalid_token_record_index = record_index
            unknown_records += 1
            diagnostics.append(
                f"record {record_index} has unsupported future schema marker {schema_version!r}"
            )
            continue
        if record_type not in SUPPORTED_RECORD_TYPES:
            unknown_records += 1
            diagnostics.append(
                f"record {record_index} has unknown record type {_display(record_type)}"
            )
            continue
        if not _supported_payload_shape(record_type, payload):
            if is_token_snapshot_record:
                invalid_token_record_index = record_index
            unknown_records += 1
            payload_type = payload.get("type") if isinstance(payload, dict) else None
            diagnostics.append(
                f"record {record_index} has unsupported or malformed "
                f"{_display(record_type)} payload shape "
                f"(payload type {_display(payload_type)})"
            )
            if is_token_snapshot_record:
                diagnostics.append(
                    f"record {record_index} claims cumulative token usage but its "
                    "snapshot is invalid; token state was not updated"
                )
            continue
        recognized_records += 1
        assert isinstance(payload, dict)
        payload_type = payload.get("type")
        if record_type == "compacted":
            compactions += 1
        if record_type == "session_meta" or payload_type == "session_meta":
            source = payload.get("source")
            parent = payload.get("parent_thread_id")
            if source == "subagent" or isinstance(parent, str) and parent:
                actor = {
                    "role": "subagent",
                    "agent_id": payload.get("agent_id")
                    if isinstance(payload.get("agent_id"), str)
                    else None,
                    "source": "session-metadata",
                }
            elif source == "root":
                actor = {
                    "role": "root",
                    "agent_id": payload.get("agent_id")
                    if isinstance(payload.get("agent_id"), str)
                    else None,
                    "source": "session-metadata",
                }
        if record_type == "response_item" and payload_type in {
            "function_call",
            "custom_tool_call",
        }:
            name = payload.get("name", payload.get("tool_name"))
            if name == "wait_agent":
                wait_calls += 1
                timeout = _tool_arguments(payload).get("timeout_ms")
                if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout >= 0:
                    wait_timeouts[str(timeout)] += 1
                call_id = payload.get("call_id")
                parsed = _parse_timestamp(timestamp_raw)
                if isinstance(call_id, str) and parsed is not None:
                    pending_waits[call_id] = parsed
            elif name == "list_agents":
                list_agents_calls += 1
        if record_type == "response_item" and payload_type in {
            "function_call_output",
            "custom_tool_call_output",
        }:
            tool_outputs += 1
            output = payload.get("output", payload.get("content", ""))
            if len(str(output).encode("utf-8")) >= 30 * 1024:
                large_tool_outputs += 1
            call_id = payload.get("call_id")
            finished = _parse_timestamp(timestamp_raw)
            if isinstance(call_id, str) and call_id in pending_waits and finished is not None:
                elapsed = int((finished - pending_waits.pop(call_id)).total_seconds() * 1000)
                if elapsed >= 0:
                    actual_waits.append(elapsed)
        if (
            record_type == "event_msg"
            and payload_type == "token_count"
        ):
            info = payload.get("info")
            assert isinstance(info, dict)
            usage = _normalized_token_snapshot(
                info.get("total_token_usage"), allow_legacy=True
            )
            assert usage is not None
            event_token_snapshot = usage
            event_token_record_index = record_index
        if record_type == "token_usage_record":
            session_id = payload.get("session_id")
            thread_id = payload.get("thread_id")
            assert isinstance(session_id, str)
            assert isinstance(thread_id, str)
            token_session_ids.add(session_id)
            token_thread_ids.add(thread_id)
            usage = _normalized_token_snapshot(payload.get("thread_token_usage"))
            assert usage is not None
            thread_token_snapshot = usage
            thread_token_record_index = record_index

    if recognized_records == 0:
        if not diagnostics:
            diagnostics.append("selected JSONL contains no recognized records")
        return _unsupported_rollout(selected, diagnostics)
    if unknown_records:
        completeness = "partial"
        diagnostics.append(
            "counters cover recognized records only and are not whole-task totals"
        )

    tokens: dict[str, int | None] | None = None
    token_scope = "unknown"
    token_snapshot: dict[str, int] | None = None
    latest_valid_token_record_index = max(
        event_token_record_index, thread_token_record_index
    )
    if invalid_token_record_index > latest_valid_token_record_index:
        diagnostics.append(
            "final cumulative token usage is unknown because a later token snapshot "
            "was invalid; the last valid snapshot was retained but is not reported "
            "as the final total"
        )
    elif thread_token_snapshot is not None:
        if len(token_thread_ids) != 1:
            diagnostics.append(
                "token metrics are unknown because token_usage_record spans multiple thread identities"
            )
        elif len(token_session_ids) != 1:
            diagnostics.append(
                "token metrics are unknown because token_usage_record spans multiple session identities"
            )
        elif thread_token_record_index > event_token_record_index:
            token_snapshot = thread_token_snapshot
            token_scope = "selected-thread-cumulative-final-snapshot"
        else:
            token_snapshot = event_token_snapshot
            token_scope = "session-cumulative-final-snapshot"
    elif event_token_snapshot is not None:
        token_snapshot = event_token_snapshot
        token_scope = "session-cumulative-final-snapshot"
    if token_snapshot is not None:
        tokens = dict(token_snapshot)
        input_tokens = token_snapshot.get("input_tokens")
        cached_tokens = token_snapshot.get("cached_input_tokens")
        if (
            isinstance(input_tokens, int)
            and isinstance(cached_tokens, int)
            and input_tokens >= cached_tokens
        ):
            tokens["new_input_tokens"] = input_tokens - cached_tokens
        else:
            tokens["new_input_tokens"] = None
            diagnostics.append(
                "new input tokens are unknown because input/cached counters were absent or inconsistent"
            )
    if wait_calls and len(actual_waits) < wait_calls:
        diagnostics.append(
            "actual wait duration is incomplete; requested timeout is retained separately"
        )
    return {
        "rollout_path": str(selected),
        "completeness": completeness,
        "completeness_scope": "selected-log-parse-only",
        "counter_scope": (
            "selected-log" if completeness == "complete" else "recognized-records-only"
        ),
        "diagnostics": diagnostics,
        "actor": actor,
        "observed_first_timestamp": first_timestamp,
        "observed_last_timestamp": last_timestamp,
        "wait_calls": wait_calls,
        "requested_wait_timeout_ms": dict(
            sorted(wait_timeouts.items(), key=lambda item: int(item[0]))
        ),
        "actual_wait_duration_ms": sum(actual_waits) if actual_waits else None,
        "actual_wait_samples": len(actual_waits),
        "list_agents_calls": list_agents_calls,
        "compactions": compactions,
        "tool_outputs": tool_outputs,
        "large_tool_outputs": large_tool_outputs,
        "large_output_threshold_bytes": 30 * 1024,
        "tokens": tokens,
        "token_scope": token_scope,
    }


def _redact_export_text(value: object, project: dict[str, object]) -> str:
    text = _display(value)
    if ABSOLUTE_WINDOWS_OR_UNC.search(text) or ABSOLUTE_POSIX.search(text):
        return "<redacted-free-text: absolute path omitted>"
    replacements = [
        str(Path.home()),
        str(project.get("repo_root") or ""),
        str(project.get("git_common_dir") or ""),
        os.environ.get("USERNAME", ""),
        os.environ.get("USER", ""),
    ]
    for sensitive in sorted({item for item in replacements if item}, key=len, reverse=True):
        text = re.sub(re.escape(sensitive), "<redacted-path>", text, flags=re.IGNORECASE)
        text = re.sub(
            re.escape(sensitive.replace("\\", "/")),
            "<redacted-path>",
            text,
            flags=re.IGNORECASE,
        )
    return text


def _task_time(record: dict[str, Any]) -> datetime | None:
    timing = record.get("timing")
    if not isinstance(timing, dict):
        return None
    return _parse_timestamp(timing.get("ended_at")) or _parse_timestamp(
        timing.get("started_at")
    )


def _load_export_tasks(
    root: Path, query: ExportQuery
) -> tuple[list[tuple[Path, dict[str, Any]]], list[str]]:
    if query.project_id is None:
        raise ObservabilityError("export requires an explicit project selector")
    project_id = _safe_component(query.project_id, "project_id")
    if query.archive_id is not None:
        _safe_component(query.archive_id, "archive_id")
    if query.latest is not None and (
        not isinstance(query.latest, int)
        or isinstance(query.latest, bool)
        or query.latest <= 0
    ):
        raise ObservabilityError("latest must be a positive integer")
    since = _parse_timestamp(query.since) if query.since is not None else None
    until = _parse_timestamp(query.until) if query.until is not None else None
    if query.since is not None and since is None:
        raise ObservabilityError("since must be an ISO-8601 timestamp")
    if query.until is not None and until is None:
        raise ObservabilityError("until must be an ISO-8601 timestamp")
    if since is not None and until is not None and since.timestamp() > until.timestamp():
        raise ObservabilityError("since must not be later than until")
    storage_root = _absolute_safe_root(root, "observability root")
    tasks_root = storage_root / "projects" / project_id / "tasks"
    warnings: list[str] = []
    tasks: list[tuple[Path, dict[str, Any]]] = []
    _assert_safe_chain(storage_root, tasks_root, "task export path")
    if not _exists_lexically(tasks_root):
        return tasks, ["selected project has no archived task summaries"]
    try:
        _directory_names(storage_root, tasks_root, "task export directory")
    except ObservabilityError as error:
        return tasks, [str(error)]
    candidates = (
        [tasks_root / query.archive_id]
        if query.archive_id is not None
        else sorted(tasks_root.iterdir(), key=lambda path: path.name)
    )
    for task_dir in candidates:
        summary = task_dir / "summary" / "summary.json"
        try:
            summary_bytes = _regular_file_bytes(
                storage_root, summary, f"task {task_dir.name} summary.json"
            )
        except ObservabilityError as error:
            warnings.append(f"task {task_dir.name} has no summary.json")
            if "missing" not in str(error).lower():
                warnings.append(str(error))
            continue
        try:
            record = json.loads(summary_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            warnings.append(f"task {task_dir.name} summary is unsupported: {error}")
            continue
        if not isinstance(record, dict):
            warnings.append(f"task {task_dir.name} summary is not an object")
            continue
        timestamp = _task_time(record)
        if since is not None or until is not None:
            if timestamp is None:
                warnings.append(
                    f"task {task_dir.name} was excluded because its timestamp is unknown"
                )
                continue
            numeric_time = timestamp.timestamp()
            if since is not None and numeric_time < since.timestamp():
                continue
            if until is not None and numeric_time > until.timestamp():
                continue
        tasks.append((task_dir, record))
    tasks.sort(
        key=lambda item: (
            (_task_time(item[1]).timestamp() if _task_time(item[1]) is not None else float("-inf")),
            item[0].name,
        ),
        reverse=True,
    )
    if query.latest is not None:
        tasks = tasks[: query.latest]
    return tasks, warnings


def _validate_stored_review_metadata(metadata: object) -> dict[str, Any]:
    if not isinstance(metadata, dict) or set(metadata) != STORED_REVIEW_FIELDS:
        raise ObservabilityError("review metadata has an unknown or incomplete schema")
    input_metadata = {field: metadata[field] for field in REVIEW_FIELDS}
    body_expected = metadata.get("body_sha256") is not None
    _validate_review_metadata(input_metadata, b"stored" if body_expected else None)
    _safe_component(metadata.get("archive_id"), "archive_id")
    project = metadata.get("project")
    if not isinstance(project, dict) or set(project) != PROJECT_FIELDS:
        raise ObservabilityError("stored review project has an unknown or incomplete schema")
    _safe_component(project.get("project_id"), "project_id")
    for field in ("name", "repo_root", "git_common_dir", "identity_source"):
        if not isinstance(project.get(field), str) or not project[field]:
            raise ObservabilityError(f"stored review project {field} must be nonempty")
    body_sha256 = metadata.get("body_sha256")
    if body_sha256 is not None and (
        not isinstance(body_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", body_sha256) is None
    ):
        raise ObservabilityError("stored review body_sha256 must be null or lowercase SHA-256")
    if metadata["completeness"] == "summary-only" and body_sha256 is not None:
        raise ObservabilityError("summary-only stored review must not name a body hash")
    if metadata["completeness"] in {"complete", "partial"} and body_sha256 is None:
        raise ObservabilityError("stored complete or partial review requires body_sha256")
    return json.loads(json.dumps(metadata, ensure_ascii=False))


def _review_metadata(
    storage_root: Path, task_dir: Path
) -> tuple[list[tuple[dict[str, Any], bytes | None]], list[str]]:
    reviews_root = task_dir / "reviews"
    try:
        _assert_safe_chain(storage_root, reviews_root, "review export path")
    except ObservabilityError as error:
        return [], [str(error)]
    if not _exists_lexically(reviews_root):
        return [], []
    try:
        _directory_names(storage_root, reviews_root, "review export directory")
    except ObservabilityError as error:
        return [], [str(error)]
    reviews: list[tuple[dict[str, Any], bytes | None]] = []
    warnings: list[str] = []
    for review_dir in sorted(reviews_root.iterdir(), key=lambda path: path.name):
        metadata_path = review_dir / "metadata.json"
        try:
            names = _directory_names(
                storage_root, review_dir, f"review {review_dir.name} directory"
            )
        except ObservabilityError as error:
            warnings.append(str(error))
            continue
        if "metadata.json" not in names:
            warnings.append(f"review {review_dir.name} is missing metadata.json")
            continue
        try:
            metadata_bytes = _regular_file_bytes(
                storage_root, metadata_path, f"review {review_dir.name} metadata"
            )
            metadata = json.loads(metadata_bytes.decode("utf-8"))
            normalized = _validate_stored_review_metadata(metadata)
        except (ObservabilityError, UnicodeDecodeError, json.JSONDecodeError) as error:
            warnings.append(f"review {review_dir.name} metadata is unsupported: {error}")
            continue
        expected_names = {"metadata.json"} | (
            {"body.txt"} if normalized["body_sha256"] is not None else set()
        )
        if names != expected_names:
            if "body.txt" in expected_names and "body.txt" not in names:
                warnings.append(f"review {review_dir.name} is missing body.txt")
            else:
                warnings.append(
                    f"review {review_dir.name} has unexpected contents: "
                    + ", ".join(sorted(names))
                )
            continue
        body: bytes | None = None
        if normalized["body_sha256"] is not None:
            body_path = review_dir / "body.txt"
            try:
                body = _regular_file_bytes(
                    storage_root, body_path, f"review {review_dir.name} body"
                )
            except ObservabilityError as error:
                warnings.append(str(error))
                continue
            if hashlib.sha256(body).hexdigest() != normalized["body_sha256"]:
                warnings.append(f"review {review_dir.name} body hash mismatch")
                continue
        reviews.append((normalized, body))
    return reviews, warnings


def export_observations(
    root: Path,
    query: ExportQuery,
    include_review_bodies: bool = False,
) -> str:
    storage_root = _absolute_safe_root(Path(root), "observability root")
    tasks, warnings = _load_export_tasks(storage_root, query)
    outcomes: Counter[str] = Counter()
    missing_count = 0
    for _, record in tasks:
        outcome = record.get("outcome")
        status = outcome.get("status") if isinstance(outcome, dict) else None
        outcomes[_display(status)] += 1
        missing = record.get("missing_evidence")
        if isinstance(missing, list):
            missing_count += len(missing)
    lines = [
        "# Optimize observability export",
        "",
        f"- Project ID: `{query.project_id}`",
        f"- Task summaries: {len(tasks)}",
        f"- Missing evidence entries: {missing_count}",
        "- Default privacy: absolute repository/session paths, raw session logs, environment variables, and review bodies are omitted.",
        "- Sharing note: path reduction is best-effort; review this export before sharing.",
        "",
        "## Outcome summary",
        "",
    ]
    if outcomes:
        lines.extend(f"- {name}: {count}" for name, count in sorted(outcomes.items()))
    else:
        lines.append("- no selected task summaries")
    for task_dir, record in tasks:
        project = record.get("project")
        if not isinstance(project, dict):
            project = {}
        archive_id = _redact_export_text(record.get("archive_id"), project)
        lines.extend(
            [
                "",
                f"## Task `{archive_id}`: {_redact_export_text(record.get('title'), project)}",
                "",
            ]
        )
        mode = record.get("mode")
        timing = record.get("timing")
        outcome = record.get("outcome")
        git = record.get("git")
        lines.append(
            f"- Mode: {_redact_export_text(mode.get('observed') if isinstance(mode, dict) else None, project)}"
        )
        lines.append(
            f"- Time: {_redact_export_text(timing.get('started_at') if isinstance(timing, dict) else None, project)} to "
            f"{_redact_export_text(timing.get('ended_at') if isinstance(timing, dict) else None, project)}"
        )
        lines.append(
            f"- Outcome: {_redact_export_text(outcome.get('status') if isinstance(outcome, dict) else None, project)}"
        )
        lines.append(
            f"- Git branch/head: {_redact_export_text(git.get('branch') if isinstance(git, dict) else None, project)} / "
            f"{_redact_export_text(git.get('head') if isinstance(git, dict) else None, project)}"
        )
        scope = record.get("change_scope")
        lines.append("- Change scope: " + (
            ", ".join(_redact_export_text(value, project) for value in scope)
            if isinstance(scope, list) and scope
            else "unknown"
        ))
        tests = record.get("tests")
        if isinstance(tests, list) and tests:
            for test in tests:
                if isinstance(test, dict):
                    lines.append(
                        f"- Test {_redact_export_text(test.get('name'), project)}: "
                        f"{_redact_export_text(test.get('status'), project)}"
                    )
        else:
            lines.append("- Tests: unknown")
        observation = record.get("rollout_observation")
        if isinstance(observation, dict):
            lines.append(
                f"- Selected log: {observation.get('completeness', 'unknown')}; "
                f"completeness_scope={_display(observation.get('completeness_scope'))}; "
                f"counter_scope={_display(observation.get('counter_scope'))}; "
                f"waits={_display(observation.get('wait_calls'))}; "
                f"actual_wait_ms={_display(observation.get('actual_wait_duration_ms'))}; "
                f"token_scope={_display(observation.get('token_scope'))}"
            )
        missing = record.get("missing_evidence")
        lines.append("- Missing evidence: " + (
            "; ".join(_redact_export_text(value, project) for value in missing)
            if isinstance(missing, list) and missing
            else "none reported"
        ))
        reviews, review_warnings = _review_metadata(storage_root, task_dir)
        warnings.extend(review_warnings)
        if reviews:
            lines.extend(["", "### Reviews", ""])
            for metadata, body in reviews:
                lines.append(
                    f"- `{_display(metadata.get('review_id'))}`: "
                    f"{_display(metadata.get('review_round'))}, "
                    f"{_display(metadata.get('completeness'))}, "
                    f"{_display(metadata.get('fidelity'))}, "
                    f"sha256={_display(metadata.get('body_sha256'))}"
                )
                if include_review_bodies and body is not None:
                    decoded_body = body.decode("utf-8", errors="replace")
                    lines.extend(
                        [
                            "",
                            f"#### Review body `{_display(metadata.get('review_id'))}`",
                            "",
                            "> Export copy of immutable local evidence. Automated secret detection/redaction is not guaranteed; review before sharing.",
                            "",
                        ]
                    )
                    lines.extend(f"> {line}" for line in decoded_body.splitlines())
        else:
            lines.append("- Reviews: none archived")
    if warnings:
        lines.extend(["", "## Export diagnostics", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines) + "\n"


def write_export(path: Path, report: str) -> str:
    output = Path(path)
    contents = report.encode("utf-8")
    safe_parent = _absolute_safe_root(output.parent, "export output")
    output = safe_parent / output.name
    _safe_mkdirs(safe_parent, safe_parent, "export output")
    _assert_safe_chain(safe_parent, output, "export output")
    if _exists_lexically(output):
        existing = _regular_file_bytes(safe_parent, output, "export output")
        if existing == contents:
            return "unchanged"
        raise ObservabilityError(
            f"export output exists with different bytes; refusing overwrite: {output}"
        )
    created = False
    try:
        with output.open("xb") as stream:
            created = True
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        existing = _regular_file_bytes(safe_parent, output, "export output")
        if existing == contents:
            return "unchanged"
        raise ObservabilityError(
            f"export output exists with different bytes; refusing overwrite: {output}"
        )
    except OSError as error:
        if created:
            output.unlink(missing_ok=True)
        raise ObservabilityError(f"could not write export {output}: {error}") from error
    return "created"
