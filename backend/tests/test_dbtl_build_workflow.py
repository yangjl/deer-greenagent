"""The Build workflow as versioned data, and what makes a step's work reusable.

Build is one governed stage and five operational steps. The steps never vary —
a deployment always reads the Design, plans, executes, summarizes, and renders —
so the order lives in a versioned spec rather than in frontend conditionals or
adapter branching.

The part that earns its complexity is the **digest chain**. Its whole purpose is
to make one guarantee mechanical rather than a matter of care: a presentational
failure must never discard scientifically complete sandbox work. Today a bad
summary or a failed deck render throws away the execution that produced them.
With every step's identity bound to its predecessors' outputs, "reuse what is
still valid" and "invalidate what is downstream of a change" stop being
judgement calls.

Three properties are load-bearing and easy to lose:

* an upstream change invalidates **all and only** its descendants;
* a successful predecessor is never rerun because a later step failed; and
* an invalidated success is *reported*, not deleted — the record of what was
  attempted is the point.
"""

import pytest

from deerflow.dbtl.build_workflow import (
    BUILD_WORKFLOW_V1,
    MAX_BUILD_PHASES,
    BuildErrorCode,
    BuildStepKey,
    ExecutorKind,
    StepAttempt,
    StepState,
    input_digest,
    phase_bundle_digest,
    phase_step_material,
    project_workflow,
    resolve_build_workflow,
    resolve_build_workflow_by_key,
)


class TestTheWorkflowIsVersionedData:
    def test_the_five_steps_are_ordered_and_stable(self):
        assert [step.key for step in BUILD_WORKFLOW_V1.steps] == [
            BuildStepKey.LOAD_DESIGN,
            BuildStepKey.PLAN_BUILD,
            BuildStepKey.EXECUTE_PHASES,
            BuildStepKey.SUMMARIZE_RESULTS,
            BuildStepKey.RENDER_REVIEW_DECK,
        ]

    def test_the_spec_is_pinned_by_key(self):
        # An attempt records the exact workflow it ran under, so a later
        # deployment can still reconstruct what was reviewed.
        assert BUILD_WORKFLOW_V1.spec_key == "generic:build-workflow:v1"
        assert resolve_build_workflow().spec_key == BUILD_WORKFLOW_V1.spec_key
        assert resolve_build_workflow_by_key("generic:build-workflow:v1") is BUILD_WORKFLOW_V1

    def test_an_unknown_workflow_key_is_refused(self):
        with pytest.raises(LookupError):
            resolve_build_workflow_by_key("generic:build-workflow:v99")

    def test_every_step_has_a_visible_label(self):
        # The frontend renders the server's label; a client string table would
        # eventually describe work that did not happen.
        assert all(step.label.strip() for step in BUILD_WORKFLOW_V1.steps)

    def test_only_execute_phases_expands_into_phases(self):
        containers = [step.key for step in BUILD_WORKFLOW_V1.steps if step.executor is ExecutorKind.PHASE_CONTAINER]
        assert containers == [BuildStepKey.EXECUTE_PHASES]

    def test_loading_the_design_and_rendering_the_deck_are_deterministic(self):
        # Neither asks a model anything, which is what makes them cheap to retry.
        by_key = {step.key: step for step in BUILD_WORKFLOW_V1.steps}
        assert by_key[BuildStepKey.LOAD_DESIGN].executor is ExecutorKind.DETERMINISTIC
        assert by_key[BuildStepKey.RENDER_REVIEW_DECK].executor is ExecutorKind.RENDERER

    def test_the_plan_is_bounded(self):
        assert MAX_BUILD_PHASES == 8


class TestStepStates:
    def test_needs_input_is_terminal(self):
        # It records the question, releases the worker, and leaves the stage
        # paused. A human answer creates a *new* attempt; it never mutates this
        # one back to running.
        assert StepState.NEEDS_INPUT.is_terminal

    def test_running_is_the_only_state_that_is_not_terminal_or_queued(self):
        assert not StepState.RUNNING.is_terminal
        assert not StepState.QUEUED.is_terminal

    def test_only_succeeded_can_be_reused(self):
        assert StepState.SUCCEEDED.is_reusable
        for state in StepState:
            if state is not StepState.SUCCEEDED:
                assert not state.is_reusable


