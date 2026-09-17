# GitHub Review Checkpoint Sync v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, test, and safely install a deterministic Windows-capable review-checkpoint synchronizer that snapshots allowed on-disk worktree bytes into an independent Git store without changing the source worktree.

**Architecture:** A standard-library Python package separates read-only source Git queries, byte-stable capture, security gates, isolated Git object construction, durable queue/upload intent state, GitHub target verification, lifecycle hooks, and Windows scheduled execution. Each registered worktree owns a distinct snapshot store and exact `codex-sync/<device>/<worktree>` ref; no GitHub target is currently authorized, so delivery remains `GITHUB=NEEDS_SETUP`.

**Tech Stack:** Python 3.11 standard library, Git CLI with argument arrays and sanitized environments, GitHub CLI for production target verification, Windows Task Scheduler, `unittest`, temporary Git repositories and bare remotes.

**Spec:** `docs/superpowers/specs/2026-09-16-github-review-checkpoint-sync-v1-design.md`

## Global Constraints

- Work in this checkout and preserve the staged `.agent/project-map.md` and `.agent/current-work.md`; never enter the observability worktree.
- Write each production behavior only after its focused test failed for the expected missing behavior.
- Source Git uses `GIT_OPTIONAL_LOCKS=0`, a sanitized repository environment, and an enforced read-only command allowlist.
- Snapshot construction hashes scanned bytes directly and cannot execute clean/smudge filters, hooks, external diff, or source configuration.
- Never invoke an LLM, Codex, subagents, transcript parsing, remote discovery/creation, source-remote edits, force push, merge, release, or deployment.
- Never emit a matched secret value. Never guess a target when none is authorized.
- Install current-user components only, with absolute runtime paths and no new plaintext credentials.

---

### Task 1: Closed models, atomic state, and isolated Git runners

**Files:**
- Create: `review_sync/__init__.py`
- Create: `review_sync/model.py`
- Create: `review_sync/fs.py`
- Create: `review_sync/git.py`
- Create: `tests/test_review_sync_core.py`

**Interfaces:**
- Produces `ReviewSyncError`, `PolicyError`, `GitError`, `canonical_json`, `atomic_write_json`, `read_json`, `workspace_lock`, `GitRunner.source`, `GitRunner.snapshot`, and `GitRunner.effective_push_url`.
- `GitRunner.source(repo, *args)` rejects commands outside `branch`, `diff`, `ls-files`, `rev-parse`, and `status` before spawning a process.

- [ ] **Step 1: Write failing tests for environment cleanup, optional-lock suppression, command rejection, atomic replacement, and lock exclusion.**

```python
def test_source_runner_removes_inherited_repository_environment():
    runner = RecordingRunner({"GIT_DIR": "wrong", "GIT_INDEX_FILE": "wrong"})
    runner.source(repo, "status", "--porcelain=v2")
    assert runner.last_env["GIT_OPTIONAL_LOCKS"] == "0"
    assert "GIT_DIR" not in runner.last_env
    assert "GIT_INDEX_FILE" not in runner.last_env

def test_source_runner_rejects_write_before_process_start():
    with self.assertRaisesRegex(PolicyError, "not read-only"):
        runner.source(repo, "add", "-A")
```

- [ ] **Step 2: Run bundled Python `-B -m unittest tests.test_review_sync_core -v`; confirm failure is the missing package/API.**
- [ ] **Step 3: Implement closed dataclasses/enums, canonical UTF-8 JSON, fsync plus atomic replace, Windows/POSIX file locks, and list-argument subprocess wrappers.**
- [ ] **Step 4: Add a real temporary-repository test with Chinese and space-containing paths; assert the source index bytes do not change.**
- [ ] **Step 5: Run the focused test and full suite; commit `feat: add isolated review sync core`.**

### Task 2: Worktree discovery and stable working-copy capture

**Files:**
- Create: `review_sync/workspace.py`
- Create: `tests/test_review_sync_workspace.py`
- Modify: `review_sync/model.py`

**Interfaces:**
- Produces `discover_workspace(cwd, runner) -> WorkspaceIdentity` and `capture_workspace(identity, policy, runner, retries=2) -> CapturedWorkspace`.
- `CapturedFile` contains repository path, mode, exact bytes, digest, and source status; `UnstableSourceError` creates no checkpoint.

