#!/usr/bin/env python3
"""Owned, conflict-sensitive global installation for Optimize context routing."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path


SKILL_NAME = "context-state-management"
STATE_NAME = ".optimize-context-install.json"
START_MARKER = "<!-- BEGIN OPTIMIZE CONTEXT ROUTER -->"
END_MARKER = "<!-- END OPTIMIZE CONTEXT ROUTER -->"
ROUTER_BLOCK = f"""{START_MARKER}
## Optimize context routing

Use the `context-state-management` skill for context recovery and long-running work. Choose LIGHTWEIGHT for small, single-session work; do not create `.agent` state. Choose STATEFUL for resumable, multi-phase, delegated, review-heavy, or compaction-risk work. A lightweight task may upgrade to STATEFUL when those conditions emerge.
Before the final response, follow the Skill's common task finalization.
{END_MARKER}
"""
STATE_FIELDS = {
    "schema_version",
    "product",
    "skill_name",
    "skill_version",
    "skill_path",
    "skill_sha256",
    "agents_path",
    "agents_existed",
    "router_segment",
}


class GlobalInstallError(ValueError):
    """Raised when a global install cannot prove safe ownership."""


def _absolute(path: Path) -> Path:
    """Return an absolute lexical path without following reparse points."""

    return Path(os.path.abspath(path))


def _exists_lexically(path: Path) -> bool:
    return os.path.lexists(path)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        _absolute(path).relative_to(_absolute(parent))
    except ValueError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    if not _exists_lexically(path):
        return False
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return True
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _assert_no_reparse_ancestors(path: Path, label: str) -> None:
    """Inspect lexical components from the drive or UNC anchor through path."""

    absolute = _absolute(path)
    anchor = Path(absolute.anchor)
    current = anchor
    if _is_reparse_point(current):
        raise GlobalInstallError(f"{label} crosses a filesystem reparse point: {current}")
    for part in absolute.relative_to(anchor).parts:
        current /= part
        if _is_reparse_point(current):
            raise GlobalInstallError(f"{label} crosses a filesystem reparse point: {current}")
        if not _exists_lexically(current):
            break


def _assert_safe_chain(root: Path, target: Path, label: str) -> None:
    """Reject existing symlinks, junctions, or mount points below an owned root."""

    root = _absolute(root)
    target = _absolute(target)
    _assert_no_reparse_ancestors(root, label)
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise GlobalInstallError(f"{label} must stay within {root}: {target}") from error
    current = root
    for part in (Path(), *relative.parts):
        if part != Path():
            current /= part
        if _is_reparse_point(current):
            raise GlobalInstallError(f"{label} crosses a filesystem reparse point: {current}")


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


def _regular_file_bytes(path: Path, label: str) -> bytes:
    if _is_reparse_point(path) or not path.is_file():
        raise GlobalInstallError(f"{label} must be a regular file: {path}")
    return path.read_bytes()


def _skill_files(root: Path) -> list[Path]:
    _assert_no_reparse_ancestors(root, "skill source/target")
    if _is_reparse_point(root) or not root.is_dir():
        raise GlobalInstallError(f"skill source/target must be a regular directory: {root}")
    files: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        if _is_reparse_point(current):
            raise GlobalInstallError(f"skill trees must not contain reparse points: {current}")
        for name in directory_names:
            path = current / name
            if _is_reparse_point(path):
                raise GlobalInstallError(f"skill trees must not contain reparse points: {path}")
            if not path.is_dir():
                raise GlobalInstallError(f"skill tree contains an unsupported entry: {path}")
        for name in file_names:
            path = current / name
            if _is_reparse_point(path):
                raise GlobalInstallError(f"skill trees must not contain reparse points: {path}")
            if not path.is_file():
                raise GlobalInstallError(f"skill tree contains an unsupported entry: {path}")
            files.append(path)
    required = {root / "SKILL.md", root / "VERSION"}
    if not required <= set(files):
        raise GlobalInstallError("skill tree must contain regular SKILL.md and VERSION files")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def skill_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _skill_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        contents = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


def _skill_version(root: Path) -> str:
    raw = _regular_file_bytes(root / "VERSION", "skill VERSION")
    try:
        version = raw.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise GlobalInstallError("skill VERSION must be UTF-8") from error
    if not version or any(character.isspace() for character in version):
        raise GlobalInstallError("skill VERSION must contain one non-whitespace token")
    return version


def _active_agents_path(codex_home: Path) -> Path:
    override = codex_home / "AGENTS.override.md"
    agents = codex_home / "AGENTS.md"
    if override.exists():
        contents = _regular_file_bytes(override, "AGENTS.override.md")
        if contents.strip():
            return override
    if agents.exists():
        _regular_file_bytes(agents, "AGENTS.md")
    return agents


def _router_segment(original: bytes) -> bytes:
    block = ROUTER_BLOCK.encode("utf-8")
    if not original:
        return block
    separator = b"\n" if original.endswith((b"\n", b"\r")) else b"\n\n"
    return separator + block


def _marker_counts(contents: bytes) -> tuple[int, int]:
    return (
        contents.count(START_MARKER.encode("utf-8")),
        contents.count(END_MARKER.encode("utf-8")),
    )


def _assert_no_unmanaged_marker(codex_home: Path) -> None:
    for path in (codex_home / "AGENTS.md", codex_home / "AGENTS.override.md"):
        if not path.exists():
            continue
        contents = _regular_file_bytes(path, path.name)
        if any(_marker_counts(contents)):
            raise GlobalInstallError(
                f"unmanaged or damaged Optimize router marker exists in {path}"
            )


def _state_bytes(state: dict[str, object]) -> bytes:
    return (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_state(path: Path) -> dict[str, object]:
    raw = _regular_file_bytes(path, "Optimize install state")
    try:
        state = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GlobalInstallError(f"Optimize install state is invalid: {path}") from error
    if not isinstance(state, dict) or set(state) != STATE_FIELDS:
        raise GlobalInstallError("Optimize install state has an unknown or incomplete schema")
    if state.get("schema_version") != 1 or state.get("product") != "Optimize":
        raise GlobalInstallError("Optimize install state identity is invalid")
    string_fields = STATE_FIELDS - {"schema_version", "agents_existed"}
    if any(not isinstance(state.get(field), str) or not state[field] for field in string_fields):
        raise GlobalInstallError("Optimize install state contains an invalid string field")
    if not isinstance(state.get("agents_existed"), bool):
        raise GlobalInstallError("Optimize install state agents_existed must be boolean")
    return state


def _validate_managed(
    home: Path,
    codex_home: Path,
    state: dict[str, object],
) -> tuple[Path, Path, bytes, bytes]:
    target = home / ".agents" / "skills" / SKILL_NAME
    _assert_safe_chain(home, target, "managed skill path")
    _assert_safe_chain(codex_home, codex_home / STATE_NAME, "install state path")
    expected_state_skill = str(target.resolve())
    if state["skill_name"] != SKILL_NAME or state["skill_path"] != expected_state_skill:
        raise GlobalInstallError("Optimize install state does not own the expected skill path")
    allowed_agents = {
        str((codex_home / "AGENTS.md").resolve()),
        str((codex_home / "AGENTS.override.md").resolve()),
    }
    if state["agents_path"] not in allowed_agents:
        raise GlobalInstallError("Optimize install state does not own an expected AGENTS path")
    agents_path = Path(str(state["agents_path"]))
    _assert_safe_chain(codex_home, agents_path, "managed AGENTS path")
    if skill_digest(target) != state["skill_sha256"]:
        raise GlobalInstallError("managed Optimize skill was modified; refusing mutation")
    agents_contents = _regular_file_bytes(agents_path, "managed AGENTS file")
    segment = str(state["router_segment"]).encode("utf-8")
    if agents_contents.count(segment) != 1 or _marker_counts(agents_contents) != (1, 1):
        raise GlobalInstallError("managed Optimize router was modified or damaged; refusing mutation")
    return target, agents_path, agents_contents, segment


def _prepare_skill(source: Path, parent: Path) -> Path:
    skill_digest(source)
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}.", dir=parent))
    temporary.rmdir()
    try:
        shutil.copytree(source, temporary, copy_function=shutil.copy2)
        skill_digest(temporary)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return temporary


def _remove_tree(path: Path) -> None:
    if _is_reparse_point(path):
        raise OSError(f"refusing to remove filesystem reparse point: {path}")
    elif path.exists():
        shutil.rmtree(path)


def _attempt_rollback(
    errors: list[str], path: Path, action: Callable[[], None]
) -> None:
    try:
        action()
    except OSError as error:
        errors.append(f"{path}: {error}")


def _verify_absent(
    path: Path, errors: list[str], *, context: str = "rollback"
) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    except OSError as error:
        errors.append(f"{path}: {context} absence verification failed: {error}")
    else:
        errors.append(f"{path}: {context} verification found an unexpected path")
    return False


def _verify_file(
    path: Path,
    original: bytes | None,
    errors: list[str],
    *,
    context: str = "rollback",
) -> bool:
    if original is None:
        return _verify_absent(path, errors, context=context)
    try:
        restored = _regular_file_bytes(path, f"{context} file {path}")
    except (OSError, GlobalInstallError) as error:
        errors.append(f"{path}: {context} verification failed: {error}")
        return False
    if restored != original:
        errors.append(f"{path}: {context} verification found different contents")
        return False
    return True


def _verify_skill(
    target: Path,
    original_digest: str | None,
    errors: list[str],
    *,
    context: str = "rollback",
) -> bool:
    if original_digest is None:
        return _verify_absent(target, errors, context=context)
    try:
        restored_digest = skill_digest(target)
    except (OSError, GlobalInstallError) as error:
        errors.append(f"{target}: {context} verification failed: {error}")
        return False
    if restored_digest != original_digest:
        errors.append(f"{target}: {context} verification found a different skill tree")
        return False
    return True


def _restore_file(
    path: Path, original: bytes | None, errors: list[str]
) -> None:
    if original is None:
        _attempt_rollback(errors, path, lambda: path.unlink(missing_ok=True))
    else:
        _attempt_rollback(errors, path, lambda: _atomic_write(path, original))


def _finish_tree_backup(
    backup: Path, target_verified: bool, errors: list[str]
) -> None:
    if not _exists_lexically(backup):
        return
    if not target_verified:
        errors.append(f"{backup}: retained because target restoration is unverified")
        return
    _attempt_rollback(errors, backup, lambda: _remove_tree(backup))
    _verify_absent(backup, errors)


def _raise_rollback_result(
    operation: str, primary_error: OSError, rollback_errors: list[str]
) -> None:
    if rollback_errors:
        details = "; ".join(rollback_errors)
        raise GlobalInstallError(
            f"{operation} failed; rollback incomplete and manual recovery is required "
            f"before retrying after {primary_error}: {details}"
        ) from primary_error
    raise GlobalInstallError(
        f"{operation} failed and was rolled back: {primary_error}"
    ) from primary_error


def _require_install_postconditions(
    *,
    target: Path,
    target_digest: str,
    agents_path: Path,
    agents_contents: bytes,
    state_path: Path,
    state_contents: bytes,
    prepared: Path,
    backup: Path,
    backup_digest: str | None,
) -> None:
    errors: list[str] = []
    context = "install postcondition"
    _verify_skill(target, target_digest, errors, context=context)
    _verify_file(agents_path, agents_contents, errors, context=context)
    _verify_file(state_path, state_contents, errors, context=context)
    _verify_absent(prepared, errors, context=context)
    _verify_skill(backup, backup_digest, errors, context=context)
    if errors:
        raise OSError("; ".join(errors))


def _require_uninstall_postconditions(
    *,
    target: Path,
    backup: Path,
    backup_digest: str,
    agents_path: Path,
    agents_contents: bytes | None,
    state_path: Path,
) -> None:
    errors: list[str] = []
    context = "uninstall postcondition"
    _verify_absent(target, errors, context=context)
    _verify_skill(backup, backup_digest, errors, context=context)
    _verify_file(agents_path, agents_contents, errors, context=context)
    _verify_absent(state_path, errors, context=context)
    if errors:
        raise OSError("; ".join(errors))


def _cleanup_committed_backup(
    backup: Path, *, completed_operation: str
) -> None:
    errors: list[str] = []
    _attempt_rollback(errors, backup, lambda: _remove_tree(backup))
    _verify_absent(backup, errors, context="cleanup")
    if errors:
        raise GlobalInstallError(
            f"Optimize global context policy {completed_operation}, but stale "
            f"backup cleanup failed: {'; '.join(errors)}"
        )


def install_global(home: Path, codex_home: Path, source_skill: Path) -> str:
    home = _absolute(home)
    codex_home = _absolute(codex_home)
    source_skill = _absolute(source_skill)
    target = home / ".agents" / "skills" / SKILL_NAME
    state_path = codex_home / STATE_NAME
    if _is_within(codex_home, target):
        raise GlobalInstallError(
            f"CODEX_HOME must not overlap the managed skill tree: {codex_home}"
        )
    if _is_within(codex_home, source_skill):
        raise GlobalInstallError(
            f"CODEX_HOME must not overlap the source skill tree: {codex_home}"
        )
    if source_skill != target and (
        _is_within(source_skill, target) or _is_within(target, source_skill)
    ):
        raise GlobalInstallError(
            "source and destination skill trees must not overlap"
        )
    _assert_safe_chain(home, target, "skill destination path")
    _assert_safe_chain(codex_home, state_path, "install state path")
    _assert_safe_chain(codex_home, codex_home / "AGENTS.md", "AGENTS.md path")
    _assert_safe_chain(
        codex_home,
        codex_home / "AGENTS.override.md",
        "AGENTS.override.md path",
    )
    source_hash = skill_digest(source_skill)
    source_version = _skill_version(source_skill)
    state_existed = state_path.exists()
    backup = target.parent / f".{SKILL_NAME}.optimize-backup"
    if _exists_lexically(backup):
        raise GlobalInstallError(f"stale managed-update backup exists: {backup}")
    stale_uninstall = target.parent / f".{SKILL_NAME}.optimize-uninstall"
    if _exists_lexically(stale_uninstall):
        raise GlobalInstallError(f"stale uninstall backup exists: {stale_uninstall}")

    if state_existed:
        state = _load_state(state_path)
        current_agents_path = _active_agents_path(codex_home)
        if str(current_agents_path.resolve()) != state["agents_path"]:
            raise GlobalInstallError(
                "active AGENTS path changed since installation; refusing to report an inactive policy"
            )
        target, agents_path, agents_original, old_segment = _validate_managed(
            home, codex_home, state
        )
        if source_hash == state["skill_sha256"] and source_version == state["skill_version"]:
            return "Optimize global context policy already installed"
        marker_offset = old_segment.find(START_MARKER.encode("utf-8"))
        if marker_offset < 0:
            raise GlobalInstallError("managed Optimize router segment is invalid")
        segment = old_segment[:marker_offset] + ROUTER_BLOCK.encode("utf-8")
        agents_updated = agents_original.replace(old_segment, segment, 1)
        agents_existed = bool(state["agents_existed"])
    else:
        if target.exists():
            raise GlobalInstallError(
                f"unmanaged skill already exists at {target}; refusing overwrite"
            )
        _assert_no_unmanaged_marker(codex_home)
        agents_path = _active_agents_path(codex_home)
        agents_existed = agents_path.exists()
        agents_original = agents_path.read_bytes() if agents_existed else b""
        segment = _router_segment(agents_original)
        agents_updated = agents_original + segment
        old_segment = b""
        state = {}

    new_state = {
        "schema_version": 1,
        "product": "Optimize",
        "skill_name": SKILL_NAME,
        "skill_version": source_version,
        "skill_path": str(target.resolve()),
        "skill_sha256": source_hash,
        "agents_path": str(agents_path.resolve()),
        "agents_existed": agents_existed,
        "router_segment": segment.decode("utf-8"),
    }
    new_state_bytes = _state_bytes(new_state)
    prepared = _prepare_skill(source_skill, target.parent)
    original_state = state_path.read_bytes() if state_existed else None
    moved_old = False
    installed_new = False
    try:
        if state_existed:
            os.replace(target, backup)
            moved_old = True
        os.replace(prepared, target)
        installed_new = True
        _atomic_write(agents_path, agents_updated)
        _atomic_write(state_path, new_state_bytes)
        _require_install_postconditions(
            target=target,
            target_digest=source_hash,
            agents_path=agents_path,
            agents_contents=agents_updated,
            state_path=state_path,
            state_contents=new_state_bytes,
            prepared=prepared,
            backup=backup,
            backup_digest=str(state["skill_sha256"]) if state_existed else None,
        )
    except OSError as error:
        rollback_errors: list[str] = []
        if installed_new:
            _attempt_rollback(rollback_errors, target, lambda: _remove_tree(target))
        if moved_old and _exists_lexically(backup):
            _attempt_rollback(
                rollback_errors, target, lambda: os.replace(backup, target)
            )
        _restore_file(
            agents_path,
            agents_original if state_existed or agents_existed else None,
            rollback_errors,
        )
        _restore_file(state_path, original_state, rollback_errors)
        _attempt_rollback(rollback_errors, prepared, lambda: _remove_tree(prepared))
        expected_digest = str(state["skill_sha256"]) if state_existed else None
        target_verified = _verify_skill(target, expected_digest, rollback_errors)
        _verify_file(
            agents_path,
            agents_original if state_existed or agents_existed else None,
            rollback_errors,
        )
        _verify_file(state_path, original_state, rollback_errors)
        _verify_absent(prepared, rollback_errors)
        _finish_tree_backup(backup, target_verified, rollback_errors)
        _raise_rollback_result("global install", error, rollback_errors)
    if moved_old:
        _cleanup_committed_backup(backup, completed_operation="updated")
    return "Optimize global context policy updated" if state_existed else "Optimize global context policy installed"


def uninstall_global(home: Path, codex_home: Path) -> str:
    home = _absolute(home)
    codex_home = _absolute(codex_home)
    state_path = codex_home / STATE_NAME
    target = home / ".agents" / "skills" / SKILL_NAME
    if _is_within(codex_home, target):
        raise GlobalInstallError(
            f"CODEX_HOME must not overlap the managed skill tree: {codex_home}"
        )
    _assert_safe_chain(home, target, "skill destination path")
    _assert_safe_chain(codex_home, state_path, "install state path")
    _assert_safe_chain(codex_home, codex_home / "AGENTS.md", "AGENTS.md path")
    _assert_safe_chain(
        codex_home,
        codex_home / "AGENTS.override.md",
        "AGENTS.override.md path",
    )
    if not state_path.exists():
        raise GlobalInstallError("Optimize global context policy is not installed")
    state_bytes = state_path.read_bytes()
    state = _load_state(state_path)
    target, agents_path, agents_original, segment = _validate_managed(
        home, codex_home, state
    )
    agents_updated = agents_original.replace(segment, b"", 1)
    update_backup = target.parent / f".{SKILL_NAME}.optimize-backup"
    if _exists_lexically(update_backup):
        raise GlobalInstallError(f"stale managed-update backup exists: {update_backup}")
    backup = target.parent / f".{SKILL_NAME}.optimize-uninstall"
    if _exists_lexically(backup):
        raise GlobalInstallError(f"stale uninstall backup exists: {backup}")
    agents_existed = bool(state["agents_existed"])
    moved = False
    try:
        os.replace(target, backup)
        moved = True
        if agents_updated or agents_existed:
            _atomic_write(agents_path, agents_updated)
        else:
            agents_path.unlink(missing_ok=True)
        state_path.unlink()
        _require_uninstall_postconditions(
            target=target,
            backup=backup,
            backup_digest=str(state["skill_sha256"]),
            agents_path=agents_path,
            agents_contents=agents_updated if agents_updated or agents_existed else None,
            state_path=state_path,
        )
    except OSError as error:
        rollback_errors: list[str] = []
        if moved and _exists_lexically(backup):
            _attempt_rollback(
                rollback_errors, target, lambda: os.replace(backup, target)
            )
        _restore_file(agents_path, agents_original, rollback_errors)
        _restore_file(state_path, state_bytes, rollback_errors)
        target_verified = _verify_skill(
            target, str(state["skill_sha256"]), rollback_errors
        )
        _verify_file(agents_path, agents_original, rollback_errors)
        _verify_file(state_path, state_bytes, rollback_errors)
        _finish_tree_backup(backup, target_verified, rollback_errors)
        _raise_rollback_result("global uninstall", error, rollback_errors)
    _cleanup_committed_backup(backup, completed_operation="uninstalled")
    return "Optimize global context policy uninstalled"
