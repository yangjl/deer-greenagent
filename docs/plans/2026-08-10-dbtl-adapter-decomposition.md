# Thin `live_stage/adapter.py` around Build and Test ownership

Measured at `eaa591c5`, 10 Aug 2026. Planning only; no implementation is
authorized by this document.

## Goal

Make the next DBTL pilots easier to debug by turning `LiveStageAdapter` into a
smaller compatibility facade and giving Build, Test, and feedback-surface logic
clear owners. Preserve governance, persistence, replay, and the public
`stage_execution.py` import surface.

Success is not a line-count trick. A maintainer should be able to trace:

1. how Build becomes reviewable;
2. the exact contract handed from Build to Test; and
3. why a retry, replan, deck, or Test route was selected

without reading unrelated Design or Learn branches.

## Why the earlier plan changes

`adapter.py` is 8,777 lines and `_execute_stage` is 2,078 lines. The earlier
horizontal-cut plan would move about 3,000 lines into ten helper modules while
keeping stage branches braided, then add a mutable `StageRun` bag to carry the
same hidden coupling. That makes the file shorter, not the workflow clearer.

Run 6 exposed three ownership failures that should drive the decomposition:

- Build verification and Test rerun interpreted the runtime-input contract
  differently. `eaa591c5` already tightened the rule — `rerun_spec.inputs` must
  match `phase_manifest.execution_inputs` exactly in runtime order — and added
  `test_a_rerun_record_cannot_drop_or_add_phase_runtime_inputs`. What remains is
  consolidation, not the rule: do not reimplement it.
- a completed Build with requested changes recommended `retry_step`, which
  replayed unchanged evidence instead of repairing it;
- a regenerated deck bound an older row when identical evidence had a newer
  artifact revision. The newest-row fix and regression test already landed in
  `eaa591c5`; the decomposition must preserve it.

## Chosen approach

Extract by responsibility, starting with Build and Test. Reuse the existing
domain types and modules; add no strategy framework, plugin layer, or generic
pipeline engine.

Alternatives rejected:

- **Horizontal phase functions:** safer mechanically, but preserves the braid.
- **One strategy class per stage:** clean in theory, too much change before the
  reliability campaign resumes.
- **Mechanical helper-file split:** improves navigation only and creates ten
  new places to search.

## Target boundaries

| Owner | Responsibility | Must not own |
|---|---|---|
| `LiveStageAdapter` | public facade, cycle/stage validation, delegation, `LiveStageResult` | Build phases, Test reruns, deck binding internals |
| `live_stage/feedback_surfaces.py` | plan/register a surface, bind the newest exact evidence match | stage verdicts or artifact creation |
| `live_stage/build_stage.py` | Build controls, planning, phase execution, publication, lineage, review package/deck | Test staging or Test outcome |
| `live_stage/test_stage.py` | independent rerun, typed checks, computed outcome, Test review surface | repairing or rewriting Build evidence |
| existing `deerflow.dbtl.*` modules | typed contracts and pure validation | repository or browser authority |

Keep the existing focused modules (`build_phases.py`,
`build_phase_verification.py`, `build_review.py`, `test_rerun.py`, and
`test_review.py`). The new stage modules coordinate them; they do not copy
their logic.

## Build → Test contract

`BuildRerunSpec` remains the sole cross-stage execution contract. Do not add a
second DTO.

Before Build can write reviewable evidence, one canonical validator must prove:

- the entry point is a published regular file;
- the command runs that staged entry point;
- ordered inputs and configuration are server-bound and portable;
- expected outputs are published, unique, and reproducible; and
- the phase manifest and final rerun spec agree where they overlap.

Build and Test call the same pure validator. Build applies it before creating a
review deck; Test then performs its independent staging, execution, typed
checks, and outcome computation. Compatibility cleanup for legacy records stays
at one read boundary and must not weaken validation for new records.

## Recovery rules learned from Run 6

- Offer `retry_step` only when a workflow step is failed or unfinished.
- When every Build step is complete and review requested changes, recommend
  `replan_build`; retrying must not append another identical artifact revision.
- Regenerating a deck for unchanged evidence may reuse the existing evidence,
  but its surface must bind the newest exact artifact row.
- A no-op replay returns the existing evidence/surface instead of pretending a
  new Build ran.
- Test never compensates for an invalid Build contract by guessing paths,
  injecting ambient inputs, or rewriting the spec.

## Main extraction risk

Build is interleaved with Design and Test throughout `_execute_stage`; this is
surgery, not a contiguous move. Twenty-one test files import `adapter` directly
(measured at `eaa591c5`), plus `agents/dbtl/stage_execution.py` as the only
non-test importer, with more coverage reaching it through the public facade.
Fifteen symbols are `monkeypatch.setattr` targets on `adapter` — including
`write_build_deck` (10 call sites), `get_sandbox_provider`, `resolve_spec_by_key`
and `_sha256_file`, several of which are defined elsewhere and merely imported
here, so the constraint binds the *caller*. A moved import fails loudly; a moved
patch target fails silently, leaving a green test that no longer patches
anything.

