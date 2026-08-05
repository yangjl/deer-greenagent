# DBTL interactive decks, deliverable contracts, and ten-minute replay plan

**Status:** Approved design; phased implementation plan. This document does not
authorize a release, merge, or deployment.

**Date:** 2026-08-05

**Scope:** Repair the current DBTL manual flow from Design through Learn, keep
authenticated HTML decks as the precise human-review channel, replace bespoke
chat progress presentation with DeerFlow's ordinary chat cards, and introduce a
typed Design → Build → Test deliverable contract. Validate the result with the
paired, human-replayable ten-minute experiment in
[`docs/dbtl-linear-10m-manual-experiment.md`](../dbtl-linear-10m-manual-experiment.md).

## 1. Outcome

A person can complete one small DBTL cycle without encountering an inert deck,
a hidden control, an ambiguous free-text transition, or a progress display that
behaves differently from ordinary chat.

The interaction model is intentionally split:

- authenticated HTML decks own evidence review, slide-specific comments, and
  review decisions;
- ordinary chat Human Input Cards own cycle setup, Start/Hold handoffs, and
  recoverable missing-input prompts;
- ordinary chat task cards own worker and meeting progress;
- free-text chat owns discussion, explanation, preparation, and cycle intent,
  but never a durable DBTL verdict; and
- the project rail and stage sheets remain read-only evidence and audit views.

The Design meeting defines at most ten human-facing deliverables. Build produces
or attempts each one, including one short human-rerunnable notebook for a
computational cycle. Test independently reruns the machine contract and audits
whether every promised deliverable was delivered or at least attempted. Learn
receives the human-owned Test outcome and the audited deliverable record; it
cannot infer promotion or publication.

## 2. Decisions already made

### 2.1 Review authority stays in HTML decks

Do not migrate stage review into chat cards. The registered deck is the only
surface allowed to submit, approve, request changes, reject, choose a
Test-compatible route, or make a Learn disposition for the evidence it shows.

Every deck is inert on disk. It becomes interactive only when the authenticated
artifact parent verifies:

- the exact deck SHA-256;
- project, cycle, stage, and originating conversation;
- stage attempt and evidence revision;
- current cycle database revision;
- the server-owned set of allowed actions; and
- that the surface is current rather than superseded.

The iframe sends bounded intents through the existing `postMessage` bridge. It
never receives credentials or calls a mutation endpoint directly. The parent
and backend both validate the intent; parent validation is a UX boundary and
backend validation is the authority boundary.

### 2.2 Comments are slide-specific

Every reviewable slide has a stable server-known `slide_id` and its own comment
draft. The final action submits all non-empty comments as a bounded ordered map.
The action also records which slide was visible when it was submitted.

The backend accepts comments only for slide IDs registered on that exact
surface. A stale or invented slide ID is refused rather than silently attached
to a different revision. A failed action restores the same drafts and client
submission ID. A successful action is immutable.

Initial bounds:

- no more than 20 commentable slides per surface;
- no more than 2,000 UTF-8 characters per slide comment;
- no more than 10,000 UTF-8 characters across one action; and
- empty comments are omitted from the durable payload.

These are transport and review bounds, not limits on deck prose.

### 2.3 Free-text chat is conversational, not authoritative

Free text may:

- start a cycle proposal;
- answer ordinary project questions;
- discuss evidence or cycle status;
- prepare supporting, explicitly ungoverned work; and
- ask for a rerun after a server-owned control has made it legal.

Free text may not:

- submit, approve, reject, revise, or route a stage;
- record a slide comment;
- answer a deck decision;
- promote or publish a Learn candidate; or
- turn Lead Agent output into governed stage evidence.

When a current deck is awaiting review, phrases such as “approve,” “continue,”
or “go ahead with Build” produce a deterministic pointer to the deck. They do
not mutate the cycle. When a Start/Hold or recovery card is waiting, the card
remains the explicit control; an unrelated visible message may start ordinary
discussion without consuming it.

### 2.4 Agent roles remain separated

- The Lead Agent is the conversational coordinator. It can discuss and prepare,
  but cannot execute, record, or advance a governed stage.
