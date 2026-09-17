# Optimize

Optimize is a small context workflow for Codex. Version 1.1 can act as a user-level default while remaining nearly invisible for small work. It creates repository-visible state only when durable recovery has clear value, and it preserves exact source verification, complete tests, independent review, and existing audit history.

The project was built from Teaching AI's M06 evidence. It addresses four recurring costs: one root task spanning several milestones, short `wait_agent` polling, repeated discovery by subagents, and broad recovery reads after interruption or compaction.

## What v1.1 provides

- LIGHTWEIGHT mode for small, single-session work with zero persistent Optimize state;
- STATEFUL mode with generic L1/L2/L3 Markdown state for resumable work units;
- schema-v1 validation plus explicit, deterministic migration to generic schema v2;
- a concise user skill and bounded global AGENTS router for mode selection, recovery, checkpoints, long waits, progressive reads, scoped delegation, and independent review;
- a standard-library Python CLI for repository initialization, validation, migration, reversible global installation, packet rendering, and metrics/evidence validation;
- executable tests and fresh-context experiments for resume, conversation-loss recovery, early-return waiting, reviewer independence, and state drift;
- a controlled rollout plan. Optimize development does not modify Teaching AI or the real user-level Codex installation.

Exact contracts remain in L0: source, tests, specs, plans, Git, approvals, and original reviewer evidence. The compact state files only navigate to that evidence.

## Requirements

- Python 3.11 or later;
- Git available on `PATH` for `validate`;
- no third-party Python packages.

On this Codex desktop installation, the bundled Python executable is:

```powershell
$OptimizePython = 'C:\Users\chest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
```

Use `python` instead when a normal Python installation is already on `PATH`.

## Operating modes

Choose LIGHTWEIGHT for clearly small, narrow, single-agent work that should finish in one session. Use targeted reads and diffs, bounded output, exact verification, and long waits if an agent is later introduced. Do not create `.agent` files or run the state validator merely because the global skill is available.

Choose STATEFUL when work is multi-phase, delegated, independently reviewed, resumable, planned across sessions, or likely to cross compaction. One durable work unit normally uses one root task and records an open `work_type`, opaque `work_id`, optional `owner`, exact Git identity, current operational state, and the next action. Authority files may be absent; source, tests, repository instructions, Git, approved decisions, and original review evidence remain L0.

Start LIGHTWEIGHT when uncertain. Upgrade immediately when stateful conditions emerge: initialize v2 state, reconstruct the current facts from Git and exact L0, fill L1/L2, optionally fill ignored L3, stage only L1/L2, validate, and continue. Optimize does not automatically downgrade stateful work.

## Commands

Install the policy into a temporary or user home only when a rollout stage authorizes it:

```powershell
& $OptimizePython scripts/optimize_context.py install-global
& $OptimizePython scripts/optimize_context.py uninstall-global
```

The installer follows the current documented user locations: `$HOME/.agents/skills/context-state-management` for the skill and the active `$CODEX_HOME/AGENTS.md` or non-empty `AGENTS.override.md` for a short router. It also writes `$CODEX_HOME/.optimize-context-install.json` with the exact managed tree digest and router segment. It never edits `config.toml`.

A repeated unchanged install is a no-op. An unchanged Optimize-managed version can be updated. An unmanaged same-name skill, unknown state schema, altered managed tree, damaged/duplicate marker, altered managed segment, active-AGENTS precedence change, overlapping source/destination/CODEX_HOME ownership, or filesystem reparse point causes a nonzero conflict before unsafe mutation. The installer inspects every existing lexical component from the drive or UNC anchor through each selected home, CODEX_HOME, and source root, then checks every source-tree and destination component; symbolic links, Windows junctions, and mount points are rejected. Uninstall removes only the unchanged owned skill tree, exact recorded router segment, and state file; bytes outside the segment and unrelated skills remain untouched. Old-tree deletion happens after the managed update or uninstall is committed. A cleanup failure reports that the operation completed and leaves a named stale backup instead of claiming rollback. Resolve a reported ownership or cleanup conflict manually rather than deleting the state file and retrying. Use `--home`, `--codex-home`, and `--source-skill` only for controlled alternate locations and tests.

Initialize schema-v2 state for a long-running work unit:

```powershell
& $OptimizePython scripts/optimize_context.py init --repo C:\path\to\repo --work-type feature --work-id parser-cache --owner root
```

