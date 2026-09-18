# Review Sync linked worktrees

The 1.1.6 installation branch did not contain Review Sync. This follow-up imports
its deployed Python package and three entry scripts as the compatibility baseline;
they were byte-identical to the old main checkout working files. Existing Review
Sync tests were copied from that checkout. The old checkout and its uncommitted
changes were not edited. This branch is now the source for the deployed follow-up.

Registration remains schema 1, with no migration or registry rewrite. Git's
worktree list --porcelain -z and discover_workspace verify root, git_dir and
common_dir. A path lookalike or a copied .git pointer is not membership proof.
The source Git wrapper permits only that exact worktree-list command.

The registered checkout retains its original identity, paths and checkpoint ref.
Other legal worktrees derive a stable 20-hex SHA-256 identifier from common_dir,
git_dir and root. Each has its own queue, state, source fingerprint, snapshot
store, state lock, sync lock, receipt directory and remote branch in the existing
checkpoint namespace. Explicit legacy worktree registrations take precedence.
Pause, disable, quarantine and upload intent remain per-worktree controls: pause
the actual worktree before handling material forbidden from upload there.

Hooks enqueue through the same membership resolver as the CLI. The ten-minute
scheduler enumerates live Git worktrees once per registration; absent/stale paths
are skipped without deleting retained state or history. Existing semantic
checkpoint and queue dedupe, upload locking and remote verification are reused.
A no-change tick returns NO_CHANGE without a new upload. This check uses the last
verified SHA; REMOTE_VERIFIED means a remote verification actually completed.

Fresh install and managed update use the same background-first AGENTS template.
Updates replace only the bounded Review Sync segment, preserve other text, and
roll back AGENTS, receipt and program on update failure. No routine task-start
status or task-end sync-now is required. Explicit sync, pause/resume, failures,
safety decisions, divergence/quarantine and urgent sync remain model entry points.
Lifecycle hook host trust remains separately observable; unit tests do not prove
that a particular App/CLI host has fired its configured hook.

Compatibility note: reviewed fixture paths use LF in .gitattributes to preserve
their exact approved digests across Windows worktrees. The existing v0.154 shape
fixture was inspected: all content is synthetic placeholders. It is approved
only by exact path and SHA-256; changed bytes remain blocked. Real sessions and
logs remain excluded.
