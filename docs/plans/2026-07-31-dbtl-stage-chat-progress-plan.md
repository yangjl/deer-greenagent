# DBTL stage receipts and progressive chat reporting

**Status:** Revised enhancement plan. Implemented on the current branch: the
deterministic post-approval Start/Hold handoff prototype (see §4.4 for the
remaining authority and idempotency gaps), the chat-first Test-gate
correction (§14), the background round-failure watcher
(`backend/app/gateway/dbtl_round_watch.py`), and the
transition-backed audit timeline (`frontend/src/core/dbtl/timeline.ts`).

**Also implemented (2026-08-01), closing §4.4 gap 6 and the §3.1 free-text
escape:** handoff-card delivery is now verified rather than assumed, and an
unanswered control is intercepted before ordinary routing. See §4.5.

Still proposed: the durable `dbtl_stage_handoffs` outbox (§4.2, §4.4 gaps 1-5),
the full `GovernedStageIntent` step with its six outcomes (Phase 2), the
execution fence (§3.2), the durable stage progress envelope (Phase 3), the
guaranteed terminal receipt (Phase 4), and any Lead-engine reuse (Phase 5).
This document does not authorize further implementation.

**Date:** 2026-07-31

**Revised from observed threads:**
`28c9301b-353e-425f-8f3d-7d86c902e295` in project `test3`, plus the
2026-08-01 isolated `design-approved` manual scenario described in §2.3.

**Scope:** Make human gate transitions and long-running Build/Test/Learn work
visible, durable, and understandable in project chat while making it
structurally impossible for the ordinary Lead Agent to take over a governed
workflow, write into a stage-owned output tree, or report a stage transition
that the DBTL repository did not record.

## 1. Outcome

After a person approves Design in its authenticated feedback deck—or records an
equivalent server-bound verdict for a later stage—DeerFlow should show one
deterministic handoff card in the originating conversation:

> **Design approved**
>
> Build is ready. Data Reconciliation was skipped by project policy.
>
> **Start Build** · **Hold here**

That card is both the compact transition receipt and the next-action control.
It is checkpointed, bound to the project/cycle/stage transition, and rendered
without a model call. Approval opens the next stage but never starts it.

After **Start Build**, one stage card reports no more than five human-scale
phases:

1. Preparing;
2. Building;
3. Verifying;
4. Recording evidence; and
5. Ready for review.

Successful checks are coalesced into the phase result. Only failed or blocked
checks expand in chat. Complete scientific diagnostics remain in the review
package and audit inspector, not as a stream of conversational validation
messages.

Every governed stage ends in exactly one visible terminal state: ready for
review, needs input, failed, cancelled, or replayed. The terminal response and
the durable DBTL repository must agree. Plain Lead Agent prose is never a stage
receipt.

## 2. Evidence and corrected problem statement

### 2.1 Earlier manual evidence

The isolated `design-approve` scenario exposed two presentation gaps:

1. a deck-backed approval changed durable DBTL state while project chat said
   nothing; and
2. `LiveStageAdapter` dispatched a `nostream` worker without a durable,
   refresh-safe stage envelope, leaving only `Working…` until completion.

Those remain valid gaps, but the `test3` thread exposed a more serious authority
failure that this plan previously treated only as a future risk.

### 2.2 Observed Lead Agent takeover

In thread `28c9301b-353e-425f-8f3d-7d86c902e295`:

- governed DBTL created one cycle, recorded the Design workers, approved
  Design, and recorded the optional-Reconciliation skip;
- durable state then remained `ready_for_build` with Build `in_progress` and
  Test `locked`;
- the unscoped message “now, start to build following the approved design”
  went through ordinary Lead Agent execution;
- that run created an ungoverned simulation package outside the stage adapter;
- “approve to build” caused another ordinary run to copy those files into the
  cycle's governed-looking `outputs/dbtl/.../build/` directory;
- “now, shall we move to Test?” received an affirmative prose answer despite
  no Build review or transition; and
- “yes, go ahead” started an ordinary validation script while durable Test was
  still locked.

The four post-Design turns used 35 model calls and roughly 684,000 tokens. The
thread journal contains 26 tool-result messages and repeated file inspection,
script repair, and ad hoc validation, but no corresponding durable Build worker
record, Build lineage, Build approval, or Test transition.

The primary defect is therefore not “too little progress reporting” or “too
many DBTL validations.” It is an **ownership escape**:

```text
human stage intent
    -> cycle scope lost
    -> ordinary Lead Agent
    -> plausible files and stage language
    -> no governed stage event
    -> chat and durable state disagree
```

Any plan that adds better-looking progress without closing that escape would
make the false workflow more convincing.

### 2.3 Generated-but-undelivered handoff regression (2026-08-01)

The isolated `design-approved` scenario exposed a narrower delivery drift after
the execution fence had already prevented the worst filesystem escape:

- Design approval committed successfully and advanced the cycle to
  `ready_for_build`, with Build `in_progress`;
