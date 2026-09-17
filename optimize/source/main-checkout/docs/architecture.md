# Optimize v1.1 Architecture

## System boundary

Optimize changes how Codex locates, persists, restores, and transfers development context. Version 1.1 adds a generic work-unit schema, lightweight/stateful selection, explicit v1 migration, and conflict-sensitive user installation. It does not alter Codex compaction, application behavior, public contracts, or the amount of testing and independent review required for correctness.

The design uses Git and small repository files because they are explicit, diffable, reviewable, and deterministic. Conversation history can help, but the workflow never needs it as the only project memory.

```text
exact authority                                                    transient

L0 instructions/source/tests/schemas/decisions/Git/review evidence
       ^ exact verification
       |
L1 project map --------> paths, anchors, commands
       |
L2 current work -------> work identity, current position, next action
       |
L3 active -------------> immediate owner and continuation
       |
conversation ----------> lowest-authority convenience context
```

## Authority and state

| Layer | Stored content | Persistence | Limit | Can prove an exact contract? |
| --- | --- | --- | ---: | --- |
| L0 | Repository instructions, source, tests, schemas, real authority documents, approved decisions, Git, original review evidence | Existing tracked artifacts | Existing project rules | Yes |
| L1 | Paths, anchors, verification commands, contract index | Tracked and stable | 8 KiB | No |
| L2 | Work type/ID, status, Git identity, current results/findings, next action | Tracked and rewritten at checkpoints | 12 KiB | No |
| L3 | One immediate action, owner, prohibitions, continuation | Ignored and disposable | 4 KiB | No |
| Existing history | Optional chronology and evidence already maintained by the project | Existing ledgers/reports | Project policy | Only when it links or contains original evidence |

Higher authority wins on conflict. In particular, a state sentence cannot override source, tests, an approved plan, or Git. This prevents a compact operational file from becoming a lossy substitute for exact project semantics.

## State transition model

L2 uses a small explicit status vocabulary:

```text
PLANNING -> IMPLEMENTING -> REVIEWING -> FIXING -> VERIFYING -> COMPLETE
                  ^             |          |
                  +-------------+----------+

Any active status may enter BLOCKED with exact evidence and a bounded next action.
```

The arrows describe common flow, not a shortcut around review. A reviewer finding can return work to `FIXING`; verification can expose a failure and return it to implementation. L2 records current operational facts rather than chronology. Optimize does not require a project to create an audit ledger.

Checkpoints occur after valuable state changes: a completed implementation/commit, review verdict, finding discovery or closure, approved clarification, verification result, blocker, before a long agent wait, milestone handoff, or task termination. Routine turns do not rewrite state.

## Verified-work HEAD semantics

L2's `head` is the most recent verified **work** commit. It cannot literally name the commit containing its own new value because the commit SHA depends on the file content.

The validator therefore accepts either:

1. declared `head` equals Git `HEAD`; or
2. declared `head` is an ancestor of Git `HEAD`, and every committed path between them is configured context state, such as `.agent/project-map.md` plus `.agent/current-work.md` or legacy `.agent/current-milestone.md`.

Any later production path is drift. The validator also requires exact existing 40-hex baseline/HEAD commits and valid ancestry, a known status, nonempty identity, contained paths, and tracked or staged L1/L2. An existing L3 must be ignored and absent from every index stage and HEAD. Schema-v2 authority may contain zero or more paths. Every listed authority file must exist as the same regular `100644`/`100755` blob in HEAD and the stage-0 index. Schema v1 retains its spec/plan/audit checks. Both schemas preserve lexical repository paths for Git checks, reject filesystem reparse points and Git symlinks, and inspect tracked, staged, and untracked worktree paths. Only configured L1/L2 and valid optional L3 state may differ without forcing reconciliation.

## Mode selection

LIGHTWEIGHT mode applies targeted reads, progressive diffs and documents, bounded output, exact verification, and the agent wait policy without creating repository state. It is the default for clearly small, single-session, single-agent work. When uncertain, Codex starts lightweight.

