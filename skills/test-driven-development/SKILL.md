---
name: test-driven-development
description: Use when test-first development helps establish behavior or prevent regressions, especially bugs, core logic and edge cases; strict red-first is not mandatory for every change.
---

# Test-Driven Development

The goal is evidence that behavior is correct and regressions are prevented, not adherence to a ritual.

## Strongly recommended test-first

Use for bug regressions, core business logic, boundary conditions, recurring defects and clearly automatable behavior changes. Write a focused test for the required behavior; observe the intended failure when feasible, implement the smallest correct change, and refactor with passing relevant tests.

A failure caused by setup errors does not establish the intended regression. A test that mirrors the implementation or checks only mocks may not protect behavior. See [writing-good-tests.md](writing-good-tests.md) when designing or changing tests.

## Flexible order

Documentation, configuration, generated code, pure renames, mechanical refactors, changes without a reasonable independent failing test, and small edits with sufficient existing coverage do not require strict red-first. Select appropriate inspection, parsing, existing tests or targeted regression coverage without asking permission merely to vary the order.

Never delete already-correct production code solely because it was written before a failing test. Keep it and add or strengthen meaningful tests where needed.

## Practical loop

1. Define observable behavior and relevant failure cases.
2. Prefer a failing regression test when it provides evidence.
3. Implement within scope and run relevant tests.
4. Refactor without changing behavior; repeat only checks invalidated by the change.
5. Broaden verification when integration risk or project requirements justify it.

Tests should exercise real contracts, normal/error paths and important edge conditions. Preserve necessary test coverage and explicit project rules; this flexibility is not permission to skip meaningful verification.
