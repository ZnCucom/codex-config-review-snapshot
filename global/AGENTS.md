<!-- BEGIN OPTIMIZE CONTEXT ROUTER -->
## Optimize context routing

Use the `context-state-management` skill for context recovery and long-running work. Choose LIGHTWEIGHT for small, single-session work; do not create `.agent` state. Choose STATEFUL for resumable, multi-phase, delegated, review-heavy, or compaction-risk work. A lightweight task may upgrade to STATEFUL when those conditions emerge.
Before the final response, follow the Skill's common task finalization.
<!-- END OPTIMIZE CONTEXT ROUTER -->

<!-- BEGIN GITHUB REVIEW CHECKPOINT SYNC V1 -->
## GitHub review checkpoint sync

For a registered worktree, check review-sync status once at task start. Before handling content the user forbids from upload, run and confirm `pause`; natural language alone is not the background hard switch. After all source and public task-state writes, request a bounded `sync-now` for completion, blocker, failed tests, or review wait. Report PENDING/UNKNOWN unless remote SHA verification completed. Never restart reasoning because sync failed, and preserve all project approval and hard-stop rules.
<!-- END GITHUB REVIEW CHECKPOINT SYNC V1 -->
