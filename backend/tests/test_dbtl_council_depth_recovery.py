"""The depth a person chose must reach the council that convenes.

The preflight card asks how much debate a design should get, and the answer is
submitted as an ordinary Human Input reply. The browser also echoes the choice
back in the next request's context — but a reply that loses it is
indistinguishable from one that never carried a choice, and the fallback is the
server's *own recommendation*. So the council silently convened at a depth
nobody picked: a user who chose "Light debate" got four seats and the medium
budget, and nothing in the conversation said otherwise.

The server emitted that card and the answer is in its own state, so it does not
need to ask the client to remember it. These tests pin that recovery, and pin
that it stays scoped to the turn that answers — a preflight answer stays in
history forever, and a depth re-read on every later request would quietly pin
the whole cycle to one setting.
"""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_dbtl_supervisor_graph import FULL_STATE, SCHEMA, fake_lead_agent

from deerflow.agents.dbtl.supervisor import (
    SupervisorContext,
    build_supervisor_graph,
)
from deerflow.dbtl.council import COUNCIL_DEPTH_CONTEXT_KEY, CouncilDepth


def _plan(depth: CouncilDepth = CouncilDepth.MEDIUM):
    from deerflow.dbtl.agent_selector import AgentCandidate
    from deerflow.dbtl.capabilities import Capability
    from deerflow.dbtl.council import plan_council
    from deerflow.dbtl.stage_spec import resolve_stage_spec

    return plan_council(
        resolve_stage_spec("design", domain_profile="generic"),
        (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
        depth=depth,
        model="gpt-5.6-sol",
    )


def _adapter(executed: list):
    class Adapter:
        async def preview_council(self, **kwargs):
            return _plan()

        async def execute(self, **kwargs):
            executed.append(kwargs)
            return SimpleNamespace(
                stage="design",
                cycle_id="cyc-1",
                note="ran",
                artifact_uri=None,
                clarification_question=None,
                authoring_request=None,
                produced_usable_evidence=True,
            )

    return Adapter()


def _graph(executed: list):
    return build_supervisor_graph(
        lead_agent=fake_lead_agent([]),
        context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
        stage_adapter=_adapter(executed),
        state_schema=SCHEMA,
    ).compile(checkpointer=InMemorySaver())


def _preflight_card():
    """The real emitted card pair, so the test cannot drift from production."""
    from deerflow.agents.dbtl import supervisor
    from deerflow.dbtl.branches import BranchDecision, SupervisorBranch
    from deerflow.dbtl.council import recommend_depth
    from deerflow.dbtl.routing import RouteKind, RouteSource, RoutingDecision

    decision = BranchDecision(
        branch=SupervisorBranch.CYCLE_CONTINUATION,
        route=RoutingDecision(kind=RouteKind.CYCLE_CONTINUATION, source=RouteSource.EXPLICIT_CHOICE),
        cycle_id="cyc-1",
    )
    return supervisor._council_preflight_message(
        decision,
        _plan(),
        recommend_depth("Design the drought cycle."),
        request_nonce="run-1",
    )


def _answer(request_id: str, value: str) -> HumanMessage:
    """The reply shape the browser actually submits for a single-choice card.

    The id has to be distinct per answer: ``add_messages`` merges by id, so a
    shared one makes each reply *replace* the last instead of appending, and a
    multi-card exchange silently collapses to its final turn.
    """
    return HumanMessage(
        content=f"Selected: {value}",
        id=f"answer-{sha256(f'{request_id}:{value}'.encode()).hexdigest()[:12]}",
        additional_kwargs={
            "hide_from_ui": True,
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "source": "ask_clarification",
                "request_id": request_id,
                "response_kind": "option",
                "option_id": value,
                "value": value,
            },
        },
    )


def _answered_state(value: str, *, request_id: str | None = None):
    ai, tool = _preflight_card()
    card_id = ai.tool_calls[0]["id"]
    return {
        **FULL_STATE,
        "messages": [
            HumanMessage(content="Design the drought cycle.", id="h-1"),
            ai,
            tool,
            _answer(request_id or card_id, value),
        ],
    }