- A Build worker receives a bounded `WorkUnit`, attempt-scoped write grant, and
  typed completion contract.
- A Test worker independently receives the frozen Build outputs and rerun
  declaration. It cannot rely on the builder's narrative as proof.
- Specialist agents are selected by capability for ordinary delegation,
  Design meeting seats, or stage work. Their output is advisory until the
  stage adapter validates and records a typed result.
- No worker, specialist, chair, or Lead Agent can approve its own evidence.

For the small replay, use the smallest credible roster: one independent Design
position, one red-team position, one chair, one Build implementer, one
independent Tester, and one Learn synthesizer.

### 2.5 Deliverables are a cross-stage contract

The Design meeting produces one `DeliverableManifest` with 1–10 human-facing
items. Infrastructure files do not count toward the ten-item limit. The Build
notebook does count.

Each planned deliverable has:

```json
{
  "id": "fit-figure",
  "title": "Observed versus fitted values",
  "kind": "figure",
  "required": true,
  "expected_paths": ["outputs/fit.png"],
  "acceptance_criteria": ["Axes are labelled", "All holdout rows appear"],
  "validation": "Open the PNG and compare plotted values with predictions.csv",
  "capabilities": ["data-analysis", "visualization"]
}
```

Allowed initial kinds are `figure`, `table`, `image`, `equation`, `software`,
`notebook`, `report`, `dataset`, and `other`. Kinds describe presentation, not
authority.

For computational cycles, Design must include exactly one short `.ipynb` or
`.Rmd` notebook that acts as the human rerun playbook. The notebook should be
linear, concise, and point to the same commands and expected outputs as the
machine rerun record. It must not hide the actual implementation in opaque
notebook state.

Build records one fulfillment entry per planned deliverable:

```json
{
  "deliverable_id": "fit-figure",
  "status": "delivered",
  "artifact_paths": ["outputs/fit.png"],
  "artifact_hashes": ["<sha256>"],
  "attempt_log": "outputs/logs/fit-figure.log",
  "notes": "Generated from the pinned holdout rows."
}
```

The closed status vocabulary is:

- `delivered` — acceptance evidence exists;
- `attempted_failed` — execution evidence exists but acceptance failed;
- `blocked` — a named external or human-owned blocker prevented completion;
- `not_attempted` — no bounded attempt evidence exists; and
- `not_applicable` — allowed only after a human-approved Design revision makes
  the item inapplicable.

Build also emits the existing typed rerun specification as JSON. It is not one
of the ten deliverables. It binds inputs, environment, command, seed, expected
outputs, and checks for Test.

Test produces two distinct results:

1. scientific validity, using the outcome-compatible validity pack; and
2. deliverable fulfillment, checking every planned item against the Build
   fulfillment manifest and rerun evidence.

A required `not_attempted` item is a hard contract failure. A documented failed
attempt is not falsely called delivered; it remains visible and can produce an
`inconclusive` or `invalidated` scientific outcome according to the applicable
policy. Delivery completeness and scientific support are displayed separately.

## 3. Reuse and minimality constraints

This plan extends existing owners rather than introducing a second review or
execution stack:

- render and bridge decks through
  `backend/packages/harness/deerflow/dbtl/council_deck.py`, the stage-specific
  deck renderers, `frontend/src/core/dbtl/design-deck-feedback.ts`, and
  `frontend/src/components/workspace/artifacts/artifact-file-detail.tsx`;
- persist surface/action provenance through
  `design_feedback_ops.py` and the existing stage feedback tables;
- express worker output through `StageWorkerResult`, `BuildExecutionBundle`,
  and the current Build rerun specification;
- run Test reruns through `live_stage/test_rerun.py`;
- render progress with existing `SubtaskCard`, `Task`, and ordinary message
  grouping; and
- preserve existing Human Input Card transport and recovery logic.

Do not add a deck frontend framework, a direct iframe API client, a second stage
review endpoint, a new progress store, or a lead-like Build graph. Prefer one
nullable JSON column on the existing action row over a new comments table unless
query requirements prove the column insufficient.

## 4. Phased implementation

Every phase is test-first, independently reviewable, and leaves the existing
manual checkpoint restorable. Do not combine a persistence migration, routing
change, and UI rewrite in one patch.

### Phase 0 — Freeze the baseline and failure inventory

**Goal:** Make the current failures reproducible before changing behavior.

**Work:**

1. Restore `linear-10m-start` in the manual profile and record the current
   branch, profile config hash, database hash, project-tree hash, selected
   model, and service health.
2. Run the DBTL and ordinary prompts from the manual guide concurrently.
3. Capture every dead or disabled deck action with surface ID, stage, deck hash,
   server `allowed_actions`, parent state, browser console error, request, and
   response.
4. Record custom progress panels, duplicate controls, hidden cards, misleading
   free-text routing, and reload behavior.
5. Preserve the already-known Test failure checkpoint; do not overwrite
   `linear-10m-start`.

**Files:**

- `docs/dbtl-linear-10m-manual-experiment.md`
- `scripts/dbtl_manual.py` only if capture metadata is missing
- focused regression fixtures under `backend/tests/` and `frontend/tests/e2e/`

**Exit:** Each observed defect has a failing automated test or a precisely
replayable manual step. The ordinary arm still has an untouched baseline.

### Phase 1 — Make every registered deck action live or visibly read-only

**Goal:** Eliminate dead buttons before adding new deck behavior.

**Backend work:**

1. Consolidate the surface read model so Design, Reconciliation, Build, Test,
   and Learn expose stage, mode, current/superseded state, evidence binding,
   allowed actions, and failure/retry state consistently.
2. Refuse registration of an actionable surface whose rendered deck does not
   contain the bridge controls required by its mode.
3. Ensure Test review and route decisions, and Learn disposition decisions,
   can be expressed through the same authenticated stage-feedback action path.
4. Keep action legality server-derived. The deck cannot invent a route that the
   Test outcome or Learn policy does not permit.
5. Preserve idempotent `client_submission_id` retry semantics and the existing
   handoff-delivery watcher.

**Frontend work:**

1. Reduce deck initialization to one state machine: loading, ready, submitting,
   accepted, stale, or failed.
2. Disable a control only with a visible reason from the parent state.
3. On failure, restore the action and drafts. On success, settle the exact
   surface. On stale state, link to the successor without rebasing the action.
4. Remove the parent-level bespoke progress banner; action state belongs in the
   deck and stage execution progress belongs in chat.

**Primary files:**

- `backend/packages/harness/deerflow/dbtl/council_deck.py`
- `backend/packages/harness/deerflow/dbtl/build_deck.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/test_review.py`
- `backend/packages/harness/deerflow/persistence/dbtl/design_feedback_ops.py`
- `backend/app/gateway/routers/dbtl_cycles.py`
- `frontend/src/core/dbtl/design-deck-feedback.ts`
- `frontend/src/core/dbtl/cycles-api.ts`
- `frontend/src/components/workspace/artifacts/artifact-file-detail.tsx`

**Tests first:**

- `backend/tests/test_dbtl_deck_bridge.py`
- `backend/tests/test_dbtl_design_feedback_surface.py`
- `backend/tests/test_dbtl_test_review_surface.py`
- `backend/tests/test_dbtl_phase8_knowledge.py`
- `frontend/tests/unit/core/dbtl/design-deck-feedback.test.ts`
- `frontend/tests/e2e/design-deck-bridge.spec.ts`
- `frontend/tests/e2e/design-deck-feedback.spec.ts`

**Exit:** Every control on every current actionable deck either completes its
server-authorized action or shows a stable reason it cannot. Reloading does not
turn a live control into an inert downloaded-deck experience.

### Phase 2 — Add durable slide-specific comments

**Goal:** Let a reviewer comment on several precise questions before submitting
one decision.

**Work:**

1. Add stable slide IDs to the common deck renderer and stage-specific slides.
2. Register the ordered commentable slide IDs and titles in server-owned surface
   metadata.
3. Add one comment draft per slide and a compact “commented” indicator in the
   deck navigation.
4. Extend the bridge intent and REST request with bounded `slide_comments` and
   `active_slide_id`.
5. Validate IDs and limits in the iframe parser, parent parser, Pydantic model,
   and repository write boundary.
