---
name: subagent-driven-development
description: Use when substantial independent tasks make delegation materially better than direct execution, or an independent reviewer has a concrete risk-control purpose; not for every implementation plan.
---

# Scoped Delegation

Ordinary small and medium implementations are completed directly by the current Astra controller.

Consider implementation delegation when there are at least two substantial independent work units, parallel execution provides clear value, shared mutable state is limited, and delegation is more reasonable than one controller doing the work. Independent review can separately be justified by risk. User requests and actual runtime permissions take precedence.

## Task boundaries

Give each selected agent one focused objective, exact relevant authority and interfaces, scope/ownership, baseline and changed files where relevant, acceptance criteria, constraints and expected return. Share minimal necessary context. Use isolation when it prevents conflicting edits; do not create a worktree or persistent ledger automatically.

Current exposed tool schemas and runtime instructions define invocation parameters, model options and waiting behavior. Do useful local work while agents run; use a bounded event wait only when dependent on them. Do not poll for liveness or automatically list agents after every wake.

Optional prompt outlines: [implementer](implementer-prompt.md), [reviewer](task-reviewer-prompt.md), [follow-up review](re-review-prompt.md). Existing brief/diff helpers may be used if they reduce context work; they are not mandatory per-task artifacts.

## Review by risk

Independent review is appropriate for high-risk/security changes, cross-module features, major architecture changes, important milestones, significant pre-merge review, or explicit user requests. Follow any mandatory project review requirements. A plan entry alone does not require a new implementer or reviewer. Small low-risk diffs may use controller self-review and targeted tests.

Reuse valid verification when relevant code, configuration, dependencies and test inputs have not changed. Verify an agent's claims against the actual diff and relevant evidence; a success message is not proof.

## Fix/re-review circuit breaker

Allow at most two consecutive substantive fix/re-review rounds using the current strategy. A small correction does not automatically require another agent. If two rounds do not converge, stop the mechanical loop and revisit original requirements, spec, diff and failing evidence. Check reviewer misunderstanding, conflicting requirements, a wrong implementation direction, and faulty tests/verification. Select a new explicit strategy before continuing; do not merely restart the count or mark unresolved issues passing.

No automatic per-task reviewer, broad final reviewer, model escalation or fixed multi-round ritual is required. Report unresolved material findings. Keep important final review when the actual integration risk or project rules require it.
