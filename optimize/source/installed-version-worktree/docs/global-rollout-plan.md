# Optimize v1.1 Controlled Global Rollout Plan

## Purpose and release gate

This plan introduces Optimize v1.1 in four reversible stages. It does not authorize a real user-level installation, a Teaching AI write, a push, or a merge. The release candidate passed the complete 154-test gate and the retained independent reviewer approved the global-lifecycle fix at `ca3ac712ed6cd096a089c1ea84ac7fc61d65f508` with zero Critical, Important, or Minor findings in the incremental scope.

On 2026-09-15 the user separately authorized a limited local ordering adjustment for the thin-observability supplement, including its bounded v1.1.4 I1 token-snapshot follow-up: completed simple/phased smoke evidence may support one controlled user-level trial after the supplement's complete tests and independent incremental review pass. This does not mark Teaching AI adoption, a structurally different production rollout, or a production performance comparison as complete. Teaching AI remains read-only and outside this installation acceptance.

The rollout preserves this order of authority:

1. current repository instructions, source, tests, contracts, approved decisions, and exact Git state;
2. original independent review and verification evidence;
3. tracked L1/L2 operational state and optional ignored L3 state;
4. conversation memory.

Token reduction never permits weaker tests, less independent review, hidden failures, or trust in stale state. Stop the affected stage whenever Git, ownership metadata, active AGENTS precedence, state validation, tests, or review evidence disagree.

## Common preparation

Before each stage:

1. Record the exact source and target repository commits, branches, status, instruction files, and relevant configuration hashes.
2. Use a dedicated branch or isolated worktree for repository adoption. Preserve existing uncommitted and staged work.
3. Start with `git diff --stat` and `git diff --name-only`; open full patches only for a named correctness question.
4. Use the bundled Python runtime or another explicitly verified Python 3 runtime. The current Windows runtime is:

   ```powershell
   $OptimizePython = 'C:\Users\chest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
   ```

5. Keep mutable test homes and `CODEX_HOME` values inside a containment-asserted temporary directory. Do not point lifecycle tests at the real user home.
6. Capture metrics at equivalent correctness gates: tests, reviews, elapsed time, cumulative input and cached input, waits, short waits, status-only calls, compactions, broad reads, large outputs, and recovery reads.

## Stage 1 — Teaching AI STATEFUL adoption

### Goal

Verify that Teaching AI can use the generic STATEFUL protocol without changing application behavior, public contracts, schema, business code, existing audit history, or correctness rules.

### Procedure

1. Re-read the real Teaching AI repository immediately before adoption. Record its current branch, exact HEAD, status, applicable `AGENTS.md`, current context-state files, exact approved authority, and current review state. Do not assume the presently observed `a3f94da9275f170896c458d18691c06ceaf0c892` remains current.
2. Create an isolated adoption branch or worktree from that explicitly selected commit.
3. If valid schema-v1 state exists, run a dry migration first:

   ```powershell
   & $OptimizePython scripts/optimize_context.py migrate --repo <teaching-ai-worktree> --dry-run
   ```

4. Inspect the proposed identity conversion. Teaching AI milestone identity must become `work_type = "milestone"` and the same milestone value in `work_id`; existing spec, plan, and audit references remain ordered authority. Current findings, completed work, verification, next action, prohibitions, branch, baseline, and verified-work HEAD must retain their meaning.
5. Run the explicit migration only after the dry result matches current Git and L0 evidence. If no v1 state exists, run explicit STATEFUL initialization with a real work identity instead. Do not invent missing authority documents or code areas.
6. Fill L1 with concise navigation to real authority, code areas, important interfaces, and verification commands. Keep L2 as current operational state rather than history. Keep L3 ignored and disposable.
7. Stage only the infrastructure paths intended for the adoption diff. Do not stage L3.
8. Run `validate --repo`, Teaching AI's complete existing verification gates, and an independent review of the infrastructure-only diff.
9. Exercise one interrupted or fresh-root recovery using repository instructions, L1, L2, optional L3, the validator, and only the exact L0 needed for the next action.

### Exit criteria

