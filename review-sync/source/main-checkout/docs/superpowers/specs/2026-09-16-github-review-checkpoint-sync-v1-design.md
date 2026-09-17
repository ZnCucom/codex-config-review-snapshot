# GitHub Review Checkpoint Sync v1 Design

## Status and authority

This design implements the user-approved Scheme A: an independent snapshot Git repository per registered worktree. It is a bounded Optimize delivery unit, not a new memory system, autonomous agent, general backup product, merge service, release mechanism, or completion authority.

The authoritative requirements are the original task request and the approved revisions in the task conversation. This document makes those requirements executable without widening them.

## Goals

Review Sync v1 gives local Codex App and CLI work a deterministic, model-independent path to a reviewable GitHub checkpoint branch. It can preserve allowed source, test, documentation, and task-status files after normal completion, blockers, failed tests, user interruption, or a later scheduler tick after Codex is no longer running.

A checkpoint is evidence for review. It is not a merge, release, deployment, approval, milestone completion, or permission to continue into another project phase.

## Non-goals

- No LLM, OpenAI API, Codex subprocess, subagent, repeated Codex launch, or transcript parsing.
- No automatic repository creation, repository discovery, remote selection, visibility change, merge, release, deployment, pull request, tag push, force push, or default-branch push.
- No replacement for Optimize L0/L1/L2/L3 state or existing project approval and hard-stop rules.
- No complete backup of separate HEAD, index, and working-tree versions. V1 snapshots the current allowed on-disk worktree bytes and records source Git status.
- No promise that secret scanning detects every secret or that external automation has no side effects.
- No cross-device installation claim. Installation applies only to the current Windows machine.

## Verified implementation baseline

- Repository: `C:/Users/chest/OneDrive/文档/ChatGPT/optimize`.
- Branch: `codex/v1.1-globalization`.
- Baseline HEAD: `d22cddae63075f3370870b7035dea3d45ef900c1`.
- Baseline index after STATEFUL initialization contains only `.agent/project-map.md` and `.agent/current-work.md` as staged additions.
- Codex CLI: `0.153.4`.
- Codex home: `C:/Users/chest/.codex`.
- GitHub CLI is authenticated, but this repository has no Git remote and no authorized private GitHub target.
- Existing test baseline: 154 tests passed.

The other observability worktree is out of scope and must not be entered or modified.

## Architecture

### Repository package

The distributable source lives in this repository:

- `review_sync/model.py`: closed configuration and state schemas, enums, validation, canonical JSON.
- `review_sync/fs.py`: atomic files, safe path handling, Windows reparse detection, and interprocess locking.
- `review_sync/git.py`: isolated source-query and snapshot-store Git runners.
- `review_sync/workspace.py`: worktree discovery, registration identity, source status, capture manifest, and stable byte collection.
- `review_sync/security.py`: path policy, file-mode policy, secret detection, workflow gate, history gate, and quarantine decisions.
- `review_sync/snapshot.py`: candidate assembly, generated handoff/manifest, byte verification, isolated commit creation, and deduplication.
- `review_sync/queue.py`: durable requests, upload intents, backoff, pause/disable coordination, and recovery transitions.
- `review_sync/github.py`: explicit target verification, effective push URL verification, precise push, remote-ref reads, and remote content verification.
- `review_sync/install.py`: owned user-level installation, hooks merge, AGENTS segment, Windows scheduled task, verification, and uninstall.
- `review_sync/cli.py`: commands and stable exit/status output.
- `scripts/review_sync.py`: thin repository entry point.
- `tests/test_review_sync_*.py`: unit and integration tests using temporary repositories and local bare remotes.

The package uses Python 3.11+ standard library plus the external `git`, `gh`, and Windows Task Scheduler programs already present on the current machine. No new Python package is installed.

### User-level layout

The default installed program path is `%CODEX_HOME%/review-sync`. Runtime data is outside the program tree at `%LOCALAPPDATA%/CodexReviewSync` and contains:

- `config.json`: schema-versioned device identity and registered projects.
- `state/<workspace-id>.json`: current source/checkpoint/pause/queue state.
- `queue/<workspace-id>/`: durable request records.
- `stores/<workspace-id>.git`: independent bare snapshot Git store.
- `receipts/<workspace-id>/`: immutable local and remote verification receipts.
- `locks/<workspace-id>.lock`: interprocess coordination.
- `install-state.json`: exact installer ownership evidence.

Uninstall removes only unchanged managed program files, exact managed hook entries, the exact AGENTS segment, and the owned scheduled task. Runtime project data and receipts remain unless the user separately and explicitly removes them.

### Source Git runner

Every source-repository Git call uses an argument array and a sanitized environment:

- `GIT_OPTIONAL_LOCKS=0`.
- `GIT_TERMINAL_PROMPT=0`.
- Remove inherited `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, and `GIT_ALTERNATE_OBJECT_DIRECTORIES`.
- Disable external diff and pager execution.
- Never run `git add`, `git update-index`, `git write-tree`, `git hash-object -w`, checkout, stash, reset, clean, commit, fetch, pull, merge, or rebase against the source repository.

The source runner resolves the actual Git top-level, absolute Git dir, common dir, and worktree identity. A hook `cwd` may be any real descendant of the worktree. Matching uses canonical path ancestry and exact stored Git identity, never an unsafe string prefix. A `cwd` outside a registered worktree is ignored.

The source runner reads porcelain-v2 status, tracked paths and modes, untracked paths allowed by Git excludes, branch, HEAD, and unmerged records. Unmerged entries are unsupported in v1: capture records `UNMERGED_UNSUPPORTED` and does not modify or resolve the index.

### Snapshot Git runner

The snapshot store runner also sanitizes inherited Git environment and additionally sets repository-local controls so snapshot construction cannot run source or inherited clean/smudge filters, hooks, external diff, pagers, credential prompts, or automatic conversion. It never changes the user's global Git configuration.

Candidate files are copied as already scanned bytes into a newly created private temporary assembly directory. The runner uses only the assembly directory and the dedicated snapshot bare repository. It creates the tree with a private temporary index belonging to the snapshot store. Supported modes are regular non-executable `100644` and executable `100755`; symbolic links, submodules, special files, and Windows reparse points are blocked.

After commit creation, every committed blob is read back with `git cat-file` and byte-compared to the scanned candidate bytes. Any mismatch quarantines the candidate and prevents it from entering the normal checkpoint chain. This verification covers CRLF, Unicode filenames, filenames containing spaces, and generated metadata.

### Worktree semantics

V1 snapshots the current allowed on-disk worktree bytes. If a file has both staged and unstaged modifications, the snapshot stores the working copy only. The manifest records the source porcelain status and states that the index-only variant was not separately archived. Source HEAD, index, staged/unstaged classification, and files must remain byte-identical before and after capture.

Capture begins with source identity, status, tracked/untracked path lists, and filesystem metadata. It copies each supported file while hashing its bytes. It then repeats source identity, status, path enumeration, and file hashes. A changed HEAD, index status, path set, mode, size, or digest causes a bounded retry. Exhausted retries produce `UNSTABLE_SOURCE` and no normal checkpoint.

Deduplication uses a semantic digest over sorted path, mode, byte digest, public task status, source HEAD/branch/status, exclusions, and supported limitations. It excludes capture timestamps, retry timestamps, hook event IDs, operation IDs, scheduler heartbeats, and repeated request timestamps.

### Generated review metadata

The assembly adds `.review-sync/HANDOFF.md` and `.review-sync/manifest.json` without writing them to the source worktree. Public fields are limited to:

- registered project and workspace IDs;
- source branch, source HEAD, stable capture time;
- current objective and changed paths;
- completed, remaining, blockers, next action, and task state;
- verification command/result and the semantic digest it applies to;
- source Git status, exclusions, limitations, and ownership uncertainty;
- the explicit distinction between source HEAD and snapshot commit.

Unknown facts are `unknown` or `not_run`. A later source digest invalidates earlier test or completion evidence. Hook events and task state are separate: a later `SessionEnd` or unknown reason cannot overwrite an applicable `complete`, `blocked`, or `review_wait` state, while changed source bytes make the old task/test evidence stale.

The snapshot commit cannot contain its own SHA. Snapshot SHA, push operation ID, and remote verification result live in external receipts and CLI output.

## Security gates

### Path and file gate

Default denial covers credentials, `.env` variants containing real values, private keys, authentication caches, raw conversations, sensitive observability logs, build/cache/output directories, unsupported data files, large files, Git LFS pointers, submodules, special files, symbolic links, and reparse points. Registration may narrow includes and add exclusions but cannot disable mandatory secret, boundary, workflow, target, and history gates.

Paths are repository-relative normalized POSIX names. Absolute paths, drive-qualified names, `..`, NULs, and paths escaping the canonical worktree are rejected. File reads use no-follow behavior where available and revalidate the opened object. Errors contain path and rule ID, never matched secret bytes.

### Secret gate and candidate quarantine

Secret scanning runs on the complete final candidate, including generated HANDOFF and manifest, before the candidate can become a parent in the normal checkpoint chain. It combines conservative filename rules, known credential/private-key patterns, and bounded high-entropy detection. SHA-1/SHA-256 identifiers, manifest hashes, ordinary source fixtures, and common legal content have explicit false-positive tests.

The upload gate scans the actual newly reachable local snapshot commits and blobs again. A suspicious unpublished candidate or chain is marked quarantined and is not assigned as the safe local head. A documented recovery command may, after explicit authorization, rebuild an unpublished clean chain from the last remote-verified safe SHA. It never rewrites published history or force-pushes.

If a suspicious commit is already remote-verified, the project pauses and reports `PUBLISHED_SENSITIVE_HISTORY`; removing a local file is not reported as removing the remote history.

### GitHub Actions gate

The workflow gate examines `.github/workflows` in the exact candidate snapshot, including uncommitted additions and changes. It also inspects the authorized remote's visible automation metadata when permissions allow. Any candidate workflow, unreviewable external automation, insufficient permission, or absence of evidence for safe isolation pauses unattended upload. Branch names and `[skip ci]` are never sufficient evidence.

The tool does not modify, omit, or disable source workflows to pass this gate. It does not claim knowledge about automation it cannot inspect.

### Target gate

Registration requires an explicit GitHub owner/repository and explicit push URL. It does not add or modify a source remote. It verifies private visibility, push permission, and owner/repository identity with non-interactive `gh` calls.

Before upload, the tool resolves the effective push URL under current Git configuration and rejects `insteadOf`, `pushInsteadOf`, or other rewriting that changes the destination identity. The only allowed ref is the stored exact ref:

`refs/heads/codex-sync/<device-id>/<worktree-id>`

Only `git push <explicit-url> <sha>:<exact-ref>` is permitted. There is no `--all`, `--mirror`, tag push, force push, or automatic conflict resolution.

## Queue and state machine

### Requests

Hooks and `request` add durable, atomically written queue entries. A request records workspace, public task status, source semantic hint, event evidence, and request reason. Repeated hooks and scheduler ticks deduplicate without treating timestamps as content.

### Pause and disable

Pause and disable are persisted under the workspace lock. The worker acquires the same lock before deciding to start an upload. Once pause is confirmed, no new upload starts. If a transfer already began before the lock transition, cancellation is best effort and the receipt reports the observable outcome; the tool never claims it can retract a completed remote update.

A Stop or SessionEnd from another session cannot resume a paused worktree. Natural-language instructions are not a background hard switch. The global protocol instructs Codex to call `pause` and wait for its persisted acknowledgement before handling content the user prohibited from upload.

`resume` reruns source, queue, security, workflow, target, and remote-state gates. Paused-period content is not implicitly authorized. The user must select one explicit action: reauthorize the pending semantic digest, abandon pending requests, or request a fresh capture.

### Upload intent and uncertain outcomes

Before `git push`, the worker atomically persists an upload intent containing:

- operation ID;
- normalized target identity and effective URL digest;
- exact remote ref;
- last remote-verified SHA or explicit absent state;
- expected snapshot SHA;
- local safe-parent SHA;
- creation and attempt counters.

Recovery compares the actual remote ref only with persisted explainable states:

1. If remote equals the previous verified SHA, retry the pending expected snapshot after current safety gates pass.
2. If remote equals the pending expected snapshot SHA, fetch/read and verify the handoff, manifest, and a normal source file, then complete the missing receipt without another push.
3. Any other remote value pauses as unexplained divergence. An unknown descendant is not accepted.

If a previously existing verified remote ref becomes absent, the project pauses as `REMOTE_REF_DELETED`; it is not silently recreated as a first upload.

Fault injection covers a remote updated while the client receives failure and a process exit after push but before receipt persistence. Recovery must reuse the same candidate/intent, preserve the queue, avoid duplicate commits, and avoid permanent false divergence.

Temporary transport failures retain the candidate and use bounded exponential backoff. Authentication, visibility, permission, policy, workflow, secret, and divergence failures persist a non-retrying pause and do not repeatedly open login UI.

## Hooks and lifecycle

User hooks are merged into `%CODEX_HOME%/hooks.json` without replacing unrelated hook entries. The managed entries are `Stop`, `Interrupt`, and `SessionEnd` command hooks with explicit absolute program and Python paths and event-appropriate one-to-three-second timeouts.

Each hook reads its actual JSON schema defensively. It does not assume every event has `turn_id`. It records only a short request and exits. `Stop` emits valid non-continuation JSON and never blocks or restarts reasoning. `Interrupt` and `SessionEnd` emit no steering output. No hook waits for network activity.

Hooks remain subject to Codex's user trust review. Installation reports installed, pending trust, or verified trigger separately. It never uses a trust bypass.

## Scheduler

The Windows installer registers one current-user task with no elevation and no new stored credential. The action uses absolute bundled-Python and installed-script paths and supplies explicit CODEX_HOME, runtime-data, Git, gh, and non-interactive environment through the wrapper. It does not depend on an interactive PATH.

The task runs every ten minutes and invokes `tick`. Installation verification distinguishes task definition, last actual Task Scheduler execution, exit code, user identity, action paths, and environment probe. A manual `tick` is not evidence that Task Scheduler ran.

Unsupported platforms retain the portable `tick` command but are reported unverified rather than claimed installed.

## Global protocol

The installer adds one short owned segment to the active global AGENTS file. It instructs Codex to:

- check `status` once at task start when the current worktree is registered;
- execute and confirm `pause` before processing content the user forbids from upload;
- keep task state separate from lifecycle events;
- after all source and task-status writes, run bounded `sync-now` for completion, blocker, failed tests, or review wait;
- report `PENDING` or `UNKNOWN` when local/remote verification does not finish within the total timeout;
- never continue reasoning merely because synchronization failed;
- preserve all project approval and hard-stop rules.

The protocol does not claim natural-language recognition is an instantaneous background kill switch.

## CLI contracts

- `install`: owned program, protocol, hooks, and scheduler installation plus component-specific verification.
- `register`: explicit source worktree, private GitHub identity, explicit push URL, and policy registration; never edits source remotes.
- `status`: model-free local state, queue, pause, install, hook trust/trigger, scheduler, local checkpoint, and remote verification state.
- `request`: durable public task-state update and sync request.
- `sync-now`: bounded capture and upload attempt; reports `LOCAL_ONLY`, `PENDING`, `REMOTE_VERIFIED`, `NEEDS_SETUP`, or policy error precisely.
- `tick`: scheduler worker over enabled registered projects with substantive-change deduplication.
- `pause`: persisted worktree upload stop acknowledgement.
- `resume`: gate reevaluation plus explicit pending policy choice.
- `disable`: persistent worker disablement without deleting project data.
- `uninstall`: removes only unchanged managed installation components and retains user data.

## Installation verification

The current Windows installation report must separate:

1. Installed program and protocol availability.
2. Hooks installed, trust state, CLI trigger result, and Codex App trigger result.
3. Scheduled task definition and evidence of an actual Task Scheduler run.
4. Scheduler interpreter, Git, gh, environment, and Windows user identity.

CLI simulation does not count as Codex App hook verification. If the app cannot complete hook trust during this task, the exact `/hooks` step remains manual and the trigger is `NOT_VERIFIED`.

## Test and acceptance matrix

Tests use temporary source repositories, independent snapshot stores, and local bare remotes. They must cover:

1. Complete, blocker, failed-test, review-wait, interrupt, and unknown lifecycle/task states.
2. Semantic deduplication despite capture, retry, hook, and heartbeat timestamps.
3. Staged, unstaged, staged-plus-unstaged, untracked, deleted, executable, CRLF, Chinese, and space-containing paths.
4. Source HEAD, index bytes, status classification, files, and branch unchanged; source Git commands use optional-lock suppression and never write index/objects.
5. Sudden task-process exit followed by an independent `tick`.
6. Temporary push failure, response loss after remote update, process exit after push, and correct intent recovery.
7. Tracked secrets, generated-metadata secrets, secret in unpublished reachable history, quarantine recovery, and published-sensitive reporting.
8. Legitimate SHAs, hashes, common source, and manifests do not disable or broadly bypass secret scanning.
9. Symlinks, Windows reparse points, submodules, LFS, large files, unsupported data, and unmerged index.
10. Multiple worktrees, descendant hook cwd resolution, duplicate hooks, duplicate scheduler ticks, and lock coordination.
11. Public, unauthorized, unregistered, paused, disabled, workflow-changing, permission-insufficient, rewritten-URL, and divergent targets.
12. Previously verified remote deletion is not treated as first upload.
13. Source HEAD, semantic digest, snapshot SHA, and remote verified SHA remain distinct.
14. Old completion/test evidence becomes stale after source change.
15. Pause/resume choices reauthorize, abandon, or recapture pending work explicitly.
16. Idempotent install and ownership-safe uninstall preserve unrelated hooks, AGENTS content, configuration, and runtime data.
17. Windows task definition uses least privilege and absolute paths; an actual scheduler run records interpreter, Git, gh, environment, and identity.

The full existing repository suite remains a regression gate.

## Current delivery outcome rules

Because no private GitHub target is authorized, the current delivery may complete local implementation, local bare-remote integration, and safe user-level installation. Its final status must be separated as:

- `IMPLEMENTATION`: test evidence for code and local integrations.
- `INSTALLATION`: actual status of program, global protocol, hooks, trust/trigger, and scheduler.
- `GITHUB`: `NEEDS_SETUP` unless a later explicit private target is supplied and verified.
- `CHATGPT_ACCESS`: `NOT_VERIFIED` unless the web product actually reads files at the verified SHA.

No missing remote target may be converted into a success claim, guessed target, repository scan, login loop, or expanded authorization.

After this delivery unit, work stops for review. No later Optimize phase starts automatically.