class TestTheFailureVocabularyIsClosed:
    def test_needs_input_is_deliberately_not_an_error(self):
        # It renders as "Waiting for you" and must not count against worker
        # failure telemetry.
        assert "needs_input" not in {code.value for code in BuildErrorCode}

    def test_there_is_no_code_for_a_disappointing_result(self):
        # No code here may stop a Build on a judgement Test or a human owns.
        codes = {code.value for code in BuildErrorCode}
        assert not any("implausible" in code or "inadequate" in code or "unconvincing" in code for code in codes)

    def test_presentational_failures_are_distinguishable_from_execution(self):
        # The UI has to be able to tell "the science is fine, the deck broke".
        assert BuildErrorCode.DECK_RENDER_FAILED.is_presentational
        assert BuildErrorCode.SUMMARY_CONTRACT_REJECTED.is_presentational
        assert not BuildErrorCode.SANDBOX_EXECUTION_FAILED.is_presentational


class TestInputDigests:
    def test_the_same_material_gives_the_same_digest(self):
        first = input_digest(BuildStepKey.LOAD_DESIGN, material={"artifact": "a1", "hash": "h1"})
        second = input_digest(BuildStepKey.LOAD_DESIGN, material={"hash": "h1", "artifact": "a1"})
        assert first == second

    def test_changed_material_changes_the_digest(self):
        first = input_digest(BuildStepKey.LOAD_DESIGN, material={"artifact": "a1"})
        second = input_digest(BuildStepKey.LOAD_DESIGN, material={"artifact": "a2"})
        assert first != second

    def test_the_step_is_part_of_its_own_identity(self):
        # Two steps fed identical material are still different steps.
        assert input_digest(BuildStepKey.PLAN_BUILD, material={"x": "1"}) != input_digest(BuildStepKey.SUMMARIZE_RESULTS, material={"x": "1"})

    def test_predecessor_order_is_significant(self):
        # Phase 2 reading phase 1's output is not the same as the reverse.
        assert input_digest(BuildStepKey.EXECUTE_PHASES, predecessors=("a", "b")) != input_digest(BuildStepKey.EXECUTE_PHASES, predecessors=("b", "a"))

    def test_a_changed_predecessor_changes_the_digest(self):
        assert input_digest(BuildStepKey.SUMMARIZE_RESULTS, predecessors=("a",)) != input_digest(BuildStepKey.SUMMARIZE_RESULTS, predecessors=("b",))

    def test_a_phase_bundle_binds_its_phases_in_order(self):
        assert phase_bundle_digest(("p1", "p2")) != phase_bundle_digest(("p2", "p1"))

    def test_an_empty_bundle_is_stable(self):
        assert phase_bundle_digest(()) == phase_bundle_digest(())

    def test_a_skill_content_change_changes_only_phase_material(self):
        base = dict(
            phase_key="fit",
            plan_digest="plan",
            capability="statistical_analysis",
            agent_name="general-purpose",
            via_generalist=True,
        )
        first = phase_step_material(**base, skill_bindings=("skill:r:sha256:aaa",))
        second = phase_step_material(**base, skill_bindings=("skill:r:sha256:bbb",))

        assert input_digest(BuildStepKey.EXECUTE_PHASES, material=first) != input_digest(BuildStepKey.EXECUTE_PHASES, material=second)


def _attempt(step, *, attempt=1, state=StepState.SUCCEEDED, input_digest_value="", output="out", **kwargs) -> StepAttempt:
    return StepAttempt(
        step=step,
        attempt=attempt,
        state=state,
        input_digest=input_digest_value,
        output_digest=output if state is StepState.SUCCEEDED else None,
        **kwargs,
    )