- the approval action recorded `review_recorded`, `handoff_status=started`, and
  the hidden handoff run id;
- the hidden run completed successfully without an LLM call and its final graph
  output contained the expected `dbtl-stage-handoff__...` card, including
  **Start Build** and **Hold here**;
- that run emitted only lifecycle/delivery journal events, not the assistant and
  tool-message events consumed by `GET /api/threads/{id}/messages/page`, so the
  card was absent both live and after refresh; and
- the owner's subsequent free text, “go ahead with build,” reached the ordinary
  Lead Agent. It spent eight model calls attempting the work before the
  stage-owned-path fence blocked it. No governed Build evidence was recorded.

This is not a card-construction failure: the deterministic Supervisor output
existed. It is a **projection/delivery failure** between successful hidden-run
state and the durable thread event feed, followed by the already-known routing
escape for unscoped free text.

Further polishing must address both layers. A successful handoff is not
`presented` until its card messages are durably visible through the canonical
thread-history endpoint. Independently, before invoking the Lead Agent, routing
must load authoritative thread-bound DBTL state. When one pending handoff makes
the intent unambiguous, it re-presents that control instead of starting ordinary
work. If ordinary work is still appropriate, the Lead Agent receives the active
cycle, stage, pending control, and allowed actions in its initial context so it
can explain the boundary before attempting tools. That awareness never grants
the Lead Agent authority to start, approve, or record a governed stage.

## 3. Authority model

The Supervisor remains the single workflow authority.

```text
deck verdict or stage-control reply
    -> authenticated transition / server-emitted card
    -> Supervisor resolves project, cycle, and active stage
    -> stage boundary admits one idempotent execution
    -> stage worker executes the versioned StageSpec
    -> adapter validates and records evidence/lineage
    -> deterministic chat projection reports repository state
    -> human review is the only gate authority
```

Three enforcement layers are required. No one layer is sufficient by itself.

### 3.1 Routing fence: control intent never reaches ordinary chat

The Supervisor must intercept a request before the ordinary branch when it is
bound to any of the following server-owned evidence:

- a post-approval Start/Hold card;
- a Test meeting/outcome card;
- a Design clarification/preflight card;
- a registered feedback surface in the originating thread; or
- a thread-bound active stage handoff.

Card answers recover their cycle from the card that the server emitted. Client
composer scope is only a convenience and cannot be required for correctness.
An echoed option value is untrusted; the selected option id resolves against
the emitted card.

Free text that names a stage-control action, such as “start Build,” “approve
Build,” or “move to Test,” must not directly execute a stage. When it clearly
refers to one unambiguous thread-bound cycle, the server returns the current
deterministic handoff/review card. When more than one cycle is possible, it asks
the person to choose. A bare assent such as “yes, go ahead” is stage control
only when it answers or immediately follows a server-authored control. When no
card or transition authorizes a stage action, the server gives a no-write status
receipt. It never asks the Lead Agent to guess workflow authority.

This fence must be deterministic and state-backed. An LLM classifier may help
interpret ordinary research language, but it may not decide whether a control
action escapes DBTL governance.

### 3.2 Execution fence: ordinary runs cannot write stage-owned paths

The observed Lead Agent copied arbitrary files into
`outputs/dbtl/<cycle>/build/`, making ungoverned work look authoritative.
Preventing repository mutations is not enough when the filesystem itself is a
human-visible authority surface.

All DBTL attempt directories must be stage-owned:

```text
outputs/dbtl/<cycle>/<stage>/
```

For an ordinary run they are readable and not writable. A stage worker receives
a server-owned, attempt-specific execution grant that permits writes only to
the active stage attempt's staging directory. The adapter validates the staged
files and atomically renames them into an immutable, content-addressed attempt
directory. A repository transaction then records the worker result, hashes,
and lineage and selects that attempt as authoritative. A failed transaction may
leave an unreferenced directory for bounded cleanup, but it cannot create a
governed result. Any friendly “current stage” path is a rebuildable projection
of the repository binding, not a second source of truth.

The restriction must cover every write route—file tools, patch tools, shell
redirection, copy/move operations, and subprocesses. A prompt instruction is
not a security boundary. Provisioned/Kubernetes and containerized sandboxes
must enforce the mount policy. A shared write-policy middleware should enforce
the same policy for path-aware tools before execution.

The guarantee is honestly backend-tiered. `LocalSandboxProvider` supports
nested read-only mappings for path-aware file tools, but its optional host bash
runs outside an OS filesystem sandbox and can bypass those checks. Screening a
shell command for `outputs/dbtl/` is useful defense in depth, not a structural
boundary. Therefore strict DBTL authority mode must disable host bash for
ordinary runs on this provider. A deployment that keeps ordinary host bash
enabled is development-only/degraded and must fail DBTL readiness rather than
claim the execution fence is active. Stage workers that require arbitrary shell
execution need a provider with enforceable mount/process isolation; a runtime
grant does not make host bash safe by itself.

