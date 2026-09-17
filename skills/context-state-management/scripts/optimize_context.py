#!/usr/bin/env python3
"""Small deterministic guards for repository-visible Codex context state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

if __name__ == "__main__":
    # Keep an installed skill tree byte-for-byte stable for integrity checks.
    sys.dont_write_bytecode = True

try:
    from .optimize_global import GlobalInstallError, install_global, uninstall_global
except ImportError:
    from optimize_global import GlobalInstallError, install_global, uninstall_global

try:
    from .optimize_observability import (
        ExportQuery,
        ObservabilityError,
        archive_review,
        default_observability_root,
        export_observations,
        project_id_for_repo,
        save_task_summary,
        write_export,
    )
except ImportError:
    from optimize_observability import (
        ExportQuery,
        ObservabilityError,
        archive_review,
        default_observability_root,
        export_observations,
        project_id_for_repo,
        save_task_summary,
        write_export,
    )


LARGE_OUTPUT_BYTES = 30 * 1024
SHORT_WAIT_MS = 5 * 60 * 1000
FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
REGULAR_GIT_MODES = {"100644", "100755"}
MILESTONE_STATUSES = {
    "PLANNING",
    "IMPLEMENTING",
    "REVIEWING",
    "FIXING",
    "VERIFYING",
    "BLOCKED",
    "COMPLETE",
}

STATE_SCHEMAS = {
    ("project-map", 1): {
        "limit": 8 * 1024,
        "fields": {
            "version",
            "spec_path",
            "plan_path",
            "current_state_path",
            "active_state_path",
            "audit_path",
        },
        "sections": {"Source of truth", "Verification", "Contract index"},
        "optional_fields": set(),
        "closed": False,
    },
    ("current-work", 1): {
        "limit": 12 * 1024,
        "fields": {
            "version",
            "milestone",
            "status",
            "branch",
            "baseline",
            "head",
            "current_agent",
        },
        "sections": {
            "Approved clarifications",
            "Completed",
            "Remaining",
            "Open findings",
            "Last verification",
            "Next action",
            "Do not",
            "Authority",
        },
        "optional_fields": set(),
        "closed": False,
    },
    ("active", 1): {
        "limit": 4 * 1024,
        "fields": {"version", "action", "agent"},
        "sections": {"Why", "Do not", "On success", "On failure"},
        "optional_fields": set(),
        "closed": False,
    },
    ("project-map", 2): {
        "limit": 8 * 1024,
        "fields": {
            "version",
            "current_state_path",
            "active_state_path",
            "authority_paths",
        },
        "sections": {
            "Authority",
            "Code map",
            "Verification",
            "Important interfaces and contracts",
            "Operational state",
        },
        "optional_fields": set(),
        "closed": True,
    },
    ("current-work", 2): {
        "limit": 12 * 1024,
        "fields": {
            "version",
            "work_type",
            "work_id",
            "status",
            "branch",
            "baseline",
            "head",
        },
        "sections": {
            "Constraints and clarifications",
            "Completed",
            "Remaining",
            "Open findings",
            "Last verification",
            "Next action",
            "Do not",
            "Authority",
        },
        "optional_fields": {"owner"},
        "closed": True,
    },
    ("active", 2): {
        "limit": 4 * 1024,
        "fields": {"version", "action"},
        "sections": {"Why", "Do not", "On success", "On failure"},
        "optional_fields": {"owner"},
        "closed": True,
    },
}


class RolloutFormatError(ValueError):
    """Raised when a rollout JSONL record cannot be decoded."""


class StateFormatError(ValueError):
    """Raised when a Markdown state file has invalid TOML front matter."""


class ContextOperationError(ValueError):
    """Raised when init or migration cannot mutate context state safely."""


class PacketFormatError(ValueError):
    """Raised when a subagent context packet has an invalid closed shape."""


@dataclass(frozen=True)
class Diagnostic:
    code: str
    path: str
    message: str


def _records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise RolloutFormatError(
                    f"{path}: line {line_number}: {error.msg}"
                ) from error
            if not isinstance(record, dict):
                raise RolloutFormatError(
                    f"{path}: line {line_number}: expected a JSON object"
                )
            yield record


def _arguments(payload: dict[str, Any]) -> dict[str, Any]:
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


def analyze_rollout(path: Path) -> dict[str, object]:
    """Return actual call, wait, compaction, output, and token metrics."""

    wait_timeouts: Counter[str] = Counter()
    wait_calls = 0
    short_wait_calls = 0
    unknown_wait_bound_calls = 0
    list_agents_calls = 0
    compactions = 0
    tool_outputs = 0
    large_tool_outputs = 0
    final_token_usage: dict[str, int] = {}

    for record in _records(path):
        record_type = record.get("type")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        payload_type = payload.get("type")

        if record_type == "compacted":
            compactions += 1

        if record_type == "response_item" and payload_type in {
            "function_call",
            "custom_tool_call",
        }:
            name = payload.get("name", payload.get("tool_name"))
            if name == "wait_agent":
                wait_calls += 1
                timeout = _arguments(payload).get("timeout_ms")
                if isinstance(timeout, int) and timeout >= 0:
                    wait_timeouts[str(timeout)] += 1
                    if timeout < SHORT_WAIT_MS:
                        short_wait_calls += 1
                else:
                    unknown_wait_bound_calls += 1
            elif name == "list_agents":
                list_agents_calls += 1

        if record_type == "response_item" and payload_type in {
            "function_call_output",
            "custom_tool_call_output",
        }:
            tool_outputs += 1
            output = payload.get("output", payload.get("content", ""))
            output_bytes = len(str(output).encode("utf-8"))
            if output_bytes >= LARGE_OUTPUT_BYTES:
                large_tool_outputs += 1

        if (
            record_type == "event_msg"
            and payload_type == "token_count"
            and isinstance(payload.get("info"), dict)
        ):
            usage = payload["info"].get("total_token_usage")
            if isinstance(usage, dict):
                final_token_usage = {
                    key: value
                    for key, value in usage.items()
                    if isinstance(key, str) and isinstance(value, int)
                }

    return {
        "rollout": str(path),
        "wait_calls": wait_calls,
        "short_wait_calls": short_wait_calls,
        "unknown_wait_bound_calls": unknown_wait_bound_calls,
        "wait_timeout_ms": dict(sorted(wait_timeouts.items(), key=lambda item: int(item[0]))),
        "list_agents_calls": list_agents_calls,
        "compactions": compactions,
        "tool_outputs": tool_outputs,
        "large_tool_outputs": large_tool_outputs,
        "large_output_threshold_bytes": LARGE_OUTPUT_BYTES,
        "final_token_usage": final_token_usage,
    }


def load_markdown_state(path: Path) -> tuple[dict[str, object], str]:
    """Load TOML front matter and the Markdown body from a state file."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "+++":
        raise StateFormatError(f"{path}: missing opening +++ front-matter marker")
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "+++"
        )
    except StopIteration as error:
        raise StateFormatError(f"{path}: missing closing +++ front-matter marker") from error
    try:
        metadata = tomllib.loads("\n".join(lines[1:closing]))
    except tomllib.TOMLDecodeError as error:
        raise StateFormatError(f"{path}: invalid TOML front matter: {error}") from error
    if not isinstance(metadata, dict):
        raise StateFormatError(f"{path}: front matter must be a TOML table")
    return metadata, "\n".join(lines[closing + 1 :])


def _headings(body: str) -> set[str]:
    return {
        line[3:].strip()
        for line in body.splitlines()
        if line.startswith("## ") and line[3:].strip()
    }


def _validate_document(path: Path, document_kind: str) -> tuple[list[Diagnostic], dict[str, object]]:
    diagnostics: list[Diagnostic] = []
    if not path.exists():
        if document_kind == "active":
            return diagnostics, {}
        return [Diagnostic("missing_file", str(path), "required state file is missing")], {}

    size = path.stat().st_size
    limit = int(STATE_SCHEMAS[(document_kind, 1)]["limit"])
    if size > limit:
        diagnostics.append(
            Diagnostic(
                "size_limit",
                str(path),
                f"{size} bytes exceeds the {limit}-byte {document_kind} budget",
            )
        )
    try:
        metadata, body = load_markdown_state(path)
    except (OSError, StateFormatError) as error:
        diagnostics.append(Diagnostic("invalid_state", str(path), str(error)))
        return diagnostics, {}

    version = metadata.get("version")
    schema = STATE_SCHEMAS.get((document_kind, version))
    if type(version) is not int or schema is None:
        diagnostics.append(
            Diagnostic(
                "invalid_version",
                str(path),
                "version must be integer 1 or 2",
            )
        )
        return diagnostics, metadata

    missing_fields = sorted(set(schema["fields"]) - metadata.keys())
    for field in missing_fields:
        diagnostics.append(
            Diagnostic("missing_field", str(path), f"missing required front-matter field: {field}")
        )
    missing_sections = sorted(set(schema["sections"]) - _headings(body))
    for section in missing_sections:
        diagnostics.append(
            Diagnostic("missing_section", str(path), f"missing required section: ## {section}")
        )
    if schema["closed"]:
        allowed_fields = set(schema["fields"]) | set(schema["optional_fields"])
        for field in sorted(metadata.keys() - allowed_fields):
            diagnostics.append(
                Diagnostic(
                    "unexpected_field",
                    str(path),
                    f"unexpected front-matter field for schema v{version}: {field}",
                )
            )
    return diagnostics, metadata


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _declared_head_is_current(
    repo: Path,
    declared_head: object,
    actual_head: str,
    allowed_committed_paths: set[str],
) -> bool:
    if not isinstance(declared_head, str) or not declared_head:
        return False
    if declared_head == actual_head:
        return True
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", declared_head, actual_head],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if ancestor.returncode != 0:
        return False
    changed = _git(repo, "diff", "--name-only", f"{declared_head}..{actual_head}", "--")
    changed_paths = {line.replace("\\", "/") for line in changed.splitlines() if line}
    return changed_paths <= allowed_committed_paths