- [ ] **Step 1: Write failing descendant-cwd, staged-plus-unstaged, deletion, untracked, executable, unmerged-index, linked-worktree, and source-immutability tests.**

```python
def test_staged_plus_unstaged_captures_only_worktree_bytes():
    before = index_path.read_bytes()
    captured = capture_workspace(discover_workspace(repo / "子 目录", git), policy, git)
    assert captured.files["both.txt"].data == b"working-copy\r\n"
    assert captured.status["both.txt"].index == "M"
    assert captured.status["both.txt"].worktree == "M"
    assert captured.notes["both.txt"] == "index-only version not separately archived"
    assert index_path.read_bytes() == before
```

- [ ] **Step 2: Run `tests.test_review_sync_workspace`; confirm failures identify absent discovery/capture behavior.**
- [ ] **Step 3: Implement canonical ancestry matching, porcelain `-z` parsing, tracked/untracked enumeration, lstat/reparse checks, and double-read stability verification.**
- [ ] **Step 4: Treat stage numbers other than zero, mode `160000`, symlinks, special files, and reparse points as explicit unsupported states; never resolve them.**
- [ ] **Step 5: Inject one mutation to prove retry and repeated mutations to prove `UNSTABLE_SOURCE`; cover CRLF, Unicode, spaces, and rename records.**
- [ ] **Step 6: Run focused/full tests; commit `feat: capture stable worktree bytes`.**

### Task 3: Path, secret, history, and workflow gates

**Files:**
- Create: `review_sync/security.py`
- Create: `tests/test_review_sync_security.py`
- Modify: `review_sync/model.py`

**Interfaces:**
- Produces `scan_candidate(files) -> ScanReport`, `workflow_digest(files)`, `enforce_workflow_gate`, and `scan_reachable_history`.
- A finding exposes only rule ID, path, and redacted diagnostic.

- [ ] **Step 1: Write failing tests for tracked credentials, private keys, `.env`, generated-metadata secrets, LFS, large/unsupported data, symlinks, reparse points, and workflow changes.**

```python
def test_private_key_blocks_without_echoing_secret():
    report = scan_candidate({"tracked.pem": CandidateFile(0o100644, PRIVATE_KEY_BYTES)})
    assert report.blocked
    assert report.findings[0].rule_id == "PRIVATE_KEY"
    assert "BEGIN PRIVATE KEY" not in report.render()
```

- [ ] **Step 2: Write false-positive tests for 40/64-hex SHAs, manifest hashes, common legal files, and ordinary source; each fixture remains fully scanned.**
- [ ] **Step 3: Run `tests.test_review_sync_security`; confirm failures identify missing gates.**
- [ ] **Step 4: Implement mandatory path rules, known credential patterns, bounded entropy detection, LFS/size/data rules, and exact candidate-workflow reviewed-digest policy.**
- [ ] **Step 5: Enumerate reachable commits/blobs with `rev-list --objects` and `cat-file`; first push scans all history, later push scans the verified-to-candidate range.**
- [ ] **Step 6: Run focused/full tests; commit `feat: enforce checkpoint security gates`.**

### Task 4: Handoff, semantic deduplication, and raw-byte Git trees

**Files:**
- Create: `review_sync/snapshot.py`
- Create: `tests/test_review_sync_snapshot.py`
- Modify: `review_sync/model.py`

**Interfaces:**
- Produces `assemble_candidate`, `semantic_digest`, `create_checkpoint`, and `verify_checkpoint_bytes`.
- Candidate scan completes before any safe-chain ref update; committed blob bytes must equal scanned bytes.

- [ ] **Step 1: Write failing tests proving a secret in HANDOFF blocks before commit and timestamp/hook retries do not change the semantic digest.**

```python
def test_handoff_secret_never_becomes_safe_parent():
    candidate = assemble_candidate(capture, TaskStatus(blocker=SECRET), captured_at=T1)
    with self.assertRaises(CandidateBlocked):
        create_checkpoint(store, candidate, None, git)
    assert list(store_refs(store)) == []
```

- [ ] **Step 2: Run `tests.test_review_sync_snapshot`; confirm missing assembly/store failures.**
- [ ] **Step 3: Generate HANDOFF and manifest with source HEAD/status, task state, test-evidence digest, exclusions, limitations, and no self SHA.**
- [ ] **Step 4: Hash raw bytes with `hash-object -w --stdin` without `--path`; recursively build trees via `mktree -z`; create commits via `commit-tree`; disable store hooks.**
- [ ] **Step 5: Read every blob back via `cat-file`, compare bytes, and update the safe ref only after equality and security pass.**
- [ ] **Step 6: Cover CRLF, Unicode, spaces, 100644/100755, parent chains, no-change dedup, task-only change, and stale verification evidence.**
- [ ] **Step 7: Run focused/full tests; commit `feat: create isolated review checkpoints`.**

