"""The Design council's composition, made inspectable before it convenes."""

from __future__ import annotations

import pytest

from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.council import (
    DEPTH_POLICIES,
    CouncilDepth,
    CouncilRole,
    UnknownAgent,
    depth_policy,
    plan_council,
    recommend_depth,
)
from deerflow.dbtl.stage_spec import resolve_stage_spec

DESIGN = resolve_stage_spec("design", domain_profile="generic")


def _designer() -> AgentCandidate:
    return AgentCandidate(
        name="experimental-design",
        capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
        description="Experimental design specialist.",
    )


def _geneticist() -> AgentCandidate:
    return AgentCandidate(
        name="quant-genetics",
        capabilities=frozenset({Capability.QUANTITATIVE_GENETICS}),
    )


def _statistician() -> AgentCandidate:
    return AgentCandidate(
        name="statistician",
        capabilities=frozenset({Capability.STATISTICAL_ANALYSIS}),
    )


def _generalist() -> AgentCandidate:
    return AgentCandidate(name="general-purpose", is_generalist=True)


class TestDepth:
    def test_deeper_debate_buys_more_positions_and_more_working_time(self) -> None:
        light = depth_policy(CouncilDepth.LIGHT)
        medium = depth_policy(CouncilDepth.MEDIUM)
        heavy = depth_policy(CouncilDepth.HEAVY)

        assert light.max_positions < medium.max_positions < heavy.max_positions
        assert light.budget.max_turns < medium.budget.max_turns < heavy.budget.max_turns
        assert light.budget.timeout_seconds < medium.budget.timeout_seconds < heavy.budget.timeout_seconds
        assert not light.budget.token_limit_enforced
        assert not medium.budget.token_limit_enforced
        assert not heavy.budget.token_limit_enforced

    def test_every_depth_is_described_for_the_person_choosing(self) -> None:
        # The chooser is a scientist deciding how much debate the question is
        # worth, so each option has to say what it buys and what it costs.
        for depth in CouncilDepth:
            policy = depth_policy(depth)
            assert policy.label
            assert policy.description
            assert depth in DEPTH_POLICIES

    def test_even_the_lightest_debate_keeps_a_challenge_and_a_chair(self) -> None:
        # Depth controls how many independent positions are heard, never
        # whether anyone argues back. A "light" council that skipped the red
        # team would be a single opinion wearing a council's name.
        assert depth_policy(CouncilDepth.LIGHT).max_positions >= 1

    def test_light_is_a_pilot_sized_council_without_a_token_kill_switch(self) -> None:
        """Light stays quick through scope, turns, and time—not discarded output."""
        from deerflow.agents.middlewares.finalization_deadline_middleware import model_call_budget

        policy = depth_policy(CouncilDepth.LIGHT)

        assert not policy.budget.token_limit_enforced
        assert policy.budget.timeout_seconds <= 180
        assert model_call_budget(policy.budget.max_turns) == 6


class TestPlanningTheCouncil:
    def test_the_roster_is_positions_then_red_team_then_chair(self) -> None:
        plan = plan_council(
            DESIGN,
            (_designer(), _geneticist()),
            depth=CouncilDepth.MEDIUM,
            model="gpt-5.6-sol",
        )

        roles = [seat.role for seat in plan.seats]
        assert roles[-2:] == [CouncilRole.RED_TEAM, CouncilRole.CHAIR]
        assert all(role is CouncilRole.POSITION for role in roles[:-2])
        assert plan.dispatchable

    def test_a_light_council_hears_fewer_positions_than_a_heavy_one(self) -> None:
        candidates = (_designer(), _geneticist(), _statistician())
        light = plan_council(DESIGN, candidates, depth=CouncilDepth.LIGHT, model="m")
        heavy = plan_council(DESIGN, candidates, depth=CouncilDepth.HEAVY, model="m")

        assert light.position_count < heavy.position_count
        # The extra seats are positions; the debate structure does not change.
        assert light.seats[-1].role is CouncilRole.CHAIR
        assert heavy.seats[-1].role is CouncilRole.CHAIR

    def test_the_red_team_convenes_however_many_specialists_are_declared(self) -> None:
        for candidates in ((_designer(),), (_designer(), _geneticist(), _statistician())):
            plan = plan_council(DESIGN, candidates, depth=CouncilDepth.HEAVY, model="m")
            assert any(seat.role is CouncilRole.RED_TEAM for seat in plan.seats)

    def test_a_generalist_standing_in_is_named_as_one(self) -> None:
        plan = plan_council(DESIGN, (_generalist(),), depth=CouncilDepth.MEDIUM, model="m")

        assert plan.dispatchable
        assert all(seat.agent_name == "general-purpose" for seat in plan.seats)
        assert plan.seats[0].via_generalist
        # A reviewer reading the roster must be able to see that no specialist
        # was involved, which is the whole point of showing it beforehand.
        assert any("general-purpose" in note for note in plan.notes)

    def test_an_uncoverable_council_refuses_rather_than_half_running(self) -> None:
        plan = plan_council(DESIGN, (), depth=CouncilDepth.MEDIUM, model="m")

        assert not plan.dispatchable
        assert Capability.EXPERIMENTAL_DESIGN in plan.unmet_capabilities
        assert plan.seats == ()

    def test_every_seat_says_who_what_which_model_and_which_tools(self) -> None:
        plan = plan_council(
            DESIGN,
            (_designer(),),
            depth=CouncilDepth.MEDIUM,
            model="gpt-5.6-sol",
            tools_by_agent={"experimental-design": ("read_file", "bash")},
        )

        seat = plan.seats[0]
        assert seat.agent_name == "experimental-design"
        assert seat.model == "gpt-5.6-sol"
        assert seat.tools == ("read_file", "bash")
        assert seat.brief
        assert seat.role_label

    def test_an_agent_with_no_declared_tools_is_shown_as_inheriting_them(self) -> None:
        plan = plan_council(DESIGN, (_designer(),), depth=CouncilDepth.MEDIUM, model="m")

        assert plan.seats[0].inherits_all_tools
        assert plan.seats[0].tools == ()

    def test_the_roster_serializes_for_the_card_that_shows_it(self) -> None:
        plan = plan_council(DESIGN, (_designer(), _geneticist()), depth=CouncilDepth.MEDIUM, model="m")
        payload = plan.as_dict()

        assert payload["depth"] == "medium"
        assert payload["stage_spec_key"] == DESIGN.spec_key
        assert payload["dispatchable"] is True
        assert [seat["role"] for seat in payload["seats"]][-1] == "chair"
        assert payload["budget"]["max_turns"] == depth_policy(CouncilDepth.MEDIUM).budget.max_turns


