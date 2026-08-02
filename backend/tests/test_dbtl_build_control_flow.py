"""A paused Build, from the pause to the answer and back.

These reuse the real repository harness from the workflow-execution suite,
because the properties under test are the ones a fake cannot show: that a
restart actually moves the digest chain, that a retry replays committed phases
instead of re-running them, and that an answer resolved against a forged card
starts nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

# `project` and `_close_test_engine` are pytest fixtures, and pytest registers
# a fixture under the module attribute it is bound to — so they are imported by
# their own names. Each test then takes a `project` parameter, which ruff reads
# as redefining the import; the per-file ignore says that is the intent rather
# than sprinkling twenty inline suppressions through the tests themselves.
# ruff: noqa: F811
from test_dbtl_build_workflow_execution import (  # noqa: F401 - fixtures are used by name
    CANDIDATES,
    TWO_PHASE_PLAN,
    _build_stage_attempt_id,
    _close_test_engine,
    _ready_for_build,
    _runtime,
    _step,
    _WritingDispatcher,
    project,
)

from deerflow.agents.dbtl.live_stage.adapter import LiveStageAdapter
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.build_control import BuildControlAction, BuildControlKind, resolve_answer
from deerflow.dbtl.build_workflow import BuildStepKey, StepState
from deerflow.dbtl.capabilities import Capability
from deerflow.persistence.dbtl import DbtlCycleRepository

pytestmark = pytest.mark.asyncio


PAUSING_PLAN = json.dumps(
    {
        "feasibility": "planned",
        "rationale": "Simulate, look, then fit.",
        "phases": [
            {
                "phase_key": "simulate",
                "title": "Simulate",
                "objective": "Generate the training set.",
                "capability": "software_and_workflow_engineering",
                "pause_after": True,
            },
            {"phase_key": "fit", "title": "Fit", "objective": "Fit the model on the simulated data.", "capability": "statistical_analysis"},
        ],
    }
)


class _RefusingSecondPhase(_WritingDispatcher):
    """Writes the first phase properly and returns prose for the second."""

    async def __call__(self, units, *, budget):
        unit = units[0]
        if unit.role == "phase" and unit.unit_id.count("-fit-"):
            self.phase_units.append(unit)
            from deerflow.dbtl.stage_runner import DispatchOutcome

            return [DispatchOutcome(unit_id=unit.unit_id, text="I could not fit the model, sorry.")]
        return await super().__call__(units, budget=budget)


def _adapter(repo: DbtlCycleRepository, *, dispatcher, confirmation: bool = False, candidates=CANDIDATES) -> LiveStageAdapter:
    return LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(dbtl=SimpleNamespace(build_workflow_steps=True, build_plan_confirmation=confirmation)),
        candidate_provider=lambda: candidates,
        dispatcher=dispatcher,
    )


async def _run(
    repo: DbtlCycleRepository,
    root: Path,
    *,
    dispatcher,
    confirmation: bool = False,
    run_id: str = "run-1",
    build_control=None,
    candidates=CANDIDATES,
):
    return await _adapter(repo, dispatcher=dispatcher, confirmation=confirmation, candidates=candidates).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Build the approved design.",
        state={},
        config=_runtime(root, run_id=run_id),
        build_control=build_control,
    )


def _answer(card: dict, option_id: str = "", *, value: str = ""):
    resolved = resolve_answer(card, {"option_id": option_id, "value": value})
    assert resolved is not None, f"{option_id or value!r} did not resolve against the emitted card"
    return resolved


class TestThePlanIsConfirmedBeforeAnythingRuns:
    async def test_the_plan_is_shown_and_no_phase_is_dispatched(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        dispatcher = _WritingDispatcher(plan=TWO_PHASE_PLAN)

        result = await _run(repo, root, dispatcher=dispatcher, confirmation=True)

        assert result.control_request is not None
        assert result.control_request["build_control_kind"] == BuildControlKind.PLAN_CONFIRMATION.value
        assert [row["title"] for row in result.control_request["build_plan_rows"]] == ["Simulate", "Fit"]
        assert dispatcher.phase_units == []

    async def test_the_control_is_recorded_before_it_is_shown(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        result = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True)

        recorded = await repo.latest_build_collaboration(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert recorded is not None
        assert recorded["request_id"] == result.control_request["request_id"]
        assert recorded["lifecycle"] == "open"

    async def test_starting_runs_every_planned_phase(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        first = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True)

        dispatcher = _WritingDispatcher(plan=TWO_PHASE_PLAN)
        result = await _run(
            repo,
            root,
            dispatcher=dispatcher,
            confirmation=True,
            run_id="run-2",
            build_control=_answer(first.control_request, "start"),
        )

        assert result.control_request is None, result.note
        assert [unit.capability for unit in dispatcher.phase_units] == ["software_and_workflow_engineering", "statistical_analysis"]
        assert result.produced_usable_evidence, result.note

    async def test_a_confirmed_plan_is_not_asked_about_again(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        first = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True)
        await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True, run_id="run-2", build_control=_answer(first.control_request, "start"))

        again = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True, run_id="run-3")

        assert again.control_request is None, again.note

    async def test_holding_dispatches_nothing_and_says_so(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        first = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True)

        dispatcher = _WritingDispatcher(plan=TWO_PHASE_PLAN)
        result = await _run(repo, root, dispatcher=dispatcher, confirmation=True, run_id="run-2", build_control=_answer(first.control_request, "hold"))

        assert dispatcher.calls == []
        assert "Holding here" in result.note
        assert result.control_request is None

    async def test_a_hold_is_not_read_as_consent_on_the_next_turn(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        first = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True)
        await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True, run_id="run-2", build_control=_answer(first.control_request, "hold"))

        again = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True, run_id="run-3")

        assert again.control_request is not None
        assert again.control_request["build_control_kind"] == BuildControlKind.PLAN_CONFIRMATION.value

    async def test_the_confirmation_is_off_by_default(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        result = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=False)

        assert result.control_request is None
        assert result.produced_usable_evidence, result.note


class TestChangingThePlanCarriesTheOwnersWords:
    async def test_it_asks_what_to_change_before_replanning(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        first = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True)

        dispatcher = _WritingDispatcher(plan=TWO_PHASE_PLAN)
        result = await _run(repo, root, dispatcher=dispatcher, confirmation=True, run_id="run-2", build_control=_answer(first.control_request, "change"))

        assert result.control_request is not None
        assert result.control_request["input_mode"] == "text"
        assert dispatcher.calls == []

    async def test_the_follow_up_is_a_different_control_from_the_one_it_answers(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        first = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True)

        follow_up = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True, run_id="run-2", build_control=_answer(first.control_request, "change"))

        assert follow_up.control_request["request_id"] != first.control_request["request_id"]

    async def test_the_words_reach_the_planner_verbatim(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        first = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True)
        follow_up = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True, run_id="run-2", build_control=_answer(first.control_request, "change"))

        dispatcher = _WritingDispatcher(plan=TWO_PHASE_PLAN)
        await _run(
            repo,
            root,
            dispatcher=dispatcher,
            confirmation=True,
            run_id="run-3",
            build_control=_answer(follow_up.control_request, value="Split the fitting phase so the cross-validation is its own step."),
        )

        assert dispatcher.planner_units, "the plan was replayed instead of redrawn"
        assert "Split the fitting phase so the cross-validation is its own step." in dispatcher.planner_units[0].prompt

    async def test_the_decision_is_recorded_as_a_replan(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        first = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True)
        follow_up = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True, run_id="run-2", build_control=_answer(first.control_request, "change"))
        await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True, run_id="run-3", build_control=_answer(follow_up.control_request, value="Split the fitting phase."))

        assert (await repo.build_control_epochs(project_id="project-1", stage_attempt_id=stage_attempt_id))["replan"] == 1


class TestAPlanThatAsksToStopIsStoppedAt:
    async def test_a_pause_boundary_raises_a_continue_control(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        dispatcher = _WritingDispatcher(plan=PAUSING_PLAN)

        result = await _run(repo, root, dispatcher=dispatcher)

        assert result.control_request is not None
        assert result.control_request["build_control_kind"] == BuildControlKind.PHASE_PAUSE.value
        assert "Simulate is finished" in result.control_request["question"]
        assert [unit.capability for unit in dispatcher.phase_units] == ["software_and_workflow_engineering"]

    async def test_a_paused_plan_writes_no_review_package(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        result = await _run(repo, root, dispatcher=_WritingDispatcher(plan=PAUSING_PLAN))

        assert result.artifact_uri is None
        assert not result.produced_usable_evidence

    async def test_continuing_does_not_re_run_the_finished_phase(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        paused = await _run(repo, root, dispatcher=_WritingDispatcher(plan=PAUSING_PLAN))

        dispatcher = _WritingDispatcher(plan=PAUSING_PLAN)
        await _run(repo, root, dispatcher=dispatcher, run_id="run-2", build_control=_answer(paused.control_request, "continue"))

        assert [unit.capability for unit in dispatcher.phase_units] == ["statistical_analysis"]
        phases = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["phases"]
        assert [(row["phase_key"], row["status"]) for row in phases] == [("simulate", StepState.SUCCEEDED.value), ("fit", StepState.SUCCEEDED.value)]


class TestAFailedStepOffersARealChoice:
    async def test_a_failed_phase_raises_the_retry_control(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        result = await _run(repo, root, dispatcher=_RefusingSecondPhase(plan=TWO_PHASE_PLAN))

        assert result.control_request is not None
        assert result.control_request["build_control_kind"] == BuildControlKind.STEP_FAILURE.value
        assert [option["value"] for option in result.control_request["options"]] == [
            BuildControlAction.RETRY_STEP.value,
            BuildControlAction.REPLAN_BUILD.value,
            BuildControlAction.RESTART_BUILD.value,
            BuildControlAction.HOLD.value,
        ]

    async def test_the_control_states_which_step_stopped_and_why(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        result = await _run(repo, root, dispatcher=_RefusingSecondPhase(plan=TWO_PHASE_PLAN))

        assert result.control_request["step_key"] == BuildStepKey.EXECUTE_PHASES.value
        assert result.control_request["rationale"]

    async def test_a_capability_nothing_covers_is_named_rather_than_swapped(self, project) -> None:
        """No registered generalist, so the statistics phase has no stand-in."""
        repo, root = project
        await _ready_for_build(repo)

        result = await _run(
            repo,
            root,
            dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN),
            candidates=(AgentCandidate(name="builder", capabilities=frozenset({Capability.SOFTWARE_ENGINEERING})),),
        )

        assert result.control_request is not None
        assert "statistical_analysis" in result.control_request["rationale"]

    async def test_retrying_reuses_the_phase_that_succeeded(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        failed = await _run(repo, root, dispatcher=_RefusingSecondPhase(plan=TWO_PHASE_PLAN))

        dispatcher = _WritingDispatcher(plan=TWO_PHASE_PLAN)
        result = await _run(repo, root, dispatcher=dispatcher, run_id="run-2", build_control=_answer(failed.control_request, "retry"))

        assert [unit.capability for unit in dispatcher.phase_units] == ["statistical_analysis"]
        assert result.produced_usable_evidence, result.note

    async def test_replanning_draws_a_new_plan_rather_than_replaying_the_old_one(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        failed = await _run(repo, root, dispatcher=_RefusingSecondPhase(plan=TWO_PHASE_PLAN))

        dispatcher = _WritingDispatcher()
        await _run(repo, root, dispatcher=dispatcher, run_id="run-2", build_control=_answer(failed.control_request, "replan"))

        assert dispatcher.planner_units, "the committed plan replayed instead of being redrawn"
        plan_step = _step(await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id), BuildStepKey.PLAN_BUILD)
        assert len(plan_step["attempts"]) == 2

    async def test_restarting_reads_the_approved_design_again(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        failed = await _run(repo, root, dispatcher=_RefusingSecondPhase(plan=TWO_PHASE_PLAN))

        await _run(repo, root, dispatcher=_WritingDispatcher(), run_id="run-2", build_control=_answer(failed.control_request, "restart"))

        load = _step(await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id), BuildStepKey.LOAD_DESIGN)
        assert len(load["attempts"]) == 2, "a restart replayed the committed design read instead of redoing it"

    async def test_holding_after_a_failure_keeps_the_finished_phase(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        failed = await _run(repo, root, dispatcher=_RefusingSecondPhase(plan=TWO_PHASE_PLAN))

        await _run(repo, root, dispatcher=_WritingDispatcher(), run_id="run-2", build_control=_answer(failed.control_request, "hold"))

        phases = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["phases"]
        assert [row["status"] for row in phases if row["phase_key"] == "simulate"] == [StepState.SUCCEEDED.value]


class TestAForgedAnswerStartsNothing:
    async def test_an_option_the_card_never_offered_resolves_to_nothing(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        first = await _run(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), confirmation=True)

        assert resolve_answer(first.control_request, {"option_id": "restart", "value": "Restart the build"}) is None
