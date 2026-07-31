# DBTL stage receipts and progressive chat reporting

**Status:** Proposed enhancement plan. No implementation is authorized by this
document.

**Date:** 2026-07-31

**Scope:** Make human gate transitions and long-running Build/Test/Learn work
visible, durable, and understandable in project chat without weakening the
Supervisor's ownership of cycle state, stage contracts, evidence binding, or
human review. Reuse the Lead Agent execution engine only if the existing stage
worker remains materially weaker after the presentation path is fixed.

## 1. Outcome

After a person approves Design, DeerFlow should immediately show a deterministic
receipt in the originating conversation:

> Design approved. Build is now unlocked. Data Reconciliation was skipped.

When the person then asks Build to run, one stage card should appear beneath
that request and report bounded, evidence-derived progress such as:

1. Reading the approved Design;
2. inspecting bound workspace inputs;
3. writing the analysis implementation;
4. executing the analysis;
5. running diagnostics and validity preparation;
6. saving versioned outputs;
7. recording input lineage and the Build package; and
8. waiting for human Build review.

The card must survive a hard refresh, distinguish progress from completion,
and always end in one visible terminal state: package ready, needs input,
failed, or cancelled. A successful parent run with an empty chat response is
not an acceptable terminal state.

## 2. Evidence and current behavior

The isolated manual scenario `design-approve` captures the intended boundary:

- cycle `cycle-dd44c546-d397-4cdf-b1b4-263d53b2f90d`;
- Design approved;
- cycle state `ready_for_build`;
- Data Reconciliation locked and skipped;
- Build `in_progress`; and
- no Build worker run yet.

The manual pass exposed two distinct presentation gaps:

1. A deck-backed approval changes durable DBTL state but does not create a chat
   run, so the rail updates while the conversation says nothing.
2. `LiveStageAdapter` dispatches its worker directly through the internal
   executor. The worker is intentionally `nostream`; task lifecycle events may
   exist, but there is no durable stage-run envelope anchored beneath the human
   request. The user therefore sees only `Working…` and cannot tell whether the
   system is reading, writing, executing, packaging, or stalled.

Commit `bbfa044c` corrected two adjacent defects: ordinary Build work is no
longer labelled as a Design meeting, and the executable Build/Test specs have
enough bounded turns to run rather than finalize immediately after reading
their inputs. Those corrections do not by themselves create a durable chat
receipt or a refresh-safe stage progress card.

## 3. Architectural decision

The Supervisor remains the workflow authority. Do **not** route Build back to
the ordinary Lead Agent branch merely to obtain a familiar progress UI.

```text
human request
    ↓
Supervisor — selects and verifies the project-owned cycle/stage
    ↓
stage boundary — approved Design, StageSpec, idempotency, evidence scope
    ↓
stage executor — existing worker first; Lead Agent engine only if later needed
    ↓
stage adapter — validates structured results and records lineage/artifacts
    ↓
human review — the only authority that advances the gate
```

The missing abstraction is a **stage presentation protocol**, not another
workflow owner. It translates authoritative transition and execution events
into a durable chat surface without exposing internal prompts, reasoning, raw
tool output, or unvalidated worker prose.

### 3.1 Why not move directly to the Lead Agent

The ordinary Lead Agent path already streams useful tool progress, but a full
handoff would introduce avoidable risks:

- two owners for checkpoint and run lifecycle;
- duplicate or reordered stream events;
- ambiguity about which component handles clarification and cancellation;
- ordinary work escaping the selected cycle or approved stage contract;
- outputs that are visible in chat but never bound into Build lineage; and
- an apparent completion that did not satisfy the stage write boundary.

If Lead Agent execution is later adopted, it must run **inside** the stage
boundary. The Supervisor and adapter continue to own scope, idempotency,
validation, persistence, and gates.

## 4. Phase 1 — durable transition receipts

### 4.1 Behavior

Every successful human gate action that changes the current path should produce
one server-authored receipt in the originating conversation. It should name:

- the decision recorded;
- the stage that concluded;
- the next unlocked or selected stage;
- whether Reconciliation was required or skipped;
- the durable transition/path-record id; and
- the next human action.

Approval does not automatically start Build. The receipt says that Build is
ready and leaves execution under an explicit chat request. A future combined
`Approve and start Build` action would be a separate, explicitly reviewed
product decision.

### 4.2 Persistence and delivery

Use the recorded DBTL transition as the authority; do not ask a model to
paraphrase it. Reuse the existing server-owned conversation-delivery path used
by DBTL round watchers rather than synthesizing a fake user or assistant run.
Delivery must be idempotent on the transition record id.

On reload, the receipt must be reconstructed from durable state or retained in
durable thread history. A client-only toast is useful as secondary feedback but
does not satisfy this phase.

### 4.3 Acceptance criteria

