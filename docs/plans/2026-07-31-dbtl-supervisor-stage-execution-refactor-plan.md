# DBTL Supervisor and Stage Execution Refactoring Plan

**Date:** 2026-07-31  
**Status:** Implemented on `codex/dbtl-supervisor-stage-refactor`

## Implementation result

The refactor landed with the existing production imports preserved:

- `agents/dbtl/supervisor.py` remains the graph factory and delegates durable
  card-history queries plus the first ordered continuation handlers to
  `supervisor_support/`.
- `agents/dbtl/stage_execution.py` is now a compatibility facade. The concrete
  adapter lives in `agents/dbtl/live_stage/adapter.py`.
- `live_stage/replay.py` owns idempotent replay, including Design clarification
  recovery and Learn synthesis repair.
- `live_stage/test_review.py` owns typed Test reconstruction and the fresh,
  server-revalidated human outcome write.
- `live_stage/types.py` owns the immutable supervisor result contract.

The remaining large Design, meeting, dispatcher, recording, artifact, and
feedback methods stay together in `live_stage/adapter.py` for now. They retain
the seams described below, but were not mechanically scattered merely to meet
a line-count target; follow-up extraction can move one tested responsibility at
a time without another public import migration. The completed change therefore
implements the dependency-direction and highest-risk authority boundaries of
this plan while treating the finer-grained filenames below as the intended
next ownership map, not a requirement to duplicate tightly coupled helpers.

## Purpose

The DBTL production path currently concentrates substantial orchestration in two files. In the working tree reviewed on 2026-07-31 they are:

- `backend/packages/harness/deerflow/agents/dbtl/supervisor.py` — 2,195 lines
- `backend/packages/harness/deerflow/agents/dbtl/stage_execution.py` — 4,112 lines

Their high-level boundary is sound: the supervisor owns conversation routing and presentation, while the stage adapter owns verified execution and durable evidence. The maintainability problem is not significant duplication between the two files; it is that both have become feature-accumulation points with very large orchestration functions.

This plan proposes responsibility-based extraction without changing DBTL behavior, authority boundaries, stream contracts, persistence semantics, or public import paths.

The line counts are evidence of accumulation, not acceptance criteria. Re-check them before implementation; the current branch is still changing progressive gates, stage review meetings, post-approval handoffs, and chat-first Test decisions. The refactor should begin only after those behaviors have characterization coverage and the active feature work has reached a stable merge point. Otherwise an extraction will either freeze a prototype as architecture or create a large conflict surface while the behavior is still moving.

## Scope and non-goals

This is a structural refactor of the production project-supervisor path. It does not:

- Implement the routing, execution, reporting, or durable-handoff fences proposed in `2026-07-31-dbtl-stage-chat-progress-plan.md`.
- Replace the current handoff prototype with its future SQL outbox/ledger design.
- Change card payloads, request IDs, visible copy, deck bytes, artifact paths, stage specs, worker budgets, or repository schemas.
- Turn the conceptual execution pipeline into one SQL transaction. Existing repository calls keep their current transaction, revision, and idempotency boundaries.
- Generalize DBTL card construction into the lead agent's `ClarificationMiddleware`; supervisor cards are deterministic graph output and do not pass through that tool middleware.
- Preserve private helper imports indefinitely. Production entry points stay stable, while tests that import underscore-prefixed helpers should move to the new owning modules.

## Current responsibilities

### `supervisor.py`

The supervisor is the conversation controller. It:

- Routes one request to ordinary chat, clarification, cycle setup, or cycle continuation.
- Creates and recovers Human Input cards.
- Revalidates and consumes post-approval Start/Hold handoffs.
- Handles Design meeting preflight, roster adjustment, participant settings, human-authored Design, and chair clarification resumes.
- Handles Test review-meeting and outcome cards, including the server-bound human outcome write.
- Admits authenticated stage-specific review-meeting requests without confusing them with a Design preflight.
- Presents review artifacts and decks through the existing message protocol.
- Delegates stage work to `LiveStageAdapter`.

The LangGraph topology remains appropriately thin: one request selects one terminal branch. Most of the file length is outside the topology itself, in card serialization, message-history recovery, and the continuation handler.

### `stage_execution.py`

The stage adapter is the production execution pipeline. It:

- Verifies project/cycle ownership, active stage state, runtime identity, and idempotent replay.
- Loads datasets, reconciliation state, Build/Test state, prior Design runs, and activity history.
- Builds stage context and versioned `StageSpec` execution plans.
- Runs Design-specific meeting behavior: hold, resume, revision interpretation, roster proposal, participant settings, red team, and chair synthesis.
- Runs Build/Test/Learn review meetings over already-recorded evidence without re-running the governed stage.
- Constructs and dispatches real `SubagentExecutor` workers.
- Validates structured worker results and records every outcome.
- Writes content-addressed review packages and stage digests.
- Records Learn synthesis, Build lineage, and server-computed Test validity projections.
- Produces decks and registers feedback surfaces.
- Reconstructs and revalidates Test chat-review snapshots before recording a human route.
- Returns a small `LiveStageResult` to the supervisor.

The central `LiveStageAdapter.execute()` method is about 900 lines and combines validation, replay repair, preparation, planning, dispatch, persistence, stage-specific post-processing, projection, and user-facing summarization. The class also owns substantial review-meeting and Test-review behavior outside `execute()`.

## Redundancy and maintainability findings

### Repeated Human Input protocol envelopes

`supervisor.py` contains eight card builders that repeat the same protocol structure:

1. Construct an `ask_clarification` tool call.
2. Construct the paired `AIMessage`.
3. Construct the paired `ToolMessage`.
4. Repeat the `human_input_request` envelope and shared fields.
5. Add card-specific payload fields.

The domain payloads are legitimately different, but the transport envelope should have one implementation so provider constraints, message pairing, transcript fallback, and protocol version fields cannot drift. The shared builder must remain local to deterministic supervisor output; it should mirror, not replace, the generic clarification middleware's public wire contract.

### Repeated message-history scans

The supervisor repeatedly scans durable messages to recover:

- Card answers and emitted requests.
- Cycle identity and routing intent.
- Pending and answered stage handoffs.
- Test review-meeting and outcome-card answers.
- Confirmed council depth.
- Participant settings.
- Approved council proposals.
- Human-authored Design and roster-adjustment text.
- Older preflight values needed by a resumed chair.

These scans are related queries over the same protocol history and would be easier to reason about behind one read-only abstraction.

### Repeated one-shot LLM setup

Both files correctly reuse `deerflow.utils.oneshot_llm.run_oneshot_llm`, but several factory functions still repeat:

- Application-config lookup.
- DBTL model-name selection.
- Missing-model fail-soft behavior.
- Closure construction around system instruction and run name.

This is modest duplication and lower priority than splitting the orchestration functions.

### Generic and Design-specific execution are interleaved

`LiveStageAdapter.execute()` supports Design, Reconciliation, Build, Test, and Learn, but much of its control flow is Design-only. Meeting hold/resume rules, revision interpretation, roster proposal, red-team dispatch, chair dispatch, and Design deck behavior obscure the generic stage transaction.

### Review meetings and Test decisions form separate workflows

The adapter now has two legal execution shapes:

- Execute the currently active stage while it is `in_progress` or `changes_requested`.
- Convene a review meeting over immutable evidence while the stage is `awaiting_review`.

The second shape deliberately bypasses ordinary executable-stage status checks and binds its successor surface to the stage's original evidence, not to the meeting attachment. Test adds a third, human-write path: reconstruct the typed validity assessment, revalidate the current evidence and meeting requirement, then record one outcome-compatible recommendation. These are not incidental branches of generic stage execution and should have explicit owners.

### Replay has stage-specific repair semantics

Idempotent replay is not only “return the prior artifact.” Design replay recovers an outstanding chair clarification, and Learn replay can repair a missing `learn.synthesized` record from already-recorded worker results. Extracting replay behind a generic cache check without carrying these behaviors would make retries appear successful while silently dropping required durable side effects.

### Production worker dispatch contains several layers

`_dispatch_units()` currently owns:

- Subagent-config resolution.
- Model and tool resolution.
- Authentication, tracing, sandbox, and project context propagation.
- Finalization middleware and token policy.
- Task lifecycle stream events.
- Thread offloading and cancellation.
- Token accounting and `DispatchOutcome` conversion.

These responsibilities form a coherent production dispatcher, but they do not need to live inside the stage adapter module.

### Feedback projection is embedded in execution

Feedback-surface planning, deck writing, registration, card binding, and answer consumption are projection concerns applied after core worker evidence is committed. Keeping them inside the execution adapter makes the transaction harder to see and increases the risk that future stage-meeting work further expands the same file.