The write-policy middleware belongs in the shared subagent/lead middleware
builder, alongside `ReadBeforeWriteMiddleware`, so ordinary lead runs,
delegated subagents, and DBTL `SubagentExecutor` workers pass through one
implementation. The worker's grant is injected by the stage dispatcher and
narrows that middleware; it never disables the policy wholesale.

The execution grant is server-owned runtime context, never accepted from a
client and never checkpointed as a reusable permission. It binds:

- project id;
- cycle id;
- stage and stage-attempt id;
- run id;
- allowed staging root; and
- expiration/cancellation state.

### 3.3 Reporting fence: only durable facts receive workflow language

Reserved workflow claims—approved, unlocked, started, completed, advanced,
ready for review, and moved to a stage—must be rendered by deterministic DBTL
projections backed by repository ids and revisions.

An ordinary Lead Agent may discuss a design, explain files, or propose work. It
may not produce a DBTL receipt or status component. In a thread with an active
cycle, its UI is explicitly labelled ordinary analysis and carries no stage
badge. The runtime supplies a read-only DBTL status snapshot and instructions
not to claim transitions, but the routing and execution fences remain the real
enforcement.

As defense in depth, terminal delivery should compare any run tagged as DBTL
stage work with the repository. A mismatch becomes a deterministic warning and
telemetry event; it is never promoted into a success receipt.

## 4. Phase 1 — compact gate handoff receipt

### 4.1 Behavior

Every successful server-bound verdict that opens another stage starts one short
hidden Supervisor run in the originating thread. That run emits the existing
native Human Input Card protocol without an LLM call.

The card names:

- the recorded decision and concluded stage;
- the next open stage;
- whether Reconciliation was required or skipped;
- **Start <stage>** and **Hold here**; and
- an expandable audit reference containing transition id, revision, actor,
  time, and bound evidence hash.

Keep ids and hashes out of the primary prose. They belong in details and export,
not in the sentence a person must read to continue.

**Hold here** is terminal for that request and dispatches no worker. **Start**
recovers the bound cycle, revalidates current durable state, and calls the live
stage adapter once. The adapter—not the card's label—resolves the executable
stage from current repository state.

If no next stage is executable, render a receipt without Start. If card delivery
fails after the verdict commits, write a visible recovery receipt explaining
that the decision is safe and how to reopen the handoff.

### 4.2 Persistence and idempotency

The DBTL transition/review record is the authority. The Human Input Card is a
checkpointed chat projection bound to that record. Delivery is idempotent on
the transition/review id plus originating thread.

Do not derive that binding from a hidden message blob alone. Persist a
`dbtl_stage_handoffs` row in the same repository transaction as the verdict,
with a unique source-review/transition id and these server-owned bindings:

- handoff id, project, cycle, originating thread, and source review/transition;
- approved stage, expected next stage, and decision revision;
- evidence/deck binding needed by the receipt;
- lifecycle: `pending`, `launching`, `presented`, `held`, `starting`,
  `consumed`, `delivery_failed`, or `superseded`;
- request id and admitted run id, when available; and
- a stable stage-execution idempotency key derived from the handoff id.

The row is also the delivery outbox. The verdict transaction finalizes the
feedback action and commits the `pending` handoff; it does not depend on
`start_run` succeeding inline. After commit, a dispatcher claims
`pending|delivery_failed -> launching` and admits the server-authenticated run.
The handoff watcher marks it `presented` only after the card is durably emitted,
or `delivery_failed` with a bounded recovery reason. A sweeper retries stranded
pending/launching deliveries idempotently, including the no-run-id failure
case that a run watcher alone cannot see.

The hidden run carries only the opaque handoff id. The Supervisor loads and
revalidates the row before rendering or consuming it. **Start** uses a
compare-and-set from `presented` to `starting` and dispatches with the stable
handoff idempotency key; duplicate card submissions replay the same attempt.
**Hold** records `held` durably. A later explicit request can present the held
handoff again without pretending that work already started.

Replaying a consumed deck action returns the existing receipt and handoff
record, plus its run when one has been admitted; it cannot create a second card
or dispatch. Reload reconstructs the same card from thread history, with the
current server state revalidated before controls are enabled.

### 4.3 Acceptance criteria

- Design approval displays exactly one combined receipt/handoff card.
- Optional Reconciliation is stated once, not repeated as a validation stream.
- Rendering the card invokes no model and no stage worker.
- Hold dispatches nothing.
- Start dispatches once even if the browser lost cycle scope.
- A forged card id or forged option value cannot select a cycle or action.
- A conflicted verdict never produces a success card.
- Refresh and replay preserve one receipt and one action ledger entry.

### 4.4 Prototype status and known gaps (2026-07-31)