def _chain(material: dict[BuildStepKey, dict[str, str]] | None = None) -> dict[BuildStepKey, str]:
    """Expected input digests for a clean walk, given per-step material."""
    material = material or {}
    expected: dict[BuildStepKey, str] = {}
    previous: tuple[str, ...] = ()
    for step in (BuildStepKey.LOAD_DESIGN, BuildStepKey.PLAN_BUILD, BuildStepKey.EXECUTE_PHASES, BuildStepKey.SUMMARIZE_RESULTS, BuildStepKey.RENDER_REVIEW_DECK):
        digest = input_digest(step, predecessors=previous, material=material.get(step, {}))
        expected[step] = digest
        previous = (f"{step.value}-out",)
    return expected


class TestSelectingWhatIsStillValid:
    def test_a_clean_run_selects_every_step(self):
        expected = _chain()
        attempts = [_attempt(step, input_digest_value=expected[step], output=f"{step.value}-out") for step in expected]

        projection = project_workflow(attempts)

        assert [step for step, selection in projection.selected.items() if selection] == list(expected)
        assert projection.next_step is None
        assert projection.is_complete

    def test_the_first_step_with_no_valid_success_is_where_work_resumes(self):
        expected = _chain()
        attempts = [
            _attempt(BuildStepKey.LOAD_DESIGN, input_digest_value=expected[BuildStepKey.LOAD_DESIGN], output="load_design-out"),
            _attempt(BuildStepKey.PLAN_BUILD, input_digest_value=expected[BuildStepKey.PLAN_BUILD], output="plan_build-out"),
        ]

        projection = project_workflow(attempts)

        assert projection.next_step is BuildStepKey.EXECUTE_PHASES
        assert not projection.is_complete

    def test_a_failed_later_step_does_not_unselect_its_predecessors(self):
        # The whole point: a bad summary must not discard the execution.
        expected = _chain()
        attempts = [
            _attempt(BuildStepKey.LOAD_DESIGN, input_digest_value=expected[BuildStepKey.LOAD_DESIGN], output="load_design-out"),
            _attempt(BuildStepKey.PLAN_BUILD, input_digest_value=expected[BuildStepKey.PLAN_BUILD], output="plan_build-out"),
            _attempt(BuildStepKey.EXECUTE_PHASES, input_digest_value=expected[BuildStepKey.EXECUTE_PHASES], output="execute_phases-out"),
            _attempt(BuildStepKey.SUMMARIZE_RESULTS, state=StepState.FAILED, input_digest_value=expected[BuildStepKey.SUMMARIZE_RESULTS]),
        ]

        projection = project_workflow(attempts)

        assert projection.selected[BuildStepKey.EXECUTE_PHASES] is not None
        assert projection.next_step is BuildStepKey.SUMMARIZE_RESULTS

    def test_the_newest_valid_success_wins(self):
        expected = _chain()
        digest = expected[BuildStepKey.LOAD_DESIGN]
        attempts = [
            _attempt(BuildStepKey.LOAD_DESIGN, attempt=1, input_digest_value=digest, output="old"),
            _attempt(BuildStepKey.LOAD_DESIGN, attempt=2, input_digest_value=digest, output="load_design-out"),
        ]

        projection = project_workflow(attempts)

        assert projection.selected[BuildStepKey.LOAD_DESIGN].attempt == 2

    def test_a_non_successful_attempt_is_never_selected(self):
        expected = _chain()
        attempts = [_attempt(BuildStepKey.LOAD_DESIGN, state=StepState.NEEDS_INPUT, input_digest_value=expected[BuildStepKey.LOAD_DESIGN])]

        projection = project_workflow(attempts)

        assert projection.selected[BuildStepKey.LOAD_DESIGN] is None
        assert projection.next_step is BuildStepKey.LOAD_DESIGN