`--owner` is optional. Initialization resolves the Git root, uses the exact current branch and HEAD, creates only `.agent/project-map.md` and `.agent/current-work.md`, and adds one `.agent/active.md` rule to `.gitignore`. It leaves `authority_paths` and project-specific navigation empty rather than guessing them. Existing v1 state is directed to the explicit migration command; partial or conflicting state is never overwritten. After filling useful navigation, stage L1/L2 and run `validate`.

Preview or perform an explicit schema-v1 migration:

```powershell
& $OptimizePython scripts/optimize_context.py migrate --repo C:\path\to\repo --dry-run
& $OptimizePython scripts/optimize_context.py migrate --repo C:\path\to\repo
```

Migration requires clean, valid v1 state. It converts the milestone identity to `work_type = "milestone"` plus `work_id`, converts `current_agent` to optional `owner`, preserves branch/commit/status and section bodies, and turns spec/plan/audit into an ordered unique authority list. It never stages or commits. When ignored active state exists, its original bytes are backed up under the repository's Git metadata and the command prints direct PowerShell rollback steps. Stage the resulting L1/L2 files and run `validate` before continuing.

Validate state in an adopted repository:

```powershell
& $OptimizePython scripts/optimize_context.py validate --repo D:\TeachingAIProject
```

The default map and optional active paths are `.agent/project-map.md` and `.agent/active.md`. State discovery prefers schema-v2 `.agent/current-work.md` and falls back to schema-v1 `.agent/current-milestone.md`. Custom paths are accepted with `--map`, `--state`, and `--active`. L1/L2 must already be tracked or staged. When L3 exists, it must be ignored and absent from every index stage and HEAD. Generic `authority_paths` and legacy spec/plan/audit files must be present in both HEAD and the stage-0 Git index with regular-file mode `100644` or `100755`; filesystem reparse points and Git symlinks are rejected. Generic authority also requires identical HEAD/index blobs. Baseline/HEAD must be exact existing 40-hex commits.

Render a closed subagent packet:

```powershell
& $OptimizePython scripts/optimize_context.py render-packet templates/subagent-context-packet.json -o packet.md
```

Replace the template's all-zero/all-one example Git IDs with the task's real full SHAs before dispatch.

Measure a Codex JSONL rollout without copying its conversation content:

```powershell
& $OptimizePython scripts/optimize_context.py analyze-rollout C:\path\to\rollout.jsonl --json
```

Validate a complete generic work-unit comparison record after copying and filling the schema-v2 template:

```powershell
& $OptimizePython scripts/optimize_context.py validate-metrics C:\path\to\work-metrics.json
```

Schema v2 uses `work_type`, `work_id`, and `work_duration_seconds`. The existing `templates/milestone-metrics.json` schema v1 and its `milestone`/`milestone_duration_seconds` names remain valid. Every metrics evidence reference is a repository-relative `path` plus SHA-256. Replace both values when copying either example; the validator requires a regular `100644`/`100755` file in both HEAD and the stage-0 index, requires both entries to identify the same blob, rejects symlinks, and hashes the canonical Git blob rather than checkout-filtered bytes. It also requires baseline/head to be exact existing commits with valid ancestry and checks counter partitions, elapsed duration, token totals, test gates, and independent-review evidence.

Validate behavioral evidence metadata and recorded file hashes:

```powershell
& $OptimizePython scripts/optimize_context.py validate-evidence tests/evidence/manifest.json
```

Schema-v1 evidence manifests retain the fixed v1 sample set. Schema v2 declares a unique, non-empty `required_sample_ids` list so another work unit can define its complete experiment matrix without adopting Teaching AI-era identifiers. Every required ID must occur exactly once and have disposition `PASS`; extra `INVALID` attempts remain valid audit evidence but cannot satisfy the required matrix. Prompt-source hashes may resolve to the same regular file path in an ancestor of current HEAD, which keeps immutable v1 experiment manifests valid after a Skill upgrade. Response artifacts still must be unchanged regular blobs in current HEAD and the stage-0 index; historical fallback never applies to claimed outcomes.

Run the automated suite:

```powershell
& $OptimizePython -m unittest discover -s tests -p 'test_*.py' -v
```

## GitHub Review Checkpoint Sync v1

Review Sync is a separate, deterministic review-checkpoint facility. It copies the allowed bytes of a registered worktree's current on-disk working copy into an independent bare Git store, generates `.review-sync/HANDOFF.md` plus a machine-readable manifest inside that snapshot, and can append one exact snapshot commit to one exact `refs/heads/codex-sync/<device>/<worktree>` ref. It does not modify the source branch, HEAD, index, staged/unstaged split, files, remotes, or formal history. A file that has both staged and unstaged changes contributes its current working-copy bytes; the manifest records the Git status but does not claim to preserve the index-only version separately.