### Task 5: Durable queue, pause/disable, and quarantine

**Files:**
- Create: `review_sync/queue.py`
- Create: `tests/test_review_sync_queue.py`
- Modify: `review_sync/model.py`

**Interfaces:**
- Produces `WorkspaceStateStore`, `enqueue_request`, `pause_workspace`, `disable_workspace`, `resume_workspace`, `begin_upload_intent`, `complete_upload_intent`, `quarantine_chain`, and `recapture_from_verified`.

- [ ] **Step 1: Write failing tests for persistent pause, duplicate hooks, task-state precedence, changed-source evidence staleness, and explicit resume choices.**

```python
def test_session_end_unknown_does_not_replace_blocked_task_state():
    enqueue_request(store, blocked_request)
    enqueue_request(store, session_end_unknown)
    assert store.load().public_task_state == "blocked"
```

- [ ] **Step 2: Add a deterministic two-worker barrier test: confirmed pause prevents crossing upload-start; an already started transfer records best-effort cancellation and actual outcome.**
- [ ] **Step 3: Run `tests.test_review_sync_queue`; confirm missing transition failures.**
- [ ] **Step 4: Implement semantic request keys, atomic transitions, operation IDs, cancellation markers, bounded backoff, and `reauthorize`, `abandon`, `recapture` resume choices.**
- [ ] **Step 5: Make suspicious unpublished history unreachable from safe ref and allow authorized recapture from last verified SHA; never rewrite published history.**
- [ ] **Step 6: Run focused/full tests; commit `feat: persist review sync coordination`.**

### Task 6: Exact target verification and uncertain upload recovery

**Files:**
- Create: `review_sync/github.py`
- Create: `review_sync/engine.py`
- Create: `tests/test_review_sync_upload.py`
- Modify: `review_sync/git.py`
- Modify: `review_sync/model.py`

**Interfaces:**
- Produces `GitHubTargetVerifier.verify`, `SyncEngine.sync_now`, `read_remote_ref`, `push_exact_ref`, and `verify_remote_checkpoint`.
- Tests inject a local-bare verifier; production configuration cannot serialize that bypass.

- [ ] **Step 1: Write a failing bare-remote test for exact non-force ref push and SHA-based readback of HANDOFF plus a source file.**
- [ ] **Step 2: Write failing recovery tests for remote at previous SHA, already at pending SHA, unexplained SHA, and previously verified ref deleted.**
- [ ] **Step 3: Inject response loss after remote update and exit after push before receipt; assert one commit, one queue item, reused intent, and no duplicate push.**
- [ ] **Step 4: Run `tests.test_review_sync_upload`; confirm the upload engine is missing.**
- [ ] **Step 5: Verify production target with non-interactive `gh repo view` for exact identity, `PRIVATE`, and push permission; resolve effective push URL and reject rewritten identity.**
- [ ] **Step 6: Persist operation ID, target/ref, prior verified SHA, expected SHA, and parent before push; accept only prior SHA or expected SHA on recovery.**
- [ ] **Step 7: Push only `<sha>:refs/heads/codex-sync/<device>/<worktree>`, read the remote ref, fetch/read files, and publish receipt only after exact verification.**
- [ ] **Step 8: Return only `LOCAL_ONLY`, `PENDING`, `REMOTE_VERIFIED`, `NEEDS_SETUP`, or a closed policy/error state; run tests and commit `feat: recover exact checkpoint uploads`.**

### Task 7: Hooks, global protocol, scheduler, and owned uninstall

**Files:**
- Create: `review_sync/install.py`
- Create: `review_sync/hook.py`
- Create: `tests/test_review_sync_install.py`
- Create: `tests/test_review_sync_hook.py`
- Modify: `review_sync/model.py`

**Interfaces:**
- Produces `install_user`, `verify_installation`, `uninstall_user`, `handle_hook`, `register_windows_task`, `run_scheduler_probe`, and `remove_windows_task`.