The core of this phase is on the branch. The card protocol lives in
`supervisor.py` (`STAGE_HANDOFF_PREFIX`, `_stage_handoff_message`,
`_pending_stage_handoff`, `_answered_stage_handoff`); the deck's approve
route starts the hidden handoff run in
`dbtl_cycles.py::_start_post_approval_handoff`, which binds cycle id,
revision, approved stage, next stage, and surface id into a hidden marker and
records `design_feedback.handoff_started`. Hold is terminal, Start
re-enters the adapter as a continuation so the adapter resolves the
executable stage from durable state, a cycle mismatch renders the no-write
notice, and a replayed consumed action returns its prior receipt without a
second run. Pinned by `test_dbtl_supervisor_graph.py` and
`test_dbtl_design_feedback_router.py`.

The prototype proves the interaction, but it is not yet the authority boundary
described by this plan:

1. **Delivery failure has no recovery receipt.** Neither call site wraps
   `_start_post_approval_handoff`. `start_run` on a busy originating thread
   raises *after* the verdict has committed; the ledger action is never
   updated to `review_recorded`, and a retry replays the pre-receipt action
   row — the verdict is durable but no handoff card ever reaches the
   conversation. The fix is the §4.2 outbox: finalize the action and pending
   handoff in the verdict transaction, then retry delivery by handoff id. A
   deck-action replay may nudge that outbox but must not repeat the verdict.
2. **The handoff run is not watched.** Refinement and meeting rounds register
   with `dbtl_round_watch`; the handoff run does not, so a handoff run that
   dies before emitting its card leaves the thread silent. The same watcher
   with a handoff-specific explanation closes admitted-run failure; the outbox
   sweeper closes failures that happen before a run id exists.
3. **The card is not yet a complete transition receipt.** It names the approved
   and next stages but does not carry a stable review/transition id,
   Reconciliation disposition, actor/time, or evidence binding. It can also be
   rendered from a marker whose cycle revision is no longer current. The
   durable handoff row above supplies and revalidates those facts.
4. **The hidden marker is not enforced as server-owned input.**
   `dbtl_post_approval_handoff` is not currently stripped at the external
   message boundary, while the approval route starts its run under the human
   session request rather than internal authentication. Replace the payload
   marker with the opaque handoff id and a dedicated server-launch context path
   that external `RunCreateRequest` bodies/config cannot populate. Supervisor
   lookup must reject a missing, wrong-thread, wrong-project, stale, or forged
   id.
5. **Start/Hold consumption has no durable single-use ledger.** The answered
   card is recoverable from checkpoint history, but two admitted answer runs
   can have different run ids and therefore different adapter idempotency keys.
   The compare-and-set lifecycle and stable handoff execution key in §4.2 close
   duplicate dispatch and make Hold/reopen behavior explicit.
6. **A successful hidden run can still be invisible to thread history.** The
   2026-08-01 `design-approved` replay completed successfully and contained the
   handoff card in its final graph output, but persisted no assistant/tool
   message events for `/api/threads/{id}/messages/page`. Treating run success as
   delivery success therefore loses the control on refresh. The handoff watcher
   must verify the canonical message projection (or explicitly persist it) before
   marking the handoff `presented`; otherwise it retries or emits a visible
   recovery receipt. Add a characterization test that starts the hidden handoff,
   reloads history from the endpoint, and proves exactly one actionable card is
   present before any free-text follow-up is admitted to ordinary routing.

### 4.5 Delivered 2026-08-01 — visible card, verified delivery, no free-text escape

Three layers failed independently in the `design-approved` replay, and each is
now closed at the layer that failed. None of this creates the durable
`dbtl_stage_handoffs` row; §4.2 and gaps 1-5 remain open.

**Delivery — the card is written to thread history.** `RunJournal` recognizes a
run's own output by locating the run's *input* message in the final graph state,
which requires that input to carry an `id`. Nothing mints one — not the
composer, not the LangGraph SDK, not `convert_to_messages` — so the entire
reconciliation path switched itself off for exactly the runs that have no model
call to persist their output. Three changes:

- `RunJournal.record_pre_run_message_identities` accepts the thread's pre-run
  messages as a fallback current-run boundary, supplied by the worker from the
  rollback snapshot it already captures. An *unknown* boundary keeps the old
  conservative behaviour, because reconciling from a guessed one would
  re-persist retained history as this run's work.
- The three deck-started run inputs (`_start_post_approval_handoff`, the chair
  resume, the review-meeting convener) stamp an explicit id, so delivery does
  not depend on the boundary fallback either.
- A deterministic supervisor reply built by `receipt_message` carries
  `deerflow_graph_receipt` and is reconciled as an assistant turn. Without it a
  Hold, a stale-card refusal, or the review-boundary guidance is spoken into a
  void. The key is server-owned and stripped from external input.

**Verification — success is not delivery.** `_watch_post_approval_handoff`
passes `success_has_follow_up=card_delivered`, which reads the run's rows back
through the canonical projection (`list_messages_by_run`) and looks for a
`dbtl_stage_handoff` request. A run that succeeded and delivered nothing is
handled exactly like a dead one: the ledger action reopens for retry and the
conversation says what happened. An unreadable store reports delivered, because
it proves nothing and a false alarm is worse than silence.

