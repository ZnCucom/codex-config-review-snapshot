from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .git import GitRunner
from .model import GitError, PolicyError, SyncRequest
from .queue import WorkspaceStateStore, enqueue_request
from .workspace import discover_workspace


EVENTS = {"Stop", "Interrupt", "SessionEnd"}


def _stop_output() -> bytes:
    return b'{"continue":true,"suppressOutput":true}\n'


def handle_hook(
    event_name: str,
    input_bytes: bytes,
    data_root: Path,
    runner: GitRunner,
) -> bytes:
    output = _stop_output() if event_name == "Stop" else b""
    if event_name not in EVENTS:
        raise PolicyError("unsupported review-sync hook event")
    try:
        payload = json.loads(input_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return output
    if not isinstance(payload, dict) or payload.get("hook_event_name") not in {None, event_name}:
        return output
    cwd_value = payload.get("cwd")
    if not isinstance(cwd_value, str) or not cwd_value:
        return output
    config_path = data_root / "config.json"
    if not config_path.exists():
        return output
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        projects = config["projects"]
        identity = discover_workspace(Path(cwd_value), runner)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, GitError, PolicyError):
        return output
    matched: dict[str, object] | None = None
    for project in projects:
        try:
            same = (
                Path(project["root"]).resolve(strict=True) == identity.root
                and Path(project["git_dir"]).resolve(strict=True) == identity.git_dir
                and Path(project["common_dir"]).resolve(strict=True) == identity.common_dir
            )
        except (OSError, KeyError, TypeError):
            continue
        if same:
            matched = project
            break
    if matched is None:
        return output
    try:
        store = WorkspaceStateStore(
            Path(str(matched["state_path"])),
            Path(str(matched["lock_path"])),
            str(matched["workspace_id"]),
        )
        current = store.load()
        source_hint = hashlib.sha256(
            (str(identity.common_dir) + "\0" + str(identity.git_dir) + "\0" + identity.head).encode("utf-8")
        ).hexdigest()
        enqueue_request(
            store,
            SyncRequest(
                source_fingerprint=source_hint,
                task_state="unknown",
                objective=current.objective,
                verification_result="stale" if current.source_fingerprint not in {None, source_hint} else current.verification_result,
                verification_source_digest=None,
                reason="user_interrupted" if event_name == "Interrupt" else "unknown",
                lifecycle_event=event_name,
                event_id=str(payload.get("turn_id") or payload.get("session_id") or "unknown"),
                created_at="hook_event",
            ),
        )
    except (OSError, PolicyError):
        return output
    return output
