---
name: requesting-code-review
description: Use when independent review is required by the user or project, or materially helps assess high-risk, security, cross-module, architectural, milestone or significant pre-merge changes.
---

# Code Review by Risk

Mandatory: independent review explicitly required by the user or applicable project rules.
Strongly recommended: high-risk/security changes, cross-module features, major architectural changes, important milestones, and important final reviews before merging.

A task being in a plan or delegated workflow is not itself a mandatory review trigger. For a small low-risk diff, the main agent can self-review and use targeted tests without spawning a reviewer.

When review is justified, provide the requirement, exact patch/baseline, affected interfaces, known limitations and available verification evidence. Use current runtime tools and permissions. The [review outline](code-reviewer.md) can support a focused request; adapt its scope to the actual risk.

Validate findings against source, requirements and test evidence before acting. Explain disagreement with evidence rather than blindly accepting it. Reuse still-valid tests; rerun affected checks after relevant changes.

A re-review is warranted by the risk of the fix or uncertainty in findings, not by every small edit. After two consecutive substantive fix/re-review rounds without convergence, stop and reassess requirements, diff, reviewer interpretation and test validity before choosing a new strategy. Do not mechanically add a final broad reviewer.