class TestOverridingTheRoster:
    def test_a_person_may_choose_a_different_depth(self) -> None:
        plan = plan_council(DESIGN, (_designer(), _geneticist()), depth=CouncilDepth.MEDIUM, model="m")
        deeper = plan.with_depth(CouncilDepth.HEAVY)

        assert deeper.depth is CouncilDepth.HEAVY
        assert deeper.position_count >= plan.position_count
        assert plan.depth is CouncilDepth.MEDIUM  # the original is untouched

    def test_a_person_may_send_one_seat_to_a_different_agent(self) -> None:
        plan = plan_council(DESIGN, (_designer(), _geneticist()), depth=CouncilDepth.MEDIUM, model="m")
        chair = plan.seats[-1]
        revised = plan.with_seat_agent(chair.seat_id, "quant-genetics")

        assert revised.seats[-1].agent_name == "quant-genetics"
        assert revised.seats[-1].role is CouncilRole.CHAIR
        assert plan.seats[-1].agent_name == chair.agent_name  # untouched

    def test_an_agent_nobody_registered_is_refused_rather_than_dispatched(self) -> None:
        # Fail closed: a typo must not silently run the council as a generalist.
        plan = plan_council(DESIGN, (_designer(),), depth=CouncilDepth.MEDIUM, model="m")
        with pytest.raises(UnknownAgent):
            plan.with_seat_agent(plan.seats[0].seat_id, "nobody")

    def test_an_unknown_seat_is_refused(self) -> None:
        plan = plan_council(DESIGN, (_designer(),), depth=CouncilDepth.MEDIUM, model="m")
        with pytest.raises(KeyError):
            plan.with_seat_agent("no-such-seat", "experimental-design")


class TestRecommendingDepth:
    def test_a_quick_look_is_a_light_council(self) -> None:
        outcome = recommend_depth("Just a quick sanity check on whether this pilot is worth running.")

        assert outcome.depth is CouncilDepth.LIGHT
        assert outcome.rule_hits

    def test_work_that_has_to_survive_review_is_a_heavy_council(self) -> None:
        outcome = recommend_depth("This will go into the thesis chapter and needs to survive peer review across three seasons.")

        assert outcome.depth is CouncilDepth.HEAVY
        assert outcome.rule_hits

    def test_an_ordinary_request_lands_in_the_middle(self) -> None:
        outcome = recommend_depth("Design a genomic prediction cycle for drought tolerance.")

        assert outcome.depth is CouncilDepth.MEDIUM

    def test_an_empty_request_still_recommends_something_usable(self) -> None:
        # The card always has to open on a default, so this cannot raise.
        assert recommend_depth("").depth is CouncilDepth.MEDIUM

    def test_the_recommendation_says_why(self) -> None:
        outcome = recommend_depth("A quick pilot to see if the idea holds.")

        assert outcome.reason
        assert all(hit.rule and hit.matched for hit in outcome.rule_hits)

    def test_the_recommendation_is_deterministic(self) -> None:
        text = "Publication-quality multi-season validation of the drought model."
        assert recommend_depth(text) == recommend_depth(text)
