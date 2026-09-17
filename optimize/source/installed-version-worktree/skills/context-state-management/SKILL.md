---
name: context-state-management
description: Use when Codex work may be resumable, multi-phase, delegated, independently reviewed, interrupted or at risk of compaction, or when recovering repository context; also governs bounded agent waits and progressive reads.
---

# Context State Management

Reduce repeated context processing while preserving exact contracts, tests, independent review, and Git evidence.

## Core rule

Operational state is navigation, never source of truth. Authority descends from repository instructions, source, tests, schemas, approved decisions, Git, and original review evidence to L1/L2/L3 state and finally conversation memory. Resolve conflicts at the highest available layer and repair stale lower state.

## Choose a mode

Use **LIGHTWEIGHT** for clearly small, single-session, single-agent work with narrow scope and little compaction or handoff risk. Apply targeted reads, bounded output, exact verification, and existing repository rules. **Do not create `.agent` state or run its validator.**

Use **STATEFUL** when work is resumable, multi-phase, delegated, independently reviewed, planned across sessions, or likely to cross compaction. One stateful work unit normally uses one root task.

When uncertain, start LIGHTWEIGHT. Upgrade immediately to STATEFUL when those conditions emerge; do not invent a parallel task directory, journal, plan, or ledger. There is no automatic downgrade.

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

## Task finalization

At completion, blocker, or handoff, both LIGHTWEIGHT and STATEFUL run this common closeout. Read `references/observability.md`. When repository-external writes are allowed, create its minimal input and invoke bundled `scripts/optimize_context.py save-task-summary` once with verified Python. Accept only the returned status and path/ID as success. Leave unknown task/model/token fields unknown; do not scan history to fill them. No `.agent` in LIGHTWEIGHT does not prohibit permitted repository-external recording.

Read-only, no-write, and sandbox restrictions take priority. If recording is forbidden, unavailable, or fails, do not retry; give one reason. End the final response with `Summary record: saved — <path-or-ID>`, `Summary record: skipped — <reason>`, or `Summary record: failed — <reason>`.

Do not record log export, read-only acceptance, or log maintenance; observability does not recursively record itself. Archive formal review returns before fixes, a long wait, or task end.

## Delegation, waiting, and review

Give agents: task, role, exact authority, baseline, HEAD, changed files, current state, scope, known findings, prohibitions, and required return fields. The receiver independently opens L0; packets and reports are claims to verify.

After dispatch, record continuation and call one bounded long wait: **5-10 minutes** for narrow analysis or **10-30 minutes** for implementation/full review. It returns early on completion. After a timeout, inspect status once and choose another long wait, steering, recovery, or failure handling. **Never repeatedly poll** an active agent or call `list_agents` merely for liveness. Status inspection is allowed after a long timeout, a dependency, an error, or an explicit user request.

Keep every review and test cycle correctness requires. Independent review uses exact authority, patch, and evidence; another agent's conclusion is not proof.

## Progressive reads

Start with diff stats/names, exact document headings, named symbols, focused test summaries, and saved artifact paths. Expand to patches, broader sections, stack traces, or repository-wide search only for a named decision. Output limits never justify skipping tests, hiding failures, or truncating evidence needed for correctness.

Stop and correct course if state is replacing L0, recovery becomes a whole-repository scan, a historical ledger is read without a named question, short waits repeat, lightweight work creates state, or stateful work relies on conversation memory alone.