The extraction must preserve the existing failure directions and ordering. Package bytes are atomically written before the repository records their binding; worker results are recorded before a deck or surface is produced; surface registration is currently fail-soft after the governed evidence exists; and a review meeting's deck remains bound to the core stage evidence rather than replacing it with the meeting package. This plan does not silently “improve” those boundaries.

## Proposed module boundaries

Exact filenames may change during implementation; the responsibility boundaries are the important part.

### Supervisor support package

Keep `agents/dbtl/supervisor.py` as the stable graph-factory module. Put extracted code under `agents/dbtl/supervisor_support/`; a package named `supervisor/` would collide with the existing `supervisor.py` module and force an avoidable import-path migration.

#### `supervisor_support/human_input_protocol.py`

Provide a typed factory for the paired `AIMessage` and `ToolMessage` envelope. Card-specific builders continue to own their titles, questions, options, fallback text, and extra artifact payload.

The factory must preserve:

- Stable request IDs and provider length/character constraints.
- Exact AI/tool message pairing.
- Existing `human_input_request` wire fields and versions.
- Plain-text transcript fallback.
- Card-specific server-owned bindings.

The factory should accept a typed payload plus explicit fallback text and return the complete paired messages. It must not infer card meaning from a prefix, and it must not own any history lookup or write authorization.

#### `supervisor_support/card_history.py`

Provide a read-only `CardHistory` or `SupervisorConversation` view over thread messages. It should expose explicit queries rather than generic mutation:

- Latest user request text.
- Answer for a known prefix.
- Emitted server request for a request ID.
- Pending/answered handoff and Test-card records.
- Confirmed depth, proposal, and participant settings for the answering turn.
- Resumed meeting setup from earlier preflight history.

Forged request IDs must continue to match nothing, and old answers must not become persistent per-cycle settings.

The view must be immutable and message-order aware. Query methods should encode their search window explicitly: answering-turn-only, latest unanswered request, or the bounded backward scan used for a chair resume. A generic “find latest prefix” helper exposed to callers would recreate the ambiguity this extraction is intended to remove.

#### `supervisor_support/ports.py`

Define small structural protocols for what the supervisor consumes from stage execution: Design preview/execution, handoff validation, feedback binding, and Test review. This replaces the current `getattr(..., None)` capability probing in production code while keeping narrow fakes easy to construct in tests. Optional deployment behavior should be represented by an explicit result/capability value, not by a missing method.

Do not make the supervisor import the concrete `LiveStageAdapter`; the injected port keeps graph tests independent from persistence, tools, and model construction.

#### `supervisor_support/continuation.py`

Extract the current `cycle_continuation()` node into a callable coordinator with injected context, stage ports, history view, and depth interpreter. Do not move the current 400-line function intact. Split its ordered responsibilities into explicit handlers:

1. Pending or answered post-approval handoff.
2. Test review-meeting and outcome-card answers.
3. Review-intent no-write guidance.
4. Authenticated stage-review meeting admission.
5. Design preflight, adjustment, human authoring, and chair resume preparation.
6. Adapter-result projection into messages and artifacts.

Each handler should return a typed `Handled`/`NotHandled` result so precedence stays visible in the coordinator. The coordinator must retain the current exact ordering: a bound card answer wins over request scope, review meetings skip Design preflight, and a resumed chair recovers only the older preflight values needed for that resume.

After extraction, `supervisor.py` should primarily contain:

- Context resolution.
- Branch decision and routing.
- Small setup/clarification nodes.
- LangGraph node and edge assembly.

### Live stage execution package

Keep `agents/dbtl/stage_execution.py` as a compatibility facade for the production imports of `LiveStageAdapter` and `LiveStageResult`. Put the implementation under `agents/dbtl/live_stage/`. Move tests that import underscore-prefixed helpers to their new owning modules; temporary private re-exports are acceptable only while a phase is in flight and should be removed before the refactor is declared complete.

#### `live_stage/types.py`

Own `LiveStageResult` plus typed phase values such as `StageExecutionRequest`, `PreparedStage`, `PlannedStageExecution`, `RecordedStageEvidence`, and `FeedbackProjection`. Keep these values free of live SQLAlchemy sessions, sandbox objects, or mutable repository rows.

#### `live_stage/context.py`

Load and validate the project-owned cycle, runtime identity, expected handoff revision/stage, active stage, datasets, reconciliation/build-test views, prior runs, activity, project manifest, and approved Design context. Return typed preparation data rather than a large set of local variables.

