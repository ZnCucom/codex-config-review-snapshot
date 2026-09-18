---
name: context-state-management
description: Use when durable context recovery is valuable for long-running or cross-session work, resuming interrupted work or compaction risk; retain lightweight defaults otherwise.
---

# Context State Management

Reduce repeated context processing while preserving exact contracts, tests, independent review, and Git evidence.

## Core rule

Operational state is navigation, never source of truth. Authority descends from repository instructions, source, tests, schemas, approved decisions, Git, and original review evidence to L1/L2/L3 state and finally conversation memory. Resolve conflicts at the highest available layer and repair stale lower state.

## Choose a mode

LIGHTWEIGHT defaults to targeted reads -> work -> proportionate verification -> finish. **Do not create `.agent` state**, run its validator, checkpoint, save a task summary, archive, read the observability reference, execute observability closeout, or create repository-external task records by default.

Use STATEFUL only when durable recovery has real value: cross-session work, substantial multi-phase milestones, compaction risk, durable handoff, a genuinely delegated workflow requiring recovery, or long-task interruption/resumption. A plan, one review or a temporary agent alone is insufficient.

When uncertain, start LIGHTWEIGHT. Upgrade when durable recovery becomes necessary; do not create a parallel journal, plan or ledger as a substitute. Do not silently discard established STATEFUL evidence.

## Stateful setup and recovery

For new state, run the bundled `scripts/optimize_context.py init --repo <repo> --work-type <type> --work-id <id> [--owner <owner>]`. Fill only useful navigation, stage L1/L2, then run `validate`. Never stage L3. `work_type` and `work_id` are open project identifiers; `owner` is optional. `authority_paths` may contain zero or more real repository files. Never manufacture spec, plan, audit, or code taxonomy.

Schema-v1 `.agent/current-milestone.md` remains valid. Recover it as v1 or run explicit `migrate --repo <repo>`; never rewrite it silently.

For v2 recovery:

1. Read applicable `AGENTS.md` instructions.
2. Read `.agent/project-map.md` (L1) and `.agent/current-work.md` (L2); read `.agent/active.md` (L3) when present.
3. Run `validate`. Verify branch, exact commits, ancestry, HEAD/index authority, and worktree drift. A later commit is acceptable only when validation proves it changes context state alone.
4. On drift, stop the affected action and reconcile state against Git and L0.
5. Open only the exact L0 source/test/authority locations needed for `Next action`. Broaden reads only when named evidence is insufficient.

| Layer | Purpose | Limit |
| --- | --- | --- |
| L0 | Exact source, tests, contracts, decisions, Git, review evidence | authoritative |
| L1 `.agent/project-map.md` | stable navigation and verification | 8 KiB |
| L2 current work or legacy milestone | canonical operational state, no chronology | 12 KiB |
| L3 `.agent/active.md` | ignored immediate action/owner/continuation | 4 KiB |

Checkpoint L2/L3 after a material implementation/commit, verdict, finding change, clarification, verification, blocker, before a long wait, handoff, or task end. Routine turns do not earn checkpoints.

## Finish and optional evidence recording

LIGHTWEIGHT finishes after proportionate verification, without automatic state or observability maintenance. General completion, an ordinary blocker or ordinary handoff does not trigger a record.

STATEFUL preserves material L2/L3 checkpoints and exact recovery evidence. This does not require an observability task summary.

Only an explicit user request, formal performance/behavior investigation, important incident forensics, or explicit need to preserve review evidence can trigger observability. At that boundary, read `references/observability.md` and use the existing CLI. Preserve unknown values and do not scan history to fill them. Read-only/no-write restrictions take priority; one failed attempt is reported, do not retry automatically. The mechanism does not recursively record itself. No summary-record footer is required when observability was not requested or needed.

## Delegation, waiting, and review

Give agents: task, role, exact authority, baseline, HEAD, changed files, current state, scope, known findings, prohibitions, and required return fields. The receiver independently opens L0; packets and reports are claims to verify.

When genuinely dependent on an agent, use a bounded event wait permitted by the current runtime. Do useful local work otherwise. Record continuation only when durable recovery requires it. **Never repeatedly poll** an agent or query status solely for liveness; inspect status for a timeout, dependency, error or explicit request.

Use independent review where risk or project rules justify it. Reuse valid tests for unchanged inputs. After two consecutive substantive fix/re-review rounds without convergence, revisit requirements, diff and failing evidence before selecting a new explicit strategy. Another agent's conclusion is not proof.

## Progressive reads

Start with diff stats/names, exact document headings, named symbols, focused test summaries, and saved artifact paths. Expand to patches, broader sections, stack traces, or repository-wide search only for a named decision. Output limits never justify skipping tests, hiding failures, or truncating evidence needed for correctness.

Stop and correct course if state is replacing L0, recovery becomes a whole-repository scan, a historical ledger is read without a named question, short waits repeat, lightweight work creates state, or stateful work relies on conversation memory alone.