- Migration or initialization is deterministic, valid, and reversible.
- Teaching AI business code, tests, schema, contracts, and historical audit files are byte-identical to the selected baseline unless a separately authorized task changes them.
- Existing safety and review rules remain active.
- The fresh-root recovery identifies the exact work unit, branch, verified-work HEAD, findings, next action, prohibitions, and authority without broad history loading.
- All Teaching AI tests and independent infrastructure review pass.

### Rollback

Before commit, use the CLI's printed rollback guidance and verify original bytes or required absence. After a reversible infrastructure commit, revert that one commit in the isolated branch. If rollback verification is incomplete, stop and preserve the named backup for manual recovery; do not continue adoption.

## Stage 2 — Structurally different STATEFUL project

### Goal

Confirm that v1.1 does not depend on Teaching AI terminology or require milestone, spec, plan, audit, domain, protocol, or web layers.

### Procedure

1. Select a repository with a materially different shape, such as a small Python package with `README`, `src`, and `tests`, or a quantitative project with its own real authority model.
2. Create an isolated branch/worktree and run:

   ```powershell
   & $OptimizePython scripts/optimize_context.py init `
     --repo <other-worktree> `
     --work-type feature `
     --work-id <real-stable-id>
   ```

3. Leave `authority_paths = []` if the repository has no separate authority documents. Populate only real code-map, interface, and verification entries.
4. Validate, checkpoint one material implementation/review transition, simulate interruption, and recover in a fresh task from L1/L2/L3 plus targeted L0.
5. Confirm that repeated initialization is an exact no-op for the same valid identity and a safe conflict for a different identity.

### Exit criteria

- No Teaching AI taxonomy or fabricated files appear.
- Empty, single, or multiple real authority paths work without weakening regular Git-blob checks.
- Recovery and checkpoints remain within the 8/12/4 KiB L1/L2/L3 budgets.
- Project tests and independent review pass at the same level required without Optimize.

### Rollback

Remove only the explicitly added L1/L2 files and the exact `.agent/active.md` ignore entry, or revert the isolated adoption commit. Preserve all pre-existing repository content and ignore rules byte-for-byte.

## Stage 3 — LIGHTWEIGHT observation

### Goal

Verify that Optimize remains almost invisible for small, self-contained work.

### Procedure

1. Run several ordinary tasks that are expected to fit one session, such as a one-file typo, a narrow documentation correction, and a small reversible bug fix.
2. Record the initial and final repository path list and Git status.
3. Confirm that the router chooses LIGHTWEIGHT, does not call `init`, does not run the state validator merely because the Skill exists, and does not create `.agent` files.
4. If one task becomes multi-phase, delegated, review-heavy, resumable, or compaction-prone, exercise the explicit LIGHTWEIGHT-to-STATEFUL upgrade once. Keep the same work identity and initialize only at that point.

### Exit criteria

- Every task that stayed lightweight added exactly zero persistent Optimize repository-state bytes.
- There is no `.agent` bureaucracy, broad recovery read, or forced fresh root for small work.
- Any upgrade was justified by observed complexity and retained full correctness gates.

### Rollback

No Optimize repository rollback should be necessary for tasks that remained LIGHTWEIGHT because they create no persistent state. Handle each task's ordinary code/document changes under its repository's normal workflow.

## Stage 4 — User-level global installation

### Entry gate

Proceed only after Stages 1–3 remain clean, their regressions have been reviewed, current official discovery behavior has been rechecked, and the user separately chooses to install globally. A report or approved release candidate is not authorization to mutate the real home.

The official Skill documentation identifies `$HOME/.agents/skills` as the user-scope Skill location. The official AGENTS documentation states that the Codex home defaults to `~/.codex` unless `CODEX_HOME` is set, and that the first nonempty global `AGENTS.override.md`, otherwise `AGENTS.md`, is active. Recheck both references when Stage 4 is actually scheduled:

- [Build Skills](https://learn.chatgpt.com/docs/build-skills)
- [Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

### Preflight

1. Resolve the real user home and effective `CODEX_HOME` lexically. Reject direct, nested, or ancestor reparse points.
2. Hash and back up the currently active global AGENTS file and record whether it existed. Record `config.toml` metadata for verification only; the installer must not edit it.
3. Confirm the target `context-state-management` Skill and `.optimize-context-install.json` are absent, or form a valid unchanged Optimize-owned pair.
4. Stop on an unmanaged same-name Skill, damaged/duplicate marker, unknown state, modified managed tree, precedence change, overlap, or stale transaction backup.
5. First perform a full install/update/uninstall cycle in a temporary home and `CODEX_HOME` using the exact release commit.

### Installation

Run the public CLI only after the preflight and separate user decision:

```powershell
& $OptimizePython scripts/optimize_context.py install-global `
  --home <resolved-user-home> `
  --codex-home <resolved-codex-home> `
  --source-skill <release>/skills/context-state-management
```

