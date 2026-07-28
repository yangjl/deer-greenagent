"""The level where the scientist is the council.

Light, medium, and heavy differ in how much debate is bought. Human Input is a
different kind of setting: it buys none, because the person already knows the
design and wants it recorded rather than argued. That makes it the one depth
that produces zero seats, which collides with an existing meaning — a plan with
no seats currently means *refused*, and showing a refusal to someone who chose
this on purpose would be a lie about what happened.

These tests pin the separation: an empty roster is only ever "the human writes
this" when the depth says so, and a genuine capability failure keeps reading as
a failure at every other depth.
"""

import pytest

from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.council import (
    DEPTH_POLICIES,
    CouncilDepth,
    CouncilRole,
    council_depth_from_config,
    depth_policy,
    plan_council,
    recommend_depth,
)
from deerflow.dbtl.stage_spec import resolve_stage_spec


@pytest.fixture
def spec():
    return resolve_stage_spec("design")


@pytest.fixture
def candidates():
    return [
        AgentCandidate(name="general-purpose", capabilities=frozenset(), available=True, is_generalist=True),
        AgentCandidate(
            name="quant-geneticist",
            capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN, Capability.QUANTITATIVE_GENETICS}),
            available=True,
        ),
    ]


class TestTheLevelItself:
    def test_human_input_is_a_depth(self):
        assert CouncilDepth("human_input") is CouncilDepth.HUMAN_INPUT

    def test_it_buys_no_positions(self):
        assert depth_policy(CouncilDepth.HUMAN_INPUT).max_positions == 0

    def test_every_depth_has_a_policy(self):
        assert set(DEPTH_POLICIES) == set(CouncilDepth)

    def test_it_reads_as_a_choice_rather_than_a_saving(self):
        """The label is what a person picks from, so it has to say what it does."""
        policy = depth_policy(CouncilDepth.HUMAN_INPUT)
        assert "debate" not in policy.label.lower()
        assert policy.description.strip()


class TestPlanning:
    def test_it_seats_nobody(self, spec, candidates):
        plan = plan_council(spec, candidates, depth=CouncilDepth.HUMAN_INPUT, model="m")

        assert plan.seats == ()
        assert plan.position_count == 0

    def test_an_empty_roster_here_means_the_human_writes_it(self, spec, candidates):
        plan = plan_council(spec, candidates, depth=CouncilDepth.HUMAN_INPUT, model="m")

        assert plan.human_authored is True
        # Still not dispatchable — there is genuinely nothing to dispatch. The
        # two properties answer different questions and the adapter must ask
        # ``human_authored`` first.
        assert plan.dispatchable is False

    def test_a_real_capability_failure_is_never_mistaken_for_it(self, spec):
        """No candidate can cover the stage, at a depth that wanted seats."""
        plan = plan_council(spec, [], depth=CouncilDepth.MEDIUM, model="m")

        assert plan.seats == ()
        assert plan.dispatchable is False
        assert plan.human_authored is False

    def test_other_depths_still_seat_a_full_council(self, spec, candidates):
        plan = plan_council(spec, candidates, depth=CouncilDepth.MEDIUM, model="m")

        assert plan.human_authored is False
        roles = {seat.role for seat in plan.seats}
        assert CouncilRole.RED_TEAM in roles
        assert CouncilRole.CHAIR in roles


class TestSwitchingDepth:
    def test_dropping_to_human_input_clears_the_roster(self, spec, candidates):
        plan = plan_council(spec, candidates, depth=CouncilDepth.HEAVY, model="m")

        reduced = plan.with_depth(CouncilDepth.HUMAN_INPUT)

        # Not a trim: the red team and chair are preserved explicitly at every
        # other depth, and leaving them here would dispatch two workers for a
        # setting whose whole point is that none run.
        assert reduced.seats == ()
        assert reduced.human_authored is True

    def test_raising_back_out_of_it_cannot_invent_seats(self, spec, candidates):
        """``with_depth`` re-scopes; it does not re-select.

        The candidate pool is not carried on the plan, so a caller that wants a
        deeper council has to call ``plan_council`` again. Silently returning an
        empty medium council would be worse than obviously returning one.
        """
        plan = plan_council(spec, candidates, depth=CouncilDepth.HUMAN_INPUT, model="m")

        raised = plan.with_depth(CouncilDepth.MEDIUM)

        assert raised.depth is CouncilDepth.MEDIUM
        assert raised.seats == ()


class TestChoosingIt:
    def test_it_is_accepted_from_the_request_context(self):
        assert council_depth_from_config({"context": {"dbtl_council_depth": "human_input"}}) is CouncilDepth.HUMAN_INPUT

    @pytest.mark.parametrize(
        "text",
        [
            "quick sanity check on the design",
            "a rigorous multi-season design for the paper",
            "simulate a maize breeding population",
            "",
        ],
    )
    def test_it_is_never_recommended(self, text):
        """Skipping the debate is the human's call, never the rule table's.

        Every other depth is a judgement about how much scrutiny a question
        deserves, which a rule can argue for. "Do not consult anyone" is a
        statement about who owns the answer, and a suggestion to that effect
        would be the system declining to help.
        """
        assert recommend_depth(text).depth is not CouncilDepth.HUMAN_INPUT
