"""The Build plan: a real answer, a bounded one, and never a silent generalist.

`plan_build` is where a Build stops being one opaque worker, so the parser has
to hold three lines at once — `single_phase` is legitimate, shape problems
degrade rather than fail, and an unregistered capability is refused out loud.
The last one is the interesting case: degrading quietly would reintroduce the
exact bug capability selection exists to prevent.
"""

from __future__ import annotations

import json

import pytest

from deerflow.agents.dbtl.live_stage.build_phases import PhaseAssignment, phase_unit, planner_unit
from deerflow.dbtl.build_plan import (
    BuildPhase,
    BuildPhasePlan,
    PlanFeasibility,
    parse_build_plan,
    phase_slug,
    single_phase_plan,
)
from deerflow.dbtl.build_workflow import MAX_BUILD_PHASES
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_runner import BUILD_PLAN_OUTPUT
from deerflow.dbtl.stage_spec import resolve_stage_spec
from deerflow.dbtl.worker_result import failed_result

ENGINEERING = Capability.SOFTWARE_ENGINEERING.value


def _phase(index: int, *, capability: str = ENGINEERING, **overrides) -> dict:
    return {
        "phase_key": f"phase-{index}",
        "title": f"Phase {index}",
        "objective": f"Do part {index} of the work.",
        "capability": capability,
        **overrides,
    }


def _plan(*phases: dict, **overrides) -> str:
    return json.dumps({"feasibility": "planned", "rationale": "It splits cleanly.", "phases": list(phases), **overrides})


class TestAPlanIsData:
    def test_the_planner_declares_its_non_stage_worker_output_contract(self) -> None:
        unit = planner_unit(attempt_id="attempt-1", agent_name="general-purpose", context="bundle")

        assert unit.output_contract == BUILD_PLAN_OUTPUT

    def test_a_decomposition_is_read_in_order(self) -> None:
        parsed = parse_build_plan(_plan(_phase(1), _phase(2)), objective="Fit a model.")

        assert parsed.plan.feasibility is PlanFeasibility.PLANNED
        assert [phase.title for phase in parsed.plan.phases] == ["Phase 1", "Phase 2"]
        assert not parsed.degraded

    def test_the_digest_binds_the_phases_and_not_the_prose(self) -> None:
        """A reworded rationale must not invalidate completed phases."""
        one = parse_build_plan(_plan(_phase(1), _phase(2), rationale="Because."), objective="x").plan
        two = parse_build_plan(_plan(_phase(1), _phase(2), rationale="Because it splits."), objective="x").plan

        assert one.digest == two.digest

    def test_reordering_the_phases_is_a_different_plan(self) -> None:
        one = parse_build_plan(_plan(_phase(1), _phase(2)), objective="x").plan
        two = parse_build_plan(_plan(_phase(2), _phase(1)), objective="x").plan

        assert one.digest != two.digest

    def test_a_phase_may_ask_to_pause_after_itself(self) -> None:
        parsed = parse_build_plan(_plan(_phase(1, pause_after=True), _phase(2)), objective="x")

        assert [phase.pause_after for phase in parsed.plan.phases] == [True, False]

    def test_phase_skill_names_are_bounded_and_part_of_the_plan_digest(self) -> None:
        one = parse_build_plan(_plan(_phase(1, skills=["python-analysis"]), _phase(2)), objective="x").plan
        two = parse_build_plan(_plan(_phase(1, skills=["another-skill"]), _phase(2)), objective="x").plan

        assert one.phases[0].skills == ("python-analysis",)
        assert one.digest != two.digest
        assert len(parse_build_plan(_plan(_phase(1, skills=[f"s{i}" for i in range(20)]), _phase(2)), objective="x").plan.phases[0].skills) == 8

    def test_a_drawing_phase_is_bound_to_the_house_plot_style_but_a_non_drawing_phase_is_not(self) -> None:
        parsed = parse_build_plan(
            _plan(
                _phase(1, outputs=["artifacts/holdout.png"], skills=["data-analysis"]),
                _phase(2, outputs=["artifacts/metrics.csv"], skills=["data-analysis"]),
            ),
            objective="x",
        ).plan

        assert parsed.phases[0].skills == ("data-analysis", "dbtl-plot-style")
        assert parsed.phases[1].skills == ("data-analysis",)

    def test_phase_unit_carries_the_complete_skill_allowlist(self) -> None:
        phase = BuildPhase(
            phase_key="fit",
            title="Fit",
            objective="Fit the model.",
            capability=Capability.STATISTICAL_ANALYSIS,
            skills=("analysis",),
        )
        unit = phase_unit(
            PhaseAssignment(phase=phase, agent_name="general-purpose", via_generalist=True),
            index=1,
            attempt_id="attempt",
            attempt_token="token",
            spec=resolve_stage_spec("build"),
            context="context",
        )

        assert unit.skills == ("analysis",)
        assert "return version=3" in unit.prompt
        assert "execution_inputs" in unit.prompt
        assert "A numeric\n  result mentioned in summary, claims, evidence, or an output file must also\n  appear in `key_outcomes`" in unit.prompt
        assert "expected_outputs contains only files the entry point itself creates" in unit.prompt

    def test_duplicate_keys_are_disambiguated_rather_than_dropped(self) -> None:
        parsed = parse_build_plan(_plan(_phase(1, phase_key="fit"), _phase(2, phase_key="fit")), objective="x")

        keys = [phase.phase_key for phase in parsed.plan.phases]
        assert len(set(keys)) == 2

    def test_a_key_is_derived_from_the_title_not_the_position(self) -> None:
        """Position-derived keys renumber on insert, so a retry of 'phase 3'
        would silently become a retry of different work."""
        assert phase_slug("Fit the model!", index=1) == "fit-the-model"
        assert phase_slug("", index=3) == "phase-3"