- [ ] **Step 1: Write failing Stop/Interrupt/SessionEnd tests: missing `turn_id`, descendant cwd, unregistered cwd no-op, valid non-continuation Stop JSON, and no steering output for other events.**
- [ ] **Step 2: Write failing installation tests preserving unrelated AGENTS/hooks bytes, idempotence, marker conflicts, managed-file modification rejection, runtime-data retention, and injected rollback.**
- [ ] **Step 3: Write failing scheduler tests for limited current-user execution, no password, absolute Python/script/Git/gh paths, ten-minute trigger, and actual scheduled probe evidence.**
- [ ] **Step 4: Run `tests.test_review_sync_hook tests.test_review_sync_install`; confirm missing APIs.**
- [ ] **Step 5: Implement owned program copy/digest, hook merge/removal, bounded AGENTS segment, task wrapper/registration, component verification, and verified rollback.**
- [ ] **Step 6: Implement hook queue registration with no transcript read or network path and event-specific stdout. Never bypass hook trust.**
- [ ] **Step 7: Run focused/full tests; commit `feat: install review sync lifecycle`.**

### Task 8: CLI, acceptance matrix, docs, and current-machine install

**Files:**
- Create: `review_sync/cli.py`
- Create: `scripts/review_sync.py`
- Create: `tests/test_review_sync_cli.py`
- Create: `tests/test_review_sync_acceptance.py`
- Modify: `README.md`
- Modify: `.agent/project-map.md`
- Modify: `.agent/current-work.md`

**Interfaces:**
- Exposes `install`, `register`, `status`, `request`, `sync-now`, `tick`, `pause`, `resume`, `disable`, and `uninstall`.
- Status separates `IMPLEMENTATION`, `INSTALLATION`, `GITHUB`, and `CHATGPT_ACCESS`.

- [ ] **Step 1: Write failing command/exit-code tests; unregistered status must report `NEEDS_SETUP` without invoking gh, scanning repositories, or attempting login.**
- [ ] **Step 2: Write local acceptance covering complete/blocker/failed tests, dedup, mixed Git states, sudden-exit tick, retry/recovery, secrets/history, multiple worktrees, pause/disable, divergence/deletion, distinct SHAs, stale evidence, and install/uninstall.**
- [ ] **Step 3: Run `tests.test_review_sync_cli tests.test_review_sync_acceptance`; confirm missing CLI behavior.**
- [ ] **Step 4: Implement argparse entry points, JSON/human output, bounded `sync-now --timeout-seconds`, and precise PENDING/UNKNOWN preservation.**
- [ ] **Step 5: Document commands, boundaries, ownership, pause acknowledgement, trust, scheduler evidence, bare-remote proof, and current target absence.**
- [ ] **Step 6: Run focused/full tests. Install with explicit bundled Python/CODEX_HOME/data/Git/gh paths.**
- [ ] **Step 7: Verify program/protocol, hook definitions/trust state, task definition, then run the actual scheduled task and read its probe; do not register a project.**
- [ ] **Step 8: Exercise installed `status`, `sync-now`, `pause`, `resume --choice abandon`, `disable`, and uninstall status/dry-run path.**
- [ ] **Step 9: Commit `feat: deliver GitHub review checkpoint sync v1`.**

### Task 9: Fresh verification and review handoff

**Files:**
- Modify: `.agent/current-work.md`
- Create: `docs/reviews/github-review-checkpoint-sync-v1-review-packet.md`
- Create: `docs/reviews/github-review-checkpoint-sync-v1-review-packet.json`

**Interfaces:**
- Produces a closed review packet with exact commits, tests, installation evidence, limitations, and four separate outcome categories.

- [ ] **Step 1: Compare branch, formal HEAD, index entries, files, and classifications with deliberate development changes; prove acceptance sync runs did not mutate their sources.**
- [ ] **Step 2: Run the full suite and installed `status --json` freshly.**
- [ ] **Step 3: Read program digest, AGENTS marker, hooks/trust, Task Scheduler last result, scheduled probe, interpreter/Git/gh paths, and Windows identity.**
- [ ] **Step 4: Write review packet with baseline/head, diff, tests, install evidence, known limits, `GITHUB=NEEDS_SETUP`, and `CHATGPT_ACCESS=NOT_VERIFIED`.**
- [ ] **Step 5: Validate STATEFUL context, inspect final diff, commit `docs: hand off review checkpoint sync v1`, and stop without another Optimize phase.**
