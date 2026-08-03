"""Asking for the meeting again must not silently change how it is run.

A Design meeting is set up once, through the preflight card: how much debate,
which seats, and each participant's model, reasoning strength, and written
instructions. That card is deliberately once-only per cycle, because every turn
that follows belongs to the meeting it opened — re-asking on the answering turn
would argue with the person who just answered.

A deliberate re-run breaks that assumption. "Run the meeting again" convenes a
*new* meeting spending another meeting's budget, and it arrives with the
once-only guard already tripped. So no card was shown; and none of the confirmed
settings could be read back either, because those readers are scoped to the turn
that answers a card and this turn answers nothing. The server re-derived
everything from scratch: a different number of participants, on different
agents, on different models, with the owner's per-seat instructions gone — and
nobody was asked about any of it.

The repair keeps both halves of the original rule rather than trading one for
the other. A re-run raises the preflight again, because no worker may be
dispatched until a person answers it; and that card opens on the setup they last
confirmed, because "again" means again. They can still change it — the card is a
proposal, and a roster you can only accept is not one.

Anchoring on *position* rather than on the phrase alone is what stops the card
re-raising forever: once emitted, the card is newer than the request that asked
for it, so the answering turn dispatches instead of asking a second time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_dbtl_council_depth_recovery import _answer, _plan, _preflight_card
from test_dbtl_live_stage_execution import (
    FakeDispatcher,
    FakeRepo,
    _cycle,
    _design_adapter,
    _runtime_config,
    _structured_result,
)
from test_dbtl_supervisor_graph import FULL_STATE, SCHEMA, fake_lead_agent

from deerflow.agents.dbtl.live_stage.adapter import LiveStageAdapter
from deerflow.agents.dbtl.supervisor import SupervisorContext, build_supervisor_graph
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.council import COUNCIL_DEPTH_CONTEXT_KEY, CouncilDepth
from deerflow.dbtl.council_proposal import CouncilProposal, ProposedSeat

KNOWN_MODELS = ("gpt-5.6-sol", "gpt-5.4-mini", "claude-opus-5")

RERUN = "run the meeting again"


# ---------------------------------------------------------------------------
# One conversation that already held a meeting.
# ---------------------------------------------------------------------------


def _answer_with_participants(request_id: str, depth: str, participants: dict) -> HumanMessage:
    """The preflight reply, carrying the dials the person edited on the cards."""
    message = _answer(request_id, depth)
    message.additional_kwargs["human_input_response"]["participants"] = participants
    return message


PARTICIPANTS = {
    "chair": {
        "model": "gpt-5.4-mini",
        "reasoning": "extended",
        "instructions": "Do not average the positions; say which one wins.",
    }
}


def _after_a_meeting(*, depth: str = "light", participants: dict | None = None, tail: list | None = None):
    """A conversation whose preflight was answered and whose meeting has run."""
    ai, tool = _preflight_card()
    card_id = ai.tool_calls[0]["id"]
    return {
        **FULL_STATE,
        "messages": [
            HumanMessage(content="Design the drought cycle.", id="h-1"),
            ai,
            tool,
            _answer_with_participants(card_id, depth, PARTICIPANTS if participants is None else participants),
            *(tail or []),
        ],
    }


def _asking_again(text: str = RERUN, **kwargs):
    return _after_a_meeting(tail=[HumanMessage(content=text, id="h-2")], **kwargs)


def _adapter(executed: list, previewed: list):
    class Adapter:
        def known_models(self):
            return KNOWN_MODELS

        async def preview_council(self, **kwargs):
            previewed.append(kwargs)
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


def _graph(executed: list, previewed: list):
    return build_supervisor_graph(
        lead_agent=fake_lead_agent([]),
        context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
        stage_adapter=_adapter(executed, previewed),
        state_schema=SCHEMA,
    ).compile(checkpointer=InMemorySaver())


def _card(final) -> dict:
    request = final["messages"][-1].artifact["human_input"]
    assert request["clarification_type"] == "council_preflight", "no preflight card was raised"
    return request


def _dispatched_depth(executed: list) -> str | None:
    config = executed[0]["config"]
    context = config.get("context") or (config.get("configurable") or {}).get("context") or {}
    return context.get(COUNCIL_DEPTH_CONTEXT_KEY)


# ---------------------------------------------------------------------------


class TestARerunAsksBeforeItSpends:
    @pytest.mark.asyncio
    async def test_asking_again_raises_the_preflight_again(self):
        executed: list = []
        previewed: list = []

        final = await _graph(executed, previewed).ainvoke(
            _asking_again(),
            config={"configurable": {"thread_id": "rerun-1"}},
        )

        assert _card(final)["clarification_type"] == "council_preflight"

    @pytest.mark.asyncio
    async def test_nobody_is_dispatched_until_it_is_answered(self):
        """The whole point of the card: a meeting's budget is a real cost."""
        executed: list = []
        previewed: list = []

        await _graph(executed, previewed).ainvoke(
            _asking_again(),
            config={"configurable": {"thread_id": "rerun-2"}},
        )

        assert executed == [], "a second meeting ran without anyone confirming it"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["run the meeting again", "re-run the meeting", "restart the meeting", "hold another round"])
    async def test_every_rerun_phrase_asks_first(self, text):
        """Routing and the adapter agree on these; the card must too."""
        executed: list = []
        previewed: list = []

        final = await _graph(executed, previewed).ainvoke(
            _asking_again(text),
            config={"configurable": {"thread_id": f"rerun-{abs(hash(text))}"}},
        )

        assert executed == []
        assert _card(final)


