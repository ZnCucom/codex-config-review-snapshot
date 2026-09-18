---
name: brainstorming
description: Use for important unresolved design choices, new projects or subsystems, major architecture or public-interface changes, or critical ambiguity; not for clear authorized small changes.
---

# Brainstorming

Choose the least process that resolves the actual uncertainty.

## Direct execution

For clear, authorized, reversible, small work with no important architecture choice:
understand -> necessary reads -> change -> proportionate verification.

Examples include a single-file or clear bug fix, README/documentation edits, small configuration changes, mechanical refactoring and a concrete step in an approved plan. No brainstorming approval gate or design artifact is required.

## Brief design

For a few nonmajor implementation choices, briefly explain the approach and execute within the existing authorization. Wait for another yes only when the user requested confirmation or a material decision remains unresolved.

## Full design

Use for a new project/subsystem, major architecture change, public interface change, choices with significant long-term consequences, or critical ambiguity.

1. Inspect the relevant existing context and clarify material requirements.
2. Compare the meaningful alternatives and their tradeoffs.
3. Present a proportionate design covering interfaces, failure cases and acceptance.
4. Obtain approval for genuinely unresolved significant choices before committing to them. Reuse prior approval when it already covers this design.
5. Write a spec or plan only when it helps implementation or durable handoff; commit documents only within the task's Git authorization.

Do not treat all creativity, a configuration change, or every task as requiring a new approval. Do not automatically escalate a small task because a skill exists.

For useful visual design exploration, [visual-companion.md](visual-companion.md) is optional. Use its interactive/server procedures only if selected for the task; ordinary text work does not need that companion.