**Routing — an unanswered control is not an opening for the lead agent.** While
a server-emitted Start/Hold card is unanswered, a request that would otherwise
route ordinary is answering *that card*. `route` intercepts it and
`represent_pending_stage_handoff` re-presents the control, dispatching nothing.
The reader is `unanswered_stage_handoff_card`, which reads the emitted card
rather than the hidden marker: the marker scan stops at the first visible user
message, and the escape *is* a visible user message. Precedence is preserved —
an answer to any server card wins, a review sentence still gets the review
boundary, and a stale card is refused rather than re-offered. Answering with
**Hold** releases ordinary conversation, because hold is a decision.

*Known trade-off, deliberate.* This implements §5's precedence rung 2 ("a live
handoff bound to the originating thread"), which carries no phrase condition, so
an unrelated question asked while a control is waiting also gets the control
back rather than an answer. That is the conservative direction — the cost is one
extra click on **Hold**, against a takeover that cost 35 model calls and left
chat and durable state disagreeing. Narrowing it to rung 4's free-text pattern
belongs with the rest of `GovernedStageIntent`, not ahead of it.

### 4.6 Review corrections (2026-08-01)

An adversarial review of §4.5 found that the fence, as first written, made
things worse. All findings are fixed; the ones that mattered:

**The card could not render at all.** It carries `design_feedback_surface_id`,
and the web UI returned `null` for every request carrying that field — a rule
written for the Design decision card, where the deck genuinely is the input
surface. On a handoff that id is only an audit binding and the deck holds no
control that could answer it. So the card was invisible for *two* independent
reasons and §4.5 fixed only one; the fence then re-presented an invisible card
on every message, turning a governance escape into a silent thread. The
suppression now keys on clarification type as an allowlist, so an unrecognized
surface-bound card stays suppressed: invisible is recoverable, wrongly
interactive is not. The characterization test asserted the card row was
*present* in the endpoint and never that it was *renderable*, which is exactly
why this was missed.

**Start had never worked.** `handle_stage_handoff` passed the raw card request
to a validator reading `cycle_id`, while the card names it `dbtl_cycle_id`, so
every Start was refused as "no project-owned cycle". Pre-existing, and invisible
because the test's fake adapter ignores its arguments. It also means that
accidental fail-closed bug was the only thing keeping forged cards away from a
real dispatch.

**A stale card trapped the thread.** A refusal emitted no card, so the card
stayed unanswered, the fence fired again, and ordinary work became unreachable
for the life of the thread. The refusal now records which card it closes and
releases the fence.

**A card could be answered for the wrong cycle.** Every ORDINARY route carries
`cycle_id=None`, so the mismatch guard never fired; a question asked with cycle
B selected got cycle A's card, which never named its cycle. The guard now reads
the request's selected cycle, and the card names the cycle it would start.

**Forged cards.** `ToolMessage.artifact` survived `normalize_input`, so a client
could place a fabricated card in checkpoint state — hijacking routing, and by
forging an *answered* card, suppressing the fence while a real control waited.
The artifact is now stripped from external input. `cycle_revision` is validated
where the card is read, since the fence forces every later request through the
marker rebuild and a non-integer value crashed the whole conversation.

**Two smaller ones.** A thread's first run has no prior checkpoint, which is a
known empty boundary rather than an unknown one; collapsing them left the first
turn of every new conversation unreconciled. And the delivery check swallowed
store errors, ending the watch and reporting "delivered" precisely when a
struggling store was the likely reason the card was missing.

One §4.5 test was mislabeled — it passed with the whole fix reverted — and is
now stated as what it is: a deliberate pin on the conservative branch.

**Awareness without authority.** An ordinary project run now receives
`dbtl_status_snapshot` in request-only context (live cycles, stage statuses, any
waiting control), rendered by `build_dbtl_status_reminder`. The block states
that the lead agent may discuss, read, and prepare, and may never start,
advance, approve, reject, or record a stage, nor call its own work a stage
result. It is orientation only; the routing fence and the stage-owned output
paths remain the enforcement.

