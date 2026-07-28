"""The roster a person approves must be the roster that argues.

The preflight card was built from capability *selection* while dispatch used the
LLM *proposal*, so the two were different computations and the card described a
council that never convened. In a generalist-only deployment — the common case —
selection yields one indistinguishable seat, so the card showed a thin roster and
four differentiated seats then ran. Approving a roster you were not shown is not
a choice.

These tests pin three things: the preview proposes, the approved proposal is
replayed rather than re-drawn, and a reviewer may send it back for changes
before anyone is dispatched.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.council import (
    CouncilDepth,
    CouncilRole,
    plan_council,
    plan_from_proposal,
)
from deerflow.dbtl.council_proposal import (
    CouncilProposal,
    ProposedSeat,
    build_proposal_prompt,
    parse_council_proposal,
    proposal_as_dict,
    proposal_from_dict,
)
from deerflow.dbtl.stage_spec import resolve_stage_spec


def _seat(focus: str, *, agent: str = "general-purpose", model: str | None = "gpt-5.6-sol") -> ProposedSeat:
    return ProposedSeat(
        focus=focus,
        brief=f"Argue from {focus}.",
        agent_name=agent,
        capability=Capability.EXPERIMENTAL_DESIGN,
        model=model,
    )


def _proposal(*focuses: str) -> CouncilProposal:
    return CouncilProposal(
        positions=tuple(_seat(focus) for focus in focuses),
        chair=_seat("synthesis"),
    )


def _selection_plan(depth: CouncilDepth = CouncilDepth.MEDIUM):
    from deerflow.dbtl.agent_selector import AgentCandidate

    return plan_council(
        resolve_stage_spec("design", domain_profile="generic"),
        (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
        depth=depth,
        model="gpt-5.6-sol",
    )


class TestTheProposedRosterIsShowable:
    def test_a_plan_built_from_a_proposal_carries_each_seat_focus(self):
        """The focus is the whole point: it is what makes seats differ.

        Three seats that all resolve to ``general-purpose`` are only a debate
        because each argues from somewhere different, and a roster that hides
        that reads as the same agent listed three times.
        """
        plan = plan_from_proposal(_selection_plan(), _proposal("trait architecture", "field logistics"))

        positions = [seat for seat in plan.seats if seat.role is CouncilRole.POSITION]
        assert [seat.focus for seat in positions] == ["trait architecture", "field logistics"]
        assert all(seat.brief == f"Argue from {seat.focus}." for seat in positions)

    def test_the_chair_is_last_and_is_the_only_stage_output(self):
        plan = plan_from_proposal(_selection_plan(), _proposal("a", "b"))

        # Two positions, then the red team, then the chair.
        assert [seat.role for seat in plan.seats] == [
            CouncilRole.POSITION,
            CouncilRole.POSITION,
            CouncilRole.RED_TEAM,
            CouncilRole.CHAIR,
        ]
        assert [seat.counts_toward_stage_output for seat in plan.seats] == [False, False, False, True]

    def test_the_red_team_borrows_the_chairs_agent_but_not_its_brief(self):
        """Labelling the red team "synthesis" would describe the opposite job."""
        plan = plan_from_proposal(_selection_plan(), _proposal("a"))

        red_team = next(seat for seat in plan.seats if seat.role is CouncilRole.RED_TEAM)
        assert red_team.focus == ""
        assert "synthesis" not in red_team.brief.lower()

    def test_positions_are_capped_by_the_chosen_depth(self):
        """Light means one position however many the model proposed."""
        plan = plan_from_proposal(_selection_plan(CouncilDepth.LIGHT), _proposal("a", "b", "c"))

        assert sum(1 for seat in plan.seats if seat.role is CouncilRole.POSITION) == 1

    def test_the_plan_keeps_the_depth_and_budget_it_was_scoped_with(self):
        base = _selection_plan(CouncilDepth.LIGHT)
        plan = plan_from_proposal(base, _proposal("a"))

        assert plan.depth is CouncilDepth.LIGHT
        assert plan.budget == base.budget
        assert plan.stage_spec_key == base.stage_spec_key

    def test_a_proposal_with_no_chair_falls_back_to_the_selected_roster(self):
        """Never show a council with nobody to synthesize it."""
        base = _selection_plan()
        plan = plan_from_proposal(base, CouncilProposal(positions=(_seat("a"),), chair=None))

        assert plan == base


class TestTheRosterSurvivesTheCard:
    """Dispatch replays the approved proposal instead of drawing a new one.

    Re-proposing at dispatch would mean a second model call, and a second call
    can legitimately return a different roster — so the seats a person approved
    and the seats that ran would differ with nothing recording that they did.
    """

    def test_a_proposal_round_trips_without_losing_a_seat(self):
        original = _proposal("trait architecture", "field logistics")

        restored = proposal_from_dict(proposal_as_dict(original))

        assert restored == original

    def test_a_restored_seat_keeps_its_brief_and_model(self):
        restored = proposal_from_dict(proposal_as_dict(_proposal("variance")))

        assert restored is not None
        assert restored.positions[0].brief == "Argue from variance."
        assert restored.positions[0].model == "gpt-5.6-sol"

    @pytest.mark.parametrize("payload", [None, {}, {"positions": []}, {"positions": [{"focus": "x"}]}, "not a mapping"])
    def test_an_unusable_payload_restores_to_nothing_rather_than_half_a_council(self, payload):
        assert proposal_from_dict(payload) is None

    def test_a_restored_proposal_is_revalidated_not_trusted(self):
        """The card is server-owned, but a seat is still checked on the way back.

        Cheap, and it means one code path decides who may hold a seat rather
        than two that can drift.
        """
        payload = proposal_as_dict(_proposal("a"))
        payload["positions"][0]["agent_name"] = "bash"

        restored = proposal_from_dict(payload)

        assert restored is None or all(seat.agent_name != "bash" for seat in restored.positions)


class TestAskingForChanges:
    def test_an_adjustment_reaches_the_proposer_verbatim(self):
        """A paraphrase is the failure this exists to fix.

        The reviewer's words are the requirement; restating them in the
        prompt's own vocabulary is how "drop the reproducibility seat" becomes
        a council that keeps it.
        """
        note = "Add a statistician and drop the reproducibility seat."
        prompt = build_proposal_prompt(
            request_text="Design a drought trial.",
            stage_context="",
            known_agents=("general-purpose",),
            known_models=("gpt-5.6-sol",),
            max_positions=2,
            adjustment=note,
        )

        assert note in prompt

    def test_no_adjustment_leaves_the_prompt_as_it_was(self):
        common = dict(
            request_text="Design a drought trial.",
            stage_context="",
            known_agents=("general-purpose",),
            known_models=("gpt-5.6-sol",),
            max_positions=2,
        )

        assert build_proposal_prompt(**common, adjustment=None) == build_proposal_prompt(**common)
        assert build_proposal_prompt(**common, adjustment="   ") == build_proposal_prompt(**common)

    def test_an_adjusted_proposal_still_refuses_an_ineligible_seat(self):
        """Asking for a change does not widen who may be seated."""
        import json

        proposal = parse_council_proposal(
            json.dumps(
                {
                    "positions": [{"focus": "reproducibility", "brief": "run it", "agent_name": "bash", "capability": "experimental_design"}],
                    "chair": {"focus": "synthesis", "brief": "weigh them", "agent_name": "general-purpose", "capability": "experimental_design"},
                }
            ),
            known_agents=("general-purpose", "bash"),
            known_models=("gpt-5.6-sol",),
            max_positions=2,
        )

        assert not proposal.positions
        assert any("bash" in reason for reason in proposal.rejected)