class TestTheCardOpensOnTheLastConfirmedSetup:
    @pytest.mark.asyncio
    async def test_it_recommends_the_depth_that_was_chosen_before(self):
        """ "Again" means again. A fresh recommendation is a different meeting."""
        executed: list = []
        previewed: list = []

        final = await _graph(executed, previewed).ainvoke(
            _asking_again(depth="light"),
            config={"configurable": {"thread_id": "rerun-depth"}},
        )

        card = _card(final)
        assert card["recommended_depth"] == "light"
        assert card["recommended_option_id"] == "light"

    @pytest.mark.asyncio
    async def test_the_roster_is_previewed_at_that_depth(self):
        """Depth decides how many seats; previewing at another one redraws them."""
        executed: list = []
        previewed: list = []

        await _graph(executed, previewed).ainvoke(
            _asking_again(depth="heavy"),
            config={"configurable": {"thread_id": "rerun-depth-2"}},
        )

        assert previewed and previewed[0].get("depth") is CouncilDepth.HEAVY

    @pytest.mark.asyncio
    async def test_the_approved_roster_is_reused_rather_than_rewritten(self):
        """A re-proposed roster is where the different participants came from."""
        executed: list = []
        previewed: list = []

        await _graph(executed, previewed).ainvoke(
            _asking_again(),
            config={"configurable": {"thread_id": "rerun-roster"}},
        )

        assert previewed, "the roster was never previewed"
        proposal = previewed[0].get("proposal")
        assert proposal is not None, "the card was drawn from a freshly written roster"
        assert proposal.chair is not None

    @pytest.mark.asyncio
    async def test_the_participant_dials_travel_with_it(self):
        """The instructions someone typed cannot be reconstructed by anyone."""
        executed: list = []
        previewed: list = []

        await _graph(executed, previewed).ainvoke(
            _asking_again(),
            config={"configurable": {"thread_id": "rerun-dials"}},
        )

        settings = previewed[0].get("participant_settings") or {}
        assert "chair" in settings
        assert settings["chair"].model == "gpt-5.4-mini"
        assert settings["chair"].reasoning == "extended"
        assert settings["chair"].instructions == PARTICIPANTS["chair"]["instructions"]


class TestTheRoundTripStillWorks:
    @staticmethod
    def _answering(final, value: str):
        return {
            **final,
            "messages": [*final["messages"], _answer(final["messages"][-1].tool_call_id, value)],
        }

    @pytest.mark.asyncio
    async def test_answering_the_new_card_runs_the_meeting(self):
        executed: list = []
        previewed: list = []
        graph = _graph(executed, previewed)

        asked = await graph.ainvoke(
            _asking_again(),
            config={"configurable": {"thread_id": "rerun-trip"}},
        )
        await graph.ainvoke(
            self._answering(asked, "heavy"),
            config={"configurable": {"thread_id": "rerun-trip-b"}},
        )

        assert executed, "the meeting never ran"
        assert _dispatched_depth(executed) == "heavy"

    @pytest.mark.asyncio
    async def test_the_card_is_not_raised_a_second_time(self):
        """The re-run request is older than the card by then, so it is settled."""
        executed: list = []
        previewed: list = []
        graph = _graph(executed, previewed)

        asked = await graph.ainvoke(
            _asking_again(),
            config={"configurable": {"thread_id": "rerun-once"}},
        )
        final = await graph.ainvoke(
            self._answering(asked, "light"),
            config={"configurable": {"thread_id": "rerun-once-b"}},
        )

        assert executed, "the meeting never ran"
        assert getattr(final["messages"][-1], "artifact", None) is None or "human_input" not in (final["messages"][-1].artifact or {})

    @pytest.mark.asyncio
    async def test_an_unreadable_answer_does_not_loop_the_card(self):
        """A stale client sending a depth nobody offers must not trap the cycle.

        The guard is positional, so an answer the server cannot read still ends
        the exchange — the meeting runs on the recommendation rather than asking
        the same question forever.
        """
        executed: list = []
        previewed: list = []
        graph = _graph(executed, previewed)

        asked = await graph.ainvoke(
            _asking_again(),
            config={"configurable": {"thread_id": "rerun-stale"}},
        )
        await graph.ainvoke(
            self._answering(asked, "exhaustive"),
            config={"configurable": {"thread_id": "rerun-stale-b"}},
        )

        assert executed, "the card asked again instead of running"


