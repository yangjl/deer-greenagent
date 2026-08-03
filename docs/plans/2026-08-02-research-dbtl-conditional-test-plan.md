# Research DBTL: Conditional Test and Retention Qualification Plan

Status: independent design proposal

Date: 2026-08-02

Scope: computational, statistical, and bioinformatics research workflows

Method: source inspection plus an independent three-position debate. No existing file under `docs/plans/` was consulted.

## Executive decision

The current mandatory `Design → Build → Test → Learn` path does not match the research workflow this product is meant to support.

The target model is:

```text
Design (pilot intent)
    ↓
Build (execute, explore, and present scientific evidence)
    ↓ human disposition on the Build deck
    ├── Revise this Build ───────────────────────────────┐
    ├── Keep and validate ──→ Test / Validate & Preserve ┤
    └── Learn from exploration ──────────────────────────┤
                                                         ↓
Learn (mandatory synthesis)
    ├── Close the cycle
    └── Propose a linked next cycle
```

Test becomes optional. It runs only when a person decides that a Build result—positive, negative, null, methodological, or descriptive—is worth retaining for later reliance or promotion. Test is not a generic end-to-end software or contract-test stage. It is a targeted scientific qualification and preservation activity for selected, hash-bound Build evidence.

Learn becomes the mandatory intellectual endpoint. It always synthesizes the initial Design, the Build evidence, and the human's Build comments. When Test ran, Learn also incorporates its qualification evidence. When Test did not run, Learn is explicitly exploratory and cannot produce promotable or publishable scientific claims.

The central separation is:

- Build determines what was attempted, what happened, and what evidence was produced.
- The human determines whether the result is worth qualifying for retention.
- Test determines whether the selected evidence is reproducible and fit to retain, without deciding whether the finding is exciting.
- Learn determines what the cycle taught us and whether another cycle is warranted.

## Debate record

### Position A — scientific workflow

Design is a provisional pilot decision, not a production specification. Build is the scientific work: implementation, simulation, statistical exploration, bioinformatics execution, and interpretation. Its primary product is a figure-first, human-readable account of the evidence, followed by methods, provenance, deviations, and limitations.

Test should therefore be elective and retention-focused. Requiring a full validity/reproducibility stage for every exploratory result spends substantial effort before the researcher has decided that the result is useful. Minimal provenance still has to be captured during Build because it cannot be reconstructed reliably later, but a clean rerun and preservation-grade qualification are deferred until the person selects evidence to keep.

### Position B — current implementation

The current implementation treats Test as both mandatory and broadly predictive:

- The pure state machine hard-wires Build approval to Test and makes approved Test a prerequisite for Learn (`backend/packages/harness/deerflow/dbtl/cycle_state.py:90-122`).
- The stage graph maps Build directly to Test (`backend/packages/harness/deerflow/dbtl/stage_routes.py:22-25`).
- Build approval promises a `Start Test / Hold here` handoff (`backend/packages/harness/deerflow/agents/dbtl/supervisor.py:764-785`).
- `generic:test:v3` requires a generic predictive validity pack for every Test (`backend/packages/harness/deerflow/dbtl/stage_spec.py:516-535`).
- The inherited Test budget permits up to three workers, 143 graph turns, 400,000 tokens, and 900 seconds (`backend/packages/harness/deerflow/dbtl/stage_spec.py:489-513`) before the optional review meeting adds its own independent, red-team, and chair work.
- The default pack requires fold composition, predictive ceiling, direction, leakage, holdout, reproducibility, and reconciled inputs (`backend/packages/harness/deerflow/dbtl/validity.py:81-103`), even though many computational/statistical/bioinformatics cycles are simulations, descriptive analyses, QC investigations, clustering, exploratory omics, or methods development rather than predictive holdouts.
- A Test worker must return exactly the required checks plus headline metrics before any review surface exists (`backend/packages/harness/deerflow/agents/dbtl/live_stage/test_review.py:25-77`).
- Test then asks two separate chat questions—whether to convene a review meeting and which outcome route to choose (`backend/packages/harness/deerflow/agents/dbtl/supervisor.py:966-1038`)—while the Test stage sheet is inspection-only (`frontend/src/components/workspace/project-rail/build-test-review.tsx:472-517`).
- The Test HTML deck is deliberately inert while the actual decision is card-owned, splitting evidence and authority across two surfaces (`backend/packages/harness/deerflow/agents/dbtl/live_stage/adapter.py:2225-2259`).
- Even a `supported` or `not_supported` Test is not offered Return to Build or Return to Design, although a valid negative or apparently successful run may still reveal a needed implementation or hypothesis change (`backend/packages/harness/deerflow/dbtl/stage_routes.py:165-179`).
- Learn requires `human_test_validity_assessment`, structurally preventing a Build-only exploratory synthesis (`backend/packages/harness/deerflow/dbtl/stage_spec.py:537-555`).