The implementation never calls an LLM, Codex, an OpenAI API, a subagent, or a chat transcript. Stop, Interrupt, and SessionEnd hooks only enqueue a short local request. A limited current-user Windows scheduled task performs the independent ten-minute fallback. Snapshot Git uses a sanitized environment and raw plumbing; source Git is read-only with optional locks disabled. Candidate bytes, generated metadata, newly reachable history, workflow contents, target privacy/write permission, visible remote Actions, effective URL rewrites, and the final remote SHA are all gates. Symlinks/reparse points, submodules, Git LFS, large/unsupported files, credential-like content, raw logs, and unapproved data formats are blocked rather than silently omitted.

No GitHub destination is bundled or inferred. Until an exact private `owner/repository` and matching push URL are explicitly registered and verified, commands report `GITHUB=NEEDS_SETUP`; no source remote is added or changed. `CHATGPT_ACCESS=NOT_VERIFIED` remains separate until the web ChatGPT session actually reads files from a specified verified snapshot SHA.

Set explicit runtime paths before using the source checkout:

```powershell
$ReviewSyncPython = 'C:\path\to\python.exe'
$ReviewSyncGit = 'C:\path\to\git.exe'
$ReviewSyncGh = 'C:\path\to\gh.exe'
$ReviewSyncHome = "$env:USERPROFILE\.codex"
$ReviewSyncData = "$env:LOCALAPPDATA\CodexReviewSync"
$ReviewSyncScript = "$PWD\scripts\review_sync.py"
```

Install the user-level program, bounded AGENTS protocol segment, merged lifecycle hooks, and limited scheduled task:

```powershell
& $ReviewSyncPython -B $ReviewSyncScript install --source-root $PWD --python $ReviewSyncPython --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
```

Installation does not trust hooks on the user's behalf. Review the installed definitions with `/hooks` in Codex App and separately in Codex CLI, then approve them in each host if the UI requests trust. A manual JSON invocation proves only the hook program contract; it is not evidence that either host fired the hook. The scheduled-task probe records the absolute Python, Git, GitHub CLI, CODEX_HOME, Windows user, platform, timestamp, and tick result. `INSTALLATION.scheduler=VERIFIED_RUN` is emitted only when an installed task exists and that matching probe is present.

Query or request an immediate bounded attempt without registering a destination:

```powershell
& $ReviewSyncPython -B $ReviewSyncScript status --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
& $ReviewSyncPython -B $ReviewSyncScript sync-now --timeout-seconds 60 --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
```

An unregistered worktree returns `NEEDS_SETUP` without invoking `gh`, attempting login, scanning other repositories, or writing a registry. For a registered worktree, `REMOTE_VERIFIED` means the exact remote ref, handoff, manifest, and one source file were read back at the expected SHA. `PENDING`, `UNKNOWN`, a policy state, or a timeout is not success; upload intent and queued work remain recoverable.

Register only an explicitly authorized private GitHub target. This verifies the exact identity, effective push URL after `insteadOf`/`pushInsteadOf`, private visibility, write permission, and remote Actions before writing the user registry. If the candidate contains `.github/workflows`, first inspect those exact working-copy bytes and pass their reported SHA-256 as `--reviewed-workflow-digest`. If the remote already has Actions workflows, add `--acknowledge-remote-automation` only after reviewing their trigger isolation:

```powershell
& $ReviewSyncPython -B $ReviewSyncScript register --repo owner/private-repository --push-url https://github.com/owner/private-repository.git --reviewed-workflow-digest <exact-digest-if-any> --acknowledge-remote-automation --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
```

Do not add the optional acknowledgement merely because the branch starts with `codex-sync/` or because a commit message could contain a skip-CI hint. Review Sync never edits source workflows, disables CI, creates a repository, changes visibility, pushes another ref, force-pushes, pulls, rebases, merges, tags, releases, or deploys.

Programmatic stop controls are persistent and scoped to the exact registered worktree:

```powershell
& $ReviewSyncPython -B $ReviewSyncScript pause --reason 'user prohibited upload' --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
& $ReviewSyncPython -B $ReviewSyncScript disable --reason 'disable unattended sync' --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
& $ReviewSyncPython -B $ReviewSyncScript resume --choice reauthorize --authorized-digest <pending-semantic-key> --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
& $ReviewSyncPython -B $ReviewSyncScript resume --choice recapture --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
& $ReviewSyncPython -B $ReviewSyncScript resume --choice abandon --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
```

`pause`/`disable` confirmation is the background hard boundary; natural-language instructions alone are not. A confirmed pause prevents a new upload start and requests cancellation when an intent already started, but cannot promise to retract bytes a remote has already accepted. Recovery accepts only the persisted previous verified SHA or the persisted expected SHA; any other remote state pauses without merge, rebase, or force push. `reauthorize` requires the matching queued semantic key and an unchanged current capture. `recapture` explicitly resets the unpublished chain to the last verified safe parent so a newly scanned current candidate can be built. `abandon` drops transport backlog but deliberately keeps the worktree paused, so the same paused contents cannot be silently recaptured.

Preview installation state or uninstall the managed program/protocol/hooks/task. Runtime registry, local stores, queues, quarantine records, and receipts are retained for recovery:

```powershell
& $ReviewSyncPython -B $ReviewSyncScript uninstall --dry-run --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
& $ReviewSyncPython -B $ReviewSyncScript uninstall --cwd $PWD --data-root $ReviewSyncData --codex-home $ReviewSyncHome --git $ReviewSyncGit --gh $ReviewSyncGh --json
```

Uninstall verifies the managed program digest, removes exactly the recorded AGENTS marker block and hook groups, preserves unrelated configuration added before or after installation, deletes the scheduled task, and leaves data intact. If an owned component is missing, ambiguous, or modified, it stops for manual review instead of deleting broadly.

## Stateful repository adoption

1. Run `init` with the repository, work type, and stable work ID.
2. Fill L1 with real authority paths, code navigation, interfaces, and verification commands; do not copy contracts or invent missing document categories.
3. Reconstruct L2 from current Git, exact L0 evidence, and current review evidence.
4. Optionally create L3 from `templates/generic/active.md`; keep it ignored and never stage it.
5. Stage `.agent/project-map.md` and `.agent/current-work.md` explicitly, preserving any pre-existing index contents.
6. Run `validate` before delegation, recovery, handoff, or reliance on state.
7. Keep the same work identity across compaction and handoffs; use a fresh root task for a later durable work unit when that boundary adds value.

Existing schema-v1 repositories may continue validating in place. Use `migrate --dry-run` and then explicit `migrate` when moving them to v2. The detailed architecture is in [docs/architecture.md](docs/architecture.md), the measured M06 baseline is in [docs/forensic-baseline.md](docs/forensic-baseline.md), and the original Teaching AI procedure is in [docs/rollout-plan.md](docs/rollout-plan.md).

## Safety properties

Validation is read-only. It rejects missing structure, invalid values or symbolic revisions, oversized/untracked/out-of-repository state, missing L1 references, branch mismatch, stale verified-work HEAD, invalid baseline ancestry, later production commits, and uncommitted production paths. Metrics and behavioral evidence additionally require immutable matching HEAD/index blobs, so unstaged or staged local bytes cannot be attested as committed evidence and line-ending checkout filters do not change the digest. A state-only checkpoint after the declared work SHA is valid because a file cannot contain the SHA of the commit that contains it.

Initialization and migration preflight collisions and use bounded writes without staging or committing. On a write failure they attempt every rollback action and claim successful rollback only after verifying each original byte sequence or required absence, including removal of the temporary migration backup; otherwise the diagnostic names the failed recovery paths, retains the migration's original L3 backup when restoration is unverified, and requires manual recovery before retrying. Global install and uninstall verify every forward postcondition before reporting success: managed skill digest or absence, exact AGENTS and state bytes or absence, prepared tree, and transaction backup. A failed forward check enters the same continuing, verified rollback path as an explicit write error. Cleanup after a committed update or uninstall also verifies backup absence and reports a named stale backup if removal failed or silently had no effect. Running the installed CLI suppresses Python bytecode output so its managed tree remains byte-for-byte stable; any added or changed file still blocks update and uninstall.

Rendered packets are also bounded: 16 KiB total, 1 KiB per list item, 50 items per list, single-line values, and full Git SHAs. These limits keep the packet navigational; exact L0 contents remain in their own files.

The workflow never uses state as proof of contract behavior. Reviewers receive exact authority and changed-file locations, then inspect those L0 files independently. Tests and review cycles are not reduced to save tokens.