class TestInvalidation:
    def test_an_upstream_change_invalidates_its_descendants(self):
        clean = _chain()
        attempts = [_attempt(step, input_digest_value=clean[step], output=f"{step.value}-out") for step in clean]

        # The approved Design changed, so `load_design`'s material differs.
        changed = _chain({BuildStepKey.LOAD_DESIGN: {"design": "v2"}})
        projection = project_workflow(attempts, expected_inputs=changed)

        assert projection.next_step is BuildStepKey.LOAD_DESIGN
        assert set(projection.invalidated) == set(clean)

    def test_it_invalidates_only_its_descendants(self):
        clean = _chain()
        attempts = [_attempt(step, input_digest_value=clean[step], output=f"{step.value}-out") for step in clean]

        # A new result contract changes only `summarize_results`.
        changed = _chain({BuildStepKey.SUMMARIZE_RESULTS: {"contract": "v2"}})
        projection = project_workflow(attempts, expected_inputs=changed)

        assert projection.selected[BuildStepKey.EXECUTE_PHASES] is not None
        assert projection.next_step is BuildStepKey.SUMMARIZE_RESULTS
        assert set(projection.invalidated) == {BuildStepKey.SUMMARIZE_RESULTS, BuildStepKey.RENDER_REVIEW_DECK}

    def test_a_failed_deck_restarts_only_the_deck(self):
        clean = _chain()
        attempts = [_attempt(step, input_digest_value=clean[step], output=f"{step.value}-out") for step in clean if step is not BuildStepKey.RENDER_REVIEW_DECK]
        attempts.append(_attempt(BuildStepKey.RENDER_REVIEW_DECK, state=StepState.FAILED, input_digest_value=clean[BuildStepKey.RENDER_REVIEW_DECK]))

        projection = project_workflow(attempts)

        assert projection.next_step is BuildStepKey.RENDER_REVIEW_DECK
        assert projection.selected[BuildStepKey.SUMMARIZE_RESULTS] is not None

    def test_an_invalidated_success_is_reported_not_deleted(self):
        # The record of what was attempted is the point.
        clean = _chain()
        attempts = [_attempt(BuildStepKey.LOAD_DESIGN, input_digest_value=clean[BuildStepKey.LOAD_DESIGN], output="load_design-out")]

        changed = _chain({BuildStepKey.LOAD_DESIGN: {"design": "v2"}})
        projection = project_workflow(attempts, expected_inputs=changed)

        assert projection.invalidated[BuildStepKey.LOAD_DESIGN][0].attempt == 1

    def test_a_stale_success_is_never_selected(self):
        clean = _chain()
        changed = _chain({BuildStepKey.LOAD_DESIGN: {"design": "v2"}})
        attempts = [_attempt(BuildStepKey.LOAD_DESIGN, input_digest_value=clean[BuildStepKey.LOAD_DESIGN], output="load_design-out")]

        projection = project_workflow(attempts, expected_inputs=changed)

        assert projection.selected[BuildStepKey.LOAD_DESIGN] is None


class TestPhases:
    def test_a_phase_attempt_carries_its_plan_and_position(self):
        attempt = StepAttempt(
            step=BuildStepKey.EXECUTE_PHASES,
            attempt=1,
            state=StepState.SUCCEEDED,
            input_digest="d",
            output_digest="o",
            phase_index=2,
            phase_key="derive_marker_matrix",
            plan_digest="plan-1",
            capability="software_and_workflow_engineering",
            agent_name="general-purpose",
            via_generalist=True,
        )
        assert attempt.is_phase

    def test_a_container_attempt_is_not_a_phase(self):
        assert not _attempt(BuildStepKey.EXECUTE_PHASES, input_digest_value="d").is_phase

    def test_phases_from_another_plan_are_not_this_plan_s_phases(self):
        # A phase attempt whose plan changed is a different phase, not a retry
        # of this one.
        clean = _chain()
        phases = [
            StepAttempt(step=BuildStepKey.EXECUTE_PHASES, attempt=1, state=StepState.SUCCEEDED, input_digest="d1", output_digest="p1", phase_index=1, phase_key="a", plan_digest="plan-1"),
            StepAttempt(step=BuildStepKey.EXECUTE_PHASES, attempt=1, state=StepState.SUCCEEDED, input_digest="d2", output_digest="p2", phase_index=1, phase_key="a", plan_digest="plan-2"),
        ]
        projection = project_workflow([_attempt(BuildStepKey.LOAD_DESIGN, input_digest_value=clean[BuildStepKey.LOAD_DESIGN], output="load_design-out"), *phases])

        assert projection.phases_for("plan-1") == (phases[0],)
        assert projection.phases_for("plan-2") == (phases[1],)
