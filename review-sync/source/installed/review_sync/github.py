from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit

from .git import GitRunner, no_window_creation_flags
from .model import GitError, PolicyError, RemoteTarget


SYNC_REF = re.compile(r"^refs/heads/codex-sync/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


class TargetVerifier(Protocol):
    def verify(self, target: RemoteTarget, git_dir: Path | None = None) -> str: ...


def _github_identity(url: str) -> str:
    if re.match(r"^[^/@:]+@github\.com:", url, flags=re.IGNORECASE):
        path = url.split(":", 1)[1]
    else:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "ssh"} or (parsed.hostname or "").lower() != "github.com":
            raise PolicyError("push URL is not an explicit github.com repository")
        if parsed.password is not None or (parsed.username not in {None, "git"}):
            raise PolicyError("push URL must not embed credentials")
        path = unquote(parsed.path).lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        raise PolicyError("push URL does not name exactly one GitHub owner/repository")
    return f"{parts[0]}/{parts[1]}".lower()


def _validate_ref(ref: str) -> None:
    if not SYNC_REF.fullmatch(ref):
        raise PolicyError("remote ref is outside the codex-sync device/worktree namespace")


class GitHubTargetVerifier:
    def __init__(
        self,
        git: GitRunner,
        *,
        gh_executable: str | Path,
        gh_executor=subprocess.run,
        timeout: float = 20.0,
    ) -> None:
        self.git = git
        self.gh_executable = str(gh_executable)
        self.gh_executor = gh_executor
        self.timeout = timeout

    def verify(self, target: RemoteTarget, git_dir: Path | None = None) -> str:
        if target.kind != "github":
            raise PolicyError("production target kind must be github")
        _validate_ref(target.remote_ref)
        configured_identity = _github_identity(target.push_url)
        if configured_identity != target.identity.lower():
            raise PolicyError("configured push URL identity does not match registration")
        effective = self.git.effective_push_url(target.push_url, git_dir)
        if _github_identity(effective) != target.identity.lower():
            raise PolicyError("effective push URL was rewritten to an unauthorized target")
        environment = dict(os.environ)
        environment.update({"GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"})
        try:
            result = self.gh_executor(
                [
                    self.gh_executable,
                    "repo",
                    "view",
                    target.identity,
                    "--json",
                    "nameWithOwner,visibility,viewerPermission",
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                check=False,
                creationflags=no_window_creation_flags(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PolicyError("GitHub target verification could not run non-interactively") from error
        if result.returncode != 0:
            raise PolicyError("GitHub authentication or repository permission could not be verified")
        try:
            payload = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PolicyError("GitHub target verification returned invalid JSON") from error
        if str(payload.get("nameWithOwner", "")).lower() != target.identity.lower():
            raise PolicyError("GitHub response identity does not match registration")
        if payload.get("visibility") != "PRIVATE":
            raise PolicyError("GitHub target is not private")
        if payload.get("viewerPermission") not in {"WRITE", "MAINTAIN", "ADMIN"}:
            raise PolicyError("GitHub target does not grant push permission")
        try:
            workflows_result = self.gh_executor(
                [
                    self.gh_executable,
                    "api",
                    f"repos/{target.identity}/actions/workflows?per_page=100",
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                check=False,
                creationflags=no_window_creation_flags(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PolicyError("GitHub automation inspection could not run non-interactively") from error
        if workflows_result.returncode != 0:
            raise PolicyError("GitHub automation could not be inspected with current permission")
        try:
            workflows_payload = json.loads(workflows_result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PolicyError("GitHub automation inspection returned invalid JSON") from error
        if int(workflows_payload.get("total_count", 0)) > 0 and not target.remote_automation_reviewed:
            raise PolicyError("remote GitHub automation exists and is not explicitly reviewed")
        return target.push_url


class LocalTargetVerifier:
    """Explicit test-only verifier; the production CLI never serializes this target kind."""

    def verify(self, target: RemoteTarget, git_dir: Path | None = None) -> str:
        if target.kind != "local-test":
            raise PolicyError("local verifier accepts only a test target")
        _validate_ref(target.remote_ref)
        path = Path(target.push_url).resolve(strict=True)
        return str(path)


def read_remote_ref(
    runner: GitRunner,
    store: Path,
    push_url: str,
    remote_ref: str,
) -> str | None:
    _validate_ref(remote_ref)
    output = runner.network(store, "ls-remote", "--refs", push_url, remote_ref)
    found: str | None = None
    for line in output.decode("utf-8", errors="strict").splitlines():
        object_id, separator, ref = line.partition("\t")
        if separator and ref == remote_ref:
            if found is not None and found != object_id:
                raise PolicyError("remote returned conflicting values for the exact ref")
            found = object_id
    return found


def push_exact_ref(
    runner: GitRunner,
    store: Path,
    push_url: str,
    remote_ref: str,
    snapshot_sha: str,
    *,
    timeout: float = 30.0,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    _validate_ref(remote_ref)
    if not re.fullmatch(r"[0-9a-f]{40}", snapshot_sha):
        raise PolicyError("snapshot SHA is not an exact full commit ID")
    runner.network(
        store,
        "push",
        "--porcelain",
        push_url,
        f"{snapshot_sha}:{remote_ref}",
        timeout=timeout,
        cancel_check=cancel_check,
    )


def verify_remote_checkpoint(
    runner: GitRunner,
    store: Path,
    push_url: str,
    remote_ref: str,
    expected_sha: str,
) -> dict[str, str]:
    actual = read_remote_ref(runner, store, push_url, remote_ref)
    if actual != expected_sha:
        raise PolicyError("remote ref does not equal the expected checkpoint SHA")
    runner.network(store, "fetch", "--no-tags", push_url, remote_ref)
    handoff = runner.snapshot(store, "show", f"{expected_sha}:.review-sync/HANDOFF.md")
    manifest_bytes = runner.snapshot(store, "show", f"{expected_sha}:.review-sync/manifest.json")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        source_paths = [item["path"] for item in manifest["files"]]
        source_path = source_paths[0]
    except (KeyError, IndexError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolicyError("remote checkpoint manifest is incomplete") from error
    source = runner.snapshot(store, "show", f"{expected_sha}:{source_path}")
    import hashlib

    return {
        "remote_verified_sha": expected_sha,
        "handoff_sha256": hashlib.sha256(handoff).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "source_path": source_path,
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }
