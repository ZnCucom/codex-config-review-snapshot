---
name: verification-before-completion
description: Use when assessing whether available evidence supports completion or correctness claims; choose verification by risk and reuse still-valid results.
---

# Verification Before Completion

Evidence before claims.

A successful verification remains valid when relevant code, configuration, dependencies and test inputs have not changed in a way that invalidates it. Evidence need not be rerun in the current message. Passing earlier checks prove only their actual scope, not unrelated behavior.

Before a claim:
1. Identify the evidence needed and its scope.
2. Check the latest result, exit status, failures and the inputs it covered.
3. Decide whether intervening changes invalidate it.
4. Run missing or affected verification only; then report the observed result and limitations.

## Match risk and scope

- Small documentation/configuration/mechanical single-file changes: minimal targeted inspection, parsing or a relevant check.
- Medium implementation changes: relevant tests and targeted build/lint/typecheck as appropriate.
- Large, high-risk or integrated changes: broader tests, integration verification and a full suite when needed or explicitly required.

Do not repeat an unchanged successful suite just because a final response, commit or another status statement is next. Do not mechanically repeat a failed command without a new hypothesis, change or diagnostic purpose.

Bug fixes should demonstrate the original failure is addressed and regression coverage is meaningful. Test-first is valuable where practical; do not destroy correct code solely to recreate test order. A linter is not a compiler, agent success is not independent evidence, and a passing subset does not imply all tests passed.

Use source/diff and relevant results to check agent claims. State missing verification honestly. Preserve project-required tests and high-impact approval rules.