6. Persist the canonical mapping on the existing feedback action. Add one JSON
   column through the next DBTL migration only if the current action JSON cannot
   preserve it without mixing input with mutable receipt state.
7. Render slide-attributed comments in refinement prompts, review projections,
   the audit timeline, and successor decks.
8. Preserve drafts in the parent while the artifact panel remains mounted; on
   failed retries restore them from the recorded failed action.

**Primary files:**

- `backend/packages/harness/deerflow/dbtl/council_deck.py`
- `backend/packages/harness/deerflow/persistence/dbtl/model.py`
- `backend/packages/harness/deerflow/persistence/dbtl/design_feedback_ops.py`
- `backend/packages/harness/deerflow/persistence/migrations/versions/0032_dbtl_slide_comments.py` only if required
- `backend/app/gateway/routers/dbtl_cycles.py`
- `frontend/src/core/dbtl/design-deck-feedback.ts`
- `frontend/src/core/dbtl/cycles-api.ts`
- `frontend/src/components/workspace/artifacts/artifact-file-detail.tsx`

**Tests first:** malformed/duplicate/unknown IDs, per-comment and total bounds,
hash-bound retry, stale surfaces, multi-slide submission, refinement prompt
attribution, refresh after failure, and legacy decks with no slide comments.

**Exit:** A reviewer can comment on at least three slides, submit once, and see
each comment attributed to the same slide in durable history and the revision
request.

### Phase 3 — Restore ordinary chat cards and progress

**Goal:** Remove DBTL-only progress chrome without losing live or durable worker
visibility.

**Work:**

1. Project Design meeting seats and stage workers into the existing task event
   path used by ordinary `task()` calls.
2. Render them with the existing `SubtaskCard`/`Task` components and normal
   message grouping.
3. Remove `DebatePanel` and `StageWorkPanel` only after standard cards cover
   running, completed, failed, cancelled, replayed, and refresh-recovered work.
4. Keep setup, Start/Hold, and recovery prompts on `HumanInputCard`; do not add a
   DBTL card wrapper.
5. Keep the composer enabled. A visible free-text message bypasses, but does not
   answer or consume, a pending card or deck.
6. Add deterministic “review the current deck” receipts for free-text verdict or
   transition phrases while a deck action is waiting.

**Primary files:**

- `backend/packages/harness/deerflow/dbtl/stage_runner.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/adapter.py`
- `frontend/src/components/workspace/messages/message-list.tsx`
- `frontend/src/components/workspace/messages/message-list-item.tsx`
- `frontend/src/components/workspace/messages/debate-panel.tsx` (delete when unused)
- `frontend/src/components/workspace/messages/stage-work-panel.tsx` (delete when unused)
- `frontend/src/core/tasks/`
- `frontend/src/core/messages/human-input.ts`

**Tests first:** standard-card live updates, terminal recovery after refresh,
meeting participant identity, failure details, no duplicate progress panel, and
free-text “approve/go ahead” causing no DBTL mutation.

**Exit:** DBTL work is visually consistent with ordinary chat progress, while
the deck remains the only stage-review input surface.

### Phase 4 — Make Design produce the deliverable manifest

**Goal:** Turn the Design meeting's synthesis into an executable product
contract rather than prose about desired outputs.

**Work:**

1. Add pure `DeliverableSpec` and `DeliverableManifest` parsing/validation near
   the existing stage contracts. Keep the maximum at ten human-facing items.
2. Require the chair synthesis to name deliverables, acceptance criteria,
   expected paths/formats, validation methods, and needed capabilities.
3. Require exactly one notebook deliverable for computational cycles. Accept
   `.ipynb` or `.Rmd`; do not require both.
4. Reject duplicate IDs, absolute/escaping paths, empty acceptance criteria,
   unsupported kinds, or a manifest beyond the cap.
5. Store the manifest inside the content-addressed Design review package and
   include a concise deliverables table in the Design deck.
6. Carry the approved manifest, not an LLM re-summary, into Build input.

**Primary files:**

- `backend/packages/harness/deerflow/dbtl/stage_spec.py`
- new pure module `backend/packages/harness/deerflow/dbtl/deliverables.py`
- `backend/packages/harness/deerflow/dbtl/consensus.py`
- `backend/packages/harness/deerflow/dbtl/review_markdown.py`
- `backend/packages/harness/deerflow/dbtl/council_deck.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/design_input.py`
- `backend/packages/harness/deerflow/dbtl/build_input.py`

