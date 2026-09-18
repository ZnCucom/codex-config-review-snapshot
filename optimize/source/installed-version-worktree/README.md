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

LIGHTWEIGHT defaults to targeted reads -> work -> proportionate verification -> finish. It does not create .agent state, run its validator, checkpoint, read observability instructions, archive, or create repository-external task summaries.

Use STATEFUL when durable recovery is genuinely valuable for long-running/cross-session work, substantial multi-phase milestones, compaction risk, durable handoff, delegated work requiring recovery, or interruption/resumption. A plan, one review or temporary agent does not independently require STATEFUL. Preserve L0 authority, L1/L2/optional L3, Git identity, drift validation, exact recovery, scoped packets and material checkpoints.

Start LIGHTWEIGHT when uncertain. Upgrade only for a real durable-recovery need, reconstruct facts from Git/L0, initialize and validate existing state formats, and continue. Do not silently discard established STATEFUL evidence.

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

When explicitly requested or justified by a formal investigation, important incident forensics or an explicit need to preserve review evidence, the existing commands can save a repository-external summary, archive an unchanged reviewer return, or export a report:

```powershell
& $OptimizePython scripts/optimize_context.py save-task-summary --repo C:\path\to\repo --input C:\path\to\task-record.json
& $OptimizePython scripts/optimize_context.py archive-review --repo C:\path\to\repo --archive-id local-stable-task-id --metadata C:\path\to\review.json --body C:\path\to\review.txt
& $OptimizePython scripts/optimize_context.py export-observability --repo C:\path\to\repo --latest 10 --output C:\path\to\report.md
```

The default store is `%LOCALAPPDATA%\Optimize\observability`; `--observability-root` selects a controlled alternate root. Records are keyed by repository/worktree identity and stable task archive ID, live outside the business repository, never create `.agent`, and never become default recovery context. Exact repeats are no-ops; inconsistent content at an existing identity is a conflict rather than an overwrite. Only an explicitly named JSONL is inspected. Cumulative token snapshots are not summed, cached/new input and session/task scope remain distinct, requested timeouts are not reported as actual waits, and malformed tails are marked partial/unsupported. Default export omits raw sessions, source, environment variables, absolute repository/session paths, and reviewer bodies; `--include-review-bodies` is explicit and still requires manual secret review. The closed JSON inputs and boundary rules are documented in [the installed Skill reference](skills/context-state-management/references/observability.md).

There is no common automatic observability closeout. Ordinary completion, blockers and handoffs do not cause a record, and LIGHTWEIGHT does not load the observability reference by default. STATEFUL still checkpoints material operational state for recovery. Only an explicitly justified recording reads the reference and makes one attempt; read-only restrictions prevail, failed recording is not automatically retried, and no status footer is required for ordinary tasks. Recording/export does not recursively record itself.

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

Thin observability is Skill-driven, not an operating-system or Codex event hook. Normal completion, a clear blocker, a controlled handoff, and receipt of a formal reviewer result are the recording boundaries. Crashes, forced termination, quota loss, unavailable logs, forbidden writes, or a Skill invocation that does not occur can leave a missing record. An ordinary logging failure warns once and does not trap completed business work in retries; missing mandatory review evidence remains a real correctness gap.

Rendered packets are also bounded: 16 KiB total, 1 KiB per list item, 50 items per list, single-line values, and full Git SHAs. These limits keep the packet navigational; exact L0 contents remain in their own files.

The workflow never uses state as proof of contract behavior. Reviewers receive exact authority and changed-file locations, then inspect those L0 files independently. Tests and review cycles are not reduced to save tokens.
