+++
version = 2
work_type = "<work-type>"
work_id = "<work-id>"
status = "PLANNING"
branch = "<branch>"
baseline = "<full-baseline-sha>"
head = "<full-most-recent-verified-work-sha>"
+++

# Current Work State

`head` names the most recent verified work commit. A later state-only commit may contain this file; any later non-state path is drift.

## Constraints and clarifications

- `<exact artifact or commit reference when one exists>`

## Completed

- `<verified result>`

## Remaining

- `<bounded remaining result>`

## Open findings

- `<finding ID, severity, and evidence, or none>`

## Last verification

- `<command>` — `<result>` at `<work SHA>`

## Next action

- `<one concrete action>`

## Do not

- `<explicit prohibition>`

## Authority

- `<path and exact heading or symbol needed next>`