The present design also conflates scientific interpretation with preservation readiness. A reproducibility failure is folded into `invalidated`, while a reproducible negative result is represented mainly through whether a headline threshold was met (`backend/packages/harness/deerflow/dbtl/validity.py:181-288`). Those are different scientific questions.

### Position C — red team

Making Test optional creates a serious selection-bias risk if “worth keeping” is interpreted as “positive” or “promising.” A null result, failed hypothesis, negative benchmark, or methodological failure may be scientifically valuable and worth preserving. Conversely, an exciting result may be unfit for retention.

The safe rule is not merely “skip Test when convenient.” It is:

1. A person explicitly chooses whether to qualify a Build for retention.
2. The complete Build attempt history remains visible, including unselected attempts.
3. Skipping Test records `not validated for retention`, not silent success.
4. Untested Learn output is non-promotable and non-publishable as a validated claim.
5. Promotion requires both a completed Test qualification and a later explicit human Learn decision.

The red team also rejects deferring all provenance to Test. Inputs, code/configuration identity, environment, seeds, output hashes, and figure-data bindings must be captured during Build because later reconstruction would be unreliable.

### Synthesis

The positions agree on the following model:

- Learn is mandatory; Test is conditional.
- Build always captures minimal immutable lineage but does not have to prove reproducibility.
- Test is entered through an explicit retention decision, not generic approval.
- Test separates scientific interpretation, evidence integrity, and retention readiness.
- Test uses a Design/profile-specific check set rather than imposing predictive checks universally.
- Skipping Test permits exploratory Learn but blocks claim promotion/publication.
- A material hypothesis or Design change normally becomes a linked next cycle instead of silently rewriting the evidence history of the current cycle.

## Product semantics

### Design: a pilot research intent

Design should record:

- research question or exploratory objective;
- hypothesis when one exists;
- intended data and analysis population;
- expected scientific evidence, preferably the figures/tables that would be informative;
- success, rejection, and “still informative if null” criteria;
- assumptions and known uncertainty;
- an initial validation profile or `exploratory/descriptive` classification.

Approval authorizes one Build attempt. It does not assert that the Design is permanently correct. Small implementation corrections may stay in the cycle; a material change to the hypothesis, population, estimand, analysis strategy, or interpretation should normally be proposed by Learn as a linked next cycle.

### Build: execute and show the science

Build follows the approved Design to implement the hypothesis or exploration. “Implementation” includes code, configuration, workflows, statistical models, simulations, data transformations, bioinformatics tools, and scientific visualizations. It is not limited to application software.

Build's review artifact must be human-readable in this order:

1. key figures;
2. high-level result summary in plain language;
3. what the evidence does and does not support;
4. figure/table/data bindings;
5. deviations from Design and limitations;
6. methods, environment, inputs, code/configuration, seeds, and rerun procedure;
7. the final Human gate.

A figure is preferred, not fabricated. If no meaningful figure exists, Build may present a table, effect summary, structured text result, or other scientific evidence and must explain why a figure is not appropriate. A result with neither verified evidence nor an honest no-result explanation does not get an actionable review deck; it gets one recovery control in chat.

Minimal lineage is always required at Build:

- approved Design revision and evidence hash;
- all examined input paths and server-computed hashes;
- code and configuration references;
- environment and random seeds when applicable;
- complete output manifest, including outputs not selected as headline results;
- figure-to-source-data bindings;
- deviations, limitations, and exact rerun procedure.

This is auditability, not proof of reproducibility. A clean independent rerun is deferred to Test.

### The Build Human gate

The final slide is the single decision surface. It contains one comment box and these dispositions, with nothing preselected:

1. **Keep and validate** — this result is worth qualifying for retention; bind the selected figures/tables/claims and open Test.
2. **Learn from this exploration** — synthesize the result and comments without Test; mark the result unvalidated and non-promotable.
3. **Revise this Build** — keep the Design and prior evidence, then run a new Build attempt using the human's comments.
4. **Revisit the research direction** — preserve this attempt and route to Learn with a recommendation for a linked next cycle. Same-cycle Design reopening is reserved for a pre-evidence correction, not for rewriting an interpretable attempt.

The generic word **Approve** is insufficient because it currently conflates three decisions: accepting that the execution happened, valuing the result, and choosing to preserve it. The new disposition must be typed and explicit.

The comment is always persisted verbatim. It is required for Revise and Revisit, and may identify:

- Build corrections;
- figure/presentation requests;
- scientific concerns;
- evidence selected for retention;
- qualification obligations for Test;
- next-cycle hypotheses.

No parallel chat review card should duplicate this gate. After the disposition, chat may acknowledge the recorded decision and show only the start/hold control for the route the person actually selected.

### Test: Validate & Preserve

Keep the internal stage key `test` for migration compatibility, but label it in the product as **Test · Validate & Preserve**.

Test answers:

> Can the exact Build evidence selected by the human be recreated, checked under the appropriate scientific profile, and retained with a clear statement of its limitations?

It does not answer:

- whether the result is positive or exciting;
- whether an arbitrary repository's entire unit/contract suite passes;
- whether every exploratory command must be replayed;
- whether a human must agree with the hypothesis.

#### Test entry contract

Test may start only when all of these are bound:

- a server-recorded `keep_and_validate` Build disposition;
- the exact Build attempt, package revision, and content hash;
- every Build attempt id in the cycle, the selected attempt id, and the verbatim selection rationale;
- the selected figures, tables, metrics, and claims;
- the complete Build output manifest, including unselected outputs;
- input/code/config/environment/seed lineage;
- the human's Build comments and selection rationale;
- a versioned qualification profile derived from Design and confirmed by the Build disposition.

Any changed hash makes the Test request stale and dispatches no worker.

#### Test activity model

Test becomes an ordered, server-owned workflow rather than one broad fan-out that succeeds only when a worker emits one large final JSON object:

1. **Freeze retention candidate**
   - Record the exact evidence subset selected for retention.
   - Bind every Build attempt id, the selected attempt/evidence, and the human's verbatim selection rationale in one append-only selection ledger to expose cherry-picking.
   - Resolve the qualification profile and explicit tolerances.
2. **Reproduce selected evidence**
   - Use a recreated, pinned runtime rather than merely a clean folder: dependency lock, interpreter/container identity, environment-variable policy, reference databases, external tool versions, and relevant cache policy are all hash-bound.
   - Audit files, datasets, references, and services actually accessed; an undeclared input is a discrepancy, not a silent pass.
   - Run the recorded procedure needed to recreate selected figures/tables/numeric conclusions.
   - Do not require unrelated end-to-end application tests.
   - Record environment drift and every discrepancy.
3. **Apply scientific qualification checks**
   - Run universal invariants and the selected profile's checks.
   - Domain assessors may run read-only checks in parallel only after reproduction outputs are frozen.
4. **Compare and qualify**
   - Compare original and reproduced outputs under predeclared tolerances.
   - Compute scientific interpretation and retention readiness as separate axes.
5. **Present figure-first evidence**
   - Show original-versus-reproduced figures or diagnostics first.
   - Follow with a short human-readable retention summary, discrepancies, checks, and limitations.
   - End with the Human gate in the same authenticated deck.

