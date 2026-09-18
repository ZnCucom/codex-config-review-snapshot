<!-- BEGIN OPTIMIZE CONTEXT ROUTER -->
## Global workflow policy

- Continue clearly authorized reversible work without repeated approval.
- Match verification to risk and scope; reuse successful evidence while relevant inputs remain unchanged.
- Current runtime instructions and exposed tool schemas govern tool interfaces.
- Avoid repeated reads, searches, tests, waits or status checks without new evidence. Do not poll agents for liveness or reread unchanged large files.
- After two consecutive tool rounds produce no new evidence, code/state change, test result or error information, stop and reassess. Do not repeat the same pattern a third time without a new explicit reason.
- Use the context-state-management skill in STATEFUL mode only when durable recovery is valuable for long-running, cross-session or interruption-prone work. Otherwise remain LIGHTWEIGHT.
- Project AGENTS.md supplies project-specific constraints.
<!-- END OPTIMIZE CONTEXT ROUTER -->

<!-- BEGIN GITHUB REVIEW CHECKPOINT SYNC V1 -->
## GitHub review checkpoint sync

Review Sync normally runs through lifecycle hooks, queue, the scheduled background sync, and remote SHA verification. Do not perform routine model-side status checks at task start or sync-now at task completion. Intervene only for an explicit sync request, pause/resume, background failure, safety decisions, remote divergence/quarantine, or a clearly necessary immediate sync. Before handling content the user forbids from upload, run and confirm `pause`; natural language alone is not the background hard switch. Report PENDING/UNKNOWN unless remote SHA verification completed. Background failure must not restart reasoning or cause an unbounded retry loop. Preserve project approval and hard-stop rules.
<!-- END GITHUB REVIEW CHECKPOINT SYNC V1 -->