- Approving `design-approve` immediately shows a receipt without a model call.
- The receipt says `Build unlocked` and `Data Reconciliation skipped` when
  `dbtl.reconciliation_required=false`.
- Refreshing and reopening the conversation shows the same receipt once.
- Replaying the consumed approval produces the existing receipt, not a second
  transition or duplicate message.
- A failed/conflicted decision never produces a success receipt.

## 5. Phase 2 — stage execution envelope and progress card

### 5.1 Backend event contract

Introduce a versioned, additive stage event envelope. Suggested shape:

```json
{
  "version": 1,
  "type": "dbtl_stage_progress",
  "run_id": "run-…",
  "project_id": "project-…",
  "cycle_id": "cycle-…",
  "stage": "build",
  "stage_attempt_id": "stage-…",
  "sequence": 4,
  "phase": "executing",
  "status": "in_progress",
  "label": "Running the approved analysis"
}
```

Required event types are:

- `dbtl_stage_started`;
- `dbtl_stage_progress`; and
- `dbtl_stage_finished`.

`phase` is a bounded server-owned enum, not model-authored free text. Initial
Build phases:

- `preparing`;
- `reading_design`;
- `inspecting_inputs`;
- `writing_implementation`;
- `executing`;
- `running_diagnostics`;
- `saving_outputs`;
- `recording_lineage`;
- `packaging`; and
- `awaiting_review`.

Test may reuse the common phases and add bounded validity phases such as
`checking_split`, `checking_leakage`, and `computing_outcome`.

Events may be inferred from validated tool lifecycle and explicit adapter
boundaries. They must not expose chain-of-thought, hidden prompts, credentials,
raw shell output, or arbitrary file contents. When no finer evidence exists,
the card should stay on the last known phase rather than invent activity.

### 5.2 Ordering and idempotency

Events carry a monotonically increasing `sequence` within `(run_id,
stage_attempt_id)`. Consumers ignore duplicates and do not regress to an older
phase. Terminal state is append-only for that run.

Persist the envelope through the existing run-event journal so an SSE reconnect
or hard refresh can rebuild the same card. The stage attempt and run id must be
present on every event; inferring either from the currently selected rail row is
unsafe when multiple conversations or cycles are active.

### 5.3 Frontend surface

Create one stage execution card anchored to the human message that initiated
the run. It should show:

- `Build`, `Test`, or `Learn` as the title;
- the current bounded phase and elapsed time;
- completed phases with checkmarks;
- a concise, sanitized current operation;
- terminal status and the review artifact when available; and
- an expandable link to validated worker steps for debugging.

Ordinary stage execution is not a meeting. It must not render the participant,
debate, consensus, or chair UI. Actual stage review meetings continue using
their meeting surface and stage-specific title.

### 5.4 Acceptance criteria

- The Build card appears before the first long-running tool finishes.
- At least one visible progress update occurs during a multi-step Build.
- Hard-refreshing mid-Build reconstructs the card from persisted events.
- Another conversation in the same project cannot attach to the run's card.
- Cancellation ends the card as cancelled and preserves the last completed
  phase.
- Retry creates a new run card while retaining the failed/cancelled audit card.
- Internal `nostream` messages remain absent from parent conversation history.

## 6. Phase 3 — guaranteed terminal chat response

Every stage branch must finish with a visible, bounded response derived from
the recorded outcome:

- **ready for review** — names and links the exact stage package;
- **needs input** — asks one focused question and preserves the current stage;
- **failed** — names the failed phase and a retry action;
- **cancelled** — confirms no gate advanced; or
- **replayed** — points to the already-recorded result.

The response may be server-authored. A model is unnecessary when the adapter
already has the structured `LiveStageResult`, artifact URI/hash, and durable
worker status. A parent run that ends `success` with an empty
`last_ai_message` must be treated as a presentation failure and repaired by the
same fail-soft delivery path used for a missing successor surface.

### Acceptance criteria

- No DBTL stage run leaves only a `Working…` indicator after termination.
- The final response and stage card agree on status and artifact binding.
- A refresh shows the same terminal response and card.
- `needs_input` retains the question and accepts one retry without convening a
  Design meeting or starting a second cycle.

## 7. Phase 4 — optional Lead Agent execution inside the stage

Do not begin this phase unless manual and telemetry evidence after Phases 1–3
shows that the existing stage worker remains materially worse at implementation
or tool use.

If required, extract or reuse the Lead Agent's execution engine under a
stage-contained adapter with these invariants:

- the Supervisor resolves project, cycle, and stage before invocation;
- the approved Design and allowed workspace scope are immutable inputs;
- the stage invocation has its own bounded run identity;
- clarification returns through the stage result rather than ordinary chat
  routing;
- only validated/sanitized progress enters the stage event envelope;
- outputs do not satisfy Build until `record_build_lineage` commits; and
- the Lead Agent cannot submit, approve, or route the human gate.