**Coverage.** `tests/test_dbtl_stage_handoff_delivery.py` (the regression at the
journal seam, the boundary fix, the retained-history guard, and one actionable
card through `GET /threads/{id}/messages/page`),
`tests/test_dbtl_supervisor_graph.py::TestPostApprovalStageHandoff` (the four
observed `test3` phrases re-presenting instead of running ordinary work, Hold
releasing ordinary chat, and the injected status block's no-authority wording),
and `tests/test_dbtl_round_watch.py` (successful-but-undelivered treated as
failure; delivered stays quiet).

## 5. Phase 2 — stage-control interception and takeover prevention

Add a first-class `GovernedStageIntent`/`StageControlRecovery` step before the
ordinary branch. It reads only server-owned cards, registered surface bindings,
thread/cycle associations, and current repository state.

Partial guards already exist and the fence should be built on them, not
beside them: the held-Design pointer, the deterministic review-language
guidance, card-bound cycle recovery, and the hidden handoff marker
(`_pending_stage_handoff`) all intercept correctly today — but every one of
them fires only when the request arrives cycle-scoped or as a server-card
answer. The observed escape is exactly the remaining path: a free-text
control message with no selected cycle routes ordinary. The new work is the
durable thread-to-active-stage binding plus the interception step that
consults it before the ordinary branch, not a rewrite of the existing card
recovery.

Required outcomes are:

- `present_handoff`;
- `present_review`;
- `present_current_status`;
- `select_cycle`;
- `execute_bound_stage`; or
- `not_governed`.

Only `not_governed` may reach the ordinary Lead Agent. The execution outcome is
available only from an answered, server-emitted Start card.

The originating thread binding must be durable. A current feedback surface
already records its originating conversation; the same binding belongs on the
active `dbtl_stage_handoffs` row so summarization or UI scope loss cannot erase
it. A project may have several active cycles, so “latest cycle in project” is
not a safe fallback.

Routing precedence is deterministic:

1. an answer to a server-emitted Human Input Card;
2. a live or held handoff bound to the originating thread;
3. a registered review surface bound to the thread;
4. a conservative free-text stage-control pattern with exactly one matching,
   thread-bound cycle;
5. explicit cycle selection or clarification; and
6. ordinary routing.

A bare assent such as “yes, go ahead” is stage control only when it answers a
server card or immediately follows a server-authored stage-control recovery in
the same thread. Otherwise it remains ordinary conversation. That ordinary
branch still has no stage authority because the execution and filesystem
fences apply independently of intent classification.

### Acceptance criteria from the observed thread

- “now, start to build following the approved design” presents or consumes the
  bound Build handoff; it never runs the ordinary Lead Agent.
- “approve to build” cannot copy files, record a review, or claim approval. It
  points to the exact Build review surface/card.
- “now, shall we move to Test?” while Build is in progress reports that Build
  still needs governed execution/review.
- “yes, go ahead” with no pending server control cannot start Test. In the
  legacy observed replay it recovers current DBTL status; unrelated assent
  remains ordinary and still lacks stage authority.
- No ordinary run can write under `outputs/dbtl/`.
- Chat stage, rail stage, stage rows, and transition head agree after every
  turn.

## 6. Phase 3 — durable stage execution envelope

### 6.1 Backend event contract

Introduce one versioned envelope persisted through the run-event journal:

```json
{
  "version": 1,
  "type": "dbtl_stage_progress",
  "run_id": "run-…",
  "project_id": "project-…",
  "cycle_id": "cycle-…",
  "stage": "build",
  "stage_attempt_id": "stage-…",
  "stage_event_id": "stage-…:3:progress",
  "stage_sequence": 3,
  "phase": "verifying",
  "status": "in_progress",
  "label": "Checking the recorded outputs"
}
```

Required event types:

- `dbtl_stage_started`;
- `dbtl_stage_progress`; and
- `dbtl_stage_finished`.

Common phases are deliberately small:

- `preparing`;
- `working`;
- `verifying`;
- `recording`;
- `awaiting_review`.

Stage-specific details may appear in the expandable audit view, but they do not
create more top-level phases. Test can report a failed check by stable check id;
it should not narrate every successful validity rule into chat.

Events come only from explicit adapter boundaries and allowlisted worker/tool
lifecycle evidence. They never expose chain-of-thought, prompts, credentials,
raw shell output, arbitrary file contents, or unvalidated worker prose. If no
new evidence exists, keep the previous phase instead of inventing activity.

Two constraints from the existing codebase apply:

- **The run-event contract is a synchronized set.** New persisted event types
  must land together in `deerflow/constants.py`, `runtime/events/catalog.py`,
  `contracts/run_event_stream_contract.json`,
  `backend/docs/RUN_EVENT_STREAM.md`, and
  `tests/test_run_event_stream_contract.py`, within the envelope limits
  (`event_type` ≤ 32 chars, `category` ≤ 16). Use a dedicated `dbtl`
  category so stage events stay out of the thread message feed the way
  `subagent` events do, while `list_events` still serves the card's
  fetch-on-expand backfill.
- **Two partial channels already exist and must be subsumed, not tripled.**
  Deck-started rounds today get a bounded `dbtl_meeting_progress` snapshot
  card plus frontend polling of the stage-worker read endpoint, and
  `dbtl_round_watch` posts terminal-failure notices. The envelope replaces
  the polling as the progress source and becomes the watcher's success-path
  complement; the watcher stays as the failure-path backstop. Shipping the
  envelope beside both, unreconciled, would give one stage three competing
  progress voices.

### 6.2 Ordering, coalescing, and refresh

`stage_sequence` increases within `(run_id, stage_attempt_id)` and is distinct
from the run-event journal's thread-wide `seq`. Every envelope also has a
stable logical `stage_event_id`, distinct from the transient SSE bridge id.
Consumers deduplicate by `stage_event_id`, order stored events by journal
`seq`, and ignore a lower `stage_sequence` for the same attempt. Terminal state
is append-only.

Coalesce rapid internal events. A phase should update only when the adapter has
evidence that the boundary changed, not on each read, command, retry, or
validation. Repeated checks inside one phase change its internal audit details,
not the number of chat messages.

The stage attempt and run id appear on every event. Never infer them from the
currently selected rail row. The producer persists the envelope first, then
publishes that same stored envelope through the run-stream bridge. SSE
reconnect keeps the bridge `Last-Event-ID` separately from the last stored
journal `seq`. On a bridge gap—and on hard refresh—the client backfills from the
authenticated event endpoint with `after_seq`, then resumes live delivery. A
live-only event therefore cannot disappear after reload, and replay cannot
invent a second phase.

### 6.3 Frontend surface

One stage execution card is anchored to the answered Start card. It shows:

- stage and attempt;
- current bounded phase and elapsed time;
- terminal status;
- one concise current operation;
- failed/blocked checks, if any; and
- the exact review artifact when ready.

Validated worker/tool details live behind one disclosure for debugging. Build,
Test, and Learn execution are not meetings and never render participants,
debate, consensus, or chair UI.

### 6.4 Acceptance criteria

- The card appears before the first long operation finishes.
- A multi-step stage produces useful phase changes without tool-call spam.
- Hard refresh reconstructs the card.
- Another thread/cycle cannot attach to it.
- Cancellation records cancelled and preserves the last phase.
- Retry creates a new attempt card and keeps the old audit card.
- Internal `nostream` and raw validation messages remain absent from chat.

## 7. Phase 4 — guaranteed terminal receipt

Every stage branch ends with deterministic prose derived from the recorded
outcome:

- **ready for review** — exact package and review action;
- **needs input** — one focused, cycle-bound Human Input Card;
- **failed** — failed phase/check and safe retry;
- **cancelled** — confirms no gate advanced; or
- **replayed** — points to the existing attempt/result.

The final response, stage card, repository stage row, artifact binding, and rail
projection must agree. A model is unnecessary when `LiveStageResult` and the
repository already contain the facts.

A successful parent run with an empty response or without its expected stage
record is a presentation failure. The fail-soft delivery path writes a recovery
receipt; it never asks the Lead Agent to summarize what probably happened.

## 8. Phase 5 — optional Lead Agent engine inside the stage boundary

Do not begin this phase unless Phases 1–4 show through manual evidence that the
existing worker remains materially weaker at implementation or tool use.

If reuse is necessary, reuse only the execution engine—not the ordinary Lead
Agent branch—and invoke it behind the attempt-specific execution grant:

- Supervisor resolves project/cycle/stage first;
- approved Design, StageSpec, and allowed inputs are immutable;
- the engine receives the stage-only writable staging root;
- clarification returns as a typed stage result;
- only sanitized adapter events enter the progress envelope;
- outputs remain provisional until worker validation and lineage commit;
- the engine cannot submit, approve, advance, or choose the next route; and
- cancellation/replay revoke or reuse the exact attempt idempotently.

The full interactive Lead Agent graph must never be nested as a second workflow
owner. Its ordinary branch remains for ordinary work only.

## 9. Test plan

### 9.1 Backend authority and routing

- Golden-test the combined gate receipt/Human Input Card payload.
- Prove card-bound cycle recovery and server-owned option resolution.
- Exercise every `GovernedStageIntent` outcome before ordinary routing.
- Replay the four observed `test3` follow-ups and prove none dispatch ordinary
  work or change stage state without a valid card/review.
- Verify ambiguous active cycles require selection rather than “latest” lookup.
- Submit the same Start answer through two separately admitted runs and prove
  the handoff compare-and-set produces one stage attempt and replays it to the
  loser.
- Verify Hold survives refresh and conversation reopen, dispatches no worker,
  and can later be presented and consumed explicitly.
- Prove externally supplied handoff metadata/context cannot create an
  actionable card or resolve an opaque handoff id.
- Prove a busy originating thread cannot strand an approval: the verdict
  and feedback action commit with one pending handoff, the outbox retry admits
  it after the thread clears, and exactly one card is delivered (closes §4.4
  gap 1).

### 9.2 Filesystem and execution grants

- Deny ordinary file tools and shell writes/copies/moves into `outputs/dbtl/`.
- Deny client-forged, checkpoint-replayed, expired, wrong-run, wrong-cycle, and
  wrong-attempt grants.
- Permit the active stage worker to write only its staging root.
- Verify staging-to-immutable-attempt promotion is atomic, and that no promoted
  directory becomes authoritative until the lineage transaction binds its
  hashes; unreferenced directories remain non-authoritative and cleanable.
- On `LocalSandboxProvider`, verify strict DBTL readiness disables ordinary
  host bash or fails closed; do not claim shell-path confinement while host
  bash remains enabled.
- Run true path-isolation tests against a provisioned/container sandbox backend.

### 9.3 Reporting

- Pin event schema, bounded phases, ordering, coalescing, deduplication, and
  terminal immutability.
- Prove the event is stored before live publication, reconnect resumes from the
  bridge id, gap recovery backfills from journal `seq`, stable stage event ids
  deduplicate replay, and `stage_sequence` never aliases the thread-wide journal
  sequence.
- Run the run-event stream contract conformance suite against the new event
  types (constants, catalog, JSON contract, and docs must agree).
- Verify successful validation does not emit one chat line per check.
- Verify failed checks are visible and link to the review evidence.
- Verify all `LiveStageResult` variants deliver a durable terminal response.
- Verify no raw internal prompt, reasoning, tool output, or credential appears.
- Verify a chat/repository mismatch produces a warning, not a success receipt.

### 9.4 Frontend

- Render every stage and terminal state with textual status.
- Rehydrate mid-run after remount.
- Ignore duplicate/out-of-order events.
- Keep concurrent threads/cycles isolated.
- Keep audit details accessible but collapsed by default.
- Prove ordinary responses cannot render a DBTL stage badge or receipt.

## 10. Manual acceptance

Use both the isolated `design-approve` scenario and the observed `test3` replay.

1. Restore `design-approve` and open its originating conversation.
2. Approve Design and verify one combined receipt/Start-Hold card.
3. Refresh, replay the deck action, and confirm one card remains.
4. Submit Start from two browser sessions and verify both resolve to one Build
   attempt and one execution card.
5. Create a fresh handoff, choose Hold, refresh/reopen the conversation, verify
   no worker ran, then explicitly reopen and Start it.
6. Refresh mid-Build and confirm the card recovers without raw tool chatter.
7. Finish Build and verify one terminal receipt points to the exact package.
8. Attempt an ordinary write into the Build directory and verify refusal.
9. In strict local mode, verify ordinary host bash is absent; if it is enabled,
   verify DBTL readiness refuses to claim the authority fence is active.
10. Approve Build and repeat the handoff/progress flow for Test.
11. Replay the four `test3` phrases with cycle scope deliberately absent and
    verify the deterministic recovery/status behavior in Section 5.

Do not capture a success checkpoint until chat, rail, stage rows, transition
head, artifacts, and review bindings agree after refresh.

## 11. Rollout and observability

Ship presentation behind one default-off flag during manual validation, but do
not flag-gate the authority or filesystem fences once proven: preventing a
workflow owner escape is correctness, not presentation preference.

Record bounded telemetry:

- gate handoff delivery/replay/failure;
- handoff compare-and-set conflicts, forged-id rejection, and held duration;
- stage-control intents intercepted before ordinary routing;
- ambiguous/unbound control requests;
- ordinary writes blocked under DBTL paths;
- time to first stage card and terminal receipt;
- progress phase count and coalescing ratio;
- refresh rehydration success;
- empty-success or missing-record repair;
- chat/repository stage mismatch; and
- any ordinary run that emitted reserved workflow language.

Rollout success requires zero Lead Agent takeovers in the manual corpus and no
chat/durable-state mismatch—not merely attractive progress cards.

## 12. Non-goals

- Automatically starting a stage because the previous stage was approved.
- Treating free-text “yes” as authority without a pending server card.
- Streaming chain-of-thought, raw tools, or every successful validation.
- Turning progress labels into evidence.
- Letting a Lead Agent or worker satisfy a human gate.
- Letting ordinary runs write into governed stage directories.
- Replacing review decks/sheets with progress cards.
- Reintroducing Reconciliation when deployment policy makes it optional.

## 13. Decisions now resolved

- Approval opens but does not start the next stage.
- The next action is a deterministic Start/Hold Human Input Card.
- Progress uses bounded operational phases, not agent narration.
- Successful validations are summarized; failures expand.
- The ordinary Lead Agent is never a fallback workflow owner.
- Any future reuse of its execution engine occurs only inside a governed stage
  attempt with an explicit, server-owned write grant.

The remaining product decision is whether terminal transition receipts are
exported as permanent thread messages or reconstructed from durable transition
activity. Either choice must appear once, survive refresh, and retain its audit
binding.

## 14. Existing Test-gate correction to retain

Typed Test evidence and server-computed outcomes remain mandatory:

- prose and arbitrary workspace files cannot satisfy Test;
- the server owns the optional-Reconciliation lineage check;
- Test meeting/outcome cards bind cycle and evidence snapshots;
- a meeting annotates but cannot rewrite the computed outcome;
- the generic stage sheet remains read-only evidence/audit UI; and
- the historical Build-approved/Test-active cursor mismatch remains repaired
  transactionally when the human records an outcome.

This correction complements the new fences. Typed Test validation protects the
gate; routing and filesystem fences prevent ordinary work from impersonating
the stage before that gate is reached.
