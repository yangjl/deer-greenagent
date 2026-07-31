# DBTL Supervisor and Stage Execution Refactoring Plan

**Date:** 2026-07-31  
**Status:** Proposal only — no implementation changes

## Purpose

The DBTL production path currently concentrates substantial orchestration in two files:

- `backend/packages/harness/deerflow/agents/dbtl/supervisor.py` — 1,725 lines
- `backend/packages/harness/deerflow/agents/dbtl/stage_execution.py` — 3,327 lines

Their high-level boundary is sound: the supervisor owns conversation routing and presentation, while the stage adapter owns verified execution and durable evidence. The maintainability problem is not significant duplication between the two files; it is that both have become feature-accumulation points with very large orchestration functions.

This plan proposes responsibility-based extraction without changing DBTL behavior, authority boundaries, stream contracts, persistence semantics, or existing import paths.

## Current responsibilities

### `supervisor.py`

The supervisor is the conversation controller. It:

- Routes one request to ordinary chat, clarification, cycle setup, or cycle continuation.
- Creates and recovers Human Input cards.
- Handles Design meeting preflight, roster adjustment, participant settings, human-authored Design, and chair clarification resumes.
- Presents review artifacts and decks through the existing message protocol.
- Delegates stage work to `LiveStageAdapter`.

The LangGraph topology remains appropriately thin: one request selects one terminal branch. Most of the file length is outside the topology itself, in card serialization, message-history recovery, and the continuation handler.

### `stage_execution.py`

The stage adapter is the production execution transaction. It:

- Verifies project/cycle ownership, active stage state, runtime identity, and idempotent replay.
- Loads datasets, reconciliation state, Build/Test state, prior Design runs, and activity history.
- Builds stage context and versioned `StageSpec` execution plans.
- Runs Design-specific meeting behavior: hold, resume, revision interpretation, roster proposal, participant settings, red team, and chair synthesis.
- Constructs and dispatches real `SubagentExecutor` workers.
- Validates structured worker results and records every outcome.
- Writes content-addressed review packages and stage digests.
- Records Learn synthesis and Build lineage.
- Produces decks and registers feedback surfaces.
- Returns a small `LiveStageResult` to the supervisor.

The central `LiveStageAdapter.execute()` method is 786 lines and combines validation, preparation, planning, dispatch, persistence, projection, and user-facing summarization.

## Redundancy and maintainability findings

### Repeated Human Input protocol envelopes

`supervisor.py` contains six card builders that repeat the same protocol structure:

1. Construct an `ask_clarification` tool call.
2. Construct the paired `AIMessage`.
3. Construct the paired `ToolMessage`.
4. Repeat the `human_input_request` envelope and shared fields.
5. Add card-specific payload fields.

The domain payloads are legitimately different, but the transport envelope should have one implementation so provider constraints, message pairing, and protocol version fields cannot drift.

### Repeated message-history scans

The supervisor repeatedly scans durable messages to recover:

- Card answers and emitted requests.
- Cycle identity and routing intent.
- Confirmed council depth.
- Participant settings.
- Approved council proposals.
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

## Proposed module boundaries

Exact filenames may change during implementation; the responsibility boundaries are the important part.

### Supervisor package

#### `human_input_protocol.py`

Provide a typed factory for the paired `AIMessage` and `ToolMessage` envelope. Card-specific builders continue to own their titles, questions, options, fallback text, and extra artifact payload.

The factory must preserve:

- Stable request IDs and provider length/character constraints.
- Exact AI/tool message pairing.
- Existing `human_input_request` wire fields and versions.
- Plain-text transcript fallback.
- Card-specific server-owned bindings.

#### `card_history.py`

Provide a read-only `CardHistory` or `SupervisorConversation` view over thread messages. It should expose explicit queries rather than generic mutation:

- Latest user request text.
- Answer for a known prefix.
- Emitted server request for a request ID.
- Confirmed depth, proposal, and participant settings for the answering turn.
- Resumed meeting setup from earlier preflight history.

Forged request IDs must continue to match nothing, and old answers must not become persistent per-cycle settings.

#### `continuation.py`

Extract the current `cycle_continuation()` node into a callable handler with injected context, stage adapter, and depth interpreter. It should own the conversation-to-adapter translation and result-to-message projection.

After extraction, `supervisor.py` should primarily contain:

- Context resolution.
- Branch decision and routing.
- Small setup/clarification nodes.
- LangGraph node and edge assembly.

### Live stage execution package

#### `live_stage/context.py`

Load and validate the project-owned cycle, runtime identity, active stage, datasets, reconciliation/build-test views, prior runs, activity, project manifest, and approved Design context. Return typed preparation data rather than a large set of local variables.

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

#### `live_stage/dispatcher.py`

Own the production `AsyncWorkerDispatcher` implementation around `SubagentExecutor`, including model/tool resolution, runtime propagation, stream events, finalization, token accounting, and cancellation.

The existing pure `deerflow.dbtl.stage_runner` remains the reusable planning/collection layer. The new dispatcher supplies its production IO boundary rather than replacing it.

#### `live_stage/artifacts.py`

Own review-package and deck file production:

- Content-addressed stage package writing.
- Atomic filesystem replacement.
- Review Markdown and digest rendering calls.
- Deck-theme loading and deck writing.

Existing `review_markdown.py`, `review_paths.py`, and `council_deck.py` remain the renderers and path-policy sources.

