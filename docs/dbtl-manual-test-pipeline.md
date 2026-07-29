# Manual DBTL testing runbook

The DBTL manual pipeline lets a developer run an expensive Design meeting once,
capture a quiet human-decision boundary, and restore that state later to test
recent UI and workflow changes through the real Gateway and frontend.

It is deliberately a local development tool. It does not add a DBTL bypass,
test endpoint, or stage-skipping switch to the product.

Use this guide when you want to:

- validate a recent DBTL implementation without rerunning already-tested work;
- reuse output from a slow or expensive LLM step;
- repeat a human review action from exactly the same starting state; or
- run one final live-model smoke test before merging.

## Quick reference

Run all commands from the repository root.

| Goal | Command |
| --- | --- |
| Create the isolated profile | `make dbtl-manual-init` |
| Update it after normal config changes | `make dbtl-manual-refresh` |
| Start the isolated application | `make dbtl-manual-dev` |
| Capture the current quiet state | `make dbtl-manual-capture SCENARIO=<name>` |
| List saved states and resumable URLs | `make dbtl-manual-list` |
| Stop the application | `make stop` |
| Restore a saved state | `make dbtl-manual-restore SCENARIO=<name>` |

The normal daily loop is:

```bash
# 1. Restore the boundary immediately before the behavior under test.
make stop
make dbtl-manual-restore SCENARIO=awaiting-review

# 2. Start the real Gateway, frontend, and proxy against the restored state.
make dbtl-manual-dev

# 3. Open the URL printed by restore and perform the focused manual check.
```

`make dbtl-manual-dev` stays attached to the terminal. Keep it running while
testing and press Ctrl+C when finished. You can run capture/list commands from a
second terminal.

## Before each test session

1. Confirm the branch and commit you intend to test.
2. Run `make dbtl-manual-list` and choose the nearest quiet boundary before the
   implementation under test.
3. Decide whether the saved scenario is still compatible with the current
   database, artifact, and workflow contracts.