Before moving a block, map its reads, writes, helpers, monkeypatches, and durable
side-effect order. Extract one owner at a time and compare stage, artifact,
surface, and lineage records after each commit. Stop and split the step more
narrowly if the move changes persistence order, governance behavior, payloads,
or requires permanent test-only compatibility exports. Do not repair behavior
inside an extraction commit.

## Expected result of this pass

`adapter.py` should become smaller, not small. Design, Learn, shared dispatch,
and compatibility paths remain, so roughly 5,000–6,000 lines and a still-
substantial `_execute_stage` are plausible. The acceptance target is removal of
Build/Test orchestration from the adapter, not a cosmetic line count. Revisit
Design/Learn only after two pilots expose the next useful boundary.

## Implementation sequence

Each step lands separately and keeps the DBTL suite green.

### 0. Freeze the observed contracts

- Record a clean DBTL baseline.
- Add integration regressions for the five Build → Test checks above, the
  `changes_requested` replan rule and no-op replay. Confirm the existing newest-
  identical-evidence regression stays green.
- Inventory private imports and monkeypatches of `adapter`; for each moved call,
  update the test to patch the new owner and prove the patch can turn the test
  red. Do not preserve test-only private imports as permanent API.

### 1. Stabilize recovery behavior

Against the current structure, make a completed `changes_requested` Build offer
and recommend `replan_build`, not `retry_step`. Retry remains available only for
a failed or unfinished step. Land this behavior-only change and its regressions
as a separate commit before moving code.

The newest-exact-artifact binding already landed before extraction in
`eaa591c5`; verify it here rather than reimplementing it. Replay both recovery
cases in the browser and treat their persisted records as the extraction
baseline.

**Also fold in the first consolidation, because it is behaviour-preserving.**
`rerun_spec` logic currently spans nine modules (`adapter.py`,
`build_phase_verification.py`, `build_phases.py`, `test_rerun.py`,
`dbtl/build_deck.py`, `build_driver.py`, `build_execution.py`, `stage_runner.py`,
`stage_spec.py`), and the `execution_inputs ⊆ declared_inputs` check appears
three times inside `build_phases.py`: standalone and identical at lines 253 and
293, and again at 319 as a sub-clause of a larger boolean. Collapsing all three
onto one named predicate changes no behaviour, is provable by the existing
suite, and is the first real move toward the single canonical validator — so it
belongs here rather than inside an extraction commit. Do not
widen scope past the duplicate check in this step; the remaining eight modules
are surveyed, not refactored.

### 2. Extract feedback-surface ownership

Move `_bound_evidence`, surface planning, and registration together. Keep the
repository write boundary and authenticated deck protocol unchanged. Replay the
Run 6 identical-evidence case in the browser before proceeding.

### 3. Extract Build coordination

Move Build planning, controls, phase execution, publication, lineage, summary,
and review-deck orchestration into `build_stage.py`. Keep worker dispatch and
persistence injected through the seams already used by tests. End with one
adapter call that returns `LiveStageResult`.

This commit is structural: preserve the recovery behavior frozen in step 1. If
the extraction needs a behavior correction, stop and land that correction
separately against the current owner first.

### 4. Extract Test coordination

Move Test preparation, independent rerun, audit, outcome, and review-surface
orchestration into `test_stage.py`. Keep `TestReviewService` as the owner of the
human outcome and route decision.

Test receives only validated Build lineage. It may report invalid evidence; it
may not repair it.

### 5. Thin the facade

Reduce `_execute_stage` to validation plus delegation. Move shared dispatch
only if Build and Test extraction leaves a clearly independent block; otherwise
leave it. Defer Design/Learn decomposition until two post-refactor pilots show
where the remaining pain is.

### 6. Pilot gate before Runs 7–8

Run one small canary through Build → Test using both an input-consuming script
and a self-contained script. Then replay Run 6 from its durable Build replan
control. Resume Runs 7–8 only when:

- Build review refuses every non-portable rerun contract;
- Test executes the exact accepted contract without normalization surprises;
- retry/replan controls select the intended work;
- every deck remains actionable after refresh; and
- no Lead or new-cycle route captures an existing-cycle repair request.

## Verification per step

1. Focused red/green contract tests.
2. All affected DBTL backend tests, Ruff, formatting, and `git diff --check`.
3. OCR review of the complete step diff.
4. Browser replay at the durable checkpoint affected by that step.
5. Compare persisted stage, artifact, surface, and lineage records before and
   after refresh; diagnostics remain read-only.

## Acceptance criteria

- `LiveStageAdapter` contains no Build or Test execution pipeline.
- Build and Test have one typed, canonical rerun validation path.
- No Build/Test coordinator method exceeds roughly 200 lines; line count is a
  review warning, not an automated target.
- Adapter compatibility exports are limited to the established public facade;
  test-only private re-exports are removed as their owners move.
- No migration, schema version, gate weakening, or direct database repair is
  introduced.
- The canary and Run 6 replay pass before the reliability campaign resumes.

## Non-goals

- No all-stage rewrite.
- No `StageRun` god object or generic pipeline framework.
- No ten-module mechanical shuffle.
- No Design/Learn refactor before Build/Test pilot evidence justifies it.
- No commit, push, or implementation as part of this planning pass.