Preserve stage-aware loading: do not fetch or interpret Design history for a non-Design stage, and do not move filesystem scans ahead of scope, replay, or status checks. Project manifests and Build file snapshots are blocking filesystem work and must remain off the event loop.

#### `live_stage/replay.py`

Own idempotent replay lookup and its stage-specific completion rules. Preserve Design clarification recovery, Learn synthesis repair, worker/trustworthy counts, and the rule that replay never dispatches workers. It returns the same `LiveStageResult` shape as a fresh run.

#### `live_stage/design.py`

Own Design-specific orchestration:

- Hold versus reconvene decision.
- Chair clarification resume.
- Change-request interpretation.
- Council planning and approved roster application.
- Human-authored Design.
- Position, red-team, and chair work-unit construction.
- Debate-completeness and presentable-chair rules.

This module should reuse the existing value/policy modules under `deerflow.dbtl`; it must not create a second source of truth for council, consensus, revision, or worker-result contracts.

#### `live_stage/review_meetings.py`

Own Build/Test/Learn review meetings over already-recorded stage evidence. It must preserve the distinct admission rule (`awaiting_review` is legal here), sanitize meeting results so they cannot overwrite stage-computed facts, require trustworthy position + red-team + chair evidence, and bind any successor surface to the original stage evidence. The meeting package is an attachment, never replacement evidence for a gate.

#### `live_stage/test_review.py`

Own typed Test assessment reconstruction, chat snapshot projection, meeting-gate evaluation, and the human outcome write. On write it must reload the server snapshot, compare attempt/evidence bindings from the emitted card, enforce any required meeting, compute the outcome from the pinned validity pack, and pass the current durable revision to the repository. Card-returned metrics, checks, outcome, and revision are evidence of what was shown, not trusted current scientific state.

#### `live_stage/dispatcher.py`

Own the production `AsyncWorkerDispatcher` implementation around `SubagentExecutor`, including model/tool resolution, runtime propagation, stream events, finalization, token accounting, and cancellation.

The existing pure `deerflow.dbtl.stage_runner` remains the reusable planning/collection layer. The new dispatcher supplies its production IO boundary rather than replacing it.

The dispatcher API must carry whether work is a governed stage or a review meeting, the server-owned project/run/thread/auth context, and the effective per-unit model/settings. It must keep child graphs `nostream`, preserve root custom lifecycle events, and retain cancellation plus token-accounting behavior.

#### `live_stage/recording.py`

Own the ordered durable writes after result collection:

- Worker result rows and artifact binding.
- Build input hashing and lineage.
- Learn synthesis and candidates.
- Test typed-assessment handoff to the review projection.

Keep optimistic revision checks and the existing re-read before Build/Learn secondary writes. This is orchestration over repository operations, not a new cross-operation database transaction.

#### `live_stage/artifacts.py`

Own review-package and deck file production:

- Content-addressed stage package writing.
- Atomic filesystem replacement.
- Review Markdown and digest rendering calls.
- Deck-theme loading and deck writing.

Existing `review_markdown.py`, `review_paths.py`, and `council_deck.py` remain the renderers and path-policy sources.

#### `live_stage/feedback.py`

Own feedback-surface planning, registration, request binding, and answer consumption. It should sit near the existing stage feedback/meeting policy modules while preserving the rule that rendering controls does not grant authorization.

Keep planning, rendering, registration, and request binding as separate operations. That separation is required to preserve today's fail-soft registration boundary and the authenticated parent's exact deck-hash/evidence checks.

#### `live_stage/adapter.py`

Retain `LiveStageAdapter` as the public facade and stable import target. Its execution flow should read as explicit phases:

```text
load and validate
    -> check replay
    -> prepare stage context
    -> plan stage-specific execution
    -> dispatch and collect
    -> write package and record durable evidence
    -> record stage-specific lineage/synthesis
    -> produce feedback projection
    -> summarize for the supervisor
```

Typed intermediate dataclasses should carry prepared context, planned execution, recorded evidence, and projected feedback between phases.

Review-meeting execution and Test human decisions should be separate facade methods, not flags that make the ordinary stage pipeline conditionally bypass its own admission rules. Preserve the public `execute(..., review_meeting_stage=...)` argument temporarily if callers require it, but translate it immediately to the dedicated method and remove internal flag branching.

## Existing modules to reuse

The refactor should build on the current pure DBTL modules rather than introduce replacement scripts:

- `stage_runner.py` — execution planning, dispatch protocol, result collection.
- `stage_spec.py` — versioned stage contracts and budgets.
- `agent_selector.py` — capability-based worker selection.
- `worker_result.py` — structured worker-result validation.
- `council.py`, `council_proposal.py`, `council_settings.py` — Design meeting policy and values.
- `revision_intent.py` — revision route interpretation.
- `validity.py` — pinned Test validity vocabulary and deterministic outcome computation.
- `review_markdown.py`, `review_paths.py`, `council_deck.py` — artifact rendering.
- `stage_feedback.py`, `stage_meetings.py`, `stage_routes.py` — feedback and transition policy.
- `transition_assessment.py` — progressive-gate assessment parsing and fail-safe defaults.
- `utils/oneshot_llm.py` — private, non-streamed one-shot model calls.

`agents/dbtl/orchestrator.py` is a Phase 1 stub run target with placeholder artifact production. It is not a replacement for the production project supervisor or `LiveStageAdapter`.

## Recommended sequence

### Phase 0 — Freeze behavior with characterization tests

1. Finish or pause the active progressive-gate/stage-chat feature work at a stable boundary.
2. Capture complete golden payloads for all eight card builders, including AI/tool pairing, fallback text, request IDs, artifacts, and `additional_kwargs`.
3. Add table-driven history tests for every answering-turn and bounded-backscan query.
4. Capture facade-level repository-call traces and `LiveStageResult` values for fresh execution, replay, Design hold/resume, review meetings, and Test outcome recording.
5. Record deck/package hashes only where byte identity is already a contract; do not create broad new goldens that make harmless renderer refactors impossible.

No production module moves in this phase.

### Phase 1 — Extract protocol helpers

1. Introduce the Human Input envelope factory.
2. Convert the eight existing card builders without changing their returned messages.
3. Introduce the message-history view and migrate existing recovery helpers.
4. Add the stage-port protocols and replace production `getattr` probing with explicit capabilities.
5. Move tests for underscore-prefixed helpers to the owning support modules; use temporary re-exports only to keep an intermediate commit green.

This phase is mostly pure transformation and provides the safest initial reduction in `supervisor.py`.

### Phase 2 — Extract the continuation handler

1. Introduce the ordered `Handled`/`NotHandled` continuation coordinator.
2. Extract handoff, Test-card, review-meeting, Design-preflight/resume, and result-projection handlers one at a time.
3. Preserve its exact ordering: server-bound card state, review intent, review-meeting admission, preflight, adapter execution, feedback binding, clarification, and artifact presentation.
4. Leave graph topology and terminal-branch behavior unchanged.

### Phase 3 — Extract request preparation and replay

1. Introduce typed execution-request and prepared-context values.
2. Move scope/runtime/handoff/status validation without reordering checks.
3. Extract replay with its Design and Learn repair semantics.
4. Keep filesystem scans off-loop and after all cheap refusal/replay paths.

### Phase 4 — Separate stage-specific orchestration

1. Extract the Design hold/resume/revision decision into a typed plan.
2. Move Design work-unit builders and debate-completeness policy together.
3. Keep generic stage planning on `stage_runner.plan_stage`/`arun_stage`.
4. Preserve approved-roster, participant-setting, and resumed-chair semantics exactly.
5. Extract review-meeting execution as a distinct evidence-reader workflow.
6. Extract Test snapshot/outcome handling and keep its fresh server revalidation.

### Phase 5 — Extract production IO services

1. Move the production subagent dispatcher.
2. Move package/deck file production.
3. Move ordered result/lineage/synthesis recording.
4. Move feedback-surface production and registration.
5. Keep `LiveStageAdapter` as a facade coordinating these injected services.

### Phase 6 — Consolidate one-shot model setup and finish the facade

After the larger boundaries are stable, introduce a small helper for configured private one-shot invocation. It may centralize app-config lookup, model-field lookup, `nostream` invocation, and missing-model detection. Call sites must continue to own their different failure directions: debate intent holds, revision interpretation takes the cheap route, roster failure falls back to selection, question drafting uses deterministic gaps, and transition assessment falls back to standard.

Reduce `stage_execution.py` to the stable production facade and intentional re-exports, remove temporary private re-exports, update `backend/AGENTS.md` plus the root `AGENTS.md`, and refresh this plan's status. Do not use a line-count target as the completion test; use visible ownership and dependency direction.

## Guardrails