The command must verify the installed Skill digest, exact active AGENTS bytes, exact ownership-state bytes, prepared-tree absence, and transaction-backup disposition before reporting success. A failed postcondition must enter verified rollback. A committed update with incomplete backup cleanup must remain committed and report the exact stale backup.

### Post-install verification

1. Confirm the installed tree digest and version match the release.
2. Confirm exactly one bounded Optimize marker segment exists in the active global AGENTS file and all other bytes are unchanged.
3. Confirm the ownership state identifies the exact Skill path, digest, active AGENTS path, prior existence flag, and router segment.
4. Confirm `config.toml`, unrelated Skills, and repository files are unchanged.
5. Start a fresh Codex task and verify one LIGHTWEIGHT and one temporary STATEFUL scenario.

### Uninstall and rollback drill

```powershell
& $OptimizePython scripts/optimize_context.py uninstall-global `
  --home <resolved-user-home> `
  --codex-home <resolved-codex-home>
```

Verify the target and ownership state are absent, the exact managed marker is removed, the user's original AGENTS bytes and later edits outside the marker are preserved, and the uninstall backup is absent. Any named stale backup or incomplete rollback stops the rollout until manually resolved and independently checked.

## Observation window and metrics

Evaluate at equivalent correctness gates rather than by raw token totals alone. For each STATEFUL work unit record:

- cumulative input, cached input, output, and reasoning tokens;
- wait count, waits below five minutes, actual elapsed wait, and status-only calls;
- compactions, recovery reads, broad plan/spec/diff reads, duplicate reads, and outputs at least 30 KiB;
- work duration, agents, review cycles, tests, failures, and final independent verdict.

Compare against the M06 evidence without relabeling the broader 66.16-million-token rollout as the isolated 50.17-million-token M06 total. Do not claim a percentage improvement until comparable real work has completed.

## Stop conditions

Stop the rollout and preserve evidence if any stage shows:

- a Critical or Important correctness/security finding;
- altered Teaching AI business code or public contracts outside separate authorization;
- loss or weakening of repository instructions, tests, review independence, or Git validation;
- a false success/rollback claim, incomplete recovery, or unowned global artifact;
- unexpected `.agent` creation in LIGHTWEIGHT work;
- active AGENTS precedence drift or user configuration mutation;
- worse context behavior without a justified correctness benefit.

Resume only from an exact new baseline after the finding is reproduced, fixed with a regression, verified, and reviewed within a bounded scope.

## Current rollout status

The original Stages 1–3 remain engineering evidence rather than a Teaching AI adoption. The bounded v1.1.4 thin-observability candidate `e643769bb9300eee3b25e4d7a5cfec2f0704dc81` passed 212 tests and the attempt-4 I1 incremental review with `APPROVE`, 0 Critical, 0 Important, and 0 Minor findings. Its original review return is archived under `optimize-v1.1-observability-global-install-attempt-4/i1-token-snapshot-followup-review` with body SHA-256 `bd3bccb2c3997f7511c3cdcdd7c764be1d0a6dbbc94a89977c042e207fb4c2fc`.

The authorized user-level installation was then executed through the existing installer. The installed Skill reports version `1.1.4`; source, installed tree, and ownership state agree on digest `cbe9a62bef257d00c95b434c3dddc34dd8f782073bc41aab961c2b5bbd1ea97f`. The active global AGENTS file contains exactly one recorded router segment, its other bytes match the preinstall backup, `config.toml` and existing Skills retain their preinstall hashes, and no transaction artifact remains. File installation is complete; fresh-App loading and behavior acceptance remain pending and must not be inferred from this task. Real Teaching AI adoption and production performance comparison remain unapplied separate actions.