This phase is complete only when cancellation, replay, checkpoint recovery, and
duplicate event suppression are proven. Reusing the full interactive graph
without those constraints is explicitly out of scope.

## 8. Test plan

### 8.1 Backend

- Unit-test transition receipt rendering for required and skipped
  Reconciliation.
- Pin the stage event schema, phase enum, ordering, deduplication, and terminal
  immutability.
- Verify tool events map only to allowlisted phase/label data.
- Verify all terminal `LiveStageResult` variants deliver a response.
- Verify replay emits the recorded terminal result without redispatch.
- Verify no internal principal can turn a progress or receipt endpoint into a
  gate decision.

### 8.2 Frontend

- Render every stage, phase, and terminal state.
- Prove ordinary Build work never renders as a Design meeting.
- Rehydrate a card from run events after remount.
- Ignore out-of-order and duplicate sequences.
- Keep two concurrent conversations and cycle ids isolated.
- Preserve accessibility: textual status, progressbar values, focusable details,
  and no reliance on colour alone.

### 8.3 Manual acceptance using `design-approve`

1. Restore the scenario:

   ```bash
   make dbtl-manual-restore-hot SCENARIO=design-approve
   ```

2. Hard-refresh and open cycle `cycle-dd44c546-d397-4cdf-b1b4-263d53b2f90d`.
3. Verify the approved Design state and skipped Reconciliation.
4. Confirm the transition receipt is visible exactly once.
5. Request the approved Build.
6. Verify a Build card appears immediately and advances through real phases.
7. Hard-refresh while Build is running and verify progress recovers.
8. Let Build finish and verify the card and final response point to the same
   Build package.
9. Retry or replay once and verify no duplicate worker dispatch or lineage.
10. Approve Build and repeat the progress/reload checks for Test.

Capture quiet checkpoints after the receipt and after Build completion only
after their reload/idempotency checks pass.

## 9. Rollout and observability

Ship Phases 1–3 behind one default-off presentation flag during the manual
window, while retaining the current rail and stage sheets as authoritative
fallbacks. Record bounded telemetry for:

- transition receipt delivery/replay/failure;
- time from run admission to first stage card;
- number and spacing of progress phases;
- refresh rehydration success;
- terminal response delivery;
- empty-success repair; and
- stage runs that still require a second execution request.

The flag may default on after the `design-approve` Build/Test walkthrough and
one uncached end-to-end cycle pass. The flag controls presentation only; it must
not change cycle state, stage routing, worker budgets, evidence contracts, or
human authority.

## 10. Non-goals

- Automatically starting Build merely because Design was approved.
- Streaming chain-of-thought or raw internal worker messages.
- Turning progress labels into scientific evidence.
- Allowing a Lead Agent or stage worker to satisfy a human gate.
- Replacing the review deck/sheet with the progress card.
- Reintroducing Data Reconciliation as a prerequisite when the deployment has
  explicitly made it optional.

## 11. Owner decisions before implementation

1. Confirm that approval should show a receipt but should **not** automatically
   start Build.
2. Confirm that the stage card shows bounded operational phases, not natural-
   language agent narration.
3. Confirm whether receipts belong permanently in thread history or are
   reconstructed from transition activity on reload; both must look durable to
   the user, but the persistence and export semantics differ.
4. Confirm that Lead Agent execution remains deferred until Phases 1–3 are
   evaluated manually.

## 12. 2026-07-31 Test-gate correction (implemented)

Manual testing exposed a separate authority gap: an unscoped follow-up went to
the Lead Agent, which could write convincing Test reports and say PASS while the
durable Test attempt remained in progress. The right-side sheet also offered a
second, high-friction input path that required people to transcribe hashes,
metrics, and checks.

The correction is intentionally chat-first:

- Test evidence is reviewable only when a stage worker returns typed metrics
  and every check in the server-pinned validity pack; prose and arbitrary files
  cannot satisfy this contract.
- The server owns the optional-Reconciliation Build-lineage check and computes
  the outcome deterministically.
- The originating conversation renders a meeting-choice card, followed by an
  outcome-compatible route card. Each card embeds the server-owned cycle and
  evidence snapshot; the answer turn therefore survives loss of the one-shot
  composer scope.
- A meeting reads and annotates the core Test evidence, then returns to the same
  route card. It cannot rewrite the computed outcome.
- The generic stage sheet is read-only evidence/audit UI. It no longer mounts
  generic evidence, blocker, submission, or verdict inputs; specialized
  Reconciliation/Learn controls remain a later chat migration.
- The exact historical Build-approved/Test-active cursor mismatch is projected
  as Test and repaired transactionally when the human outcome is recorded.

Acceptance is pinned by `test_dbtl_test_chat_review.py`, the DBTL backend suite,
and the stage-sheet DOM suite. The next manual checkpoint should be captured
only after both the optional meeting route and direct outcome route survive a
hard refresh.