def _dispatched_depth(executed: list) -> str | None:
    config = executed[0]["config"]
    context = config.get("context") or (config.get("configurable") or {}).get("context") or {}
    return context.get(COUNCIL_DEPTH_CONTEXT_KEY)


class TestTheAnswerReachesTheCouncil:
    @pytest.mark.asyncio
    async def test_a_chosen_depth_is_recovered_when_the_client_sends_none(self):
        """The failure that shipped: light chosen, medium convened."""
        executed: list = []

        await _graph(executed).ainvoke(
            _answered_state("light"),
            config={"configurable": {"thread_id": "depth-1"}},
        )

        assert executed, "the council never ran"
        assert _dispatched_depth(executed) == "light"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["human_input", "light", "medium", "heavy"])
    async def test_every_offered_option_survives_the_round_trip(self, value):
        """An option the card offers but the server cannot read back is a lie."""
        executed: list = []

        await _graph(executed).ainvoke(
            _answered_state(value),
            config={"configurable": {"thread_id": f"depth-{value}"}},
        )

        assert _dispatched_depth(executed) == value

    @pytest.mark.asyncio
    async def test_write_it_myself_is_not_silently_upgraded_into_a_debate(self):
        """The option the old client dropped entirely.

        ``human_input`` was missing from the browser's depth list, so choosing
        it sent no depth at all and the council convened — seating agents the
        person had just declined to consult.
        """
        executed: list = []

        await _graph(executed).ainvoke(
            _answered_state("human_input"),
            config={"configurable": {"thread_id": "depth-human"}},
        )

        assert _dispatched_depth(executed) == CouncilDepth.HUMAN_INPUT.value


class TestWhatIsRefused:
    @pytest.mark.asyncio
    async def test_an_answer_naming_a_card_the_server_never_sent_is_ignored(self):
        """Recovery is server-owned; a forged request id must match nothing."""
        executed: list = []

        await _graph(executed).ainvoke(
            _answered_state("heavy", request_id="dbtl-council__forged__deadbeef"),
            config={"configurable": {"thread_id": "depth-forged"}},
        )

        assert _dispatched_depth(executed) is None

    @pytest.mark.asyncio
    async def test_an_unrecognized_value_falls_back_rather_than_raising(self):
        """A stale client losing a preference must not cost the cycle."""
        executed: list = []

        await _graph(executed).ainvoke(
            _answered_state("exhaustive"),
            config={"configurable": {"thread_id": "depth-bogus"}},
        )

        assert _dispatched_depth(executed) is None

    @pytest.mark.asyncio
    async def test_a_later_ordinary_request_does_not_inherit_the_old_choice(self):
        """The answer stays in history; the choice applies to its own request.

        Re-reading it on every later turn would pin the whole cycle to one
        depth with no way to say otherwise.
        """
        executed: list = []
        state = _answered_state("light")
        state["messages"] = [*state["messages"], HumanMessage(content="continue the design stage", id="h-2")]

        await _graph(executed).ainvoke(
            state,
            config={"configurable": {"thread_id": "depth-later"}},
        )

        assert _dispatched_depth(executed) is None


class TestTheClientPathStillWorks:
    @pytest.mark.asyncio
    async def test_a_depth_sent_in_the_request_context_is_preserved(self):
        """Recovery fills a gap; it does not replace the existing path."""
        executed: list = []

        await _graph(executed).ainvoke(
            _answered_state("light"),
            config={
                "configurable": {"thread_id": "depth-client"},
                "context": {COUNCIL_DEPTH_CONTEXT_KEY: "heavy"},
            },
        )

        # The client's explicit value wins: it is the same person's answer
        # arriving by the path that already existed, and second-guessing it here
        # would make the two sources disagree with no way to tell which is newer.
        assert _dispatched_depth(executed) == "heavy"