**Tests first:** 1/10/11 items, notebook requirement by cycle class, unsafe paths,
duplicate IDs, missing acceptance criteria, content-hash stability, and exact
Build propagation.

**Exit:** An approved Design evidence package contains one valid manifest, and
Build receives the same hash-bound bytes.

### Phase 5 — Make Build fulfill or attempt every deliverable

**Goal:** Produce reviewable products plus one concise human rerun playbook and
one machine rerun contract.

**Work:**

1. Extend the existing Build plan so every planned deliverable is assigned to a
   bounded phase and no deliverable silently disappears during plan reduction.
2. Extend `StageWorkerResult`/`BuildExecutionBundle` with bounded fulfillment
   entries keyed by approved deliverable ID.
3. Verify artifact containment, existence, hashes, role, and claimed status at
   the adapter boundary. Worker prose is not delivery evidence.
4. Ensure the notebook opens, has a short ordered playbook, references pinned
   inputs, and invokes or explains the same command recorded by the rerun JSON.
   Do not execute notebook cells as the Build authority check; Test owns the
   independent rerun.
5. Publish the rerun JSON beside the Build evidence using the existing
   `BuildRerunSpec`; do not invent a second machine contract.
6. Generate a Build deck with product previews where safe, a planned-versus-
   actual table, attempt evidence, and slide comment fields.
7. Refuse `ready_for_review` when a planned item is absent from the fulfillment
   manifest. `attempted_failed` and `blocked` may reach review; `not_attempted`
   is visibly a contract failure.

**Primary files:**

- `backend/packages/harness/deerflow/dbtl/worker_result.py`
- `backend/packages/harness/deerflow/dbtl/build_execution.py`
- `backend/packages/harness/deerflow/dbtl/build_plan.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/build_phases.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/build_phase_verification.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/build_recorder.py`
- `backend/packages/harness/deerflow/dbtl/build_summary.py`
- `backend/packages/harness/deerflow/dbtl/build_deck.py`

**Tests first:** full/partial/blocked/not-attempted manifests, forged paths,
wrong hashes, notebook missing or too opaque, rerun conflict, product previews,
and one-phase manual fixture compatibility.

**Exit:** Build review names every promised product and can never imply that a
missing item was delivered.

### Phase 6 — Make Test rerun and audit deliverables

**Goal:** Test both reproducibility and promise fulfillment independently.

**Work:**

1. Start Test from the persisted Build rerun JSON in a fresh Test workspace.
2. Retain command, environment, input hashes, exit status, logs, produced
   hashes, and output comparisons as server-owned rerun evidence.
3. Add a deterministic deliverable audit that joins approved Design items to
   Build fulfillment entries and actual published files.
4. For visual/non-executable products, verify existence, hash, media/type
   readability, dimensions/schema when applicable, and declared acceptance
   checks that can be evaluated mechanically. Mark human-only checks explicitly
   rather than inventing a pass.
5. Require the Tester worker to inspect every item and return a typed audit. The
   server overwrites any model claim contradicted by filesystem/rerun facts.
6. Keep scientific validity outcome separate from delivery completeness. Both
   appear in the Test deck.
7. Move the Test review meeting, computed outcome explanation, and
   outcome-compatible route into the authenticated Test deck. Remove duplicate
   verdict cards from chat; retain only recovery and the next Start/Hold card.

**Primary files:**

- `backend/packages/harness/deerflow/agents/dbtl/live_stage/test_rerun.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/test_review.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/adapter.py`
- `backend/packages/harness/deerflow/dbtl/validity.py`
- `backend/packages/harness/deerflow/persistence/dbtl/build_test_ops.py`
- `backend/packages/harness/deerflow/dbtl/council_deck.py`
- `frontend/src/core/dbtl/validity-view.ts`

**Tests first:** clean rerun, nonzero exit, changed inputs, changed outputs,
missing notebook, every fulfillment status, unreadable visual artifact, forged
worker pass, outcome-compatible routes, retry selecting the newest complete
attempt, and Test deck refresh.