#### `live_stage/feedback.py`

Own feedback-surface planning, registration, request binding, and answer consumption. It should sit near the existing stage feedback/meeting policy modules while preserving the rule that rendering controls does not grant authorization.

#### `live_stage/adapter.py`

Retain `LiveStageAdapter` as the public facade and stable import target. Its execution flow should read as explicit phases:

```text
load and validate
    -> check replay
    -> prepare stage context
    -> plan stage-specific execution
    -> dispatch and collect
    -> record durable evidence
    -> produce feedback projection
    -> summarize for the supervisor
```

Typed intermediate dataclasses should carry prepared context, planned execution, recorded evidence, and projected feedback between phases.

## Existing modules to reuse

The refactor should build on the current pure DBTL modules rather than introduce replacement scripts:

- `stage_runner.py` — execution planning, dispatch protocol, result collection.
- `stage_spec.py` — versioned stage contracts and budgets.
- `agent_selector.py` — capability-based worker selection.
- `worker_result.py` — structured worker-result validation.
- `council.py`, `council_proposal.py`, `council_settings.py` — Design meeting policy and values.
- `revision_intent.py` — revision route interpretation.
- `review_markdown.py`, `review_paths.py`, `council_deck.py` — artifact rendering.
- `stage_feedback.py`, `stage_meetings.py`, `stage_routes.py` — feedback and transition policy.
- `utils/oneshot_llm.py` — private, non-streamed one-shot model calls.

`agents/dbtl/orchestrator.py` is a Phase 1 stub run target with placeholder artifact production. It is not a replacement for the production project supervisor or `LiveStageAdapter`.

## Recommended sequence

### Phase 1 — Extract protocol helpers

1. Introduce the Human Input envelope factory.
2. Convert the six existing card builders without changing their returned messages.
3. Introduce the message-history view and migrate existing recovery helpers.
4. Keep compatibility re-exports for private helpers directly imported by tests where useful during the transition.

This phase is mostly pure transformation and provides the safest initial reduction in `supervisor.py`.

### Phase 2 — Extract the continuation handler

1. Move `cycle_continuation()` into an injected handler.
2. Preserve its exact ordering: review intent, recovered card state, preflight, adapter execution, feedback binding, clarification, and artifact presentation.
3. Leave graph topology and terminal-branch behavior unchanged.

### Phase 3 — Separate Design orchestration

1. Extract the Design hold/resume/revision decision into a typed plan.
2. Move Design work-unit builders and debate-completeness policy together.
3. Keep generic stage planning on `stage_runner.plan_stage`/`arun_stage`.
4. Preserve approved-roster, participant-setting, and resumed-chair semantics exactly.

### Phase 4 — Extract production IO services

1. Move the production subagent dispatcher.
2. Move package/deck file production.
3. Move feedback-surface production and registration.
4. Keep `LiveStageAdapter` as a facade coordinating these injected services.

### Phase 5 — Consolidate one-shot model factories

After the larger boundaries are stable, introduce a small DBTL factory for configured fail-soft one-shot readers/writers. Do not obscure the different failure directions: debate intent holds, revision interpretation takes the cheap route, roster failure falls back to selection, and transition assessment falls back to standard.

## Guardrails

This must remain a behavior-preserving refactor. In particular:

- Ordinary work continues to invoke the real lead-agent graph.
- Every supervisor branch remains terminal.
- Client cycle selection remains intent, not authorization.
- The adapter continues to verify project/cycle ownership before dispatch.
- Card replies are accepted only when bound to a server-emitted request.
- Preflight choices remain answering-turn-scoped unless explicitly recovered for a chair resume.
- Internal one-shot and subagent calls remain `nostream` where required.
- Idempotent replay never dispatches workers twice.
- A worker result or deck never satisfies a human gate.
- Failed or contract-rejected participants cannot be converted into valid debate evidence.
- Review packages retain content addressing and atomic writes.
- Feedback surfaces retain exact project, cycle, thread, stage-attempt, evidence, and deck-hash bindings.
- Existing Design output should remain byte-identical where tests currently require it.
- Public factory/import paths should remain stable through facade modules or re-exports.

## Verification strategy

Perform extractions in small commits and run the focused suites after each boundary moves. At minimum, preserve coverage for:

- Supervisor routing, stream contract, and reducer idempotency.
- Card IDs, depth recovery, proposal recovery, and participant settings.
- Live stage execution and graph integration.
- Human-authored Design and chair clarification resume.
- Refinement/revision rounds and debate-completeness rules.
- Seat lifecycle events and token accounting.
- Deck theme, deck registration, and feedback-surface behavior.
- Test-stage review surfaces and progressive transition assessment.

Golden tests should compare complete emitted message payloads before and after the card-protocol extraction. Stage execution tests should compare recorded repository calls and `LiveStageResult` values before and after each adapter extraction.

## Expected result

The objective is not to minimize total repository lines. Much of the current length documents important safety and governance invariants, and those explanations should move with the code they describe rather than be deleted.

The desired result is:

- A small supervisor graph file whose topology is visible at a glance.
- A small `LiveStageAdapter` facade whose transaction phases are visible at a glance.
- Design meeting behavior isolated from generic stage execution.
- Protocol serialization, history recovery, dispatch, persistence, and feedback projection each having one clear owner.
- Smaller tests aligned with those responsibility boundaries.

