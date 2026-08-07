"""The typed control a person answers while a Build is paused."""

from __future__ import annotations

import pytest

from deerflow.dbtl.build_control import (
    MAX_CONTROL_OPTIONS,
    BuildControlAction,
    BuildControlKind,
    BuildControlOption,
    BuildControlRequest,
    change_plan_request,
    execution_preflight_request,
    paused_build_recovery_request,
    phase_pause_request,
    plan_confirmation_request,
    plan_rows,
    resolve_answer,
    step_failure_request,
    worker_question_request,
)
from deerflow.dbtl.build_plan import BuildPhase, BuildPhasePlan, PlanFeasibility
from deerflow.dbtl.capabilities import Capability

BINDINGS = {
    "cycle_id": "cycle-1",
    "stage_attempt_id": "sa-1",
    "workflow_spec_key": "generic:build-workflow:v1",
    "cycle_revision": 7,
}


def _plan(*, pause_after: bool = False) -> BuildPhasePlan:
    return BuildPhasePlan(
        feasibility=PlanFeasibility.PLANNED,
        phases=(
            BuildPhase(
                phase_key="simulate",
                title="Simulate the founder population",
                objective="Draw a founder population from the approved parameters.",
                capability=Capability.SOFTWARE_ENGINEERING,
                pause_after=pause_after,
            ),
            BuildPhase(
                phase_key="fit",
                title="Fit and evaluate the model",
                objective="Fit the model and report cross-validated accuracy.",
                capability=Capability.STATISTICAL_ANALYSIS,
            ),
        ),
        rationale="Simulation and fitting are separable and the second reads the first's output.",
        assumptions=("Marker density is as declared.",),
        open_questions=("Should family structure be held out?",),
    )


class TestTheRequestStatesItsBindings:
    def test_a_confirmation_card_names_the_cycle_step_and_plan_it_belongs_to(self) -> None:
        card = plan_confirmation_request(plan=_plan(), input_digest="d0", **BINDINGS).as_card()

        assert card["clarification_type"] == "dbtl_build_control"
        assert card["dbtl_cycle_id"] == "cycle-1"
        assert card["cycle_revision"] == 7
        assert card["stage_attempt_id"] == "sa-1"
        assert card["step_key"] == "plan_build"
        assert card["plan_digest"] == _plan().digest
        assert card["input_digest"] == "d0"

    def test_the_phases_ride_on_the_card_so_the_choice_is_about_visible_work(self) -> None:
        card = plan_confirmation_request(plan=_plan(), **BINDINGS).as_card()

        titles = [row["title"] for row in card["build_plan_rows"]]
        assert titles == ["Simulate the founder population", "Fit and evaluate the model"]
        assert card["build_plan_rows"][1]["capability"] == "statistical_analysis"

    def test_the_planners_assumptions_and_open_questions_are_shown_before_it_runs(self) -> None:
        card = plan_confirmation_request(plan=_plan(), **BINDINGS).as_card()

        assert card["assumptions"] == ["Marker density is as declared."]
        assert card["open_questions"] == ["Should family structure be held out?"]

    def test_a_control_must_state_a_question(self) -> None:
        with pytest.raises(ValueError):
            BuildControlRequest(
                kind=BuildControlKind.PLAN_CONFIRMATION,
                question="  ",
                cycle_id="c",
                stage_attempt_id="s",
                workflow_spec_key="w",
                step_key="plan_build",
                options=(
                    BuildControlOption(id="a", label="A", action=BuildControlAction.START_BUILD),
                    BuildControlOption(id="b", label="B", action=BuildControlAction.HOLD),
                ),
            )

    def test_a_choice_needs_more_than_one_option(self) -> None:
        with pytest.raises(ValueError):
            BuildControlRequest(
                kind=BuildControlKind.PLAN_CONFIRMATION,
                question="Start?",
                cycle_id="c",
                stage_attempt_id="s",
                workflow_spec_key="w",
                step_key="plan_build",
                options=(BuildControlOption(id="a", label="A", action=BuildControlAction.START_BUILD),),
            )

    def test_a_control_is_a_decision_not_a_form(self) -> None:
        options = tuple(BuildControlOption(id=f"o{index}", label=f"Option {index}", action=BuildControlAction.HOLD) for index in range(MAX_CONTROL_OPTIONS + 1))
        with pytest.raises(ValueError):
            BuildControlRequest(
                kind=BuildControlKind.STEP_FAILURE,
                question="What next?",
                cycle_id="c",
                stage_attempt_id="s",
                workflow_spec_key="w",
                step_key="execute_phases",
                options=options,
            )

    def test_a_recommendation_must_be_one_of_the_offered_options(self) -> None:
        with pytest.raises(ValueError):
            BuildControlRequest(
                kind=BuildControlKind.STEP_FAILURE,
                question="What next?",
                cycle_id="c",
                stage_attempt_id="s",
                workflow_spec_key="w",
                step_key="execute_phases",
                recommended_option_id="nothing_like_this",
                options=(
                    BuildControlOption(id="retry", label="Retry", action=BuildControlAction.RETRY_STEP),
                    BuildControlOption(id="hold", label="Hold", action=BuildControlAction.HOLD),
                ),
            )


