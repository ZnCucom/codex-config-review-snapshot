# Optimize v1 Context Efficiency Design

**Status:** Approved by the user request dated 2026-09-13

**Scope:** Codex development workflow and context management for Teaching AI. This design does not change Teaching AI business behavior, domain contracts, protocol contracts, schema, or production feature semantics.

## Problem

Teaching AI's M04-M06 work used one long root conversation. The M06 portion combined a large root context with multiple independent agent contexts, repeated discovery, large tool output, compaction recovery, and short polling. The result was high cumulative input processing even though reasoning and output were a small part of the total.

The repository already had durable state, but its `progress.md` grew to about 23.8 KB and mixed current state with audit history, RED/GREEN evidence, review chronology, decisions, and next actions. It helped auditability while becoming expensive recovery input.

Optimize v1 must reduce repeated context processing while preserving exact source verification, independent review, full testing, and an auditable history.

## Evidence and constraints

- The long local Codex rollout containing M04-M06 records 77 `wait_agent` calls; 63 use 60-second timeouts. It also records 21 `list_agents` calls, four compactions, and 37 tool results at least 30 KiB.
- The same rollout spans multiple milestones and user resumptions. Its final cumulative counters include work after M06, so milestone-specific token totals remain those established by the user's forensic baseline rather than being relabeled as whole-rollout totals.
- Teaching AI has one 9.1 KB root `AGENTS.md`, one project skill, one 2,300-line implementation plan, and a 23.8 KB ignored `progress.md`.
- Codex CLI 0.153.4 exposes multi-agent support and a `wait_agent` call with an explicit timeout. Its tool contract permits a bounded long wait and returns early on agent activity or completion.
- Neither the current official configuration reference nor local strict-config evidence establishes `default_wait_timeout` or `max_wait_timeout` as supported config keys. Optimize v1 therefore does not write those keys.
- Experimental context management and opaque memory are outside the primary recovery path.

## Considered approaches

### Repository state and a standard-library validator — selected

Keep exact facts in existing source-of-truth files, add three small navigation/operational files, define one short skill, and validate state with a dependency-free command. This provides deterministic recovery and catches stale Git identity without creating a service.

### Markdown protocol only

This has the smallest code footprint, but branch/HEAD drift, missing fields, and size growth remain manual checks. The M06 evidence shows that the workflow needs an executable guard, so documentation alone is insufficient.

### Orchestration service or state database

A service could automate dispatch, polling, and state transitions, but it would add lifecycle, storage, migration, and trust concerns. Codex already provides agent coordination and Git already provides durable history. This is unnecessary for v1.

## Authority model

Authority is resolved in this order:

1. Current repository state, source, executable tests, and schema/DDL.
2. Approved design specification, implementation plan, and original approval or clarification.
3. Git history and immutable checkpoints.
4. Original reviewer findings and their evidence.
5. `.agent/current-milestone.md` operational state.
6. `.agent/project-map.md` navigation.
7. `.agent/active.md` ephemeral working register.
8. Conversation memory.

A lower layer may point to a higher layer but cannot override it. When state and Git disagree, work stops at the affected action, Git is inspected, and the state file is reconciled before work continues.

## State architecture

### L0 — source of truth

Approved specs, plans, source, tests, Git, original clarifications, reviewer reports, public contracts, and DDL remain exact. Optimize never replaces them with summaries.

### L1 — `.agent/project-map.md`

This tracked, stable navigation file contains paths, milestone anchors, verification commands, and a small contract index. It does not copy requirements or implementation history. Target limit: 8 KiB.

### L2 — `.agent/current-milestone.md`

This tracked file is the canonical current operational state. It records milestone, status, branch, baseline, the most recent verified work HEAD, completed and remaining work, open findings, latest verification, next action, prohibitions, and exact authority links. It is rewritten at checkpoints instead of appended to. Target limit: 12 KiB.

The state file cannot contain the SHA of the commit that contains itself. A valid state-only checkpoint may therefore be a descendant of the declared work HEAD when every intervening path is `.agent/project-map.md` or `.agent/current-milestone.md`. Any other committed path after the declared work HEAD is drift and fails validation.

Closed findings move to the historical ledger or review report; L2 retains only a compact reference when closure affects current action.

### L3 — `.agent/active.md`

This ignored file is a short working register for the immediate action, owner, reason, prohibitions, and success/failure continuation. Target limit: 4 KiB. It is never authoritative, and a missing L3 file is valid after a new checkout.

### Historical state

The existing `.superpowers/.../progress.md` remains an append-only audit ledger. New agents do not read it during normal recovery. L2 links to exact ledger sections or reviewer artifacts only when history is needed to resolve a question.

## Deterministic recovery

Recovery after compaction, interruption, restart, or a new root task follows one bounded sequence:

1. Read the root router (`AGENTS.md`).
2. Read `.agent/project-map.md`.
3. Read `.agent/current-milestone.md`.
4. Read `.agent/active.md` if present.
5. Run the state validator, which checks required structure, budgets, branch, and verified-work HEAD ancestry/path drift.
6. If validation fails, reconcile against Git and L0 before acting.
7. Load only the exact L0 sections and files named by the current action.

