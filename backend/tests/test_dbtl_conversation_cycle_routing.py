"""A conversation that opened a cycle can be asked, in words, to run its work.

The composer's cycle scope applies to the next request only — deliberately, so
a scope chosen once cannot keep steering a conversation forever. The cost is
that the *second* consecutive cycle request arrives unscoped, and the routing
ladder then has nothing to continue: it falls through to the classifier, which
reads "run the meeting again" as ordinary chat and hands it to the lead agent.

That is not a hypothetical. It happened on a real Design stage: the owner asked
for the meeting again, the lead agent read the trial data itself, wrote a file
named like a design package into `outputs/`, and answered — no council, no
worker rows, no artifact, and a stage that looked answered while its gate had
not moved. The routing fence and the stage-owned output paths held (nothing was
recorded), but the person got a deliverable from the one actor that must never
produce one.

The narrow repair is a new rung: when a conversation *originated* a live cycle
and the request deterministically names stage work, that cycle is recovered
server-side. This is the same principle the Human Input cards already use —
"card replies recover their cycle from the emitted request, so losing the
next-request cycle scope cannot strand Test" — applied to the request a person
types instead of a card they answer.

Four properties keep it from becoming a sticky scope by the back door:

1. It fires only on the deterministic re-run phrases, the same ones the stage
   adapter uses to decide whether to convene. Ordinary conversation about a
   cycle is untouched.
2. It fires only in the conversation that opened the cycle, and only while that
   cycle is live.
3. An explicit `ordinary` choice still wins, because that is a person saying
   "not the cycle" and a phrase match must not argue with them.
4. It recovers a cycle; it never invents one.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.branches import SupervisorBranch, SupervisorContext, resolve_branch
from deerflow.dbtl.meeting_intent import wants_new_debate
from deerflow.dbtl.routing import (
    ExplicitChoice,
    RouteKind,
    RouteSource,
    RoutingRequest,
    route_request,
)

CYCLE = "cycle-b0a0d276"
PROJECT = "project-9b5ac365"


def _request(text: str, **overrides) -> RoutingRequest:
    base = {
        "text": text,
        "project_id": PROJECT,
        "selected_cycle_id": None,
        "explicit_choice": None,
        "thread_cycle_id": CYCLE,
    }
    base.update(overrides)
    return RoutingRequest(**base)


class TestTheRerunPhrasesAreOneDefinition:
    """Routing and the stage adapter must agree, or one convenes and the other
    refuses the same sentence."""

    @pytest.mark.parametrize(
        "text",
        [
            "run the meeting again",
            "re-run the meeting",
            "restart the meeting",
            "hold another round",
            "let's debate it again",
            "restat the meeting",  # the enumerated one-slip typo
        ],
    )
    def test_it_recognizes_asking_for_another_round(self, text: str):
        assert wants_new_debate(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "what did the meeting decide?",
            "summarize the design",
            "who was in the meeting?",
            "",
        ],
    )
    def test_it_does_not_recognize_ordinary_talk(self, text: str):
        assert wants_new_debate(text) is False

    def test_the_adapter_reads_the_same_predicate(self):
        """Pinned by identity, not by behaviour: two regexes that agree today
        drift apart the first time one is edited."""
        from deerflow.agents.dbtl.live_stage import adapter

        assert adapter._wants_new_debate is wants_new_debate


class TestTheConversationRecoversItsOwnCycle:
    def test_an_unscoped_rerun_request_continues_the_cycle(self):
        decision = route_request(_request("run the meeting again"))

        assert decision.kind is RouteKind.CYCLE_CONTINUATION
        assert decision.cycle_id == CYCLE
        assert decision.source is RouteSource.THREAD_CYCLE

    def test_the_branch_resolver_agrees(self):
        decision = resolve_branch(
            "run the meeting again",
            SupervisorContext(project_id=PROJECT, thread_cycle_id=CYCLE),
        )

        assert decision.branch is SupervisorBranch.CYCLE_CONTINUATION
        assert decision.cycle_id == CYCLE

    def test_a_conversation_with_no_live_cycle_is_ordinary(self):
        decision = route_request(_request("run the meeting again", thread_cycle_id=None))

        assert decision.kind is RouteKind.ORDINARY

    def test_ordinary_talk_about_the_cycle_stays_ordinary(self):
        """The rung must not become a sticky scope: only the re-run phrases."""
        decision = route_request(_request("what parameters did the meeting capture?"))

        assert decision.kind is RouteKind.ORDINARY


class TestItNeverOverridesAPerson:
    def test_an_explicit_ordinary_choice_wins(self):
        decision = route_request(_request("run the meeting again", explicit_choice=ExplicitChoice.ORDINARY))

        assert decision.kind is RouteKind.ORDINARY
        assert decision.source is RouteSource.EXPLICIT_CHOICE

    def test_an_explicitly_selected_cycle_wins_over_the_conversation_one(self):
        """A project runs several cycles at once; the named one is the answer."""
        decision = route_request(
            _request(
                "run the meeting again",
                selected_cycle_id="cycle-other",
                explicit_choice=ExplicitChoice.CONTINUE_CYCLE,
            )
        )

        assert decision.cycle_id == "cycle-other"

    def test_a_typed_start_request_still_opens_setup(self):
        decision = route_request(_request("start a new cycle for drought tolerance"))

        assert decision.kind is RouteKind.CYCLE_SETUP

    def test_it_recovers_a_cycle_and_never_invents_one(self):
        """No live cycle for the conversation means no continuation, however
        the request is worded."""
        for text in ("run the meeting again", "re-run the council", "another round"):
            decision = route_request(_request(text, thread_cycle_id=None))
            assert decision.cycle_id is None


class TestTheDefaultIsUnchanged:
    """A caller that knows nothing about the new field behaves as before."""

    def test_the_field_is_optional(self):
        decision = route_request(
            RoutingRequest(
                text="run the meeting again",
                project_id=PROJECT,
                selected_cycle_id=None,
                explicit_choice=None,
            )
        )

        assert decision.kind is RouteKind.ORDINARY

    def test_a_selected_cycle_still_routes_the_old_way(self):
        decision = route_request(_request("please continue", selected_cycle_id=CYCLE, thread_cycle_id=None))

        assert decision.kind is RouteKind.CYCLE_CONTINUATION
        assert decision.source is RouteSource.SELECTED_CYCLE