STATEFUL mode is selected when work is resumable, multi-phase, delegated, independently reviewed, planned across sessions, or likely to cross compaction. It activates L1/L2/optional L3, Git validation, checkpoints, scoped packets, and deterministic recovery. A lightweight task upgrades as soon as any of those conditions emerges: initialize generic state, reconstruct current facts from Git and L0, fill and stage L1/L2, optionally fill ignored L3, validate, then continue. There is no automatic downgrade.

The choice is intentionally a short policy rather than a classifier service, daemon, database, or repository scanner. Lightweight mode has zero persistent Optimize state cost.

## Recovery protocol

After a new root task, compaction, interruption, or uncertain state:

1. read the repository router;
2. read L1, L2, and optional L3;
3. run the validator;
4. reconcile any branch, HEAD, structure, size, or worktree diagnostic against Git and L0;
5. open the exact L0 authority referenced by `Next action` and `Authority`;
6. continue with the smallest sufficient source/diff/test view.

Broad repository scans, entire-plan/spec reads, full ledgers, and whole-branch diffs are escalation tools. They are used when targeted evidence cannot answer the question.

Schema-v2 recovery reads `.agent/current-work.md`. Schema-v1 recovery continues to read `.agent/current-milestone.md`; migration is never implicit.

## Work-unit lifecycle

One durable stateful work unit normally uses one root task. `work_type` and `work_id` are open, non-empty project identifiers, so milestones, features, issues, bug fixes, experiments, migrations, research, and releases use the same mechanism without a fixed taxonomy. `owner` is optional and becomes useful for delegation. Before closing, the work unit persists the final verified work SHA, review verdict, open/closed finding disposition, verification evidence, prohibitions, and any next-unit prerequisites. A later durable work unit may start in a fresh root task, validate repository state, and load its exact L0 authority. Lightweight work creates no artificial task boundary.

This boundary removes prior work history from the default input while retaining every durable fact needed to resume. A work unit may use as many implementer, task-review, fix-review, and final-review cycles as correctness requires.

## Generic authority and initialization

Schema-v2 L1 contains `current_state_path`, `active_state_path`, and an ordered `authority_paths` list. The list may be empty because repository instructions, source, tests, schemas, Git, approved decisions, and original review evidence are L0 without additional documents. Optimize records where authority exists; it does not require a spec, plan, audit ledger, or architecture taxonomy.

`init --repo <repo> --work-type <type> --work-id <id> [--owner <owner>]` resolves the Git root, exact branch, and HEAD; creates generic L1/L2; and adds exactly one `.agent/active.md` ignore rule. It refuses non-Git repositories, detached HEAD, v1 state, partial or conflicting state, and different existing identity. It never stages, commits, edits repository AGENTS, or guesses documents and code areas. Identical valid v2 initialization is a no-op.

`migrate --repo <repo>` requires clean, valid v1 state and converts milestone identity, current owner, state pointers, headings, and ordered unique spec/plan/audit authority to v2 without editing business code, contracts, or history. It supports `--dry-run`, never stages or commits, and backs up an existing ignored L3 under Git metadata while printing exact rollback commands. Initialization and migration attempt every rollback action after a write failure. They report successful rollback only after verifying each original byte sequence or required absence, including temporary-backup removal; an incomplete rollback instead names each failed recovery path, retains the original L3 backup when repository restoration is unverified, and requires manual repair before retrying. Re-running against an already staged valid migration reports that it already exists.

## Agent waiting

After dispatch, the parent records the agent and continuation and makes one bounded long wait. The normal range is 5–10 minutes for a narrow investigation and 10–30 minutes for implementation or a full review. The current runtime returns early when the agent produces a relevant event or completes.

After a long wait expires, one status inspection decides whether to wait again, steer, recover, or handle failure. Repeated sub-five-minute waits and `list_agents` calls used only for liveness are protocol violations.

The wait policy changes no `config.toml` setting. The current documented configuration exposes `features.multi_agent`, while `default_wait_timeout` and `max_wait_timeout` are not documented supported keys. The wait range therefore lives in the skill and each explicit tool call.

## User-level installation

The current official discovery locations are `$HOME/.agents/skills` for user skills and `$CODEX_HOME/AGENTS.md` for global instructions, with `$CODEX_HOME` defaulting to `$HOME/.codex`. A non-empty `AGENTS.override.md` has precedence and therefore receives the router instead.