class TestSinglePhaseIsARealAnswer:
    def test_one_phase_is_reported_as_single_phase(self) -> None:
        parsed = parse_build_plan(_plan(_phase(1)), objective="x")

        assert parsed.plan.feasibility is PlanFeasibility.SINGLE_PHASE
        assert not parsed.degraded
        assert parsed.plan.dispatchable

    def test_a_declared_single_phase_carries_no_failure_note(self) -> None:
        assert single_phase_plan(objective="Fit a model.").note == ""


class TestShapeProblemsDegradeRatherThanFail:
    def test_unreadable_output_still_produces_a_runnable_plan(self) -> None:
        parsed = parse_build_plan("I thought about it and here is my plan: do the thing.", objective="Fit a model.")

        assert parsed.plan.feasibility is PlanFeasibility.SINGLE_PHASE
        assert parsed.degraded and parsed.reasons == ("unparseable_plan",)
        assert "could not be read" in parsed.plan.note
        assert parsed.plan.phases[0].objective == "Fit a model."

    def test_a_fenced_plan_is_repaired(self) -> None:
        parsed = parse_build_plan("```json\n" + _plan(_phase(1), _phase(2)) + "\n```", objective="x")

        assert parsed.plan.feasibility is PlanFeasibility.PLANNED
        assert not parsed.degraded

    def test_an_over_long_plan_is_refused_and_says_so(self) -> None:
        parsed = parse_build_plan(_plan(*[_phase(index) for index in range(MAX_BUILD_PHASES + 1)]), objective="x")

        assert parsed.plan.feasibility is PlanFeasibility.SINGLE_PHASE
        assert parsed.reasons == ("plan_too_long",)
        assert str(MAX_BUILD_PHASES) in parsed.plan.note

    def test_a_phase_with_no_objective_stops_the_decomposition(self) -> None:
        parsed = parse_build_plan(_plan(_phase(1), {"title": "Nameless"}), objective="x")

        assert parsed.plan.feasibility is PlanFeasibility.SINGLE_PHASE
        assert parsed.degraded

    def test_an_empty_plan_degrades(self) -> None:
        parsed = parse_build_plan(json.dumps({"feasibility": "planned", "phases": []}), objective="x")

        assert parsed.plan.feasibility is PlanFeasibility.SINGLE_PHASE
        assert parsed.reasons == ("no_phases",)


class TestAnUnknownCapabilityIsNeverQuietlyTheGeneralist:
    def test_the_plan_stops_and_names_the_capability(self) -> None:
        """Not a degradation — a question.

        Collapsing to one software-engineering phase still *ran* the work, under
        a capability nobody asked for, leaving a note as the only trace. That is
        the silent swap wearing the degradation rule's clothes.
        """
        parsed = parse_build_plan(_plan(_phase(1), _phase(2, capability="telepathy")), objective="x")

        assert parsed.plan.feasibility is PlanFeasibility.NEEDS_INPUT
        assert parsed.reasons == ("unknown_capability",)
        assert "telepathy" in parsed.plan.clarification_question
        # Nothing is dispatchable, so nobody is left believing a phase ran.
        assert not parsed.plan.dispatchable
        assert parsed.plan.phases == ()

    def test_the_question_offers_the_capabilities_that_do_exist(self) -> None:
        parsed = parse_build_plan(_plan(_phase(1), _phase(2, capability="telepathy")), objective="x")

        assert Capability.SOFTWARE_ENGINEERING.value in parsed.plan.clarification_question

    def test_an_unnamed_capability_is_treated_the_same_way(self) -> None:
        parsed = parse_build_plan(_plan(_phase(1), {"title": "T", "objective": "O"}), objective="x")

        assert parsed.reasons == ("unknown_capability",)
        assert parsed.plan.feasibility is PlanFeasibility.NEEDS_INPUT