def _repo_relative(repo: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None


def _repo_lexical_relative(repo: Path, path: Path) -> str | None:
    """Return a repository-relative path without following a final symlink."""

    repository = Path(os.path.abspath(repo))
    candidate = Path(os.path.abspath(path))
    try:
        return candidate.relative_to(repository).as_posix()
    except ValueError:
        return None


def _is_reparse_point(path: Path) -> bool:
    if not os.path.lexists(path):
        return False
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return True
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _path_crosses_reparse(root: Path, path: Path) -> bool:
    repository = Path(os.path.abspath(root))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(repository)
    except ValueError:
        return True
    current = repository
    if _is_reparse_point(current):
        return True
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
            return True
    return False


def _working_tree_paths(repo: Path) -> set[str]:
    commands = (
        ("diff", "--name-only", "--"),
        ("diff", "--cached", "--name-only", "--"),
        ("ls-files", "--others", "--exclude-standard"),
    )
    paths: set[str] = set()
    for arguments in commands:
        output = _git(repo, *arguments)
        paths.update(line.replace("\\", "/") for line in output.splitlines() if line)
    return paths


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_tracked(repo: Path, relative_path: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", relative_path],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode == 0


def _git_entry(repo: Path, relative_path: str, source: str) -> tuple[str, str] | None:
    """Return the exact mode and object ID for one stage-0 index or HEAD entry."""

    if source == "index":
        arguments = ("ls-files", "--stage", "--", relative_path)
    elif source == "head":
        arguments = ("ls-tree", "HEAD", "--", relative_path)
    else:
        raise ValueError(f"unknown Git entry source: {source}")
    try:
        output = _git(repo, *arguments)
    except (OSError, subprocess.CalledProcessError):
        return None
    lines = [line for line in output.splitlines() if line]
    if len(lines) != 1:
        return None
    header = lines[0].split("\t", 1)[0].split()
    if source == "index":
        if len(header) < 3 or header[2] != "0":
            return None
        return header[0], header[1]
    if len(header) < 3 or header[1] != "blob":
        return None
    return header[0], header[2]


def _git_entry_mode(repo: Path, relative_path: str, source: str) -> str | None:
    entry = _git_entry(repo, relative_path, source)
    return entry[0] if entry is not None else None


def _git_blob_bytes(repo: Path, object_id: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", object_id],
        check=True,
        capture_output=True,
    )
    return result.stdout


def _verified_git_evidence(
    repo: Path,
    raw_path: object,
    expected_hash: object,
    label: str,
    errors: list[str],
) -> bytes | None:
    """Verify evidence against identical canonical blobs in HEAD and stage-0 index."""

    if not _is_nonempty_string(raw_path):
        errors.append(f"{label}.path must be a non-empty repository-relative path")
        return None
    if not isinstance(expected_hash, str) or re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_hash
    ) is None:
        errors.append(f"{label}.sha256 must be an exact 64-hex digest")
        return None
    path = repo / str(raw_path)
    lexical_relative = _repo_lexical_relative(repo, path)
    resolved_relative = _repo_relative(repo, path)
    if lexical_relative is None or resolved_relative is None:
        errors.append(f"{label}.path must stay within the evidence repository: {raw_path}")
        return None
    if _path_crosses_reparse(repo, path):
        errors.append(f"{label}.path must not cross a filesystem reparse point: {raw_path}")
        return None
    if not path.is_file():
        errors.append(f"{label}.path does not exist: {raw_path}")
        return None
    head_entry = _git_entry(repo, lexical_relative, "head")
    index_entry = _git_entry(repo, lexical_relative, "index")
    if head_entry is None or index_entry is None:
        errors.append(
            f"{label}.path must be a tracked regular file in HEAD and the stage-0 "
            f"index: {raw_path}"
        )
        return None
    head_mode, head_object = head_entry
    index_mode, index_object = index_entry
    if head_mode not in REGULAR_GIT_MODES or index_mode not in REGULAR_GIT_MODES:
        errors.append(
            f"{label}.path must be a regular Git file (100644 or 100755) in HEAD "
            f"and the stage-0 index: {raw_path} "
            f"(HEAD={head_mode}, index={index_mode})"
        )
        return None
    if head_object != index_object:
        errors.append(
            f"{label}.path must use the same Git blob in HEAD and the stage-0 index: "
            f"{raw_path} (HEAD={head_object}, index={index_object})"
        )
        return None
    try:
        contents = _git_blob_bytes(repo, head_object)
    except (OSError, subprocess.CalledProcessError) as error:
        errors.append(f"{label}.path Git blob cannot be read: {raw_path}: {error}")
        return None
    actual_hash = hashlib.sha256(contents).hexdigest()
    if actual_hash.lower() != expected_hash.lower():
        errors.append(f"{label} SHA-256 mismatch: {raw_path}")
        return None
    return contents


def _verified_git_source_evidence(
    repo: Path,
    raw_path: object,
    expected_hash: object,
    label: str,
    errors: list[str],
) -> bytes | None:
    """Verify a behavioral prompt source in HEAD/index or an ancestor at the same path."""

    current_errors: list[str] = []
    current = _verified_git_evidence(
        repo, raw_path, expected_hash, label, current_errors
    )
    if current is not None:
        return current
    if not _is_nonempty_string(raw_path) or not isinstance(expected_hash, str):
        errors.extend(current_errors)
        return None
    if re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is None:
        errors.extend(current_errors)
        return None
    lexical_relative = _repo_lexical_relative(repo, repo / str(raw_path))
    if lexical_relative is None:
        errors.extend(current_errors)
        return None
    try:
        commits = _git(repo, "rev-list", "HEAD", "--", lexical_relative).splitlines()
    except (OSError, subprocess.CalledProcessError):
        errors.extend(current_errors)
        return None
    for commit in commits:
        try:
            output = _git(repo, "ls-tree", commit, "--", lexical_relative)
        except (OSError, subprocess.CalledProcessError):
            continue
        lines = [line for line in output.splitlines() if line]
        if len(lines) != 1:
            continue
        header = lines[0].split("\t", 1)[0].split()
        if (
            len(header) < 3
            or header[0] not in REGULAR_GIT_MODES
            or header[1] != "blob"
        ):
            continue
        try:
            contents = _git_blob_bytes(repo, header[2])
        except (OSError, subprocess.CalledProcessError):
            continue
        if hashlib.sha256(contents).hexdigest().lower() == expected_hash.lower():
            return contents
    errors.extend(current_errors)
    return None


def _is_commit(repo: Path, value: object) -> bool:
    if not isinstance(value, str) or FULL_SHA.fullmatch(value) is None:
        return False
    try:
        resolved = _git(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
    except subprocess.CalledProcessError:
        return False
    return resolved.lower() == value.lower()


def _validate_authority_reference(
    repo: Path,
    map_path: Path,
    label: str,
    value: object,
    *,
    require_same_blob: bool,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not _is_nonempty_string(value):
        return [
            Diagnostic(
                "invalid_field",
                str(map_path),
                f"{label} must be a non-empty repository-relative path",
            )
        ]
    reference = repo / str(value)
    lexical_relative = _repo_lexical_relative(repo, reference)
    resolved_relative = _repo_relative(repo, reference)
    if lexical_relative is None or resolved_relative is None:
        return [
            Diagnostic(
                "path_outside_repo",
                str(map_path),
                f"{label} must resolve within the target repository: {value!r}",
            )
        ]
    if _path_crosses_reparse(repo, reference):
        return [
            Diagnostic(
                "invalid_reference_mode",
                str(map_path),
                f"{label} must not cross a filesystem reparse point: {lexical_relative}",
            )
        ]
    if not reference.exists():
        return [
            Diagnostic(
                "missing_reference",
                str(map_path),
                f"{label} does not exist: {lexical_relative}",
            )
        ]
    if not reference.is_file():
        return [
            Diagnostic(
                "invalid_reference",
                str(map_path),
                f"{label} must name a regular file: {lexical_relative}",
            )
        ]
    head_entry = _git_entry(repo, lexical_relative, "head")
    index_entry = _git_entry(repo, lexical_relative, "index")
    if head_entry is None or index_entry is None:
        return [
            Diagnostic(
                "untracked_reference",
                str(map_path),
                f"{label} must be tracked in HEAD and present in the Git index: "
                f"{lexical_relative}",
            )
        ]
    head_mode, head_object = head_entry
    index_mode, index_object = index_entry
    if head_mode not in REGULAR_GIT_MODES or index_mode not in REGULAR_GIT_MODES:
        return [
            Diagnostic(
                "invalid_reference_mode",
                str(map_path),
                f"{label} must be a regular Git file (100644 or 100755) in HEAD "
                f"and the index: {lexical_relative} "
                f"(HEAD={head_mode}, index={index_mode})",
            )
        ]
    if require_same_blob and head_object != index_object:
        diagnostics.append(
            Diagnostic(
                "reference_blob_mismatch",
                str(map_path),
                f"{label} must use the same Git blob in HEAD and the stage-0 index: "
                f"{lexical_relative} (HEAD={head_object}, index={index_object})",
            )
        )
    return diagnostics


def _validate_semantics(
    repo: Path,
    map_path: Path,
    state_path: Path,
    active_path: Path | None,
    project_map: dict[str, object],
    state: dict[str, object],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    relative_paths = {
        "map": _repo_relative(repo, map_path),
        "state": _repo_relative(repo, state_path),
        "active": _repo_relative(repo, active_path) if active_path is not None else None,
    }
    for name, relative in relative_paths.items():
        if relative is None:
            path = {"map": map_path, "state": state_path, "active": active_path}[name]
            diagnostics.append(
                Diagnostic(
                    "path_outside_repo",
                    str(path),
                    f"configured {name} path must be contained within the target repository",
                )
            )

    if project_map:
        map_version = project_map.get("version")
        scalar_fields = (
            ("spec_path", "plan_path", "current_state_path", "active_state_path", "audit_path")
            if map_version == 1
            else ("current_state_path", "active_state_path")
        )
        for field in scalar_fields:
            if field in project_map and not _is_nonempty_string(project_map[field]):
                diagnostics.append(
                    Diagnostic("invalid_field", str(map_path), f"{field} must be a non-empty string")
                )

        expected_paths = {
            "current_state_path": relative_paths["state"],
            "active_state_path": relative_paths["active"],
        }
        for field, expected in expected_paths.items():
            if expected is not None and project_map.get(field) != expected:
                diagnostics.append(
                    Diagnostic(
                        "path_mismatch",
                        str(map_path),
                        f"{field} {project_map.get(field)!r} != configured path {expected!r}",
                    )
                )

        if map_version == 1:
            references = [(field, project_map.get(field)) for field in ("spec_path", "plan_path", "audit_path")]
            require_same_blob = False
        elif map_version == 2:
            raw_authority = project_map.get("authority_paths")
            references = []
            require_same_blob = True
            if not isinstance(raw_authority, list):
                diagnostics.append(
                    Diagnostic(
                        "invalid_field",
                        str(map_path),
                        "authority_paths must be a TOML array of repository-relative paths",
                    )
                )
            else:
                seen: set[str] = set()
                for index, value in enumerate(raw_authority):
                    label = f"authority_paths[{index}]"
                    if not _is_nonempty_string(value):
                        diagnostics.append(
                            Diagnostic(
                                "invalid_field",
                                str(map_path),
                                f"{label} must be a non-empty repository-relative path",
                            )
                        )
                        continue
                    normalized = str(value).replace("\\", "/")
                    if normalized in seen:
                        diagnostics.append(
                            Diagnostic(
                                "invalid_field",
                                str(map_path),
                                f"authority_paths must not contain duplicates: {value!r}",
                            )
                        )
                        continue
                    seen.add(normalized)
                    references.append((label, value))
        else:
            references = []
            require_same_blob = False
        for label, value in references:
            diagnostics.extend(
                _validate_authority_reference(
                    repo,
                    map_path,
                    label,
                    value,
                    require_same_blob=require_same_blob,
                )
            )

    if state:
        state_version = state.get("version")
        identity_fields = (
            ("milestone", "branch", "current_agent")
            if state_version == 1
            else ("work_type", "work_id", "branch")
        )
        for field in identity_fields:
            if field in state and not _is_nonempty_string(state[field]):
                diagnostics.append(
                    Diagnostic("invalid_field", str(state_path), f"{field} must be a non-empty string")
                )
        if state_version == 2 and "owner" in state and not _is_nonempty_string(state["owner"]):
            diagnostics.append(
                Diagnostic("invalid_field", str(state_path), "owner must be a non-empty string when present")
            )
        if "status" in state and state.get("status") not in MILESTONE_STATUSES:
            diagnostics.append(
                Diagnostic(
                    "invalid_status",
                    str(state_path),
                    f"status must be one of {', '.join(sorted(MILESTONE_STATUSES))}",
                )
            )
        for field in ("baseline", "head"):
            if not _is_commit(repo, state.get(field)):
                diagnostics.append(
                    Diagnostic(
                        "invalid_sha",
                        str(state_path),
                        f"{field} must be an existing exact 40-hex commit ID",
                    )
                )

    for name in ("map", "state"):
        relative = relative_paths[name]
        if relative is not None and not _is_tracked(repo, relative):
            path = map_path if name == "map" else state_path
            diagnostics.append(
                Diagnostic(
                    "untracked_state",
                    str(path),
                    f"L1/L2 state must be tracked or staged in Git: {relative}",
                )
            )
    return diagnostics


def _validate_active_persistence(repo: Path, active_path: Path) -> list[Diagnostic]:
    """Require existing L3 state to remain local, ignored, and absent from Git."""

    local_exists = os.path.lexists(active_path)
    relative = _repo_lexical_relative(repo, active_path)
    if relative is None or _path_crosses_reparse(repo, active_path):
        return [
            Diagnostic(
                "active_persistence",
                str(active_path),
                "L3 active state must be a regular path within the repository",
            )
        ]
    diagnostics: list[Diagnostic] = []
    try:
        index_entries = _git(repo, "ls-files", "--stage", "--", relative)
        head_entries = _git(repo, "ls-tree", "HEAD", "--", relative)
    except (OSError, subprocess.CalledProcessError) as error:
        return [Diagnostic("git_error", str(active_path), str(error))]
    if index_entries or head_entries:
        diagnostics.append(
            Diagnostic(
                "active_persistence",
                str(active_path),
                f"L3 active state must be absent from the Git index and HEAD: {relative}",
            )
        )
    if local_exists:
        ignored = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "--no-index", "--quiet", "--", relative],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if ignored.returncode != 0:
            diagnostics.append(
                Diagnostic(
                    "active_persistence",
                    str(active_path),
                    f"L3 active state must be ignored by Git: {relative}",
                )
            )
    return diagnostics


def validate_state(
    repo: Path,
    map_path: Path,
    state_path: Path,
    active_path: Path | None,
) -> list[Diagnostic]:
    """Validate state structure, budgets, and declared Git branch/HEAD."""

    diagnostics: list[Diagnostic] = []
    map_diagnostics, project_map = _validate_document(map_path, "project-map")
    state_diagnostics, state = _validate_document(state_path, "current-work")
    diagnostics.extend(map_diagnostics)
    diagnostics.extend(state_diagnostics)
    active: dict[str, object] = {}
    if active_path is not None:
        active_diagnostics, active = _validate_document(active_path, "active")
        diagnostics.extend(active_diagnostics)
        diagnostics.extend(_validate_active_persistence(repo, active_path))

    map_version = project_map.get("version") if project_map else None
    state_version = state.get("version") if state else None
    active_version = active.get("version") if active else None
    if map_version in {1, 2} and state_version in {1, 2} and map_version != state_version:
        diagnostics.append(
            Diagnostic(
                "schema_version_mismatch",
                str(state_path),
                f"project map schema v{map_version} cannot be paired with current state schema v{state_version}",
            )
        )
    if (
        active_version in {1, 2}
        and state_version in {1, 2}
        and active_version != state_version
    ):
        diagnostics.append(
            Diagnostic(
                "schema_version_mismatch",
                str(active_path),
                f"active schema v{active_version} cannot be paired with current state schema v{state_version}",
            )
        )
    if active:
        if "action" in active and not _is_nonempty_string(active["action"]):
            diagnostics.append(
                Diagnostic("invalid_field", str(active_path), "action must be a non-empty string")
            )
        owner_field = "agent" if active_version == 1 else "owner"
        if owner_field in active and not _is_nonempty_string(active[owner_field]):
            diagnostics.append(
                Diagnostic(
                    "invalid_field",
                    str(active_path),
                    f"{owner_field} must be a non-empty string when present",
                )
            )

    diagnostics.extend(
        _validate_semantics(repo, map_path, state_path, active_path, project_map, state)
    )

    if state:
        try:
            actual_branch = _git(repo, "branch", "--show-current")
            actual_head = _git(repo, "rev-parse", "HEAD")
        except (OSError, subprocess.CalledProcessError) as error:
            diagnostics.append(Diagnostic("git_error", str(repo), str(error)))
        else:
            declared_branch = state.get("branch")
            if declared_branch != actual_branch:
                diagnostics.append(
                    Diagnostic(
                        "branch_mismatch",
                        str(state_path),
                        f"declared branch {declared_branch!r} != Git branch {actual_branch!r}",
                    )
                )
            declared_baseline = state.get("baseline")
            declared_head = state.get("head")
            valid_baseline = _is_commit(repo, declared_baseline)
            valid_head = _is_commit(repo, declared_head)
            if valid_baseline and valid_head:
                ancestry = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo),
                        "merge-base",
                        "--is-ancestor",
                        str(declared_baseline),
                        str(declared_head),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                if ancestry.returncode != 0:
                    diagnostics.append(
                        Diagnostic(
                            "baseline_not_ancestor",
                            str(state_path),
                            "declared baseline must be an ancestor of declared verified work HEAD",
                        )
                    )

            allowed_committed_paths = {
                relative
                for relative in (
                    _repo_relative(repo, map_path),
                    _repo_relative(repo, state_path),
                )
                if relative is not None
            }
            if state_version == 2:
                legacy_relative = _repo_relative(
                    repo, state_path.parent / "current-milestone.md"
                )
                if legacy_relative is not None:
                    allowed_committed_paths.add(legacy_relative)
            if valid_head and not _declared_head_is_current(
                repo, declared_head, actual_head, allowed_committed_paths
            ):
                diagnostics.append(
                    Diagnostic(
                        "head_mismatch",
                        str(state_path),
                        "declared verified work HEAD "
                        f"{declared_head!r} is stale for Git HEAD {actual_head!r}",
                    )
                )

            allowed_worktree_paths = {
                relative
                for relative in (
                    _repo_relative(repo, map_path),
                    _repo_relative(repo, state_path),
                    _repo_relative(repo, active_path) if active_path is not None else None,
                )
                if relative is not None
            }
            if state_version == 2:
                legacy_relative = _repo_relative(
                    repo, state_path.parent / "current-milestone.md"
                )
                if legacy_relative is not None:
                    allowed_worktree_paths.add(legacy_relative)
            changed_worktree_paths = _working_tree_paths(repo) - allowed_worktree_paths
            if changed_worktree_paths:
                ordered = sorted(changed_worktree_paths)
                preview = ", ".join(ordered[:10])
                remainder = len(ordered) - 10
                if remainder > 0:
                    preview += f", and {remainder} more"
                diagnostics.append(
                    Diagnostic(
                        "working_tree_drift",
                        str(repo),
                        "uncommitted paths outside context state require reconciliation: "
                        + preview,
                    )
                )

    return diagnostics


GENERIC_TEMPLATE_ROOT = Path(__file__).parents[1] / "templates" / "generic"
ACTIVE_IGNORE_RULE = ".agent/active.md"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_initial_state(
    branch: str,
    head: str,
    work_type: str,
    work_id: str,
    owner: str | None,
) -> tuple[str, str]:
    project_map = (GENERIC_TEMPLATE_ROOT / "project-map.md").read_text(encoding="utf-8")
    current = (GENERIC_TEMPLATE_ROOT / "current-work.md").read_text(encoding="utf-8")
    replacements = {
        'work_type = "<work-type>"': f"work_type = {_toml_string(work_type)}",
        'work_id = "<work-id>"': f"work_id = {_toml_string(work_id)}",
        'branch = "<branch>"': f"branch = {_toml_string(branch)}",
        'baseline = "<full-baseline-sha>"': f"baseline = {_toml_string(head)}",
        'head = "<full-most-recent-verified-work-sha>"': f"head = {_toml_string(head)}",
    }
    for placeholder, rendered in replacements.items():
        if placeholder not in current:
            raise ContextOperationError(
                f"generic current-work template is missing placeholder: {placeholder}"
            )
        current = current.replace(placeholder, rendered, 1)
    if owner is not None:
        head_line = f"head = {_toml_string(head)}"
        current = current.replace(
            head_line,
            f"{head_line}\nowner = {_toml_string(owner)}",
            1,
        )
    return project_map, current


def _git_root(repo: Path) -> Path:
    try:
        return Path(_git(repo.resolve(), "rev-parse", "--show-toplevel")).resolve()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ContextOperationError(f"not a usable Git repository: {repo}") from error


def _merged_gitignore(original: bytes | None) -> bytes:
    if original is None:
        return f"{ACTIVE_IGNORE_RULE}\n".encode("utf-8")
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContextOperationError(".gitignore must be UTF-8 before Optimize can update it") from error
    if ACTIVE_IGNORE_RULE in text.splitlines():
        return original
    separator = b"" if not original or original.endswith((b"\n", b"\r")) else b"\n"
    return original + separator + f"{ACTIVE_IGNORE_RULE}\n".encode("utf-8")


def _atomic_write(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _restore_files(
    originals: Iterable[tuple[Path, bytes | None]],
) -> list[str]:
    """Restore or remove files and return every failed rollback action."""

    errors: list[str] = []
    for path, original in originals:
        try:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, original)
        except OSError as error:
            errors.append(f"{path}: {error}")
            continue
        if original is None:
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                errors.append(f"{path}: rollback verification failed: {error}")
            else:
                errors.append(f"{path}: rollback verification found an unexpected path")
            continue
        try:
            restored = path.read_bytes()
        except OSError as error:
            errors.append(f"{path}: rollback verification failed: {error}")
        else:
            if restored != original:
                errors.append(f"{path}: rollback verification found different contents")
    return errors


def _existing_v2_matches(
    repo: Path,
    map_path: Path,
    state_path: Path,
    active_path: Path,
    work_type: str,
    work_id: str,
    owner: str | None,
) -> bool:
    map_diagnostics, project_map = _validate_document(map_path, "project-map")
    state_diagnostics, state = _validate_document(state_path, "current-work")
    if map_diagnostics or state_diagnostics:
        return False
    if project_map.get("version") != 2 or state.get("version") != 2:
        return False
    expected_owner = state.get("owner") if owner is None else owner
    if owner is None and "owner" in state:
        return False
    if state.get("owner") != expected_owner:
        return False
    if state.get("work_type") != work_type or state.get("work_id") != work_id:
        return False
    diagnostics = validate_state(repo, map_path, state_path, active_path)
    return {item.code for item in diagnostics} <= {"untracked_state", "working_tree_drift"}


def initialize_repository(
    repo: Path,
    work_type: str,
    work_id: str,
    owner: str | None = None,
) -> tuple[Path, bool]:
    """Create collision-safe generic L1/L2 state and the L3 ignore rule."""

    identities = {"work_type": work_type, "work_id": work_id}
    if owner is not None:
        identities["owner"] = owner
    for field, value in identities.items():
        if not _is_nonempty_string(value):
            raise ContextOperationError(f"{field} must be a non-empty string")

    root = _git_root(repo)
    try:
        branch = _git(root, "branch", "--show-current")
        head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    except (OSError, subprocess.CalledProcessError) as error:
        raise ContextOperationError("repository must have a readable branch and HEAD commit") from error
    if not branch:
        raise ContextOperationError("repository must be on a named branch before initialization")

    agent = root / ".agent"
    map_path = agent / "project-map.md"
    state_path = agent / "current-work.md"
    legacy_path = agent / "current-milestone.md"
    active_path = agent / "active.md"
    gitignore = root / ".gitignore"

    if _path_crosses_reparse(root, agent):
        raise ContextOperationError(
            ".agent path crosses a filesystem reparse point; no files were changed"
        )
    if _path_crosses_reparse(root, gitignore):
        raise ContextOperationError(
            ".gitignore path crosses a filesystem reparse point; no files were changed"
        )
    if agent.exists() and not agent.is_dir():
        raise ContextOperationError(
            "partial or conflicting .agent state exists; no files were changed"
        )
    if gitignore.exists() and not gitignore.is_file():
        raise ContextOperationError(".gitignore must be a regular file")
    original_gitignore = gitignore.read_bytes() if gitignore.exists() else None
    desired_gitignore = _merged_gitignore(original_gitignore)

    if legacy_path.exists():
        raise ContextOperationError(
            f"v1 state detected; run migrate --repo {_toml_string(str(root))}"
        )
    existing_managed = [path for path in (map_path, state_path) if path.exists()]
    if len(existing_managed) == 2 and _existing_v2_matches(
        root, map_path, state_path, active_path, work_type, work_id, owner
    ):
        if desired_gitignore != original_gitignore:
            _atomic_write(gitignore, desired_gitignore)
        return root, False
    if existing_managed or active_path.exists():
        try:
            map_metadata, _ = load_markdown_state(map_path)
        except (OSError, StateFormatError):
            map_metadata = {}
        if map_metadata.get("version") == 1:
            raise ContextOperationError(
                f"v1 state detected; run migrate --repo {_toml_string(str(root))}"
            )
        raise ContextOperationError(
            "partial or conflicting .agent state exists; no files were changed"
        )

    project_map, current = _render_initial_state(branch, head, work_type, work_id, owner)
    writes = (
        (gitignore, desired_gitignore, original_gitignore),
        (map_path, project_map.encode("utf-8"), None),
        (state_path, current.encode("utf-8"), None),
    )
    completed: list[tuple[Path, bytes | None]] = []
    try:
        for path, contents, original in writes:
            if path == gitignore and contents == original:
                continue
            _atomic_write(path, contents)
            completed.append((path, original))
    except OSError as error:
        rollback_errors = _restore_files(reversed(completed))
        try:
            if agent.exists() and not any(agent.iterdir()):
                agent.rmdir()
        except OSError as rollback_error:
            rollback_errors.append(f"{agent}: {rollback_error}")
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise ContextOperationError(
                "initialization failed; rollback incomplete and manual recovery is "
                f"required before retrying after {error}: {details}"
            ) from error
        raise ContextOperationError(f"initialization failed and was rolled back: {error}") from error
    return root, True


def _render_markdown_state(metadata_lines: list[str], body: str) -> str:
    normalized_body = body.strip("\n")
    return "+++\n" + "\n".join(metadata_lines) + "\n+++\n\n" + normalized_body + "\n"


def _replace_heading(body: str, old: str, new: str) -> str:
    return re.sub(
        rf"(?m)^## {re.escape(old)}[ \t]*$",
        f"## {new}",
        body,
    )


def _ensure_sections(body: str, sections: Iterable[str], note: str) -> str:
    present = _headings(body)
    result = body.rstrip()
    for section in sections:
        if section not in present:
            result += f"\n\n## {section}\n\n- {note}"
    return result + "\n"


def _migration_backup_path(repo: Path) -> Path:
    try:
        raw = _git(
            repo,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "optimize-context-v1-active.md",
        )
    except subprocess.CalledProcessError as error:
        raise ContextOperationError("cannot resolve Git metadata path for active backup") from error
    return Path(raw)


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _migration_rollback_text(root: Path, active_backup: Path | None) -> str:
    lines = [
        "rollback commands (run from the repository root):",
        "git reset HEAD -- .agent/project-map.md .agent/current-milestone.md .agent/current-work.md",
        "git restore -- .agent/project-map.md .agent/current-milestone.md",
        "Remove-Item -LiteralPath .agent/current-work.md -ErrorAction SilentlyContinue",
    ]
    if active_backup is not None:
        lines.extend(
            [
                f"Copy-Item -LiteralPath {_powershell_literal(str(active_backup))} "
                "-Destination .agent/active.md -Force",
                f"Remove-Item -LiteralPath {_powershell_literal(str(active_backup))}",
            ]
        )
    return "\n".join(lines)


def _render_v2_migration(
    project_map: dict[str, object],
    map_body: str,
    state: dict[str, object],
    state_body: str,
    active: dict[str, object],
    active_body: str,
) -> tuple[str, str, str | None]:
    authority: list[str] = []
    for field in ("spec_path", "plan_path", "audit_path"):
        value = project_map.get(field)
        if isinstance(value, str) and value not in authority:
            authority.append(value)
    map_metadata = [
        "version = 2",
        'current_state_path = ".agent/current-work.md"',
        f"active_state_path = {_toml_string(str(project_map['active_state_path']))}",
        "authority_paths = [" + ", ".join(_toml_string(path) for path in authority) + "]",
    ]
    converted_map_body = _replace_heading(map_body, "Source of truth", "Authority")
    converted_map_body = _replace_heading(
        converted_map_body,
        "Contract index",
        "Important interfaces and contracts",
    )
    converted_map_body = converted_map_body.replace(
        ".agent/current-milestone.md", ".agent/current-work.md"
    )
    converted_map_body = _ensure_sections(
        converted_map_body,
        (
            "Authority",
            "Code map",
            "Verification",
            "Important interfaces and contracts",
            "Operational state",
        ),
        "Not recorded in the v1 project map.",
    )

    state_metadata = [
        "version = 2",
        'work_type = "milestone"',
        f"work_id = {_toml_string(str(state['milestone']))}",
        f"status = {_toml_string(str(state['status']))}",
        f"branch = {_toml_string(str(state['branch']))}",
        f"baseline = {_toml_string(str(state['baseline']))}",
        f"head = {_toml_string(str(state['head']))}",
    ]
    current_agent = state.get("current_agent")
    if _is_nonempty_string(current_agent):
        state_metadata.append(f"owner = {_toml_string(str(current_agent))}")
    converted_state_body = _replace_heading(
        state_body,
        "Approved clarifications",
        "Constraints and clarifications",
    )
    converted_state_body = _ensure_sections(
        converted_state_body,
        STATE_SCHEMAS[("current-work", 2)]["sections"],
        "Not recorded in the v1 current state.",
    )

    converted_active: str | None = None
    if active:
        active_metadata = [
            "version = 2",
            f"action = {_toml_string(str(active['action']))}",
        ]
        agent = active.get("agent")
        if _is_nonempty_string(agent):
            active_metadata.append(f"owner = {_toml_string(str(agent))}")
        converted_active = _render_markdown_state(active_metadata, active_body)
    return (
        _render_markdown_state(map_metadata, converted_map_body),
        _render_markdown_state(state_metadata, converted_state_body),
        converted_active,
    )


def migrate_repository(repo: Path, *, dry_run: bool = False) -> tuple[Path, str, str]:
    """Convert valid v1 state to v2 without staging or committing it."""

    root = _git_root(repo)
    agent = root / ".agent"
    map_path = agent / "project-map.md"
    legacy_path = agent / "current-milestone.md"
    state_path = agent / "current-work.md"
    active_path = agent / "active.md"

    if state_path.exists():
        if legacy_path.exists():
            raise ContextOperationError(
                "migration destination .agent/current-work.md already exists; no files were changed"
            )
        diagnostics = validate_state(root, map_path, state_path, active_path)
        if diagnostics:
            preview = "; ".join(f"{item.code}: {item.message}" for item in diagnostics[:5])
            raise ContextOperationError(
                f"existing v2 state is not valid after migration: {preview}"
            )
        return root, "already", "v2 context state already migrated and valid"

    try:
        status = _git(root, "status", "--porcelain", "--untracked-files=all")
    except subprocess.CalledProcessError as error:
        raise ContextOperationError("cannot inspect repository cleanliness") from error
    if status:
        raise ContextOperationError(
            "migration requires a clean tracked/index worktree; no files were changed"
        )
    if not map_path.is_file() or not legacy_path.is_file():
        raise ContextOperationError("valid v1 project-map and current-milestone files are required")

    diagnostics = validate_state(root, map_path, legacy_path, active_path)
    if diagnostics:
        preview = "; ".join(f"{item.code}: {item.message}" for item in diagnostics[:8])
        raise ContextOperationError(f"v1 state must validate before migration: {preview}")
    project_map, map_body = load_markdown_state(map_path)
    state, state_body = load_markdown_state(legacy_path)
    active: dict[str, object] = {}
    active_body = ""
    if active_path.exists():
        active, active_body = load_markdown_state(active_path)
    if project_map.get("version") != 1 or state.get("version") != 1:
        raise ContextOperationError("migrate accepts only schema-v1 state")

    converted_map, converted_state, converted_active = _render_v2_migration(
        project_map, map_body, state, state_body, active, active_body
    )
    active_backup = _migration_backup_path(root) if active else None
    rollback = _migration_rollback_text(root, active_backup)
    if dry_run:
        return (
            root,
            "dry-run",
            "dry run: would convert project-map.md, move current-milestone.md to "
            "current-work.md"
            + (", and convert active.md" if active else "")
            + "\n"
            + rollback,
        )

    originals = {
        map_path: map_path.read_bytes(),
        legacy_path: legacy_path.read_bytes(),
        state_path: None,
    }
    if active:
        originals[active_path] = active_path.read_bytes()
        if active_backup is not None and active_backup.exists():
            raise ContextOperationError(
                f"active backup destination already exists: {active_backup}"
            )
    try:
        if active_backup is not None:
            _atomic_write(active_backup, originals[active_path] or b"")
        _atomic_write(map_path, converted_map.encode("utf-8"))
        _atomic_write(state_path, converted_state.encode("utf-8"))
        if converted_active is not None:
            _atomic_write(active_path, converted_active.encode("utf-8"))
        legacy_path.unlink()
    except OSError as error:
        rollback_errors = _restore_files(originals.items())
        if active_backup is not None and not rollback_errors:
            rollback_errors.extend(_restore_files(((active_backup, None),)))
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise ContextOperationError(
                "migration failed; rollback incomplete and manual recovery is required "
                f"before retrying after {error}: {details}"
            ) from error
        raise ContextOperationError(f"migration failed and was rolled back: {error}") from error
    return root, "migrated", "v1 context migrated to v2\n" + rollback


PACKET_FIELDS = (
    "task",
    "role",
    "authority",
    "baseline",
    "head",
    "changed_files",
    "current_state",
    "scope",
    "known_findings",
    "do_not",
    "return_fields",
)

PACKET_HEADINGS = {
    "task": "TASK",
    "role": "ROLE",
    "authority": "AUTHORITY",
    "baseline": "BASELINE",
    "head": "HEAD",
    "changed_files": "CHANGED FILES",
    "current_state": "CURRENT STATE",
    "scope": "SCOPE",
    "known_findings": "KNOWN FINDINGS",
    "do_not": "DO NOT",
    "return_fields": "RETURN",
}
PACKET_MAX_BYTES = 16 * 1024
PACKET_MAX_ITEMS = 50
PACKET_MAX_ITEM_BYTES = 1024
PACKET_SCALAR_BYTES = {"task": 2048, "role": 256, "baseline": 40, "head": 40}

METRIC_COUNTER_FIELDS = {
    "wait_calls",
    "short_wait_calls",
    "unknown_wait_bound_calls",
    "long_wait_early_returns",
    "long_wait_timeouts",
    "wait_elapsed_seconds",
    "list_agents_calls",
    "liveness_only_list_agents_calls",
    "full_plan_reads",
    "full_spec_reads",
    "diff_calls",
    "full_diff_calls",
    "duplicate_file_reads",
    "recovery_reads",
    "tool_outputs",
    "large_tool_outputs",
    "duplicate_tool_output_bytes",
    "compactions",
    "subagent_count",
    "review_cycles",
}
METRIC_TOKEN_FIELDS = {
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
}
REQUIRED_EVIDENCE_IDS = {
    "green_wait",
    "green_recovery",
    "green_review",
    "green_drift",
    "green_ledger",
    "red_wait",
    "red_recovery",
    "red_review",
    "red_drift",
    "red_ledger",
    "resume",
    "reviewer",
    "wait",
}


def _packet_values(data: dict[str, object], field: str) -> list[str]:
    value = data[field]
    if field in {"task", "role", "baseline", "head"}:
        if not isinstance(value, str) or not value.strip():
            raise PacketFormatError(f"{field} must be a non-empty string")
        if "\n" in value or "\r" in value:
            raise PacketFormatError(f"{field} must be single-line")
        if len(value.encode("utf-8")) > PACKET_SCALAR_BYTES[field]:
            raise PacketFormatError(
                f"{field} exceeds {PACKET_SCALAR_BYTES[field]} bytes"
            )
        if field in {"baseline", "head"} and FULL_SHA.fullmatch(value) is None:
            raise PacketFormatError(f"{field} must be an exact 40-hex commit ID")
        return [value]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise PacketFormatError(f"{field} must be a list of non-empty strings")
    if len(value) > PACKET_MAX_ITEMS:
        raise PacketFormatError(f"{field} must contain at most {PACKET_MAX_ITEMS} items")
    for item in value:
        if "\n" in item or "\r" in item:
            raise PacketFormatError(f"{field} entries must be single-line")
        if len(item.encode("utf-8")) > PACKET_MAX_ITEM_BYTES:
            raise PacketFormatError(
                f"{field} entry exceeds {PACKET_MAX_ITEM_BYTES} bytes"
            )
    return value


def render_packet(data: dict[str, object]) -> str:
    """Render a closed navigation-and-scope packet in deterministic order."""

    expected = set(PACKET_FIELDS)
    missing = sorted(expected - data.keys())
    unknown = sorted(data.keys() - expected)
    if missing:
        raise PacketFormatError(f"missing packet fields: {', '.join(missing)}")
    if unknown:
        raise PacketFormatError(f"unknown packet fields: {', '.join(unknown)}")

    sections: list[str] = []
    code_fields = {"authority", "changed_files"}
    scalar_fields = {"task", "role", "baseline", "head"}
    for field in PACKET_FIELDS:
        values = _packet_values(data, field)
        heading = f"# {PACKET_HEADINGS[field]}"
        if field in scalar_fields:
            body = values[0]
        else:
            body = "\n".join(
                f"- `{value}`" if field in code_fields else f"- {value}"
                for value in values
            )
        sections.append(f"{heading}\n\n{body}")
    rendered = "\n\n".join(sections) + "\n"
    if len(rendered.encode("utf-8")) > PACKET_MAX_BYTES:
        raise PacketFormatError(f"rendered packet exceeds {PACKET_MAX_BYTES} bytes")
    return rendered


def _closed_keys(
    value: object, expected: set[str], label: str, errors: list[str]
) -> dict[str, object]:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return {}
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    errors.extend(f"missing {label} field: {field}" for field in missing)
    errors.extend(f"unknown {label} field: {field}" for field in unknown)
    return value


def _timestamp(value: object, label: str, errors: list[str]) -> datetime | None:
    if not _is_nonempty_string(value):
        errors.append(f"{label} must be a non-empty ISO 8601 timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label} must be a valid ISO 8601 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{label} must include a UTC offset")
        return None
    return parsed


def _metrics_file(
    value: object, evidence_root: Path, label: str, errors: list[str]
) -> None:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        errors.append(f"{label} must contain exactly path and sha256")
        return
    _verified_git_evidence(
        evidence_root, value.get("path"), value.get("sha256"), label, errors
    )


def validate_metrics(data: object, evidence_root: Path | None = None) -> list[str]:
    """Validate a complete, comparable context-metrics record."""

    errors: list[str] = []
    evidence_root = (evidence_root or Path.cwd()).resolve()
    common_top_fields = {
        "version",
        "work_scope",
        "correctness_gate",
        "started_at",
        "ended_at",
        "baseline",
        "head",
        "counters",
        "tokens",
        "correctness",
        "evidence",
        "notes",
    }
    version = data.get("version") if isinstance(data, dict) else None
    if version == 2:
        identity_fields = {"work_type", "work_id"}
        duration_field = "work_duration_seconds"
    else:
        identity_fields = {"milestone"}
        duration_field = "milestone_duration_seconds"
    top_fields = common_top_fields | identity_fields
    record = _closed_keys(data, top_fields, "top-level", errors)
    if not record:
        return errors
    if type(record.get("version")) is not int or record.get("version") not in {1, 2}:
        errors.append("version must be integer 1 or 2")
    for field in (*sorted(identity_fields), "work_scope", "correctness_gate"):
        if not _is_nonempty_string(record.get(field)):
            errors.append(f"{field} must be a non-empty string")
    valid_commits: dict[str, str] = {}
    for field in ("baseline", "head"):
        value = record.get(field)
        if not _is_commit(evidence_root, value):
            errors.append(f"{field} must be an existing exact commit ID")
        else:
            valid_commits[field] = str(value)
    if set(valid_commits) == {"baseline", "head"}:
        ancestry = subprocess.run(
            [
                "git",
                "-C",
                str(evidence_root),
                "merge-base",
                "--is-ancestor",
                valid_commits["baseline"],
                valid_commits["head"],
            ],
            capture_output=True,
        )
        if ancestry.returncode != 0:
            errors.append("baseline must be an ancestor of head")

    started = _timestamp(record.get("started_at"), "started_at", errors)
    ended = _timestamp(record.get("ended_at"), "ended_at", errors)

    counters = _closed_keys(
        record.get("counters"), METRIC_COUNTER_FIELDS | {duration_field}, "counter", errors
    )
    for field in METRIC_COUNTER_FIELDS | {duration_field}:
        if field not in counters:
            continue
        value = counters[field]
        if type(value) is not int or value < 0:
            errors.append(f"counter {field} must be a non-negative integer")
    if all(type(counters.get(field)) is int for field in ("short_wait_calls", "wait_calls")):
        if counters["short_wait_calls"] > counters["wait_calls"]:
            errors.append("short_wait_calls cannot exceed wait_calls")
    if all(
        type(counters.get(field)) is int
        for field in ("short_wait_calls", "unknown_wait_bound_calls", "wait_calls")
    ):
        classified = counters["short_wait_calls"] + counters["unknown_wait_bound_calls"]
        if classified > counters["wait_calls"]:
            errors.append(
                "short_wait_calls + unknown_wait_bound_calls cannot exceed wait_calls"
            )
        explicit_long = max(0, counters["wait_calls"] - classified)
        if all(
            type(counters.get(field)) is int
            for field in ("long_wait_early_returns", "long_wait_timeouts")
        ) and (
            counters["long_wait_early_returns"] + counters["long_wait_timeouts"]
            != explicit_long
        ):
            errors.append("long wait outcomes must equal explicitly bounded long waits")
    if all(
        type(counters.get(field)) is int
        for field in ("liveness_only_list_agents_calls", "list_agents_calls")
    ) and counters["liveness_only_list_agents_calls"] > counters["list_agents_calls"]:
        errors.append("liveness_only_list_agents_calls cannot exceed list_agents_calls")
    if all(type(counters.get(field)) is int for field in ("full_diff_calls", "diff_calls")):
        if counters["full_diff_calls"] > counters["diff_calls"]:
            errors.append("full_diff_calls cannot exceed diff_calls")
    if all(
        type(counters.get(field)) is int
        for field in ("large_tool_outputs", "tool_outputs")
    ) and counters["large_tool_outputs"] > counters["tool_outputs"]:
        errors.append("large_tool_outputs cannot exceed tool_outputs")
    duration = counters.get(duration_field)
    if started is not None and ended is not None and type(duration) is int:
        if ended < started:
            errors.append("ended_at must not precede started_at")
        elif duration != int((ended - started).total_seconds()):
            errors.append(f"{duration_field} must equal ended_at - started_at")

    tokens = _closed_keys(record.get("tokens"), METRIC_TOKEN_FIELDS, "token", errors)
    for field in METRIC_TOKEN_FIELDS:
        if field not in tokens:
            continue
        value = tokens[field]
        if type(value) is not int or value < 0:
            errors.append(f"token {field} must be a non-negative integer")
    if all(type(tokens.get(field)) is int for field in ("cached_input_tokens", "input_tokens")):
        if tokens["cached_input_tokens"] > tokens["input_tokens"]:
            errors.append("cached_input_tokens cannot exceed input_tokens")
    if all(
        type(tokens.get(field)) is int
        for field in ("input_tokens", "output_tokens", "total_tokens")
    ) and tokens["total_tokens"] != tokens["input_tokens"] + tokens["output_tokens"]:
        errors.append("total_tokens must equal input_tokens + output_tokens")

    correctness = _closed_keys(
        record.get("correctness"),
        {"test_gates", "independent_review_verdict", "review_evidence"},
        "correctness",
        errors,
    )
    test_gates = correctness.get("test_gates")
    if not isinstance(test_gates, list) or not test_gates:
        errors.append("correctness.test_gates must not be empty")
    else:
        for index, gate in enumerate(test_gates):
            if not isinstance(gate, dict) or set(gate) != {"command", "result", "evidence"}:
                errors.append(
                    f"correctness.test_gates[{index}] must contain command, result, evidence"
                )
                continue
            if not _is_nonempty_string(gate.get("command")):
                errors.append(
                    f"correctness.test_gates[{index}] command must be non-empty"
                )
            if gate.get("result") not in {"PASS", "FAIL"}:
                errors.append(f"correctness.test_gates[{index}] result must be PASS or FAIL")
            _metrics_file(
                gate.get("evidence"),
                evidence_root,
                f"correctness.test_gates[{index}].evidence",
                errors,
            )
    if correctness.get("independent_review_verdict") not in {
        "APPROVE",
        "CHANGES_REQUIRED",
        "PENDING",
    }:
        errors.append(
            "correctness.independent_review_verdict must be APPROVE, CHANGES_REQUIRED, or PENDING"
        )
    review_evidence = correctness.get("review_evidence")
    if not isinstance(review_evidence, list) or not review_evidence:
        errors.append("correctness.review_evidence must not be empty")
    else:
        for index, path in enumerate(review_evidence):
            _metrics_file(
                path,
                evidence_root,
                f"correctness.review_evidence[{index}]",
                errors,
            )

    evidence = _closed_keys(
        record.get("evidence"),
        {"rollout", "manual_count_log", "method_notes"},
        "evidence",
        errors,
    )
    if "method_notes" in evidence and not _is_nonempty_string(evidence["method_notes"]):
        errors.append("evidence.method_notes must be a non-empty string")
    for field in ("rollout", "manual_count_log"):
        if field in evidence:
            _metrics_file(
                evidence[field], evidence_root, f"evidence.{field}", errors
            )
    notes = record.get("notes")
    if not isinstance(notes, list) or not all(_is_nonempty_string(item) for item in notes):
        errors.append("notes must be a list of non-empty strings")
    return errors


def validate_evidence(manifest_path: Path, repo: Path | None = None) -> list[str]:
    """Validate behavioral run metadata, source hashes, and full-response artifacts."""

    errors: list[str] = []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"manifest cannot be read: {error}"]
    if not isinstance(data, dict):
        return ["evidence manifest must be an object"]
    expected = {
        "version",
        "generated_at",
        "runtime",
        "source_hashes",
        "response_hashes",
        "samples",
    }
    version = data.get("version")
    if version == 2:
        expected.add("required_sample_ids")
    for field in sorted(expected - data.keys()):
        errors.append(f"missing evidence manifest field: {field}")
    for field in sorted(data.keys() - expected):
        errors.append(f"unknown evidence manifest field: {field}")
    if type(version) is not int or version not in {1, 2}:
        errors.append("evidence manifest version must be integer 1 or 2")
    _timestamp(data.get("generated_at"), "generated_at", errors)
    if not _is_nonempty_string(data.get("runtime")):
        errors.append("runtime must be a non-empty string")

    required_ids = REQUIRED_EVIDENCE_IDS
    if version == 2:
        declared_ids = data.get("required_sample_ids")
        if (
            not isinstance(declared_ids, list)
            or not declared_ids
            or not all(_is_nonempty_string(item) for item in declared_ids)
            or len(set(declared_ids)) != len(declared_ids)
        ):
            errors.append(
                "required_sample_ids must contain unique non-empty strings"
            )
            required_ids = set()
        else:
            required_ids = set(declared_ids)

    if repo is None:
        try:
            repo = Path(_git(manifest_path.parent, "rev-parse", "--show-toplevel"))
        except (OSError, subprocess.CalledProcessError):
            return errors + ["cannot determine evidence repository root"]
    repo = repo.resolve()

    evidence_contents: dict[str, bytes] = {}
    for field in ("source_hashes", "response_hashes"):
        values = data.get(field)
        if not isinstance(values, dict) or not values:
            errors.append(f"{field} must be a non-empty object")
            continue
        for raw_path, expected_hash in values.items():
            if not _is_nonempty_string(raw_path) or not isinstance(expected_hash, str):
                errors.append(f"{field} entries must map paths to SHA-256 strings")
                continue
            verifier = (
                _verified_git_source_evidence
                if field == "source_hashes"
                else _verified_git_evidence
            )
            contents = verifier(
                repo, raw_path, expected_hash, f"{field}[{raw_path}]", errors
            )
            if contents is not None:
                evidence_contents[str(raw_path)] = contents

    samples = data.get("samples")
    if not isinstance(samples, list):
        return errors + ["samples must be a list"]
    sample_fields = {
        "id",
        "condition",
        "scenario",
        "prompt_paths",
        "response_path",
        "response_heading",
        "task_name",
        "started_at",
        "completed_at",
        "expected",
        "actual",
        "disposition",
        "notes",
    }
    identifiers: list[str] = []
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            errors.append(f"samples[{index}] must be an object")
            continue
        for field in sorted(sample_fields - sample.keys()):
            errors.append(f"samples[{index}] missing field: {field}")
        for field in sorted(sample.keys() - sample_fields):
            errors.append(f"samples[{index}] unknown field: {field}")
        identifier = sample.get("id")
        if _is_nonempty_string(identifier):
            identifiers.append(str(identifier))
        else:
            errors.append(f"samples[{index}].id must be non-empty")
        for field in (
            "condition",
            "scenario",
            "response_path",
            "response_heading",
            "task_name",
            "expected",
            "actual",
            "notes",
        ):
            if not _is_nonempty_string(sample.get(field)):
                errors.append(f"samples[{index}].{field} must be non-empty")
        disposition = sample.get("disposition")
        if disposition not in {"PASS", "FAIL", "INVALID"}:
            errors.append(f"samples[{index}].disposition must be PASS, FAIL, or INVALID")
        elif _is_nonempty_string(identifier) and identifier in required_ids and disposition != "PASS":
            errors.append(f"required sample must PASS: {identifier}")

        started = _timestamp(sample.get("started_at"), f"samples[{index}].started_at", errors)
        completed = _timestamp(
            sample.get("completed_at"), f"samples[{index}].completed_at", errors
        )
        if started is not None and completed is not None and completed < started:
            errors.append(f"samples[{index}] completed_at must not precede started_at")

        prompt_paths = sample.get("prompt_paths")
        if not isinstance(prompt_paths, list) or not prompt_paths or not all(
            _is_nonempty_string(item) for item in prompt_paths
        ):
            errors.append(f"samples[{index}].prompt_paths must be a non-empty string list")
        else:
            for raw_reference in prompt_paths:
                raw_path, separator, fragment = str(raw_reference).partition("#")
                if raw_path not in evidence_contents:
                    errors.append(f"samples[{index}] prompt path has no recorded hash: {raw_path}")
                    continue
                if separator:
                    try:
                        text = evidence_contents[raw_path].decode("utf-8")
                    except UnicodeDecodeError:
                        errors.append(f"samples[{index}] prompt is not UTF-8: {raw_reference}")
                    else:
                        if f"## {fragment}" not in text:
                            errors.append(
                                f"samples[{index}] prompt heading is missing: {raw_reference}"
                            )

        response_path = sample.get("response_path")
        response_heading = sample.get("response_heading")
        if _is_nonempty_string(response_path):
            response_key = str(response_path)
            if response_key not in evidence_contents:
                errors.append(
                    f"samples[{index}] response path has no recorded hash: {response_path}"
                )
            else:
                try:
                    text = evidence_contents[response_key].decode("utf-8")
                except UnicodeDecodeError:
                    errors.append(f"samples[{index}] response is not UTF-8: {response_path}")
                else:
                    if _is_nonempty_string(response_heading) and (
                        f"## {response_heading}" not in text
                    ):
                        errors.append(
                            f"samples[{index}] response heading is missing: {response_heading}"
                        )

    duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
    errors.extend(f"duplicate sample id: {item}" for item in duplicates)
    missing_ids = sorted(required_ids - set(identifiers))
    errors.extend(f"missing required sample: {item}" for item in missing_ids)
    return errors


def _print_analysis(result: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print(f"rollout: {result['rollout']}")
    print(
        f"wait calls: {result['wait_calls']} ({result['short_wait_calls']} short, "
        f"{result['unknown_wait_bound_calls']} unknown bound)"
    )
    print(f"requested wait timeout ceilings (ms): {result['wait_timeout_ms']}")
    print(f"list_agents calls: {result['list_agents_calls']}")
    print(f"compactions: {result['compactions']}")
    print(
        "large tool outputs: "
        f"{result['large_tool_outputs']} / {result['tool_outputs']} "
        f"(>= {result['large_output_threshold_bytes']} bytes)"
    )
    print(f"final cumulative token usage: {result['final_token_usage']}")


def _default_skill_source() -> Path:
    runtime_root = Path(__file__).resolve().parents[1]
    if (
        runtime_root.name == "context-state-management"
        and (runtime_root / "SKILL.md").is_file()
    ):
        return runtime_root
    return runtime_root / "skills" / "context-state-management"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser(
        "analyze-rollout", help="extract context-efficiency metrics from Codex JSONL"
    )
    analyze.add_argument("rollout", type=Path)
    analyze.add_argument("--json", action="store_true", dest="as_json")
    save_summary = subparsers.add_parser(
        "save-task-summary",
        help="save one repository-external task summary",
    )
    save_summary.add_argument("--repo", type=Path, required=True)
    save_summary.add_argument("--input", type=Path, required=True)
    save_summary.add_argument("--observability-root", type=Path)
    archive = subparsers.add_parser(
        "archive-review",
        help="archive one reviewer return and its provenance",
    )
    archive.add_argument("--repo", type=Path, required=True)
    archive.add_argument("--archive-id", required=True)
    archive.add_argument("--metadata", type=Path, required=True)
    archive.add_argument("--body", type=Path)
    archive.add_argument("--observability-root", type=Path)
    export = subparsers.add_parser(
        "export-observability",
        help="export scoped task summaries and optional reviewer bodies",
    )
    selector = export.add_mutually_exclusive_group(required=True)
    selector.add_argument("--repo", type=Path)
    selector.add_argument("--project-id")
    export.add_argument("--archive-id")
    export.add_argument("--latest", type=int)
    export.add_argument("--since")
    export.add_argument("--until")
    export.add_argument("--include-review-bodies", action="store_true")
    export.add_argument("--observability-root", type=Path)
    export.add_argument("--output", type=Path, required=True)
    validate = subparsers.add_parser(
        "validate", help="validate layered context state against Git"
    )
    validate.add_argument("--repo", type=Path, required=True)
    validate.add_argument("--map", type=Path, default=Path(".agent/project-map.md"))
    validate.add_argument("--state", type=Path)
    validate.add_argument("--active", type=Path, default=Path(".agent/active.md"))
    initialize = subparsers.add_parser(
        "init", help="initialize generic stateful context in a Git repository"
    )
    initialize.add_argument("--repo", type=Path, required=True)
    initialize.add_argument("--work-type", required=True)
    initialize.add_argument("--work-id", required=True)
    initialize.add_argument("--owner")
    migrate = subparsers.add_parser(
        "migrate", help="convert valid schema-v1 context state to schema v2"
    )
    migrate.add_argument("--repo", type=Path, required=True)
    migrate.add_argument("--dry-run", action="store_true")
    install = subparsers.add_parser(
        "install-global", help="install the owned Optimize user skill and global router"
    )
    install.add_argument("--home", type=Path)
    install.add_argument("--codex-home", type=Path)
    install.add_argument(
        "--source-skill",
        type=Path,
        default=_default_skill_source(),
    )
    uninstall = subparsers.add_parser(
        "uninstall-global", help="remove only the unchanged Optimize-owned global files"
    )
    uninstall.add_argument("--home", type=Path)
    uninstall.add_argument("--codex-home", type=Path)
    packet = subparsers.add_parser(
        "render-packet", help="render a closed subagent context packet from JSON"
    )
    packet.add_argument("input", type=Path)
    packet.add_argument("-o", "--output", type=Path)
    metrics = subparsers.add_parser(
        "validate-metrics", help="validate a complete work comparison record"
    )
    metrics.add_argument("input", type=Path)
    evidence = subparsers.add_parser(
        "validate-evidence", help="validate behavioral experiment evidence and hashes"
    )
    evidence.add_argument("input", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze-rollout":
            _print_analysis(analyze_rollout(args.rollout), args.as_json)
            return 0
        if args.command == "save-task-summary":
            raw = json.loads(args.input.read_text(encoding="utf-8"))
            result = save_task_summary(args.repo, raw, args.observability_root)
            print(
                f"{result.status} {result.archive_id}: "
                f"{result.json_path} and {result.markdown_path}"
            )
            return 0
        if args.command == "archive-review":
            raw = json.loads(args.metadata.read_text(encoding="utf-8"))
            body = args.body.read_bytes() if args.body is not None else None
            result = archive_review(
                args.repo,
                args.archive_id,
                raw,
                body,
                args.observability_root,
            )
            print(
                f"{result.status} {result.review_id}: "
                f"{result.metadata_path}"
                + (f" and {result.body_path}" if result.body_path is not None else "")
            )
            return 0
        if args.command == "export-observability":
            project_id = (
                args.project_id
                if args.project_id is not None
                else project_id_for_repo(args.repo)
            )
            query = ExportQuery(
                project_id=project_id,
                archive_id=args.archive_id,
                latest=args.latest,
                since=args.since,
                until=args.until,
            )
            report = export_observations(
                args.observability_root
                if args.observability_root is not None
                else default_observability_root(),
                query,
                include_review_bodies=args.include_review_bodies,
            )
            status = write_export(args.output, report)
            print(f"{status} observability export: {args.output}")
            return 0
        if args.command == "validate":
            repo = args.repo.resolve()
            map_path = args.map if args.map.is_absolute() else repo / args.map
            if args.state is None:
                generic_state = repo / ".agent/current-work.md"
                state_path = (
                    generic_state
                    if generic_state.exists()
                    else repo / ".agent/current-milestone.md"
                )
            else:
                state_path = args.state if args.state.is_absolute() else repo / args.state
            active_path = args.active if args.active.is_absolute() else repo / args.active
            diagnostics = validate_state(repo, map_path, state_path, active_path)
            if diagnostics:
                for diagnostic in diagnostics:
                    print(
                        f"{diagnostic.code}: {diagnostic.path}: {diagnostic.message}",
                        file=sys.stderr,
                    )
                return 1
            print("context state valid")
            return 0
        if args.command == "init":
            root, created = initialize_repository(
                args.repo,
                args.work_type,
                args.work_id,
                args.owner,
            )
            if created:
                print(f"stateful context initialized in {root}")
            else:
                print(f"stateful context already initialized in {root}")
            print(
                "fill the navigation sections, stage .agent/project-map.md and "
                ".agent/current-work.md, then run validate"
            )
            return 0
        if args.command == "migrate":
            _, _, message = migrate_repository(args.repo, dry_run=args.dry_run)
            print(message)
            return 0
        if args.command in {"install-global", "uninstall-global"}:
            home = Path(os.path.abspath(args.home or Path.home()))
            codex_home = Path(
                os.path.abspath(
                    args.codex_home
                    or Path(os.environ.get("CODEX_HOME", str(home / ".codex")))
                )
            )
            if args.command == "install-global":
                print(install_global(home, codex_home, args.source_skill))
            else:
                print(uninstall_global(home, codex_home))
            return 0
        if args.command == "render-packet":
            raw = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise PacketFormatError("packet input must be a JSON object")
            rendered = render_packet(raw)
            if args.output:
                args.output.write_text(rendered, encoding="utf-8")
            else:
                print(rendered, end="")
            return 0
        if args.command == "validate-metrics":
            raw = json.loads(args.input.read_text(encoding="utf-8"))
            try:
                evidence_root = Path(
                    _git(args.input.parent, "rev-parse", "--show-toplevel")
                )
            except (OSError, subprocess.CalledProcessError):
                evidence_root = args.input.parent.resolve()
            errors = validate_metrics(raw, evidence_root)
            if errors:
                for error in errors:
                    print(f"metrics_error: {error}", file=sys.stderr)
                return 1
            print("work metrics valid" if raw.get("version") == 2 else "milestone metrics valid")
            return 0
        if args.command == "validate-evidence":
            errors = validate_evidence(args.input)
            if errors:
                for error in errors:
                    print(f"evidence_error: {error}", file=sys.stderr)
                return 1
            print("behavioral evidence valid")
            return 0
    except (
        OSError,
        json.JSONDecodeError,
        PacketFormatError,
        RolloutFormatError,
        StateFormatError,
        ContextOperationError,
        GlobalInstallError,
        ObservabilityError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