4. Restore the scenario while DeerFlow is stopped.
5. Start with `make dbtl-manual-dev`.
6. Open the URL printed by restore, or open
   [http://localhost:2026](http://localhost:2026).
7. Test only the behavior you changed plus its immediate downstream effects.
8. Record the commit, scenario, expected result, actual result, and evidence.
9. Before merge or release, run one uncached end-to-end smoke test through every
   changed stage.

## What one checkpoint contains

A checkpoint keeps these two authorities together:

- the isolated profile's unified SQLite database, including threads, run
  history, DBTL revisions, evidence rows, reviews, and registered feedback
  surfaces; and
- the isolated human-visible project tree, including the exact Markdown, JSON,
  data, and HTML deck bytes referenced by those rows.

The pair matters. A feedback action binds the project, cycle, originating
conversation, database revision, evidence artifact/hash, and deck hash. Copying
only a worker-result JSON or only `deerflow.db` creates a state that the real
application should reject.

Everything is stored under the gitignored directory:

```text
.deer-flow/manual-dbtl/
├── config.yaml
├── live/
│   ├── db/deerflow.db
│   └── projects/
├── scenarios/<name>/
│   ├── deerflow.db
│   ├── projects/
│   └── manifest.json
├── scenario-backups/
└── restore-backups/
```

Scenario folders can contain research inputs and conversation history. Do not
commit or share them as ordinary test fixtures.

## First-time setup

Create the isolated profile:

```bash
make dbtl-manual-init
```

The generated config inherits the normal model and sandbox configuration but:

- uses its own unified SQLite database and project root;
- enables `dbtl.mode=graph_enabled` and Design deck feedback;
- disables memory extraction, scheduled tasks, run-ownership heartbeats,
  channel connections, and configured IM channel workers; and
- removes a legacy standalone checkpointer so all state being tested stays in
  the captured database.

If the normal `config.yaml` model or sandbox settings change, regenerate the
profile:

```bash
make dbtl-manual-refresh
```

Start the full application with the isolated profile:

```bash
make dbtl-manual-dev
```

On the first launch:

1. Create an admin account at
   [http://localhost:2026/setup](http://localhost:2026/setup). This account
   exists only in the isolated manual database.
2. Create or adopt a project inside this isolated instance.
3. Add the smallest representative input data needed by the flow.
4. Run the DBTL flow normally through the slow step you want to reuse.
5. Wait until the run is complete or the application is waiting for human
   input.
6. Capture that state from a second terminal.

Create a cheap setup checkpoint after the admin account and project exist:

```bash
make dbtl-manual-capture SCENARIO=project-ready
```

Then create checkpoints after expensive boundaries, such as:

```bash
make dbtl-manual-capture SCENARIO=design-complete
make dbtl-manual-capture SCENARIO=awaiting-review
```

## Capture useful decision boundaries

Capture only after the run has finished or while DeerFlow is waiting for a
person. Capture refuses pending/running agent runs, queued/running scheduled
runs, and in-flight Design feedback actions.

Recommended starting scenarios:

| Scenario | Capture when | What it makes cheap |
| --- | --- | --- |
| `project-ready` | Admin and representative project are configured | Starting a fresh cycle without repeating setup |
| `design-preflight` | The meeting setup card is waiting for confirmation | Participant/depth configuration |
| `chair-choice` | A chair is paused with structured options | Option/comment submission and same-thread resume |
| `chair-text` | A chair is paused on the free-text fallback | Exact-question response handling |
| `design-complete` | The Design package and review deck exist but are not submitted | “Submit for review” |
| `awaiting-review` | Design is awaiting a verdict | Approve, Request changes, and Reject |
| `consumed` | A deck action has a durable receipt | Disabled controls and replayed reads |
| `stale-surface` | A later round has superseded the open deck | Read-only stale handling and newest-deck link |

For example:

```bash
make dbtl-manual-capture SCENARIO=chair-choice
make dbtl-manual-capture SCENARIO=awaiting-review
```

Capture uses SQLite's backup API, copies symlinks as symlinks, records the Git
commit and Alembic revision, hashes the database and project tree, and writes
the resumable project URL into `manifest.json`.

Use names that describe the state before the next action, not the feature branch
or the result you expect. For example, `awaiting-review` remains useful across
many implementations; `my-new-button-test` does not.

To intentionally replace a scenario, the old copy is archived first:

```bash
make dbtl-manual-capture SCENARIO=chair-choice REPLACE=1
```

List available checkpoints:

```bash
make dbtl-manual-list
```

## Restore and focus on the recent behavior

Stop DeerFlow before replacing the isolated live state:

```bash
make stop
make dbtl-manual-restore SCENARIO=awaiting-review
make dbtl-manual-dev
```

Restore verifies the scenario database and project-tree hashes plus SQLite
integrity before changing the live profile. It backs up the previous isolated
live database and project tree under `restore-backups/`, swaps the restored pair
together, and prints the conversation URL to open. `make dbtl-manual-dev` holds
an OS-level runtime lock for the life of the stack; restore takes that same lock
and fails closed while the stack is running, even when the caller cannot observe
the Gateway socket from its network namespace.

The tool never reads from or writes to the normal database/project root after
the profile is generated. Restore operates only under
`.deer-flow/manual-dbtl/live`.

## Recommended test sequence

For each implementation, test in increasing scope:

1. **Focused replay:** Restore the closest checkpoint and test only the changed
   action.
2. **Immediate continuation:** Continue through the next gate or stage to catch
   state-transition and routing problems.
3. **Refresh/reopen:** Reload the browser and reopen the conversation to verify
   the result is durable rather than client-only.
4. **Duplicate/retry behavior:** Repeat the action when appropriate and confirm
   idempotency, disabled controls, or a clear refusal.
5. **Live end-to-end smoke test:** Start from `project-ready` or a new project
   and run every changed stage without restoring an intermediate checkpoint.

The checkpoint run validates recent behavior from a known state. The uncached
smoke test validates that earlier stages can still produce that state.

## Suggested manual checks for deck input

### Paused chair

1. Restore `chair-choice`.
2. Open the printed originating-conversation URL.
3. Open the deck and navigate to “Needs your decision.”
4. Verify the deck is inert until the authenticated parent handshake completes.
5. Choose one option, add a comment, and submit.
6. Verify one receipt appears and controls stay disabled.
7. Verify the chair resumes in the originating conversation, not whichever
   conversation is currently active.

This last step still invokes a model. Use a recorded model replay when testing
the resume repeatedly without cost; the checkpoint eliminates all work before
the click.

### Submit and verdict

1. Restore `design-complete` and verify “Submit for review” is the only gate
   action.
2. Submit, then capture or continue to the `awaiting-review` state.
3. Restore `awaiting-review` separately for each verdict.
4. Verify Approve accepts an optional comment.
5. Verify Request changes requires an issue selection or actionable comment and
   begins a focused refinement in the originating conversation.
6. Verify Reject requires a comment and starts no worker run.

### Refusal states

Use captured or frontend-fixture scenarios for:

- wrong conversation/project;
- changed deck bytes;
- stale database or evidence revision;
- superseded surface;
- already-consumed action; and
- retry with the same versus a changed client submission ID.

## Boundaries

- Checkpoints are local acceleration aids, not evidence that the skipped LLM
  stage still works on the current commit.
- Run at least one live-model end-to-end smoke test before merging or releasing
  DBTL orchestration changes.
- Regenerate a scenario after an incompatible schema, artifact, or workflow
  contract change. Startup may migrate an older SQLite checkpoint forward, but
  a newer checkpoint is not expected to run on an older branch.
- Do not capture an active run. A quiescent human-input boundary is the unit of
  reuse.
- For frontend-only bridge and accessibility iteration, the existing mocked
  Playwright deck scenarios are faster. For orchestration after a human action,
  use the existing `ReplayChatModel` path described in
  `backend/docs/REPLAY_E2E.md`.

## Troubleshooting

### Start says the manual stack is already running

Open [http://localhost:2026](http://localhost:2026); a second copy is not
needed. If the app is unavailable, return to the terminal that launched
`make dbtl-manual-dev`, press Ctrl+C, and retry. A job suspended with Ctrl+Z
still owns the safety lock; resume it with `fg`, then stop it with Ctrl+C.

### Restore says the manual stack is running

This is the restore safety lock working. Stop the stack, then retry:

```bash
make stop
make dbtl-manual-restore SCENARIO=<name>
```

Do not delete `runtime.lock` to bypass the refusal.

### Capture says a run or action is active

Wait for the run to finish, cancel it through the normal product flow, or move
to a human-input boundary. Never copy the SQLite file manually while work is in
flight.

### The browser returns to setup after restore

The restored checkpoint was captured before the isolated admin account existed.
Complete setup again, or restore/capture a `project-ready` scenario after setup.

### A scenario fails integrity or no longer works

Do not edit files inside `scenarios/<name>/`. Recreate the scenario from a
compatible branch when database migrations, artifact formats, stage specs, or
feedback contracts have changed.

### Ports 8001, 3000, or 2026 are already in use

Run `make stop`, verify no other DeerFlow worktree is intentionally using those
ports, and start again with `make dbtl-manual-dev`.

### Normal model or sandbox settings changed

Refresh only the generated isolated configuration:

```bash
make dbtl-manual-refresh
```

This does not intentionally replace the isolated live database or saved
scenarios.

## Test record template

Copy this block into an issue, pull request, or lab notebook:

```markdown
### Manual DBTL test

- Date:
- Tester:
- Branch / commit:
- Scenario restored:
- Area under test:
- Expected:
- Actual:
- Result: PASS / FAIL / BLOCKED
- Evidence (screenshot, deck, log, or URL):
- Live-model end-to-end smoke test: PASS / FAIL / NOT RUN
- Notes:
```