`install-global` writes three owned objects: the self-contained `context-state-management` skill, one 438-byte marker-bounded router segment, and `.optimize-context-install.json`. The state records the exact target, skill version, full tree digest, active AGENTS path, original-file existence, and exact router segment. The installer does not edit `config.toml`, other skills, or repository files.

Fresh installation, unchanged no-op, managed update, and uninstall use preflighted ownership checks and atomic file/tree replacement with rollback before commit. Each selected home, CODEX_HOME, and source root is checked lexically from its drive or UNC anchor before source traversal and destination checks continue below that root. Any existing filesystem reparse point, including a symbolic link, Windows junction, or mount point, stops the lifecycle. CODEX_HOME may not sit inside the managed or source skill tree, and distinct source/destination skill trees may not contain one another. An unmanaged same-name skill, unknown or corrupt install state, changed managed tree, duplicate/damaged marker, changed managed segment, or changed active-AGENTS precedence stops before unsafe mutation. Before reporting a forward operation as committed, the lifecycle verifies the new target digest or absence, exact AGENTS/state bytes or absence, prepared-tree disposition, and the recovery backup's exact digest. A failed forward postcondition enters the same rollback path as an explicit mutation error. That path attempts every recovery action and verifies the prior state before claiming rollback; incomplete recovery names every detected path, requires manual repair, and retains the transaction backup when target restoration is unverified. Old-tree cleanup follows a verified update or uninstall and verifies backup absence; an explicit or silent cleanup failure leaves the committed state intact, names the stale backup, and never claims rollback from a consumed copy. The installed CLI suppresses generated Python bytecode, so normal use does not invalidate its exact tree digest. Uninstall removes only unchanged owned bytes and preserves later AGENTS edits outside the marker segment.

The skill package includes its Python CLI and generic templates, so another repository can initialize, validate, migrate, and uninstall without a checkout of Optimize. Development tests use temporary homes; v1.1 does not perform the real global installation.

## Progressive evidence loading

| Evidence | Default first read | Escalation |
| --- | --- | --- |
| Git changes | `--stat`, `--name-only` | Per-file patches, then required whole diff |
| Plan/spec | Heading/milestone locator | Exact section, then named cross-section |
| Source | L1/L2 paths and symbols | Nearby search, then repository search |
| Tests | Command, exit, count summary | Relevant failure/stack, then debug logs |
| Large output | Artifact path and counts | Selected ranges needed for a decision |

This policy changes loading order, not evidence requirements. Failures and contract-sensitive evidence are expanded until the decision is supportable.

## Scoped packets and review

The renderer accepts a closed JSON shape and produces TASK, ROLE, AUTHORITY, BASELINE, HEAD, CHANGED FILES, CURRENT STATE, SCOPE, KNOWN FINDINGS, DO NOT, and RETURN sections. It enforces a 16 KiB rendered limit, 1 KiB single-line list entries, 50 entries per list, bounded scalar fields, and exact 40-hex Git IDs. The closed shape and budgets reject source-sized payloads even when placed in an allowed field.

Packets eliminate repeated discovery. They do not carry conclusions as proof. A reviewer opens the named authority, patches, and tests independently and returns a verdict with severity, file, evidence, contract impact, and action.

Task review covers the task diff and contract. Fix review covers the original finding, regression test, and incremental diff. Final review begins with a branch file/risk map and expands to every patch required for a whole-branch verdict.

## Metrics

Each adopted work unit records:

- waits under five minutes and liveness-only status calls;
- full plan/spec/diff reads, large outputs at least 30 KiB, duplicate reads, and recovery reads;
- compactions, agent count, review cycles, and elapsed time;
- cumulative input, cached input, output, and reasoning counters;
- verification results and independent review verdict.

The primary comparison is cumulative processed input for equivalent correctness gates. Cached-input ratio alone is not a target because efficient work can still reuse cached input.