class TestNeedsInputIsExplicit:
    def test_a_question_is_carried_verbatim(self) -> None:
        parsed = parse_build_plan(
            json.dumps({"feasibility": "needs_input", "clarification_question": "Which trial year is the holdout?"}),
            objective="x",
        )

        assert parsed.plan.feasibility is PlanFeasibility.NEEDS_INPUT
        assert parsed.plan.clarification_question == "Which trial year is the holdout?"
        assert not parsed.plan.dispatchable

    def test_asking_without_a_question_degrades_rather_than_stalling(self) -> None:
        """A Build stopped on a question nobody can read is worse than one that
        runs as a single piece."""
        parsed = parse_build_plan(json.dumps({"feasibility": "needs_input"}), objective="x")

        assert parsed.plan.feasibility is PlanFeasibility.SINGLE_PHASE
        assert parsed.reasons == ("needs_input_without_question",)


class TestThePlanTypeRefusesIncoherentValues:
    def test_a_planned_verdict_needs_more_than_one_phase(self) -> None:
        with pytest.raises(ValueError, match="at least two phases"):
            BuildPhasePlan(
                feasibility=PlanFeasibility.PLANNED,
                phases=(BuildPhase(phase_key="a", title="A", objective="o", capability=Capability.SOFTWARE_ENGINEERING),),
            )

    def test_needs_input_must_carry_its_question(self) -> None:
        with pytest.raises(ValueError, match="must state the question"):
            BuildPhasePlan(feasibility=PlanFeasibility.NEEDS_INPUT)

    def test_phase_keys_must_be_unique(self) -> None:
        phase = BuildPhase(phase_key="a", title="A", objective="o", capability=Capability.SOFTWARE_ENGINEERING)
        with pytest.raises(ValueError, match="unique"):
            BuildPhasePlan(feasibility=PlanFeasibility.PLANNED, phases=(phase, phase))


class TestThePlannerContractNamesWhatItAllows:
    def test_the_registered_capabilities_are_listed_for_the_planner(self) -> None:
        from deerflow.dbtl.build_plan import PLANNER_CONTRACT

        assert Capability.SOFTWARE_ENGINEERING.value in PLANNER_CONTRACT
        assert "never quietly replaced by a generalist" in PLANNER_CONTRACT
        assert str(MAX_BUILD_PHASES) in PLANNER_CONTRACT


class TestCorrectionUnitCanActuallyFinish:
    """Regression for the dominant retry-failure mode: every correction of a
    reporting/packaging phase token_capped just above the old 40K ceiling
    (48–55K observed), so retries burned budget and produced nothing. The
    correction budget must leave real headroom, and the correction prompt must
    carry the same hard-rules discipline as the first attempt — corrections
    repeated exactly the mistakes those rules name, including returning a
    diagnosis with no implementation.
    """

    def _correction(self):
        from deerflow.agents.dbtl.live_stage.build_phases import (
            phase_correction_unit,
        )

        phase = BuildPhase(
            phase_key="replay",
            title="Package Replay",
            objective="Package the replay and final report.",
            capability=Capability.SCIENTIFIC_REPORTING,
            skills=(),
        )
        return phase_correction_unit(
            PhaseAssignment(phase=phase, agent_name="general-purpose", via_generalist=True),
            index=3,
            attempt_id="attempt",
            attempt_token="token",
            spec=resolve_stage_spec("build"),
            previous_workspace="/mnt/user-data/outputs/.dbtl-stage-work/x/build/y",
            previous_result=failed_result(
                capability=phase.capability.value,
                agent_name="general-purpose",
                reason="declared output missing",
            ),
            failure="declared output missing",
            result_contract="contract",
        )

    def test_the_correction_budget_has_headroom_past_the_observed_cap(self) -> None:
        from deerflow.agents.dbtl.live_stage.build_phases import (
            CORRECTION_MAX_TOKENS,
        )

        unit = self._correction()

        assert unit.max_tokens == CORRECTION_MAX_TOKENS
        # 40K provably capped every reporting correction; anything at or below
        # the observed 48-55K usage band re-introduces the failure mode.
        assert unit.max_tokens >= 200_000

    def test_the_correction_prompt_carries_the_hard_rules(self) -> None:
        prompt = self._correction().prompt

        assert "A diagnosis alone is a failed correction" in prompt
        assert "never build a venv or pip install" in prompt
        assert "importlib.metadata.version" in prompt
        assert "exactly ONE JSON object" in prompt
        assert "No absolute path literal" in prompt
        # Self-verification mechanics sank two live corrections: a reproduce
        # script invoked from the wrong cwd, then audit globs missing real files.
        assert "cd into DBTL_WORKSPACE" in prompt
        assert "exact relative path" in prompt
        # Four of five post-fix correction failures came from copying the prior
        # broken entry point and patching one symptom, inheriting the rest.
        assert "REWRITE that file cleanly" in prompt
        assert "py_compile" in prompt