class TestEveryOptionSaysWhatChoosingItCosts:
    def test_execution_preflight_offers_no_replan_that_cannot_add_a_tool(self) -> None:
        card = execution_preflight_request(**BINDINGS).as_card()

        assert card["error_code"] == "execution_tool_unavailable"
        assert [option["id"] for option in card["options"]] == ["retry", "hold"]
        assert "No planner or Build worker ran" in card["rationale"]

    def test_reopening_a_held_build_mints_a_new_bound_exchange(self) -> None:
        card = paused_build_recovery_request(
            previous={
                "id": "dbc-old",
                "cycle_id": "cycle-1",
                "stage_attempt_id": "sa-1",
                "workflow_spec_key": "generic:build-workflow:v1",
                "step_key": "execute_phases",
                "plan_digest": "plan-1",
            },
            cycle_revision=8,
            requested_action="replan",
        ).as_card()

        assert card["input_digest"] == "resume-after:dbc-old"
        assert card["recommended_option_id"] == "replan"
        assert [option["id"] for option in card["options"]] == [
            "retry",
            "replan",
            "restart",
            "hold",
        ]

    def test_reopening_requested_changes_does_not_claim_the_build_is_still_held(self) -> None:
        card = paused_build_recovery_request(
            previous={
                "id": "dbc-old",
                "cycle_id": "cycle-1",
                "stage_attempt_id": "sa-1",
                "workflow_spec_key": "generic:build-workflow:v1",
                "step_key": "execute_phases",
                "plan_digest": "plan-1",
            },
            cycle_revision=9,
            requested_action="replan",
            changes_requested=True,
        ).as_card()

        assert card["question"] == "This Build has requested changes. What should happen next?"
        assert "requested changes remain recorded" in card["rationale"]
        assert "earlier Hold" not in card["rationale"]

    def test_replan_states_that_finished_phases_are_discarded(self) -> None:
        card = step_failure_request(
            step_key="execute_phases",
            step_label="Run the build",
            error_code="execution_contract_rejected",
            error_summary="The second phase returned prose.",
            completed_phases=1,
            **BINDINGS,
        ).as_card()

        replan = next(option for option in card["options"] if option["id"] == "replan")
        assert "discarded" in replan["description"]

    def test_retry_states_that_finished_phases_are_kept(self) -> None:
        card = step_failure_request(
            step_key="execute_phases",
            step_label="Run the build",
            error_code="execution_contract_rejected",
            error_summary="",
            completed_phases=2,
            **BINDINGS,
        ).as_card()

        retry = next(option for option in card["options"] if option["id"] == "retry")
        assert "2 finished phases stay recorded" in retry["description"]

    def test_only_replan_and_restart_declare_that_they_discard_work(self) -> None:
        discards = {action for action in BuildControlAction if action.discards_work}

        assert discards == {BuildControlAction.REPLAN_BUILD, BuildControlAction.RESTART_BUILD}

    def test_changing_the_plan_does_not_itself_dispatch(self) -> None:
        assert not BuildControlAction.CHANGE_PLAN.dispatches
        assert not BuildControlAction.START_MEETING.dispatches
        assert not BuildControlAction.HOLD.dispatches
        assert BuildControlAction.START_BUILD.dispatches
        assert BuildControlAction.RETRY_STEP.dispatches