Only independent read-only assessments should run in parallel. Candidate freezing and reproduction are ordered because concurrent writers evaluating different world states would make the retention record ambiguous.

#### Qualification profiles

Every profile contains a small server-owned invariant core:

- evidence and hash binding;
- input/code/config/environment lineage completeness;
- selected claim-to-evidence traceability;
- clean-rerun attempt recorded;
- pinned runtime/dependency/reference manifest and actual-input audit;
- output comparison with declared tolerances;
- discrepancies and limitations preserved.

Profile-specific checks are additive. Initial profiles should include:

- `predictive_holdout` — leakage, folds, holdout integrity, direction, plausible ceilings;
- `simulation_study` — seed policy, stochastic tolerance, parameter recovery, Monte Carlo uncertainty;
- `statistical_estimation` — estimand identity, assumptions, sensitivity, uncertainty/calibration;
- `omics_bioinformatics` — sample identity, reference/database versions, filtering, normalization, multiplicity, batch/confounding checks;
- `descriptive_exploration` — transformation traceability, robustness/sensitivity, no causal or predictive overclaim.

A non-predictive cycle must never fail because a predictive holdout check was absent. `not_applicable` is permitted only when the profile explicitly allows it and the rationale is recorded.

#### Three-axis Test result

Do not collapse scientific meaning, inferential integrity, and reproducibility into one enum.

Scientific interpretation:

- `supports_design_expectation`
- `contradicts_design_expectation`
- `ambiguous`
- `descriptive_only`

Evidence integrity:

- `intact`
- `limited`
- `invalidated`
- `unknown`

`invalidated` is required for leakage, broken sample identity, uncontrolled confounding that defeats the claimed inference, impossible ceilings, corrupted folds, or another profile-defined fatal integrity failure. Such a result is not merely contradictory or ambiguous: the affected scientific inference is unusable.

Retention readiness:

- `qualified`
- `qualified_with_limitations`
- `not_qualified`
- `cannot_assess`

Examples that must be representable:

- scientifically supported but not reproducible;
- scientifically not supported but reproducible and worth retaining as a valid negative result;
- descriptive-only and qualified with limitations;
- scientifically ambiguous because required evidence is missing, with retention impossible.
- headline-supporting but integrity-invalidated because leakage or sample-identity failure makes the inference unusable.

Retention readiness governs whether the evidence can be preserved as qualified, but it does not by itself authorize every scientific claim. Evidence integrity and scientific interpretation constrain claim scope: invalidated evidence cannot support the invalidated inference; descriptive evidence may support only descriptive or methodological candidates; ambiguous evidence may support uncertainty/method lessons but not a positive inferential claim. Scientific interpretation is carried into Learn without being rewritten as a quality score.

#### Test Human gate

Replace the current meeting-choice card followed by an outcome card with one authenticated Test deck. The final slide offers:

- **Keep with qualification and continue to Learn**;
- **Do not keep; continue to Learn with the failure lessons**;
- **Retry Test**;
- **Return to Build**;
- **Revisit through Learn / propose next cycle**.

Every option remains subject to the server-computed qualification state. The deck may offer a review meeting for high-stakes work, but the meeting must produce a successor deck rather than creating another chat-owned decision channel. If no usable Test evidence exists, chat receives one recovery card instead of an empty deck.

Test failure never deletes or downgrades the Build evidence. It only changes whether that evidence may be retained as qualified.

After an interpretable Build exists, a material Design change is always represented by Learn followed by a linked next cycle; Test never rewrites the current cycle's Design. The `Revisit through Learn / propose next cycle` route must therefore be available from every Test integrity/readiness state. A same-cycle Return to Design is reserved for a pre-evidence correction before an interpretable Build exists.

### Learn: mandatory cycle synthesis

Learn accepts a tagged input union.

Exploratory path:

```text
ExploratoryLearnInput = {
  approved_design,
  all_build_attempts,
  selected_build_evidence,
  human_build_comments,
  test_run: false,
  retention_status: "not_validated"
}
```

Qualified path:

```text
QualifiedLearnInput = {
  approved_design,
  all_build_attempts,
  selected_build_evidence,
  human_build_comments,
  test_run: true,
  test_qualification,
  retention_status
}
```

Every Learn output contains:

- the initial Design question and assumptions;
- what Build actually did;
- figure-first Build findings and their evidence references;
- the human's comments and selected disposition;
- Test findings when Test ran, or an explicit statement that retention qualification was not run;
- supported, contradicted, unresolved, and purely exploratory lessons;
- methods or workflow lessons;
- limitations and what must not be claimed;
- a proposed next-cycle question/design when warranted.

An exploratory Learn may record lessons, methods, limitations, and next-cycle proposals, but it cannot create a promotable/publication-eligible scientific claim. A qualified Test path may create a provisional candidate; promotion and publication remain later, separate human decisions.

Starting a next cycle is explicit. It records `parent_cycle_id`, source evidence hashes, the human rationale, and the proposed change. It never silently mutates the completed cycle's Design.

## Durable model changes

### 1. Build disposition record

Add an append-only, human-owned record bound to the Build review surface:

```text
dbtl_build_dispositions
  id
  project_id
  cycle_id
  build_stage_attempt_id
  build_artifact_id / revision / content_hash
  disposition                  # keep_and_validate | learn_exploratory | revise_build | propose_next_cycle
  selected_evidence_refs       # figures/tables/metrics/claims
  all_build_attempt_ids
  selected_build_attempt_id
  selection_rationale_verbatim
  qualification_profile_key    # nullable unless keep_and_validate
  human_comment
  reviewer_user_id / role
  bound_db_revision
  feedback_surface_id / deck_hash
  created_at
```

This record is not inferred from generic `approve`. A repository method such as `record_build_disposition` owns the transaction, the stage transition, idempotency, and next-stage status update.

### 2. Explicit skipped Test state

Add `skipped` (or an equally explicit terminal `not_run`) to stage status. It must carry:

- reason `human_selected_exploratory_learning`;
- the Build disposition id;
- reviewer identity;
- bound Build evidence hash.

`locked` is wrong because it implies work remains blocked. `approved` is wrong because no Test occurred.

### 3. Retention qualification record

Replace the single mixed validity assessment with a versioned record containing:

- Build disposition and selected evidence binding;
- all Build attempt ids, selected attempt/evidence, and verbatim selection rationale;
- qualification profile key/version;
- scientific interpretation;
- evidence integrity;
- retention readiness;
- reproduction procedure, pinned runtime/dependency/reference manifest, actual-input audit, and recreated-environment execution record;
- original-versus-reproduced comparisons and tolerances;
- universal and profile-specific checks;
- discrepancies and limitations;
- human Test decision and route.

Existing `dbtl_validity_assessments` rows remain immutable. New attempts use a new version/table or additive fields with an explicit schema version; old rows are projected through a compatibility adapter.

### 4. Transition graph

Add route slugs:

- `keep_and_validate`: Build → Test
- `learn_exploratory`: Build → Learn, Test marked skipped
- `revise_build`: Build → Build
- `propose_next_cycle`: Build → Learn
- `learn_qualified`: Test → Learn
- `learn_not_retained`: Test → Learn
- `retry_test`: Test → Test
- `return_to_build`: Test → Build
- `learn_and_propose_next_cycle`: Test → Learn, with the linked-cycle proposal required at the Learn gate

The graph may still display Design/Build/Test/Learn, but Build no longer has a single `_NEXT_STAGE`. The chosen, human-bound route determines the edge.

Learn's prerequisite becomes:

```text
approved Build
AND (
  explicit exploratory Test skip bound to that Build
  OR completed Test qualification bound to that Build
)
```

### 5. Knowledge guard

At the server write boundary, reject candidate promotion/publication unless:

- `test_run = true`;
- retention readiness is `qualified` or `qualified_with_limitations`;
- evidence integrity is `intact` or an explicitly policy-compatible `limited` state;
- the latest Test human disposition is `keep_with_qualification`, or a later explicit superseding retention decision is bound to the same evidence and recorded in the audit chain;
- the candidate binds the same selected Build evidence and Test qualification;
- the candidate's type and claim scope are compatible with scientific interpretation and evidence integrity (`descriptive_only` and `ambiguous` cannot become unrestricted positive inferential claims; `invalidated` cannot promote the invalidated inference);
- a human explicitly promotes it.

This must be enforced in persistence, not only hidden or disabled in the UI.

## Frontend changes

### Project rail

- Label Test as `Test · Validate & Preserve`.
- Before Build disposition: Test is `Not requested`, not spinning.
- Exploratory route: Test is `Skipped — result not selected for retention`.
- Retention route: Test becomes `Ready`, `Running`, `Waiting for you`, or its terminal qualification status.
- Learn states whether it is synthesizing exploratory or qualified evidence.

### Build deck

- Keep the current Design-shell visual language.
- Put figures and high-level scientific summary first.
- Show all human choices on the final Human gate slide.
- Persist comments and evidence selections without creating a second chat gate.
- Do not use generic Approve/Reject labels for the retention decision.

### Test deck

- Use the same deck shell.
- Show the retention candidate and profile before diagnostics.
- Put original/reproduced comparisons and diagnostic figures first.
- Show scientific interpretation, evidence integrity, and retention readiness as separate labelled sections.
- Put the only Test decision gate on the final slide.
- Do not surface raw contract JSON or duplicate cards in chat.

### Chat

- Acknowledge the recorded Build/Test decision.
- Show one start/hold card only when the selected next activity has not already started.
- For exploratory Build, the card says `Start Learn / Hold here`.
- For retained Build, the card says `Start Test / Hold here`.
- A Test run with no reviewable evidence gets one recovery card with retry/return choices.

## Backend implementation sequence

### Phase 0 — pin the new semantics in pure tests

Before changing persistence or UI, add route/state tests that prove:

- Build can reach Test or Learn only through an explicit human disposition;
- Test skipped is distinguishable from approved, rejected, and locked;
- Learn accepts either a bound Test qualification or a bound exploratory skip;
- no knowledge candidate can be promoted from the exploratory path;
- all old cycle states remain readable.

### Phase 1 — persistence and compatibility

- Add Build disposition and retention qualification persistence.
- Add the append-only all-attempt retention-selection ledger and bind it into Test inputs.
- Add the explicit skipped/not-run stage status.
- Add repository read projections and idempotent writes.
- Backfill existing approved-Build/Test-open records as legacy `keep_and_validate` dispositions.
- Project existing Test assessments into the two-axis read model without rewriting old rows.
- Treat the captured `build-approve` scenario as a compatibility fixture: it must resume as a legacy retention-selected Test, not be converted to exploratory Learn.

### Phase 2 — conditional stage graph

- Replace Build's fixed next-stage assumption with disposition-driven routes.
- Add atomic Build→Learn and Build→Test transitions.
- Update Learn prerequisites to the tagged union.
- Preserve downstream invalidation rules when returning to Build or opening a linked next cycle.
- Update handoff generation to use the recorded route, never stage ordering alone.

### Phase 3 — Build gate and comment/evidence selection

- Replace Build's generic review actions with typed dispositions.
- Add bounded figure/table/claim selection to the final deck slide.
- Record comments and selection atomically with the route.
- Keep full Build manifests and all attempt ids visible to Test/Learn.
- Add stale/hash mismatch refusals before any worker dispatch.

### Phase 4 — Test workflow and contract

- Add a new versioned Test StageSpec for retention qualification; keep v1-v3 resolvable.
- Replace the universal predictive pack with a profile registry and invariant core.
- Implement ordered freeze → reproduce → assess → compare → summarize steps with replayable step records.
- Make the server derive the final qualification from persisted typed step outputs; do not depend on one worker's giant terminal object.
- Remove generic end-to-end software/contract-test language from Test prompts.
- Render one figure-first Test deck and final Human gate.
- Remove the default two-chat-card meeting/outcome ceremony.

### Phase 5 — Learn convergence and knowledge boundary

- Feed Learn the approved Design, every Build attempt, selected evidence, exact human comments, and optional Test qualification.
- Render exploratory versus qualified status prominently.
- Prevent exploratory Learn from generating promotable scientific candidates.
- Add an explicit linked-next-cycle proposal and human confirmation path.

### Phase 6 — frontend convergence and manual scenarios

- Update rail statuses, labels, activity wording, and artifact surfaces.
- Ensure live events, durable stage rows, chat/deck controls, and refresh projections agree.
- Capture scenarios for:
  - Build waiting for disposition;
  - exploratory Build → Learn with Test skipped;
  - retained Build → Test ready;
  - Test qualified → Learn;
  - Test not qualified → Build/Learn;
  - legacy `build-approve` resume.

## Acceptance criteria

1. Build review has exactly one final in-deck gate with no preselected choice and no parallel chat review card.
2. A human can select a null or negative result for retention; no route depends on a positive headline threshold.
3. `Learn from this exploration` starts no Test worker and records Test as explicitly skipped/not run.
4. Exploratory Learn is produced from Design + Build + human comments and is rejected by promotion/publication APIs.
5. `Keep and validate` starts Test only for the selected Build artifact hash; any changed binding produces a visible refusal and zero worker dispatches.
6. Every Build records minimal immutable lineage, even when Test is skipped.
7. A non-predictive simulation does not receive predictive holdout/fold/ceiling gates; a predictive design does.
8. Test recreates the pinned dependency/runtime/reference environment, audits actual inputs, reproduces selected evidence, and compares declared outputs/tolerances. An undeclared input or running only repository unit/contract tests cannot satisfy Test.
9. Scientifically supported + not reproducible and scientifically not supported + reproducible are both representable.
10. Headline-supporting evidence with leakage or broken sample identity is represented as integrity-invalidated and cannot support the affected scientific claim.
11. Test can return to Build or continue to Learn as not retained without deleting its or Build's evidence.
12. Every Test integrity/readiness state offers `Revisit through Learn / propose next cycle`; the resulting cycle binds its parent and source evidence instead of rewriting the current Design.
13. With multiple Build attempts, the Test record contains every attempt id, the selected attempt/evidence, and the human's verbatim selection rationale.
14. A Test decision of `Do not keep` blocks later promotion unless a new explicit, evidence-bound human retention decision supersedes it.
15. Learn names the exact Design revision, Build attempts, selected figures/evidence, human comments, and Test qualification when present.
16. A proposed next cycle records parent cycle, source evidence hashes, and human rationale; the old cycle remains unchanged.
17. Before retention selection, no Test spinner, worker card, or Test review control exists.
18. After refresh, stage row, activity status, deck/chat decision, and durable transition all describe the same state.
19. Existing legacy Build-approved/Test-active cycles remain resumable and are treated as retention-selected, not silently skipped.

## Explicit non-goals

- Do not remove software verification from Build. Build may and should run targeted unit, smoke, or contract checks needed to establish that the implementation executed as intended. Those checks are Build quality checks, not the meaning of the Test stage.
- Do not let a human label a result “validated” without Test evidence.
- Do not treat reproducibility as scientific truth or positivity as quality.
- Do not auto-start a new cycle from an agent suggestion.
- Do not rewrite old StageSpecs, validity rows, or transition history in place.
- Do not make Test optional only in the frontend while Learn/promotion persistence still assumes it ran.

## Recommended first implementation slice

The first safe vertical slice is the exploratory bypass:

1. add a typed Build disposition;
2. add explicit Test `skipped` state;
3. add atomic Build→Learn route;
4. change Learn to accept a bound exploratory skip;
5. block knowledge promotion from that path;
6. render `Learn from this exploration` on the Build deck;
7. prove it with one backend state-machine test, one persistence test, one deck bridge test, one frontend projection test, and one restored manual scenario.

Only after that slice is safe should the existing Test internals be replaced with profile-driven retention qualification. This order avoids a half-migration where the UI can skip Test but persistence accidentally treats the result as validated.
