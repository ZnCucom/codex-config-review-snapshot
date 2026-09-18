---
name: writing-plans
description: Use when a nontrivial task benefits from explicit implementation boundaries, dependencies and acceptance criteria; a small clear change does not need a separate plan document.
---

# Implementation Plans

Provide enough structure to carry out and verify the work without duplicating the codebase.

Include:
- Goal and constraints.
- Architecture or approach and important interfaces.
- Meaningful task boundaries and dependencies.
- Relevant files or symbols and only the context needed to find them.
- Acceptance criteria and proportionate verification.

A task is a work unit that can be completed and verified independently and produce meaningful progress. Do not split by a fixed number of minutes or make every read/edit/test command a separate step. Group closely related changes. Commit at useful coherent boundaries when authorized, not after each small action.

Use pseudocode, interfaces or short snippets for critical algorithms/contracts. Do not prewrite full implementations or repeat adjacent code merely to assume a worker has no context. Exact code is appropriate when the code itself is the required contract.

Default handoff: Astra executes directly in the current session. Recommend delegation only for substantial independent units with a real parallel benefit or an independent review justified by risk. A plan alone is not a trigger for SDD, STATEFUL, a separate worktree or another approval.

Review the plan once for missing requirements and contradictory interfaces. Correct it directly; do not add an automatic reviewer loop. If the user already authorized execution, continue without asking them to choose a workflow again.
