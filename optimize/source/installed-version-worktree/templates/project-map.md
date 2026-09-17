+++
version = 1
spec_path = "<approved-spec-path>"
plan_path = "<approved-plan-path>"
current_state_path = ".agent/current-milestone.md"
active_state_path = ".agent/active.md"
audit_path = "<historical-ledger-path>"
+++

# Project Map

This file is navigation. It does not restate contracts or implementation history.

## Source of truth

- Approved specification: `<path and useful heading anchors>`
- Approved implementation plan: `<path and current milestone heading>`
- Original approvals/clarifications: `<artifact paths or commit references>`
- Reviewer evidence: `<review artifact directory>`

## Code map

- Domain: `<path>`
- Protocol: `<path>`
- Service/application: `<path>`
- Infrastructure: `<path>`
- Web: `<path>`
- Tests: `<path>`

## Verification

- Focused: `<command>`
- Affected subsystem: `<command>`
- Type/lint/build: `<commands>`
- Broader milestone gate: `<commands or plan anchor>`

## Contract index

| Contract | Exact authority |
| --- | --- |
| `<name>` | `<path and heading/symbol>` |

## Operational state

- Current milestone: `.agent/current-milestone.md`
- Ephemeral action: `.agent/active.md` when present
- Historical ledger: `<audit_path above>`; read only for a specific unresolved question