Repository-wide scans, entire-plan reads, entire-spec reads, complete progress-ledger reads, and whole-branch diffs are escalation steps triggered by evidence, not recovery defaults.

## Thread lifecycle

Each milestone uses a new root task. The prior milestone finishes only after its final verification, review verdict, finding disposition, checkpoint SHA, and next milestone prerequisites are persisted in tracked L0/L2 artifacts.

A new milestone task starts from the router, map, and state files, validates Git identity, reads the exact milestone section and referenced contracts, and then creates L3 for its immediate action. Conversation history is optional evidence and never the sole memory.

## Checkpoints

L2/L3 are updated only on high-value events:

- implementation or commit completion;
- review verdict or new/closed finding;
- approved clarification;
- verification completion;
- blocker discovery;
- before a long-running subagent;
- milestone handoff or task termination.

Routine tool calls and commentary do not trigger state rewrites.

## Agent wait policy

After dispatch, persist the agent identity and expected continuation, then make one bounded long wait. The default protocol range is 10-30 minutes for implementation or full review and 5-10 minutes for narrow analysis. Completion or a message returns early.

After a timeout, one status inspection is allowed to decide among another long wait, steering, recovery, or failure handling. Repeated short waits and repeated `list_agents` calls used only for liveness are prohibited. Status checks are reserved for an expired long wait, a dependency that needs current status, an agent error, or an explicit user request.

These are workflow values, not unsupported Codex config keys.

## Tool-output policy

- Diff: read `--stat` and `--name-only`, classify risk, then load patches per relevant file. Load a full diff only for a required whole-branch review.
- Plan/spec: locate headings first, then read the exact milestone or contract ranges.
- Source: use L1/L2 affected paths first; expand search only when the named files do not resolve the question.
- Tests: capture command, exit status, pass/fail count, and focused failure evidence. Suppress routine successful detail from the model-visible result when the tool supports it.
- Commands: cap ordinary output and redirect large generated evidence to an artifact, returning its path and a compact summary.

Correctness can always trigger broader reads. Output limits never justify skipping verification or hiding failures.

## Subagent context packet

Each dispatch packet contains:

- task and role;
- exact authority paths and section anchors;
- baseline and expected HEAD;
- changed files or allowed write set;
- current state and known findings;
- implementation/review focus;
- prohibitions;
- required return fields.

The packet supplies navigation and scope. Reviewers independently inspect L0 and never treat an implementer report or current state as proof.

## Review protocol

Task review checks the task diff and exact contracts. Fix review checks the finding, its regression evidence, and the incremental diff while confirming accepted findings did not regress. Final whole-branch review starts from a name/stat risk map and then reads all required patches. Every review returns severity, file/evidence, contract impact, and action. Re-review cycles continue as correctness requires; context discovery is reused through packets and artifact paths.

## Executable guard

`scripts/optimize_context.py` provides:

- `validate`: parse the three state files, enforce required fields/sections and byte budgets, and compare declared branch/HEAD with Git;
- `analyze-rollout`: count actual Codex tool calls, wait durations, status calls, compactions, large tool results, and cumulative token counters from JSONL without copying the rollout into documentation;
- `render-packet`: combine a structured packet input with the standard reviewer/implementer format.

The tool uses only Python's standard library and never edits the target repository.

## Validation experiments

- Resume: a fresh agent receives only the router, L1, L2, and L3 and must identify milestone, branch, HEAD, completed work, next action, prohibitions, and exact L0 references.
- Compaction recovery: delete conversational assumptions from the fixture and recover entirely from repository state.
- Wait: dispatch a bounded agent, call one long wait, and verify it returns on completion without a short-poll loop.
- Reviewer: give a reviewer a packet and verify it independently reads the named source/test evidence.
- State drift: declare a non-current HEAD and require `validate` to fail with a precise diagnostic.
- Forensics: run the analyzer on a small fixture with known tool calls and token counters.

## Metrics

Per milestone, record short waits (under five minutes), `list_agents` calls, full plan/spec reads, outputs over 30 KiB, full-diff calls, duplicate reads, compactions, recovery reads, agent count, review cycles, cumulative input/cached/output/reasoning tokens, duration, and verification/review results.

The primary comparison is cumulative processed input for equivalent correctness gates. Cached-input ratio is descriptive and is not optimized in isolation.

## Teaching AI rollout

Optimize v1 is first tested in its own fixtures. The initial Teaching AI integration is a small reversible infrastructure-only change: add L1/L2, ignore L3, install the project skill, and add a short router entry to `AGENTS.md`. Existing correctness rules remain in place during the first milestone trial. The historical ledger remains unchanged.

Because Teaching AI is already on M07, the first live adoption should checkpoint the current M07 state before any further agent dispatch. If that checkpoint cannot be reconstructed exactly from Git and existing review evidence, rollout pauses for reconciliation rather than guessing.

## Non-goals

- Changing Codex compaction internals.
- Enabling experimental context management or opaque memory as a dependency.
- Reducing test coverage, review independence, or review cycles needed for correctness.
- Summarizing public contracts into operational state.
- Changing Teaching AI product code or contracts.
- Building a scheduler, daemon, state database, or autonomous orchestration framework.