class TestAnAnswerIsResolvedAgainstTheCardTheServerEmitted:
    def test_a_chosen_option_carries_the_cards_bindings(self) -> None:
        card = plan_confirmation_request(plan=_plan(), input_digest="d0", **BINDINGS).as_card()

        answer = resolve_answer(card, {"option_id": "start", "value": "Start the build"})

        assert answer is not None
        assert answer.action is BuildControlAction.START_BUILD
        assert answer.cycle_id == "cycle-1"
        assert answer.cycle_revision == 7
        assert answer.plan_digest == _plan().digest
        assert answer.input_digest == "d0"

    def test_an_option_the_card_never_offered_selects_nothing(self) -> None:
        card = plan_confirmation_request(plan=_plan(), **BINDINGS).as_card()

        assert resolve_answer(card, {"option_id": "restart", "value": "Restart the build"}) is None

    def test_a_payload_that_is_not_ours_is_refused(self) -> None:
        assert resolve_answer({"clarification_type": "dbtl_stage_handoff", "options": []}, {"option_id": "start"}) is None
        assert resolve_answer(None, {"option_id": "start"}) is None

    def test_free_text_alone_cannot_answer_a_card_that_asked_for_a_choice(self) -> None:
        card = plan_confirmation_request(plan=_plan(), **BINDINGS).as_card()

        assert resolve_answer(card, {"value": "just start it"}) is None

    def test_a_free_text_control_takes_the_persons_words_verbatim(self) -> None:
        card = worker_question_request(
            question="Which trait column is the response?",
            rationale="Two columns are plausible and picking the wrong one invalidates the fit.",
            step_key="execute_phases",
            **BINDINGS,
        ).as_card()

        answer = resolve_answer(card, {"value": "  Use GY_kg_ha, not the plot yield.  "})

        assert answer is not None
        assert answer.action is BuildControlAction.ANSWER_DIRECTLY
        assert answer.comment == "Use GY_kg_ha, not the plot yield."

    def test_an_empty_free_text_reply_is_not_an_answer(self) -> None:
        card = worker_question_request(question="Which trait?", rationale="", step_key="execute_phases", **BINDINGS).as_card()

        assert resolve_answer(card, {"value": "   "}) is None

    def test_clicking_a_button_does_not_attribute_its_label_to_the_person(self) -> None:
        card = plan_confirmation_request(plan=_plan(), **BINDINGS).as_card()

        answer = resolve_answer(card, {"option_id": "hold", "value": "Hold here"})

        assert answer is not None and answer.comment == ""

    def test_a_comment_typed_beside_a_choice_is_kept(self) -> None:
        card = plan_confirmation_request(plan=_plan(), **BINDINGS).as_card()

        answer = resolve_answer(card, {"option_id": "change", "value": "Split the fitting phase in two."})

        assert answer is not None
        assert answer.action is BuildControlAction.CHANGE_PLAN
        assert answer.comment == "Split the fitting phase in two."


class TestThePauseCardCountsWhatIsLeft:
    def test_it_names_the_phase_that_finished_and_how_many_remain(self) -> None:
        card = phase_pause_request(
            plan=_plan(pause_after=True),
            completed_phases=1,
            paused_phase_title="Simulate the founder population",
            **BINDINGS,
        ).as_card()

        assert card["question"].startswith("Simulate the founder population is finished.")
        assert "1 of 2 phases are recorded. 1 still to run." == card["rationale"]
        assert card["step_key"] == "execute_phases"

    def test_continuing_does_not_re_run_what_is_committed(self) -> None:
        card = phase_pause_request(plan=_plan(pause_after=True), completed_phases=1, paused_phase_title="Simulate", **BINDINGS).as_card()

        keep = next(option for option in card["options"] if option["id"] == "continue")
        assert "not run again" in keep["description"]


class TestChangingThePlanIsASecondExchange:
    def test_it_asks_for_words_and_keeps_the_original_bindings(self) -> None:
        previous = plan_confirmation_request(plan=_plan(), input_digest="d0", **BINDINGS).as_card()

        follow_up = change_plan_request(previous=previous).as_card()

        assert follow_up["input_mode"] == "free_text"
        assert follow_up["options"] == []
        assert follow_up["dbtl_cycle_id"] == "cycle-1"
        assert follow_up["plan_digest"] == previous["plan_digest"]
        assert follow_up["input_digest"] == "d0"

    def test_a_pause_asks_only_about_the_phases_that_have_not_run(self) -> None:
        previous = phase_pause_request(plan=_plan(pause_after=True), completed_phases=1, paused_phase_title="Simulate", **BINDINGS).as_card()

        follow_up = change_plan_request(previous=previous, remaining_only=True).as_card()

        assert "have not run yet" in follow_up["question"]


class TestPlanRows:
    def test_a_plan_with_no_phases_renders_no_rows(self) -> None:
        assert plan_rows(BuildPhasePlan(feasibility=PlanFeasibility.NEEDS_INPUT, clarification_question="Which trait?")) == ()