class TestTheRosterOnTheCardIsTheRosterThatRuns:
    """A confirmed roster outranks the cheap revision route.

    "Request changes" normally reads the objection and, when the chair can fold
    it into the synthesis alone, dispatches the chair over the positions already
    recorded — the right default, because reconvening spends a second meeting
    re-arguing the parts the reviewer accepted.

    It is the wrong answer to a person who just read a roster of three and
    pressed **Start meeting**. The card described a council; one chair ran. That
    is the same failure the roster proposal exists to prevent, arriving through
    the route that avoids the cost of it. A confirmed preflight is somebody
    saying "these seats, on this question, now", and the reviewer's objection
    still reaches them — it travels as the round's change request either way.
    """

    @staticmethod
    def _repo():
        class Repo(FakeRepo):
            async def list_activity(self, cycle_id: str, *, project_id: str):
                return [
                    {
                        "sequence": 1,
                        "event_type": "stage.reviewed",
                        "actor_user_id": "user-1",
                        "payload": {
                            "stage": "design",
                            "decision": "changes_requested",
                            "rationale": "The holdout site is doing two jobs.",
                        },
                    }
                ]

        repo = Repo(_cycle())
        repo.worker_runs = [
            {
                "unit_id": "dbtl-x-position-1",
                "role": "position",
                "capability": "experimental_design",
                "agent_name": "designer",
                "status": "completed",
                "summary": "Group the folds by genotype.",
                "round": 1,
            }
        ]
        return repo

    @pytest.mark.asyncio
    async def test_a_confirmed_roster_convenes_instead_of_a_lone_chair(self, tmp_path: Path):
        dispatcher = FakeDispatcher(text=_structured_result())

        await _design_adapter(self._repo(), dispatcher).execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text=RERUN,
            state={},
            config=_runtime_config(tmp_path),
            approved_council_proposal=TestTheAdapterHonoursTheSetupItIsGiven._proposal(),
        )

        assert dispatcher.calls, "nothing was dispatched"
        # Flattened across waves: a meeting dispatches positions, then the red
        # team, then the chair, so the first wave alone says nothing about who
        # was seated.
        roles = [str(unit.role) for units, _ in dispatcher.calls for unit in units]
        assert roles == ["position", "red_team", "chair"], f"the card promised a meeting and {roles} ran"

    @pytest.mark.asyncio
    async def test_the_objection_still_reaches_that_meeting(self):
        """The route changes; the reviewer's words are not lost with it."""
        from deerflow.agents.dbtl.live_stage.adapter import _change_request

        activity = await self._repo().list_activity("cycle-1", project_id="project-1")

        assert _change_request(activity) == "The holdout site is doing two jobs."

    @pytest.mark.asyncio
    async def test_without_a_confirmed_roster_the_chair_still_answers_alone(self, tmp_path: Path):
        """The default is unchanged: an unattended refinement stays cheap."""
        dispatcher = FakeDispatcher(text=_structured_result())

        await _design_adapter(self._repo(), dispatcher).execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text=RERUN,
            state={},
            config=_runtime_config(tmp_path),
        )

        assert dispatcher.calls
        # Flattened across waves: a meeting dispatches positions, then the red
        # team, then the chair, so the first wave alone says nothing about who
        # was seated.
        roles = [str(unit.role) for units, _ in dispatcher.calls for unit in units]
        assert roles == ["chair"]


