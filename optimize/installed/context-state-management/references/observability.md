# Thin observability CLI

Use this reference only at a task/review recording boundary or for an explicit export. These records are local evidence, not project state, and are never loaded for recovery by default.

Resolve the selected Skill directory as `$SkillRoot` and invoke `$SkillRoot\scripts\optimize_context.py` with a verified Python 3 runtime. Do not assume that `python` is available. Reuse an already verified interpreter, such as the current bundled runtime when present, and prefer `-B` to avoid bytecode writes during checks. Do not install a runtime or change `PATH`, `config.toml`, or product logic to locate one. The default root is `%LOCALAPPDATA%\Optimize\observability`; `--observability-root` selects a controlled alternate root.

Task summary finalization gets one attempt. Explicit read-only or no-write requirements and sandbox denial skip it. An unavailable interpreter or a failed command is reported once without retry. Do not save a task summary for observability export, read-only acceptance, or observability/log maintenance; those operations do not recursively record themselves.

## Task summary

Create one UTF-8 JSON input outside the business repository and run:

```powershell
& $OptimizePython "$SkillRoot\scripts\optimize_context.py" save-task-summary `
  --repo <repository> --input <task-record.json>
```

The input is a closed schema. Use `null` with an explanatory source when a value was not available. Do not use `0`, `PASS`, or a Codex session ID unless the fact was actually observed. `archive_id` may be a stable local ID such as `local-<uuid>`; it is not a session ID. Do not scan historical tasks or sessions to replace unknown values.

Exit code zero prints `created` for a new record or `unchanged` for the same record's safe retry, followed by the archive ID and actual JSON/Markdown paths. Only that returned output supports `Summary record: saved`. A forbidden attempt is `skipped`; an unavailable runtime, nonzero exit, missing output, or contradictory result is `failed`. Report the reason once and finish the business task.

The JSON and derived Markdown publish together as one immutable `summary/` directory below the task archive. Concurrent identical publishes become a no-op; a different publish at the same identity is a conflict. Every existing storage component is checked lexically and filesystem reparses are rejected.

```json
{
  "schema_version": 1,
  "archive_id": "local-stable-task-id",
  "title": "Short task title",
  "identities": {
    "work": {"value": null, "source": "unavailable: no work ID exposed"},
    "task": {"value": null, "source": "unavailable: no task ID exposed"},
    "session": {"value": null, "source": "unavailable: no session ID exposed"}
  },
  "runtime": {
    "optimize_version": {"value": "1.1.5", "source": "Skill VERSION"},
    "loaded_path": {"value": null, "source": "unavailable"},
    "model": {"value": null, "source": "unavailable"},
    "reasoning_effort": {"value": null, "source": "unavailable"},
    "run_version": {"value": null, "source": "unavailable"}
  },
  "mode": {"observed": "LIGHTWEIGHT", "source": "agent-observed", "upgraded": false},
  "timing": {"started_at": null, "ended_at": null, "source": "unavailable"},
  "git": {"branch": null, "baseline": null, "head": null, "source": "not checked"},
  "outcome": {"status": "completed", "source": "agent-reported"},
  "change_scope": [],
  "tests": [{"name": "not run", "status": null, "evidence": null}],
  "reviews": [],
  "counters": {
    "wait_calls": {"value": null, "source": "unavailable", "scope": "task"}
  },
  "evidence": [],
  "anomalies": [],
  "missing_evidence": ["No selected session log was available"],
  "next_action": null,
  "rollout": null
}
```

To inspect one explicit session/task JSONL, replace `rollout` with `{"path":"C:\\absolute\\selected.jsonl","source":"runtime-provided","scope":"session-cumulative"}`. The inspector never searches for another log. It explicitly recognizes the verified Codex Desktop 0.154 record shapes rather than accepting arbitrary future types. For `token_usage_record`, it selects the final cumulative `thread_token_usage` snapshot for one session/thread and never adds response, turn, thread, or duplicate `token_count` snapshots; multiple thread/session identities leave token metrics unknown. Cached/new input remain separate, and a requested timeout is never treated as actual wait time. Empty or unknown-only outer/inner record shapes are unsupported rather than complete; mixed recognized and unknown records are partial, and retained counters are explicitly scoped to recognized records rather than claimed as whole-task totals. `complete` describes parsing of the selected log only, not proof that the log covers the whole task.

## Reviewer return

Choose the same stable task archive ID before dispatch or when the first report arrives. Save the returned body as bytes without editing it, create UTF-8 metadata, then run:

```powershell
& $OptimizePython "$SkillRoot\scripts\optimize_context.py" archive-review `
  --repo <repository> --archive-id <stable-task-id> `
  --metadata <review-metadata.json> --body <review-body.txt>
```

Metadata schema:

```json
{
  "schema_version": 1,
  "review_id": "initial-review",
  "source_tool": "wait_agent",
  "canonical_task_identity": "/root/reviewer",
  "agent_id": null,
  "baseline": "full baseline SHA",
  "head": "full reviewed head SHA",
  "observed_repo_head": "full repository SHA when received",
  "received_at": "2026-09-15T12:00:00Z",
  "source_locator": "wait_agent final return",
  "completeness": "complete",
  "fidelity": "captured-tool-return",
  "review_round": "initial",
  "summary": null
}
```

Allowed completeness values are `complete`, `partial`, and `summary-only`. Allowed fidelity values are `captured-tool-return`, `unverified-transcription`, and `summary-only`; the archive never claims `verified-verbatim`. For `summary-only`, omit `--body`, set both classifications to `summary-only`, and put the available summary in `summary`. Use another `review_id` and `review_round: "rereview"` for a later review.

## Export

Default export omits raw session logs, source, environment variables, absolute repository/session paths, and review bodies:

```powershell
& $OptimizePython "$SkillRoot\scripts\optimize_context.py" export-observability `
  --repo <repository> --latest 10 --output <report.md>
```

Use `--archive-id`, `--since <ISO-8601>`, and `--until <ISO-8601>` to narrow the selection. Export validates the closed stored-review schema, exact directory contents, regular non-reparse files, and the body SHA-256 before reporting or including a review. Invalid evidence is omitted with a diagnostic. Free-text fields containing an absolute path are conservatively replaced as a whole in the sharing copy so path fragments are not retained; structured mode, status, counts, and archive identity remain. Add `--include-review-bodies` only when explicitly requested. That report warns that automated secret detection/redaction is not guaranteed; inspect it before sharing. Export is idempotent only for identical bytes and refuses to overwrite a different existing file.
