from __future__ import annotations

import hashlib
from pathlib import Path

from .git import GitRunner
from .model import GitError, PolicyError, WorkspaceIdentity
from .workspace import discover_workspace


def workspace_id(identity: WorkspaceIdentity) -> str:
    material = "\0".join((str(identity.common_dir), str(identity.git_dir), str(identity.root)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def registered_worktrees(project: dict, runner: GitRunner):
    """Use Git's administrative list; missing/pruned checkouts are not errors."""
    common = Path(project["common_dir"]).resolve(strict=True)
    records = runner.source(common, "worktree", "list", "--porcelain", "-z")
    for field in records.split(b"\0"):
        if not field.startswith(b"worktree "):
            continue
        root = Path(field[len(b"worktree "):].decode("utf-8", errors="surrogateescape"))
        if not root.is_dir():
            continue
        try:
            identity = discover_workspace(root, runner)
        except (OSError, GitError, PolicyError):
            continue
        if identity.root == root.resolve() and identity.common_dir == common:
            yield identity


def _exact(project: dict, identity: WorkspaceIdentity) -> bool:
    return all(Path(project[key]).resolve(strict=False) == getattr(identity, key)
               for key in ("root", "git_dir", "common_dir"))


def runtime_project(project: dict, identity: WorkspaceIdentity, data_root: Path) -> dict:
    if _exact(project, identity):
        return project  # Retain schema-1 IDs, paths, remote refs and history.
    identifier = workspace_id(identity)
    target = dict(project["target"])
    target["remote_ref"] = target["remote_ref"].rsplit("/", 1)[0] + "/" + identifier
    return dict(project, workspace_id=identifier, root=str(identity.root),
                git_dir=str(identity.git_dir), common_dir=str(identity.common_dir),
                state_path=str(data_root / "state" / (identifier + ".json")),
                lock_path=str(data_root / "locks" / (identifier + ".lock")),
                store_path=str(data_root / "stores" / (identifier + ".git")),
                receipt_dir=str(data_root / "receipts" / identifier), target=target)


def match_project(projects: list, identity: WorkspaceIdentity,
                  data_root: Path, runner: GitRunner) -> dict | None:
    # Explicit legacy registrations retain precedence over derived identities.
    ordered = sorted(projects, key=lambda p: not _exact(p, identity))
    for project in ordered:
        if Path(project["common_dir"]).resolve(strict=False) != identity.common_dir:
            continue
        for candidate in registered_worktrees(project, runner):
            if candidate == identity:
                return runtime_project(project, identity, data_root)
    return None