class TestWhatIsUnchanged:
    @pytest.mark.asyncio
    async def test_an_ordinary_cycle_request_does_not_raise_the_card(self):
        """Only a deliberate re-run convenes; the hold rule owns the rest."""
        executed: list = []
        previewed: list = []

        await _graph(executed, previewed).ainvoke(
            _asking_again("continue the design stage"),
            config={"configurable": {"thread_id": "rerun-ordinary"}},
        )

        assert executed, "an ordinary cycle request was turned into a setup card"

    @pytest.mark.asyncio
    async def test_a_first_design_request_still_raises_a_fresh_preflight(self):
        """No prior card means nothing to reuse, and nothing to reuse it from."""
        executed: list = []
        previewed: list = []

        final = await _graph(executed, previewed).ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content="Design the drought cycle.", id="h-1")]},
            config={"configurable": {"thread_id": "rerun-first"}},
        )

        assert executed == []
        assert _card(final)
        assert previewed[0].get("proposal") is None
        assert not previewed[0].get("participant_settings")


class TestTheAdapterHonoursTheSetupItIsGiven:
    """The supervisor hands the prior setup down; the adapter has to use it."""

    @staticmethod
    def _adapter(written: list) -> LiveStageAdapter:
        async def roster_writer(prompt: str) -> str:
            written.append(prompt)
            return "{}"

        return LiveStageAdapter(
            repo=FakeRepo(_cycle()),
            app_config=SimpleNamespace(),
            candidate_provider=lambda: (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
            roster_writer=roster_writer,
        )

    @staticmethod
    def _proposal() -> CouncilProposal:
        """A roster the person already approved. The red team borrows the chair's
        agent by design, so a proposal names only positions and a chair."""
        return CouncilProposal(
            positions=(
                ProposedSeat(
                    capability=Capability.EXPERIMENTAL_DESIGN,
                    agent_name="designer",
                    focus="Grouped validation",
                    brief="Argue the split from the trial structure.",
                ),
            ),
            chair=ProposedSeat(
                capability=Capability.EXPERIMENTAL_DESIGN,
                agent_name="designer",
                focus="Synthesis",
                brief="Settle it.",
            ),
        )

    @pytest.mark.asyncio
    async def test_an_approved_roster_is_not_written_again(self, tmp_path: Path):
        """Re-writing it is what changed the seats; it also costs a model call."""
        written: list = []

        plan = await asyncio.wait_for(
            self._adapter(written).preview_council(
                project_id="project-1",
                cycle_id="cycle-1",
                request_text=RERUN,
                config=_runtime_config(tmp_path),
                proposal=self._proposal(),
            ),
            timeout=5,
        )

        assert written == [], "the roster writer ran on a roster already approved"
        assert plan is not None
        # The red team's focus is deliberately blank: it borrows the chair's
        # agent, and labelling it "synthesis" would describe the opposite job.
        assert [seat.focus for seat in plan.seats] == ["Grouped validation", "", "Synthesis"]

    @pytest.mark.asyncio
    async def test_the_supplied_depth_wins_over_the_recommendation(self, tmp_path: Path):
        written: list = []

        plan = await asyncio.wait_for(
            self._adapter(written).preview_council(
                project_id="project-1",
                cycle_id="cycle-1",
                request_text=RERUN,
                config=_runtime_config(tmp_path),
                depth=CouncilDepth.LIGHT,
                proposal=self._proposal(),
            ),
            timeout=5,
        )

        assert plan is not None
        assert plan.depth is CouncilDepth.LIGHT

    @pytest.mark.asyncio
    async def test_the_participant_dials_reach_the_previewed_seats(self, tmp_path: Path):
        """The card is drawn from this plan, so an unapplied dial is invisible."""
        from deerflow.dbtl.council_settings import ParticipantSettings

        written: list = []

        plan = await asyncio.wait_for(
            self._adapter(written).preview_council(
                project_id="project-1",
                cycle_id="cycle-1",
                request_text=RERUN,
                config=_runtime_config(tmp_path),
                proposal=self._proposal(),
                participant_settings={
                    "chair": ParticipantSettings(
                        participant_id="chair",
                        reasoning="extended",
                        instructions="Do not average the positions.",
                    )
                },
            ),
            timeout=5,
        )

        assert plan is not None
        chair = next(seat for seat in plan.seats if seat.seat_id.endswith("chair"))
        assert chair.reasoning == "extended"
        assert chair.instructions == "Do not average the positions."