`templates/work-metrics.json` is the schema-v2 generic record. It identifies a work unit with open `work_type`/`work_id` strings and uses `work_duration_seconds`. The legacy `templates/milestone-metrics.json` schema v1 remains valid with `milestone` and `milestone_duration_seconds`. Both versions combine automatic rollout aggregates with explicitly evidenced manual classifications that JSONL cannot reliably infer, including full plan/spec reads, full diffs, duplicate reads, recovery reads, agent/review counts, actual elapsed wait, duration, and correctness gates. Every rollout/manual/test/review artifact is a contained repository-relative file with a verified SHA-256 and must be a regular `100644`/`100755` entry in both HEAD and the stage-0 index. Both Git entries must identify the same blob; SHA-256 and behavioral-evidence heading checks use canonical blob bytes, so checkout filters such as `core.autocrlf` cannot change the result. Baseline/head must resolve to exact commits with valid ancestry. `validate-metrics` rejects mixed identities, missing, local-only, staged-only, symlinked, or hash-drifted evidence, impossible wait partitions/outcomes, duration/token contradictions, and incomplete fields. Omitted or invalid wait bounds are surfaced separately by `analyze-rollout` rather than silently treated as long waits.

Behavioral manifests distinguish inputs from outcomes. Schema v1 retains its fixed required sample IDs. Schema v2 declares a unique non-empty `required_sample_ids` list, allowing each generic work unit to define a closed experiment matrix. Every required ID must occur exactly once with disposition `PASS`; additional `INVALID` attempts remain visible but cannot close the matrix. A prompt-source hash first checks current HEAD/index; if the source has since evolved, it may resolve only to the same path, exact SHA-256, and regular Git blob in an ancestor of current HEAD. This preserves immutable v1 experiment manifests across a Skill upgrade. Response hashes have no historical fallback and must remain identical regular blobs in current HEAD and the stage-0 index.

## Measured overhead

Measurements use UTF-8 file bytes from the v1.1 working tree and a fresh temporary-home install:

| Item | Bytes | Interpretation |
| --- | ---: | --- |
| Frequently loaded `SKILL.md` | 4,715 | 663 words; down from v1's 6,667 bytes and 979 words |
| Global AGENTS router segment | 438 | Only triggers mode selection and skill use |
| Generic L1 template | 790 | Bounded at 8 KiB after filling |
| Generic L2 template | 806 | Bounded at 12 KiB after filling |
| Optional generic L3 template | 281 | Ignored and bounded at 4 KiB |
| Stateful recovery template total, L1+L2 | 1,596 | L3 raises the total to 1,877 bytes when present |
| LIGHTWEIGHT persistent repository state | 0 | No `.agent` files or validator run |
| Self-contained installed skill file payload | 117,736 | Seven files; mostly dormant CLI code, not prompt text |
| Observed fresh temporary install | 119,087 | Skill plus 438-byte AGENTS file and 913-byte path-dependent ownership state |

The measured fresh-install footprint is about 116 KiB. The temporary-install fixture uses a 45-character `TemporaryDirectory` root with `home` and `codex` children, making its path-dependent state size reproducible. The same seven skill files occupy 120,849 bytes in a clean `core.autocrlf=true` checkout because installed source bytes retain checkout line endings; the same short-path fixture then totals 122,200 bytes, including the 438-byte router and 913-byte ownership state. Prompt overhead is governed by the router and routed 4,715-byte canonical skill (4,772 checked-out CRLF bytes), while stateful recovery remains inside the unchanged 8/12/4 KiB budgets. The installation-state JSON varies with absolute path length.

## Executable surface

`scripts/optimize_context.py` uses only the Python standard library. Validation/analysis commands are read-only; explicit lifecycle commands have bounded writes:

- `analyze-rollout` extracts aggregate structural and token counters from JSONL;
- `validate` checks v1/v2 state structure, budgets, Git identity, state-only descendants, authority blobs, and worktree drift;
- `init` creates generic state only for an explicitly stateful Git repository;
- `migrate` explicitly converts valid clean v1 state to v2, with dry-run and rollback instructions;
- `install-global` and `uninstall-global` manage only the owned user skill/router/state in selected homes;
- `render-packet` validates and renders a deterministic bounded navigation packet;
- `validate-metrics` checks complete v1 milestone or v2 work records, real commit ranges, and Git-blob-backed correctness evidence;
- `validate-evidence` verifies behavioral sample coverage, metadata, headings, and immutable Git blob hashes.

The intentionally small executable surface avoids introducing a scheduler, database, daemon, or second source of project truth.