This must remain a behavior-preserving refactor. In particular:

- Ordinary work continues to invoke the real lead-agent graph.
- Every supervisor branch remains terminal.
- Client cycle selection remains intent, not authorization.
- The adapter continues to verify project/cycle ownership before dispatch.
- Handoff cycle/stage/revision bindings are revalidated before Start can dispatch.
- Card replies are accepted only when bound to a server-emitted request.
- Preflight choices remain answering-turn-scoped unless explicitly recovered for a chair resume.
- Internal one-shot and subagent calls remain `nostream` where required.
- Idempotent replay never dispatches workers twice.
- Design and Learn replay retain their stage-specific repair behavior.
- A worker result or deck never satisfies a human gate.
- Failed or contract-rejected participants cannot be converted into valid debate evidence.
- Review meetings read recorded evidence and cannot replace the stage's computed facts or core evidence binding.
- Test outcomes continue to be computed from typed worker evidence under the pinned validity pack and revalidated from the server before the human write.
- Review packages retain content addressing and atomic writes.
- Blocking workspace scans and artifact writes remain off the event loop.
- Worker evidence remains durable even when later deck rendering or fail-soft surface registration fails.
- Feedback surfaces retain exact project, cycle, thread, stage-attempt, evidence, and deck-hash bindings.
- Existing Design output should remain byte-identical where tests currently require it.
- Public factory/import paths should remain stable through facade modules or intentional re-exports.
- The harness/app import firewall remains intact; no extracted harness module imports `app.*`.

## Verification strategy

Perform extractions in small commits and run the focused suites after each boundary moves. At minimum, preserve coverage for:

- Supervisor routing, stream contract, and reducer idempotency.
- Card IDs, depth recovery, proposal recovery, and participant settings.
- Stage-handoff recovery/revalidation and Test review/outcome card precedence.
- Live stage execution and graph integration.
- Human-authored Design and chair clarification resume.
- Refinement/revision rounds and debate-completeness rules.
- Seat lifecycle events and token accounting.
- Replay behavior, including Learn synthesis repair.
- Build lineage and Test typed-validity reconstruction.
- Review-meeting admission, result sanitization, and core-evidence binding.
- Deck theme, deck registration, and feedback-surface behavior.
- Test-stage review surfaces and progressive transition assessment.

Golden tests should compare complete emitted message payloads before and after the card-protocol extraction, including both Test-card variants. Stage execution tests should compare ordered repository calls and `LiveStageResult` values before and after each adapter extraction. Prefer characterization at the public facade plus focused pure-unit tests for each extracted phase; do not duplicate the full adapter matrix in every new module.

Suggested focused gate after each applicable phase:

```bash
cd backend
uv run pytest \
  tests/test_dbtl_supervisor_graph.py \
  tests/test_dbtl_supervisor_stream_contract.py \
  tests/test_dbtl_card_ids.py \
  tests/test_dbtl_council_depth_recovery.py \
  tests/test_dbtl_meeting_participants.py \
  tests/test_dbtl_live_stage_execution.py \
  tests/test_dbtl_live_stage_graph_integration.py \
  tests/test_dbtl_refinement_round.py \
  tests/test_dbtl_revision_round.py \
  tests/test_dbtl_human_authored_design.py \
  tests/test_dbtl_review_meeting_dispatch.py \
  tests/test_dbtl_test_chat_review.py \
  tests/test_dbtl_test_review_surface.py \
  tests/test_dbtl_stage_meeting_surface.py \
  tests/test_dbtl_transition_assessment.py
uv run ruff check packages/harness/deerflow/agents/dbtl tests
```

Before completing the refactor, also run the harness import-boundary test and the repository's normal backend gate (`make test-core` at minimum; full `make test` when the branch is otherwise ready). Use the exact current test names when implementation begins, since active DBTL work may add or rename focused files.

## Expected result

The objective is not to minimize total repository lines. Much of the current length documents important safety and governance invariants, and those explanations should move with the code they describe rather than be deleted.

The desired result is:

- A small supervisor graph file whose topology is visible at a glance.
- A small `LiveStageAdapter` facade whose execution phases are visible at a glance.
- Design meeting behavior isolated from generic stage execution.
- Stage review meetings and Test human decisions represented as explicit workflows rather than flags inside ordinary execution.
- Protocol serialization, history recovery, dispatch, persistence, and feedback projection each having one clear owner.
- Smaller tests aligned with those responsibility boundaries.