**Exit:** Test can account for every Design deliverable and reproduce the Build
without trusting its author.

### Phase 7 — Complete Reconciliation and Learn deck ownership

**Goal:** Remove the remaining split-brain review surfaces.

**Reconciliation:**

1. Render matrix rows as stable deck questions/slides with evidence and
   row-specific decisions.
2. Keep server enforcement that agents may propose but cannot close judgement
   rows.
3. Submit decisions through the authenticated parent and existing
   reconciliation endpoints; do not add generic approve authority.

**Learn:**

1. Render Design intent, slide comments, Build fulfillment, Test rerun, delivery
   audit, scientific outcome, and provisional candidates in the Learn deck.
2. Keep candidate disposition, promotion, publication, supersession, and
   retraction distinct. No stage verdict implies publication.
3. Ensure every allowed Learn action is live through the authenticated parent
   and disabled actions state why.
4. Keep the manual smoke result provisional and unpublished.

**Primary files:**

- `backend/packages/harness/deerflow/dbtl/reconciliation.py`
- `backend/packages/harness/deerflow/persistence/dbtl/reconciliation_ops.py`
- `backend/packages/harness/deerflow/dbtl/knowledge.py`
- `backend/packages/harness/deerflow/persistence/dbtl/knowledge_ops.py`
- `backend/packages/harness/deerflow/dbtl/council_deck.py`
- `frontend/src/core/dbtl/reconciliation-view.ts`
- `frontend/src/core/dbtl/knowledge-view.ts`
- `frontend/src/components/workspace/project-rail/learn-review.tsx`

**Exit:** All evidence decisions are deck-owned; stage sheets remain inspection
only, and chat contains only setup/handoff/recovery controls.

### Phase 8 — Paired ten-minute replay and repair loop

**Goal:** Prove the complete product path, not just isolated contracts.

1. Restore `linear-10m-start` and start the manual profile.
2. Sign in with the dedicated manual account and select the documented model.
3. Submit the DBTL and ordinary prompts within the same minute.
4. In DBTL, follow the replay choices and require the Design meeting to produce:
   the four original outputs, one short notebook, and no more than ten total
   human-facing deliverables.
5. Add comments on at least three different deck slides, request one revision,
   confirm the comments reach the correct refinement input, then approve the
   successor revision.
6. Exercise every enabled action at least once across success or a captured
   reversible checkpoint. Do not click a destructive/terminal alternative on
   the only live cycle when a restored checkpoint can test it safely.
7. Reload after each stage reaches review and confirm controls, drafts/failure
   recovery, progress history, and current surface state remain correct.
8. Verify Build publishes the notebook and rerun JSON; Test reruns the JSON and
   accounts for every deliverable; Learn remains provisional.
9. Verify ordinary chat completes the same numerical task without creating any
   DBTL stage event or writing into a stage-owned path.
10. Record wall time by stage, model calls, tokens, retries, dead controls,
    hidden controls, duplicate UI, output hashes, and final DBTL revisions.

If a blocker appears, capture it, add a failing regression, apply the smallest
owner-local fix, rerun the focused test, and resume from the nearest safe manual
checkpoint. Do not reset or hand-edit the governed state to make the replay
pass.

**Pass conditions:**

- both arms meet the numerical/file criteria in the manual guide;
- the DBTL arm completes Design → Build → Test → Learn in under ten minutes
  after prompts are submitted;
- every stage result is server-recorded and hash-bound;
- all human review input is attributable to a deck slide or an explicit
  setup/handoff/recovery card;
- every planned deliverable is `delivered`, `attempted_failed`, or `blocked`
  with evidence; none is silently missing or `not_attempted`;
- Test independently reruns the machine contract;
- no dead, invisible, duplicate, or misleading control remains;
- free-text stage verdicts cause no mutation;
- chat progress uses only standard task/message components; and
- Learn creates no implicit promotion or publication.

The first run may fail the ten-minute budget while exposing defects. A release
claim requires a fresh run from `linear-10m-start` after the last fix.

### Phase 9 — Review, simplification, and release evidence

**Open Code Review:**