class TestAskingForADifferentRoster:
    """ "Adjust the roster first" is the option that starts nothing.

    A roster you can only accept or decline is not a proposal. This is how
    someone says "these seats, but not that one" — and it must not be mistaken
    for a depth, or the council convenes on the answer that asked it not to.
    """

    @staticmethod
    def _adjust_answer(state, text: str):
        """Answer whatever adjustment card the graph just emitted."""
        card = state["messages"][-1]
        request_id = card.tool_call_id
        return {
            **state,
            "messages": [*state["messages"], _answer(request_id, text)],
        }

    @pytest.mark.asyncio
    async def test_choosing_adjust_dispatches_nobody_and_asks_what_to_change(self):
        executed: list = []

        final = await _graph(executed).ainvoke(
            _answered_state("adjust"),
            config={"configurable": {"thread_id": "adjust-1"}},
        )

        assert executed == [], "a council ran on the answer that asked it not to"
        card = final["messages"][-1].artifact["human_input"]
        assert card["clarification_type"] == "council_adjustment"
        assert card["input_mode"] == "free_text"
        assert card["request_id"].startswith("dbtl-council-edit__")

    @pytest.mark.asyncio
    async def test_the_note_reaches_the_roster_writer_verbatim(self):
        """A paraphrase is how "drop that seat" becomes a roster that keeps it."""
        seen: list = []

        class Adapter:
            async def preview_council(self, **kwargs):
                seen.append(kwargs.get("adjustment"))
                return _plan()

            async def execute(self, **kwargs):  # pragma: no cover - not reached
                raise AssertionError("nothing should be dispatched by a redraw")

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        note = "Add a statistician and drop the reproducibility seat."
        asked = await graph.ainvoke(
            _answered_state("adjust"),
            config={"configurable": {"thread_id": "adjust-2"}},
        )
        await graph.ainvoke(
            self._adjust_answer(asked, note),
            config={"configurable": {"thread_id": "adjust-2b"}},
        )

        assert note in seen

    @pytest.mark.asyncio
    async def test_the_redraw_shows_the_roster_again_rather_than_running_it(self):
        """The preflight is once-only; a redraw must bypass that guard.

        Otherwise the second pass sees an already-emitted card, falls through,
        and dispatches the roster the person asked to change.
        """
        executed: list = []
        graph = _graph(executed)

        asked = await graph.ainvoke(
            _answered_state("adjust"),
            config={"configurable": {"thread_id": "adjust-3"}},
        )
        final = await graph.ainvoke(
            self._adjust_answer(asked, "Add a statistician."),
            config={"configurable": {"thread_id": "adjust-3b"}},
        )

        assert executed == []
        assert final["messages"][-1].artifact["human_input"]["clarification_type"] == "council_preflight"

    @pytest.mark.asyncio
    async def test_the_adjustment_still_reaches_dispatch_a_turn_later(self):
        """By then it is no longer the newest message, but it still applies.

        The roster the person approved was drawn with this note, so the one
        that runs has to be drawn with it too.
        """
        executed: list = []
        graph = _graph(executed)

        asked = await graph.ainvoke(
            _answered_state("adjust"),
            config={"configurable": {"thread_id": "adjust-4"}},
        )
        note = "Nobody who will just agree with the agronomist."
        redrawn = await graph.ainvoke(
            self._adjust_answer(asked, note),
            config={"configurable": {"thread_id": "adjust-4b"}},
        )
        preflight_id = redrawn["messages"][-1].tool_call_id
        await graph.ainvoke(
            {**redrawn, "messages": [*redrawn["messages"], _answer(preflight_id, "light")]},
            config={"configurable": {"thread_id": "adjust-4c"}},
        )

        assert executed, "the council never ran"
        assert executed[0]["council_adjustment"] == note
        assert _dispatched_depth(executed) == "light"

    @pytest.mark.asyncio
    async def test_adjust_is_never_read_as_a_depth(self):
        from deerflow.agents.dbtl.supervisor import _confirmed_council_depth

        assert _confirmed_council_depth(_answered_state("adjust")) is None