1. Run `ocr delegate preview` for the complete diff.
2. Resolve the applicable review rules with `ocr delegate rule`.
3. Review every changed file against correctness, authorization, replay,
   idempotency, stale-surface, and filesystem-boundary risks.
4. Fix every high/critical finding and rerun affected tests.

**Ponytail review:**

- remove stage-specific bridge branches replaced by the shared contract;
- delete obsolete custom progress components and tests only after standard
  cards cover their behavior;
- remove duplicate card/deck review paths;
- reject new dependencies and speculative abstractions;
- prefer pure validators and existing JSON contracts; and
- leave an explicit `ponytail:` debt comment only for a deliberate, bounded
  deferral.

**Verification commands:**

```bash
cd backend
uv run ruff check app packages tests
uv run pytest \
  tests/test_dbtl_deck_bridge.py \
  tests/test_dbtl_design_feedback_surface.py \
  tests/test_dbtl_test_review_surface.py \
  tests/test_dbtl_build_summary_and_deck.py \
  tests/test_dbtl_build_workflow_execution.py \
  tests/test_dbtl_test_rerun.py \
  tests/test_dbtl_phase8_knowledge.py

cd ../frontend
pnpm test -- \
  tests/unit/core/dbtl/design-deck-feedback.test.ts \
  tests/unit/core/tasks/stage-work.test.ts \
  tests/unit/core/messages/human-input.test.ts
pnpm typecheck
pnpm lint
pnpm test:e2e -- \
  tests/e2e/design-deck-bridge.spec.ts \
  tests/e2e/design-deck-feedback.spec.ts

cd ..
make check
```

Adjust the exact Rstest filter syntax to the installed runner rather than
creating a wrapper. Run broader DBTL suites after focused tests pass.

**Documentation updates required with implementation:**

- `README.md`
- `AGENTS.md`
- `backend/AGENTS.md`
- `frontend/AGENTS.md`
- `docs/dbtl-linear-10m-manual-experiment.md`
- this plan's status and measured results

## 5. Error and recovery rules

- **Unverified deck:** display only; no action bridge.
- **Stale deck:** show its evidence and comments read-only, link to its
  successor, and never rebase the pending action.
- **Failed mutation:** preserve the same payload and client submission ID,
  restore drafts, and retry idempotently.
- **Recorded verdict with failed handoff:** keep the verdict immutable and retry
  only delivery of the Start/Hold card.
- **Worker failure:** record one terminal task state and attempt evidence; never
  synthesize a successful deliverable.
- **Missing required deliverable:** Build cannot claim complete; Test records
  the exact missing or unattempted item.
- **Rerun mismatch:** preserve both expected and observed hashes and let policy
  compute the validity outcome.
- **Unknown slide ID/action/route:** fail closed with a visible reason.
- **Free-text control phrase:** return the current server-owned deck/card pointer
  without mutation.
- **Service shutdown:** normal SIGTERM during `make dbtl-manual-dev` cleanup must
  not turn a successful operator stop into a misleading experiment failure.

## 6. Non-goals

- Replacing decks with chat forms.
- Giving an iframe credentials or direct API access.
- Making the Lead Agent a stage executor.
- Letting Build grade itself or Test repair Build outputs in place.
- Treating the notebook as the machine contract; the JSON remains authoritative
  for Test rerun.
- Counting logs, manifests, or rerun JSON against the ten human-facing
  deliverables.
- Automatically promoting or publishing Learn candidates.
- Building a new presentation framework, workflow engine, progress store, or
  specialist-agent hierarchy.
- Optimizing token cost before correctness, although the measured ten-minute
  replay must record cost and latency.

## 7. Delivery order and checkpoints

The safe order is:

```text
baseline
  → live deck controls
  → slide comments
  → ordinary chat progress
  → Design deliverables
  → Build fulfillment + notebook/JSON
  → Test rerun + audit
  → Reconciliation/Learn deck completion
  → paired replay
  → OCR + Ponytail + full verification
```

Do not start a later phase while an earlier authority boundary is failing. In
particular, do not add more deck controls before the current control can prove
its surface and evidence binding, and do not relax Build completion before Test
can consume its rerun contract and fulfillment manifest.
