"""Phase 5 — the supervisor graph itself.

The no-go is "routing drops messages, project scope, file access, or artifact
inspection", so the central test here is parameterized over *every* branch and
asserts the whole of ``ThreadState`` survives each one. A single happy-path test
would pass while three branches quietly truncated state.

The lead agent is injected as a fake compiled graph. That is not only to avoid
an LLM call: it lets the ordinary branch assert *delegation actually happened*,
which a real agent's output could not distinguish from the supervisor answering
by itself.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deerflow.agents.dbtl.live_stage.types import LiveStageResult
from deerflow.agents.dbtl.supervisor import _make_llm_question_writer, build_supervisor_graph
from deerflow.agents.dbtl.supervisor_support.card_history import latest_cycle_request_text as _latest_cycle_request_text
from deerflow.agents.thread_state import get_thread_state_schema
from deerflow.dbtl.branches import SupervisorBranch, SupervisorContext
from deerflow.dbtl.council import request_context
from deerflow.dbtl.routing import ExplicitChoice
from deerflow.dbtl.setup_questions import SetupQuestion

MODE = "full"
SCHEMA = get_thread_state_schema(MODE)


class NoopStageAdapter:
    async def execute(self, *, cycle_id: str | None, **_kwargs) -> LiveStageResult:
        return LiveStageResult(
            stage="design",
            cycle_id=cycle_id,
            note="Stage execution is unavailable, so nothing has been recorded.",
        )


# One state carrying something in every channel a run depends on. If a branch
# drops any of these, the corresponding product feature breaks: sandbox ->
# file access, artifacts -> artifact inspection, thread_data -> uploads.
FULL_STATE = {
    "messages": [HumanMessage(content="what is in the workspace?", id="human-1")],
    "artifacts": ["/mnt/user-data/outputs/report.md"],
    "sandbox": {"sandbox_id": "local:thread-1"},
    "thread_data": {
        "workspace_path": "/mnt/user-data/workspace",
        "uploads_path": "/mnt/user-data/uploads",
        "outputs_path": "/mnt/user-data/outputs",
    },
    "title": "Existing title",
    "summary_text": "an earlier summary",
    "uploaded_files": ["notes.csv"],
    "skill_context": [{"name": "genomic-selection", "path": "/mnt/skills/gs", "description": "d"}],
    "goal": {
        "objective": "finish the yield analysis",
        "status": "active",
        "created_at": "2026-07-25T00:00:00Z",
        "updated_at": "2026-07-25T00:00:00Z",
        "continuation_count": 1,
        "max_continuations": 8,
        "no_progress_count": 0,
        "max_no_progress_continuations": 2,
    },
    "todos": [{"content": "read the trial data", "status": "completed"}],
    "delegations": [
        {
            "id": "task-1",
            "description": "summarize the trial",
            "subagent_type": "general-purpose",
            "status": "completed",
            "created_at": "2026-07-25T00:00:00Z",
        }
    ],
    "viewed_images": {
        "/mnt/user-data/uploads/plot.png": {
            "mime_type": "image/png",
            "size": 1024,
            "actual_path": "/mnt/user-data/uploads/plot.png",
        }
    },
    "promoted": {"catalog_hash": "abc123", "names": ["mcp__weather__forecast"]},
}


def fake_lead_agent(marker: list[str]):
    """A stand-in for the compiled lead agent, recording that it ran."""

    def model(state):
        marker.append("lead_agent")
        return {"messages": [AIMessage(content="the workspace has 2 files", id="ai-1")]}

    builder = StateGraph(SCHEMA)
    builder.add_node("model", model)
    builder.add_edge(START, "model")
    builder.add_edge("model", END)
    return builder.compile(checkpointer=False)


async def fake_question_writer(request_text: str, missing_fields):
    """Stands in for the model that writes the post-approval questions.

    Injected in every test: without it the graph would build the configured
    chat model and make a real call, which is both slow and non-deterministic —
    and the question wording is exactly what these tests assert on.
    """
    return (
        SetupQuestion(
            id="trait",
            question="Which trait should this cycle target?",
            why="Design cannot pin an outcome without it.",
            recommendation="plant height",
            grounded=False,
        ),
        SetupQuestion(
            id="scope",
            question="Which populations are in scope?",
            recommendation="all populations in the project",
            grounded=True,
        ),
    )


def compile_supervisor(
    context: SupervisorContext,
    marker: list[str] | None = None,
    question_writer=fake_question_writer,
):
    graph = build_supervisor_graph(
        lead_agent=fake_lead_agent(marker if marker is not None else []),
        context=context,
        stage_adapter=NoopStageAdapter(),
        state_schema=SCHEMA,
        question_writer=question_writer,
    )
    return graph.compile(checkpointer=InMemorySaver())


BRANCH_CASES = [
    pytest.param(
        "what is in the workspace?",
        SupervisorContext(project_id="proj-1", project_name="G2F"),
        SupervisorBranch.ORDINARY,
        id="ordinary",
    ),
    pytest.param(
        "start a DBTL cycle",
        SupervisorContext(project_id="proj-1", project_name="G2F"),
        SupervisorBranch.CLARIFICATION,
        id="clarification",
    ),
    pytest.param(
        "I want to test whether the new hybrids beat the check for grain yield across three environments in 2026, validated on held-out sites",
        SupervisorContext(project_id="proj-1", project_name="G2F"),
        SupervisorBranch.CYCLE_SETUP,
        id="cycle_setup",
    ),
    pytest.param(
        "draft the design package",
        SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
        SupervisorBranch.CYCLE_CONTINUATION,
        id="cycle_continuation",
    ),
]


class TestCompleteThreadStateOnEveryBranch:
    @pytest.mark.parametrize(("text", "context", "expected"), BRANCH_CASES)
    @pytest.mark.asyncio
    async def test_every_channel_survives_routing(self, text, context, expected):
        state = {**FULL_STATE, "messages": [HumanMessage(content=text, id="human-1")]}
        graph = compile_supervisor(context)

        final = await graph.ainvoke(state, config={"configurable": {"thread_id": f"br-{expected}"}})

        # Project scope, file access, uploads, and artifact inspection.
        assert final["sandbox"] == {"sandbox_id": "local:thread-1"}
        assert final["thread_data"]["workspace_path"] == "/mnt/user-data/workspace"
        assert final["artifacts"] == ["/mnt/user-data/outputs/report.md"]
        assert final["uploaded_files"] == ["notes.csv"]
        assert final["title"] == "Existing title"
        assert final["summary_text"] == "an earlier summary"
        assert [s["path"] for s in final["skill_context"]] == ["/mnt/skills/gs"]

        # Goals, plan mode, delegation ledger, vision, and deferred-tool
        # promotions. Each of these is a product feature that would break in a
        # different, hard-to-attribute way if a branch dropped its channel.
        assert final["goal"]["objective"] == "finish the yield analysis"
        assert final["goal"]["continuation_count"] == 1
        assert [t["content"] for t in final["todos"]] == ["read the trial data"]
        assert [d["id"] for d in final["delegations"]] == ["task-1"]
        assert "/mnt/user-data/uploads/plot.png" in final["viewed_images"]
        assert final["promoted"] == {"catalog_hash": "abc123", "names": ["mcp__weather__forecast"]}

        # The user's own message is never dropped or rewritten.
        assert final["messages"][0].id == "human-1"
        assert final["messages"][0].content == text
        # Every branch answers exactly once. A branch that answers with a Human
        # Input Card still answers once — the call/result pair is the single
        # turn its renderer restores from, not two replies.
        replies = final["messages"][1:]
        if isinstance(replies[-1], ToolMessage):
            assert len(replies) == 2
            assert replies[0].tool_calls[0]["id"] == replies[-1].tool_call_id
        else:
            assert len(replies) == 1

    @pytest.mark.asyncio
    async def test_ordinary_branch_delegates_to_the_real_lead_agent(self):
        marker: list[str] = []
        graph = compile_supervisor(SupervisorContext(project_id="proj-1", project_name="G2F"), marker)

        final = await graph.ainvoke({**FULL_STATE}, config={"configurable": {"thread_id": "delegation"}})

        assert marker == ["lead_agent"], "ordinary work must run the lead agent, not the supervisor"
        assert final["messages"][-1].content == "the workspace has 2 files"

    @pytest.mark.asyncio
    async def test_a_follow_up_question_does_not_restart_the_selected_cycles_debate(self):
        marker: list[str] = []

        class Adapter:
            async def execute(self, **kwargs):
                raise AssertionError("a follow-up question must not dispatch DBTL workers")

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent(marker),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    HumanMessage(
                        content="What parameters did the design stage capture for the simulation?",
                        id="follow-up",
                    )
                ],
            },
            config={"configurable": {"thread_id": "ordinary-cycle-follow-up"}},
        )

        assert marker == ["lead_agent"]
        assert final["messages"][-1].content == "the workspace has 2 files"

    @pytest.mark.asyncio
    async def test_a_parked_cycle_routes_to_lead_with_unapproved_bound_context(self):
        seen_context: dict[str, object] = {}

        def model(_state, config):
            seen_context.update(request_context(config))
            return {"messages": [AIMessage(content="I can discuss the parked Design.", id="ai-parked")]}

        lead = StateGraph(SCHEMA)
        lead.add_node("model", model)
        lead.add_edge(START, "model")
        lead.add_edge("model", END)

        class ParkedAdapter:
            async def parked_design_context(self, **_kwargs):
                return {
                    "cycle_id": "cyc-1",
                    "cycle_title": "Drought trial",
                    "stage": "design",
                    "approval_status": "unapproved",
                    "evidence": {
                        "uri": "/mnt/user-data/outputs/design.md",
                        "content_hash": "a" * 64,
                    },
                }

            async def execute(self, **_kwargs):
                raise AssertionError("a parked cycle must not dispatch stage workers")

        graph = build_supervisor_graph(
            lead_agent=lead.compile(checkpointer=False),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=ParkedAdapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    HumanMessage(
                        content="Draft the design package.",
                        id="parked-request",
                    )
                ],
            },
            config={"configurable": {"thread_id": "parked-cycle"}},
        )

        assert final["messages"][-1].content == "I can discuss the parked Design."
        brief = seen_context["dbtl_parked_design_brief"]
        assert isinstance(brief, dict)
        assert brief["approval_status"] == "unapproved"
        assert brief["evidence"]["content_hash"] == "a" * 64

    @pytest.mark.asyncio
    async def test_genomic_simulation_request_uses_native_dbtl_inquiry_without_lead_agent(self):
        marker: list[str] = []
        graph = compile_supervisor(
            SupervisorContext(project_id="proj-1", project_name="G2F"),
            marker,
        )
        text = "simulate a population of 100 maize inbreds with 10 SNP markers for each and 1 plant height phenotype for each"

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content=text, id="human-1")]},
            config={"configurable": {"thread_id": "genomic-simulation-inquiry"}},
        )

        assert marker == []
        call, card = final["messages"][-2:]
        assert isinstance(call, AIMessage)
        assert isinstance(card, ToolMessage)
        assert call.tool_calls[0]["name"] == "ask_clarification"
        request = card.artifact["human_input"]
        # Confirmation first: the human decides whether a durable record should
        # exist before being asked to describe one.
        assert request["clarification_type"] == "cycle_setup_confirmation"
        assert request["request_id"].startswith("dbtl-setup-confirm__")
        assert request["source"] == "ask_clarification"

    @pytest.mark.asyncio
    async def test_plain_start_cycle_request_uses_native_inquiry_with_existing_cycles(self):
        marker: list[str] = []
        graph = compile_supervisor(
            SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                project_cycle_count=2,
                has_unfinished_cycles=True,
            ),
            marker,
        )
        text = "let's start a cycle to understand the simulation"

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content=text, id="human-1")]},
            config={"configurable": {"thread_id": "plain-start-cycle-inquiry"}},
        )

        assert marker == []
        call, card = final["messages"][-2:]
        assert isinstance(call, AIMessage)
        assert isinstance(card, ToolMessage)
        assert call.tool_calls[0]["name"] == "ask_clarification"
        request = card.artifact["human_input"]
        assert request["clarification_type"] == "cycle_setup_confirmation"
        assert request["request_id"].startswith("dbtl-setup-confirm__")

    @pytest.mark.asyncio
    async def test_fresh_project_prior_applies_only_on_the_first_conversation_turn(self):
        context = SupervisorContext(
            project_id="proj-1",
            project_name="G2F",
            project_cycle_count=0,
            has_unfinished_cycles=False,
        )
        fresh_marker: list[str] = []
        fresh_graph = compile_supervisor(context, fresh_marker)

        fresh = await fresh_graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="Evaluate the trial", id="human-1")],
            },
            config={"configurable": {"thread_id": "fresh-project-prior"}},
        )

        assert fresh_marker == []
        assert isinstance(fresh["messages"][-1], ToolMessage)
        assert fresh["messages"][-1].name == "ask_clarification"

        established_marker: list[str] = []
        established_graph = compile_supervisor(context, established_marker)
        established = await established_graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    HumanMessage(content="Show the files", id="human-1"),
                    AIMessage(content="There are two files.", id="ai-1"),
                    HumanMessage(content="Evaluate the trial", id="human-2"),
                ],
            },
            config={"configurable": {"thread_id": "established-project-prior"}},
        )

        assert established_marker == ["lead_agent"]
        assert any(isinstance(message, AIMessage) and message.content == "the workspace has 2 files" for message in established["messages"])

    @pytest.mark.parametrize(
        ("text", "context", "expected"),
        [case.values for case in BRANCH_CASES if case.id != "ordinary"],
    )
    @pytest.mark.asyncio
    async def test_non_ordinary_branches_never_run_the_lead_agent(self, text, context, expected):
        marker: list[str] = []
        graph = compile_supervisor(context, marker)

        await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content=text, id="human-1")]},
            config={"configurable": {"thread_id": f"nolead-{expected}"}},
        )

        assert marker == []

    @pytest.mark.asyncio
    async def test_cycle_setup_uses_the_native_human_input_card(self):
        text, context, _expected = BRANCH_CASES[2].values
        graph = compile_supervisor(context)

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content=text, id="human-1")]},
            config={"configurable": {"thread_id": "cycle-setup-fallback"}},
        )

        call, card = final["messages"][-2:]
        assert isinstance(call, AIMessage)
        assert isinstance(card, ToolMessage)
        assert call.tool_calls[0]["name"] == "ask_clarification"
        assert call.tool_calls[0]["id"] == card.tool_call_id
        request = card.artifact["human_input"]
        assert request["clarification_type"] == "cycle_setup_confirmation"
        assert request["request_id"].startswith("dbtl-setup-confirm__")
        assert request["input_mode"] == "single_choice"
        assert [option["value"] for option in request["options"]] == [
            "create_cycle",
            "keep_ordinary",
            "not_sure",
        ]
        assert request["dbtl_cycle_setup"]["objective"]
        # The card's prose is one line now; the setup payload still carries the
        # objective, which is what creation actually needs.
        assert request["context"].strip().count("\n") == 0

    @pytest.mark.asyncio
    async def test_explicit_keep_ordinary_reaches_the_lead_agent(self):
        # The chip's "keep this ordinary" must win over research-shaped text.
        marker: list[str] = []
        graph = compile_supervisor(
            SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                explicit_choice=ExplicitChoice.ORDINARY,
            ),
            marker,
        )

        await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    HumanMessage(
                        content="test whether hybrids beat the check on held-out sites",
                        id="human-1",
                    )
                ],
            },
            config={"configurable": {"thread_id": "explicit-ordinary"}},
        )

        assert marker == ["lead_agent"]


class TestSetupClarificationIsACard:
    """Approve first, then answer questions — and both must be cards.

    The order carries the human-in-the-loop guarantee: nobody should have to
    describe a research record to discover they are being offered one. So the
    first card asks whether to start a cycle at all, and only an approval opens
    the design questions.

    The other half is that the answer has to come back to the branch that asked
    it. That is the easy one to miss — the explicit choice that produced the
    card is per-request by design, so it is gone by the time the answer
    arrives, and routing on the answer alone lands in ordinary work with the
    user's reply silently absorbed by the lead agent.
    """

    SETUP_CONTEXT = SupervisorContext(
        project_id="proj-1",
        project_name="test2",
        explicit_choice=ExplicitChoice.START_CYCLE,
    )
    REQUEST = "start a phenotype data curation plan. be simple"
    ANSWER = "Target trait is plant height, seasons 2020-2023, all populations, validated on held-out sites"

    @staticmethod
    def card_reply(request_id: str, value: str) -> HumanMessage:
        """The shape the Gateway persists when a card is answered."""
        return HumanMessage(
            content=value,
            id="human-answer",
            additional_kwargs={
                "hide_from_ui": True,
                "human_input_response": {
                    "version": 1,
                    "kind": "human_input_response",
                    "source": "ask_clarification",
                    "request_id": request_id,
                    "response_kind": "text",
                    "value": value,
                },
            },
        )

    async def ask(self, thread_id: str):
        graph = compile_supervisor(self.SETUP_CONTEXT)
        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content=self.REQUEST, id="human-1")]},
            config={
                "configurable": {"thread_id": thread_id},
                "context": {"run_id": "run-1"},
            },
        )
        return final["messages"]

    async def approve(self, asked, thread_id: str, marker: list[str] | None = None):
        """Answer the confirmation card with "create this cycle"."""
        request_id = asked[-1].artifact["human_input"]["request_id"]
        graph = compile_supervisor(
            # No explicit choice: the chip applies to one request, so the answer
            # arrives without it. Recovering the intent is part of the point.
            SupervisorContext(project_id="proj-1", project_name="test2"),
            marker,
        )
        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [*asked, self.card_reply(request_id, "create_cycle")]},
            config={
                "configurable": {"thread_id": thread_id},
                "context": {"run_id": "run-approve"},
            },
        )
        return final

    @pytest.mark.asyncio
    async def test_the_first_card_asks_whether_to_start_a_cycle_at_all(self):
        messages = await self.ask("setup-card")

        call, card = messages[-2], messages[-1]
        assert isinstance(card, ToolMessage)
        assert card.name == "ask_clarification"
        # The call/result pair the card renderer restores from.
        assert isinstance(call, AIMessage)
        assert call.tool_calls[0]["name"] == "ask_clarification"
        assert call.tool_calls[0]["id"] == card.tool_call_id

        request = card.artifact["human_input"]
        assert request["source"] == "ask_clarification"
        assert request["clarification_type"] == "cycle_setup_confirmation"
        # A decision, not a form: the user picks, they do not fill in blanks.
        assert request["input_mode"] == "single_choice"
        assert [option["value"] for option in request["options"]] == [
            "create_cycle",
            "keep_ordinary",
            "not_sure",
        ]
        # The card is one line of framing. The gates, the record effect, and
        # the no-record notice were cut deliberately: they were identical on
        # every card and buried a yes/no decision under boilerplate.
        assert request["context"].strip().count("\n") == 0
        assert "dbtl cycle" in request["context"].lower()

    @pytest.mark.asyncio
    async def test_approval_opens_questions_the_model_wrote_and_already_answered(self):
        asked = await self.ask("setup-approve")
        marker: list[str] = []
        final = await self.approve(asked, "setup-approve-2", marker)

        assert marker == [], "an answered confirmation must not fall through to ordinary work"
        card = final["messages"][-1]
        assert isinstance(card, ToolMessage)
        request = card.artifact["human_input"]
        assert request["clarification_type"] == "cycle_setup"
        assert request["request_id"].startswith("dbtl-setup__")

        # The questions are the model's, not the classifier's rule names.
        assert "Which trait should this cycle target?" in request["question"]
        assert "target trait" not in request["question"].lower()

        # Every question carries a proposed answer, so the human corrects
        # rather than composes.
        assert "plant height" in request["question"]
        assert "accept" in request["question"].lower()

        # And the structured form travels with it for a richer card later.
        assert [q["id"] for q in request["setup_questions"]] == ["trait", "scope"]
        assert request["setup_questions"][0]["grounded"] is False

    @pytest.mark.asyncio
    async def test_a_suggestion_is_never_presented_as_something_the_user_said(self):
        """These answers become a durable record; provenance is the safeguard."""
        asked = await self.ask("setup-provenance")
        final = await self.approve(asked, "setup-provenance-2")
        question = final["messages"][-1].artifact["human_input"]["question"]

        trait_block, scope_block = question.split("2.")
        assert "suggested" in trait_block.lower(), "an assumed value must be labelled"
        assert "from your request" in scope_block.lower()

    @pytest.mark.asyncio
    async def test_answering_the_questions_does_not_ask_them_again(self):
        """An approval lives in history forever; the guard is what ends the loop."""
        asked = await self.ask("setup-loop")
        approved = await self.approve(asked, "setup-loop-2")
        questions_id = approved["messages"][-1].artifact["human_input"]["request_id"]

        marker: list[str] = []
        graph = compile_supervisor(SupervisorContext(project_id="proj-1", project_name="test2"), marker)
        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [*approved["messages"], self.card_reply(questions_id, self.ANSWER)]},
            config={"configurable": {"thread_id": "setup-loop-3"}},
        )

        answer = final["messages"][-1]
        assert isinstance(answer, AIMessage), "the questions must not be re-raised as a card"
        # It acknowledges and points forward. This used to pin the words "review
        # the Design stage", which named an action the repository refuses while
        # the stage holds no artifact -- and a chat-created cycle has none.
        assert "Recorded your design inputs" in answer.content
        assert "submit" not in answer.content.lower()
        # Authored by the graph with no model call, so it needs the receipt
        # marker or it never reaches the thread's durable feed.
        assert answer.additional_kwargs.get("deerflow_graph_receipt") is True
        assert marker == []

    @pytest.mark.asyncio
    async def test_answering_existing_cycle_questions_stays_out_of_discovery(self):
        asked = await self.ask("setup-discovery")
        approved = await self.approve(asked, "setup-discovery-2")
        questions = approved["messages"][-1].artifact["human_input"]

        graph = compile_supervisor(
            SupervisorContext(
                project_id="proj-1",
                project_name="test2",
                discovery_enabled=True,
            )
        )
        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    *approved["messages"],
                    self.card_reply(questions["request_id"], self.ANSWER),
                ],
            },
            config={"configurable": {"thread_id": "setup-discovery-3"}},
        )

        answer = final["messages"][-1]
        assert isinstance(answer, AIMessage)
        assert "Recorded your design inputs" in answer.content
        assert "Conversational discovery" not in answer.content

    @pytest.mark.asyncio
    async def test_declining_the_confirmation_creates_nothing_and_says_so(self):
        asked = await self.ask("setup-decline")
        request_id = asked[-1].artifact["human_input"]["request_id"]
        graph = compile_supervisor(SupervisorContext(project_id="proj-1", project_name="test2"))

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [*asked, self.card_reply(request_id, "keep_ordinary")]},
            config={"configurable": {"thread_id": "setup-decline-2"}},
        )

        answer = final["messages"][-1]
        assert isinstance(answer, AIMessage)
        assert "No DBTL cycle was created." in answer.content
        assert final["artifacts"] == FULL_STATE["artifacts"]

    @pytest.mark.asyncio
    async def test_declining_confirmation_stays_ordinary_when_discovery_is_enabled(self):
        asked = await self.ask("setup-decline-with-discovery")
        request_id = asked[-1].artifact["human_input"]["request_id"]
        graph = compile_supervisor(
            SupervisorContext(
                project_id="proj-1",
                project_name="test2",
                discovery_enabled=True,
            )
        )

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [*asked, self.card_reply(request_id, "keep_ordinary")],
            },
            config={"configurable": {"thread_id": "setup-decline-with-discovery-2"}},
        )

        answer = final["messages"][-1]
        assert isinstance(answer, AIMessage)
        assert answer.content == "Kept as ordinary chat. No DBTL cycle was created."

    @pytest.mark.asyncio
    async def test_declining_the_confirmation_suppresses_later_classifier_cards(self):
        asked = await self.ask("setup-decline-suppression")
        request_id = asked[-1].artifact["human_input"]["request_id"]
        graph = compile_supervisor(SupervisorContext(project_id="proj-1", project_name="test2"))
        declined = await graph.ainvoke(
            {**FULL_STATE, "messages": [*asked, self.card_reply(request_id, "keep_ordinary")]},
            config={"configurable": {"thread_id": "setup-decline-suppression-2"}},
        )

        marker: list[str] = []
        graph = compile_supervisor(SupervisorContext(project_id="proj-1", project_name="test2"), marker)
        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    *declined["messages"],
                    HumanMessage(
                        content=("I want to test whether the new hybrids beat the check for grain yield across three environments in 2026, validated on held-out sites"),
                        id="human-after-decline",
                    ),
                ],
            },
            config={"configurable": {"thread_id": "setup-decline-suppression-3"}},
        )

        assert marker == ["lead_agent"]
        assert final["messages"][-1].content == "the workspace has 2 files"

    @pytest.mark.asyncio
    async def test_the_clients_default_scope_does_not_strand_the_answer(self):
        # The client sends a scope with every request and falls back to
        # "ordinary" for a card it has no special handling for. That is a
        # filled-in default, not the user choosing ordinary work, so it must
        # not beat the intent recovered from the card being answered.
        asked = await self.ask("setup-default-scope")
        request_id = asked[-1].artifact["human_input"]["request_id"]

        marker: list[str] = []
        graph = compile_supervisor(
            SupervisorContext(
                project_id="proj-1",
                project_name="test2",
                explicit_choice=ExplicitChoice.ORDINARY,
            ),
            marker,
        )

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [*asked, self.card_reply(request_id, "create_cycle")]},
            config={"configurable": {"thread_id": "setup-default-scope-2"}},
        )

        assert marker == []
        card = final["messages"][-1]
        assert isinstance(card, ToolMessage)
        assert card.artifact["human_input"]["clarification_type"] == "cycle_setup"

    @pytest.mark.asyncio
    async def test_a_model_outage_still_asks_the_deterministic_gaps(self):
        """A drafting failure costs a plainer card, never the approved cycle.

        Exercises the real production writer rather than a stand-in, because
        the behaviour under test *is* its failure path.
        """
        setup_context = SupervisorContext(project_id="proj-1", project_name="test2")
        asked = await self.ask("setup-outage")
        request_id = asked[-1].artifact["human_input"]["request_id"]
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=setup_context,
            stage_adapter=NoopStageAdapter(),
            state_schema=SCHEMA,
            question_writer=_make_llm_question_writer(setup_context),
        ).compile(checkpointer=InMemorySaver())

        # Patched at the source module: the writer imports it inside the call,
        # so a supervisor-module attribute would never be consulted.
        with patch("deerflow.utils.oneshot_llm.run_oneshot_llm", side_effect=RuntimeError("down")):
            final = await graph.ainvoke(
                {**FULL_STATE, "messages": [*asked, self.card_reply(request_id, "create_cycle")]},
                config={"configurable": {"thread_id": "setup-outage-2"}},
            )

        card = final["messages"][-1]
        assert isinstance(card, ToolMessage)
        question = card.artifact["human_input"]["question"]
        assert "target trait" in question.lower(), "the deterministic gaps are the fallback"
        assert "suggested" not in question.lower(), "a fallback may not invent an answer"

    @pytest.mark.asyncio
    async def test_server_creates_the_cycle_and_opens_design_without_a_browser_kickoff(self):
        asked = await self.ask("server-owned-setup")
        confirmation_id = asked[-1].artifact["human_input"]["request_id"]
        created_with: list[dict] = []

        async def create_cycle(**kwargs):
            created_with.append(kwargs)
            return {"id": kwargs["cycle_id"]}

        create_graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="test2"),
            stage_adapter=NoopStageAdapter(),
            state_schema=SCHEMA,
            question_writer=fake_question_writer,
            cycle_creator=create_cycle,
        ).compile(checkpointer=InMemorySaver())
        created = await create_graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [*asked, self.card_reply(confirmation_id, "create_cycle")],
            },
            config={
                "configurable": {"thread_id": "server-owned-setup"},
                "context": {"run_id": "run-create", "user_id": "owner-1"},
            },
        )

        assert len(created_with) == 1
        create_args = created_with[0]
        assert create_args["project_id"] == "proj-1"
        assert create_args["originating_thread_id"] == "server-owned-setup"
        assert create_args["created_by"] == "owner-1"
        assert create_args["idempotency_key"].startswith("setup:")
        assert created["messages"][-3].additional_kwargs["deerflow_graph_receipt"] is True
        setup_request = created["messages"][-1].artifact["human_input"]
        assert setup_request["clarification_type"] == "cycle_setup"
        assert setup_request["dbtl_cycle_id"] == create_args["cycle_id"]

        previewed: list[dict] = []
        executed: list[dict] = []

        class Adapter:
            async def preview_council(self, **kwargs):
                previewed.append(kwargs)
                return TestCouncilPreflight._plan()

            async def execute(self, **kwargs):
                executed.append(kwargs)
                return SimpleNamespace(
                    stage="design",
                    cycle_id=kwargs["cycle_id"],
                    note="ran",
                    artifact_uri=None,
                    clarification_question=None,
                    produced_usable_evidence=True,
                )

        setup_id = setup_request["request_id"]
        design_graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="test2"),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
            question_writer=fake_question_writer,
        ).compile(checkpointer=InMemorySaver())
        preflight = await design_graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [*created["messages"], self.card_reply(setup_id, self.ANSWER)],
            },
            config={
                "configurable": {"thread_id": "server-owned-setup"},
                "context": {"run_id": "run-design", "user_id": "owner-1"},
            },
        )

        assert executed == []
        assert len(previewed) == 1
        assert previewed[0]["cycle_id"] == create_args["cycle_id"]
        assert self.REQUEST in previewed[0]["request_text"]
        assert self.ANSWER in previewed[0]["request_text"]
        preflight_request = preflight["messages"][-1].artifact["human_input"]
        assert preflight_request["clarification_type"] == "council_preflight"
        assert preflight_request["dbtl_cycle_id"] == create_args["cycle_id"]
        assert self.REQUEST in preflight_request["dbtl_request_text"]
        assert self.ANSWER in preflight_request["dbtl_request_text"]

        # Replaying the same setup answer may recompute a preview, but it must
        # address the same server card rather than minting a second control.
        replay = await design_graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [*created["messages"], self.card_reply(setup_id, self.ANSWER)],
            },
            config={
                "configurable": {"thread_id": "server-owned-setup-replay"},
                "context": {"run_id": "different-run", "user_id": "owner-1"},
            },
        )
        assert replay["messages"][-1].artifact["human_input"]["request_id"] == preflight_request["request_id"]

        execute_graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="test2"),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
            question_writer=fake_question_writer,
        ).compile(checkpointer=InMemorySaver())
        await execute_graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    *preflight["messages"],
                    self.card_reply(preflight_request["request_id"], "light"),
                ],
            },
            config={
                "configurable": {"thread_id": "server-owned-setup"},
                "context": {
                    "run_id": "run-meeting",
                    "user_id": "owner-1",
                    "dbtl_council_depth": "light",
                },
            },
        )

        assert len(executed) == 1
        assert executed[0]["cycle_id"] == create_args["cycle_id"]
        assert self.REQUEST in executed[0]["request_text"]
        assert self.ANSWER in executed[0]["request_text"]

    @pytest.mark.asyncio
    async def test_question_writer_failure_cannot_hide_a_created_cycle(self):
        asked = await self.ask("server-owned-setup-writer-failure")
        confirmation_id = asked[-1].artifact["human_input"]["request_id"]

        async def create_cycle(**kwargs):
            return {"id": kwargs["cycle_id"]}

        async def failed_writer(_request_text, _missing_fields):
            raise RuntimeError("question service unavailable")

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="test2"),
            stage_adapter=NoopStageAdapter(),
            state_schema=SCHEMA,
            question_writer=failed_writer,
            cycle_creator=create_cycle,
        ).compile(checkpointer=InMemorySaver())
        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [*asked, self.card_reply(confirmation_id, "create_cycle")],
            },
            config={
                "configurable": {"thread_id": "server-owned-setup-writer-failure"},
                "context": {"run_id": "run-create", "user_id": "owner-1"},
            },
        )

        receipt, _call, card = final["messages"][-3:]
        assert "was created" in receipt.content
        assert card.artifact["human_input"]["clarification_type"] == "cycle_setup"
        assert "target trait" in card.artifact["human_input"]["question"].lower()

    @pytest.mark.asyncio
    async def test_a_design_answer_still_feeds_the_running_stage(self):
        # The sibling clarification. Both prefixes resume, but to different
        # places: a design answer is the stage's input, not a routing signal.
        # Pinned here because the two now share the reading code.
        received: list[str] = []

        class Adapter:
            async def execute(self, **kwargs):
                received.append(kwargs["request_text"])
                return SimpleNamespace(
                    note="n",
                    clarification_question=None,
                    artifact_uri=None,
                    satisfies_gate=False,
                )

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="test2", selected_cycle_id="cyc-1"),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    HumanMessage(content="draft the design package", id="human-1"),
                    self.card_reply("dbtl-design__cyc-1__abc123", "Hold out the 2023 sites"),
                ],
            },
            config={"configurable": {"thread_id": "design-answer"}},
        )

        assert received == ["Hold out the 2023 sites"]

    @pytest.mark.asyncio
    async def test_a_forged_request_id_recovers_no_intent(self):
        # The originating request is read back from the card the server itself
        # emitted. A reply naming a card that was never sent must not be able
        # to conjure cycle-setup intent out of ordinary text.
        marker: list[str] = []
        graph = compile_supervisor(SupervisorContext(project_id="proj-1", project_name="test2"), marker)

        await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    HumanMessage(content="what is in the workspace?", id="human-1"),
                    self.card_reply("dbtl-setup__forged", self.ANSWER),
                ],
            },
            config={"configurable": {"thread_id": "setup-forged"}},
        )

        assert marker == ["lead_agent"]


class TestOrdinaryIsIndistinguishable:
    """The exit review's standard: ordinary chat must look untouched."""

    @pytest.mark.asyncio
    async def test_ordinary_branch_adds_nothing_of_its_own(self):
        graph = compile_supervisor(SupervisorContext(project_id="proj-1", project_name="G2F"))

        final = await graph.ainvoke({**FULL_STATE}, config={"configurable": {"thread_id": "indistinguishable"}})

        # Exactly the human turn plus the agent's answer — no routing note, no
        # banner, no supervisor commentary.
        assert [m.id for m in final["messages"]] == ["human-1", "ai-1"]

    @pytest.mark.asyncio
    async def test_completed_cycle_followup_is_read_only_and_carries_learn_evidence(self):
        captured: dict = {}

        class CompletedCycleAdapter(NoopStageAdapter):
            async def active_cycle_status(self, *, project_id):
                assert project_id == "proj-1"
                return []

            async def conversation_cycle_status(self, *, project_id, thread_id):
                assert (project_id, thread_id) == ("proj-1", "cycle-thread")
                return {
                    "cycle_id": "cycle-1",
                    "title": "Deterministic regression",
                    "state": "completed",
                    "parked": False,
                    "stages": {
                        "design": "approved",
                        "build": "approved",
                        "test": "approved",
                        "learn": "approved",
                    },
                    "artifacts": [
                        {
                            "stage": "learn",
                            "artifact_type": "learn_synthesis",
                            "uri": "/mnt/user-data/outputs/learn-review.md",
                            "content_hash": "abc123",
                        }
                    ],
                }

        def capturing_lead_agent():
            builder = StateGraph(SCHEMA)

            def node(_state, config):
                captured.update(request_context(config))
                return {"messages": [AIMessage(content="cycle summary", id="ai-cycle-summary")]}

            builder.add_node("lead", node)
            builder.add_edge(START, "lead")
            builder.add_edge("lead", END)
            return builder.compile(checkpointer=False)

        graph = build_supervisor_graph(
            lead_agent=capturing_lead_agent(),
            context=SupervisorContext(project_id="proj-1", project_name="G2F"),
            stage_adapter=CompletedCycleAdapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    HumanMessage(
                        content="what do we learned from this cycle?",
                        id="human-cycle-followup",
                    )
                ],
            },
            config={"configurable": {"thread_id": "cycle-thread"}},
        )

        assert final["messages"][-1].content == "cycle summary"
        assert captured["dbtl_read_only_context"] == {
            "active": True,
            "reason": "completed_cycle_followup",
        }
        cycle = captured["dbtl_status_snapshot"]["cycles"][0]
        assert cycle["cycle_id"] == "cycle-1"
        assert cycle["state"] == "completed"
        assert cycle["artifacts"][0]["uri"] == "/mnt/user-data/outputs/learn-review.md"


class TestPostApprovalStageHandoff:
    """A deck verdict opens the next stage, but a person still starts it."""

    MARKER = {
        "version": 1,
        "cycle_id": "cyc-1",
        "cycle_revision": 7,
        "approved_stage": "design",
        "next_stage": "build",
        "surface_id": "surface-1",
    }

    @staticmethod
    def _reply(request_id: str, option_id: str, *, echoed_value: str | None = None) -> HumanMessage:
        return HumanMessage(
            content=option_id,
            id=f"answer-{option_id}",
            additional_kwargs={
                "hide_from_ui": True,
                "human_input_response": {
                    "version": 1,
                    "kind": "human_input_response",
                    "source": "ask_clarification",
                    "request_id": request_id,
                    "response_kind": "option",
                    "option_id": option_id,
                    "value": echoed_value or option_id,
                },
            },
        )

    @staticmethod
    def _adapter(executed: list[dict], *, refusal: str | None = None):
        class Adapter:
            async def validate_stage_handoff(self, **kwargs):
                return refusal

            async def execute(self, **kwargs):
                executed.append(kwargs)
                return SimpleNamespace(
                    stage="build",
                    cycle_id=kwargs["cycle_id"],
                    note="Build dispatch was admitted.",
                    artifact_uri=None,
                    clarification_question=None,
                    produced_usable_evidence=False,
                )

        return Adapter()

    async def _ask(self, adapter, *, thread_id: str, marker: dict | None = None):
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
                explicit_choice=ExplicitChoice.CONTINUE_CYCLE,
            ),
            stage_adapter=adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())
        return await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    HumanMessage(content="Review the Design package.", id="human-1"),
                    AIMessage(content="The package is ready.", id="ai-ready"),
                    HumanMessage(
                        content="Design approval is recorded. Ask before starting Build.",
                        id="handoff-marker",
                        additional_kwargs={
                            "hide_from_ui": True,
                            "dbtl_post_approval_handoff": marker or self.MARKER,
                        },
                    ),
                ],
            },
            config={
                "configurable": {"thread_id": thread_id},
                "context": {"run_id": f"run-{thread_id}"},
            },
        )

    @pytest.mark.asyncio
    async def test_approval_surfaces_a_cycle_bound_card_without_dispatching(self):
        executed: list[dict] = []
        asked = await self._ask(self._adapter(executed), thread_id="handoff-ask")

        assert executed == []
        call, card = asked["messages"][-2:]
        assert isinstance(call, AIMessage)
        assert isinstance(card, ToolMessage)
        request = card.artifact["human_input"]
        assert request["clarification_type"] == "dbtl_stage_handoff"
        assert request["dbtl_cycle_id"] == "cyc-1"
        assert request["approved_stage"] == "design"
        assert request["next_stage"] == "build"
        assert [option["id"] for option in request["options"]] == [
            "start_next_stage",
            "hold_here",
        ]

    @pytest.mark.asyncio
    async def test_representing_a_handoff_keeps_one_request_with_a_new_delivery(self):
        executed: list[dict] = []
        first = await self._ask(self._adapter(executed), thread_id="handoff-delivery-one")
        second = await self._ask(self._adapter(executed), thread_id="handoff-delivery-two")

        first_call, first_card = first["messages"][-2:]
        second_call, second_card = second["messages"][-2:]
        first_request = first_card.artifact["human_input"]["request_id"]
        second_request = second_card.artifact["human_input"]["request_id"]

        assert first_request == second_request
        assert first_call.tool_calls[0]["id"] == second_call.tool_calls[0]["id"] == first_request
        assert first_card.tool_call_id == second_card.tool_call_id == first_request
        assert first_call.id != second_call.id
        assert first_card.id != second_card.id

    @pytest.mark.asyncio
    async def test_hold_keeps_the_stage_open_and_dispatches_nothing(self):
        executed: list[dict] = []
        adapter = self._adapter(executed)
        asked = await self._ask(adapter, thread_id="handoff-hold-ask")
        request_id = asked["messages"][-1].artifact["human_input"]["request_id"]
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F"),
            stage_adapter=adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    *asked["messages"],
                    self._reply(request_id, "hold_here"),
                ],
            },
            config={"configurable": {"thread_id": "handoff-hold-answer"}},
        )

        assert executed == []
        assert "no stage work was started" in final["messages"][-1].content.lower()

    @pytest.mark.asyncio
    async def test_start_recovers_card_cycle_and_dispatches_the_governed_stage(self):
        executed: list[dict] = []
        adapter = self._adapter(executed)
        asked = await self._ask(adapter, thread_id="handoff-start-ask")
        request_id = asked["messages"][-1].artifact["human_input"]["request_id"]
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            # Deliberately omit selected_cycle_id: the durable card owns scope.
            context=SupervisorContext(project_id="proj-1", project_name="G2F"),
            stage_adapter=adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    *asked["messages"],
                    # The reply echo is untrusted; the emitted option id wins.
                    self._reply(
                        request_id,
                        "start_next_stage",
                        echoed_value="hold_here",
                    ),
                ],
            },
            config={"configurable": {"thread_id": "handoff-start-answer"}},
        )

        assert len(executed) == 1
        assert executed[0]["cycle_id"] == "cyc-1"
        assert executed[0]["project_id"] == "proj-1"
        assert executed[0]["request_text"] == "Start the governed build stage now."
        assert executed[0]["expected_stage"] == "build"
        assert executed[0]["expected_cycle_revision"] == 7

    @pytest.mark.parametrize(
        ("surface_id", "reuses_recorded_evidence"),
        [
            ("test-evidence:attempt-1", True),
            ("validity-assessment-1", False),
        ],
    )
    @pytest.mark.asyncio
    async def test_only_path_recovery_reuses_test_evidence(self, surface_id, reuses_recorded_evidence):
        executed: list[dict] = []
        adapter = self._adapter(executed)
        marker = {
            **self.MARKER,
            "approved_stage": "build",
            "next_stage": "test",
            "surface_id": surface_id,
        }
        asked = await self._ask(adapter, thread_id=f"test-recovery-{surface_id}", marker=marker)
        request_id = asked["messages"][-1].artifact["human_input"]["request_id"]
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F"),
            stage_adapter=adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        await graph.ainvoke(
            {**FULL_STATE, "messages": [*asked["messages"], self._reply(request_id, "start_next_stage")]},
            config={"configurable": {"thread_id": f"answer-{surface_id}"}},
        )

        assert len(executed) == 1
        assert executed[0].get("reuse_recorded_test_evidence", False) is reuses_recorded_evidence

    @pytest.mark.asyncio
    async def test_an_unoffered_option_cannot_close_the_card_or_dispatch_the_stage(self):
        """A request id authenticates the card, not an arbitrary answer to it."""
        executed: list[dict] = []
        adapter = self._adapter(executed)
        asked = await self._ask(adapter, thread_id="handoff-invalid-option-ask")
        request_id = asked["messages"][-1].artifact["human_input"]["request_id"]
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F"),
            stage_adapter=adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    *asked["messages"],
                    self._reply(request_id, "not_an_offered_option"),
                ],
            },
            config={
                "configurable": {"thread_id": "handoff-invalid-option-answer"},
                "context": {"run_id": "run-handoff-invalid-option-answer"},
            },
        )

        assert executed == []
        card = final["messages"][-1]
        assert isinstance(card, ToolMessage)
        assert card.artifact["human_input"]["clarification_type"] == "dbtl_stage_handoff"

    @pytest.mark.asyncio
    async def test_stale_answer_is_refused_before_dispatch(self):
        executed: list[dict] = []
        asked = await self._ask(self._adapter(executed), thread_id="handoff-stale-ask")
        request_id = asked["messages"][-1].artifact["human_input"]["request_id"]
        stale_adapter = self._adapter(
            executed,
            refusal="The cycle changed after this prompt was rendered. Nothing was started.",
        )
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F"),
            stage_adapter=stale_adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    *asked["messages"],
                    self._reply(request_id, "start_next_stage"),
                ],
            },
            config={"configurable": {"thread_id": "handoff-stale-answer"}},
        )

        assert executed == []
        assert "nothing was started" in final["messages"][-1].content.lower()

    async def _follow_up(self, adapter, asked, text, *, thread_id, marker, context=None):
        """Send unscoped free text into a thread that still holds the card."""
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent(marker),
            context=context
            or SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                explicit_choice=ExplicitChoice.ORDINARY,
            ),
            stage_adapter=adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())
        return await graph.ainvoke(
            {**FULL_STATE, "messages": [*asked["messages"], HumanMessage(content=text)]},
            config={
                "configurable": {"thread_id": thread_id},
                "context": {"run_id": f"run-{thread_id}"},
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "follow_up",
        [
            "go ahead with build",
            "now, start to build following the approved design",
            "approve to build",
            "now, shall we move to Test?",
        ],
    )
    async def test_unscoped_free_text_re_presents_the_card_instead_of_running_the_lead_agent(self, follow_up):
        """The observed escape, closed at the branch that let it through.

        Each of these ran ordinary work in the `test3` thread while a governed
        cycle owned the request: 35 model calls, ~684k tokens, and no durable
        Build record. The card is still unanswered, so the request is answering
        it — and only the card can carry that answer into a governed dispatch.
        """
        executed: list[dict] = []
        lead_marker: list[str] = []
        adapter = self._adapter(executed)
        asked = await self._ask(adapter, thread_id=f"handoff-escape-ask-{abs(hash(follow_up))}")

        final = await self._follow_up(
            adapter,
            asked,
            follow_up,
            thread_id=f"handoff-escape-{abs(hash(follow_up))}",
            marker=lead_marker,
        )

        assert lead_marker == [], "ordinary lead agent must not receive a pending stage control"
        assert executed == [], "re-presenting a control must dispatch no stage work"
        card = final["messages"][-1]
        assert isinstance(card, ToolMessage)
        request = card.artifact["human_input"]
        assert request["clarification_type"] == "dbtl_stage_handoff"
        assert request["next_stage"] == "build"

    @pytest.mark.asyncio
    async def test_a_held_control_releases_ordinary_conversation(self):
        """Hold is a decision, and re-presenting it would argue with the person.

        The fence must intercept a *waiting* control, not become a trap that
        answers every later message with the same card.
        """
        executed: list[dict] = []
        lead_marker: list[str] = []
        adapter = self._adapter(executed)
        asked = await self._ask(adapter, thread_id="handoff-held-ask")
        request_id = asked["messages"][-1].artifact["human_input"]["request_id"]
        held = {**asked, "messages": [*asked["messages"], self._reply(request_id, "hold_here")]}

        final = await self._follow_up(
            adapter,
            held,
            "what does the design say about the training population?",
            thread_id="handoff-held-follow-up",
            marker=lead_marker,
        )

        assert lead_marker == ["lead_agent"]
        assert executed == []
        assert final["messages"][-1].content == "the workspace has 2 files"

    @pytest.mark.asyncio
    async def test_a_stale_card_is_refused_once_and_then_releases_the_conversation(self):
        """A dead control must not become a dead conversation.

        The card is refused when the cycle moves on, and the fence re-presents
        on every message — so without a release the refusal repeats forever and
        ordinary work is unreachable for the life of the thread. That is worse
        than the escape the fence exists to prevent.
        """
        executed: list[dict] = []
        lead_marker: list[str] = []
        asked = await self._ask(self._adapter(executed), thread_id="handoff-stale-release-ask")
        stale = self._adapter(executed, refusal="The cycle changed after this prompt was rendered. Nothing was started.")

        first = await self._follow_up(stale, asked, "go ahead with build", thread_id="handoff-stale-1", marker=lead_marker)
        assert lead_marker == []
        assert executed == []
        assert "nothing was started" in first["messages"][-1].content.lower()

        second = await self._follow_up(
            stale,
            {"messages": first["messages"]},
            "what does the design say about the training population?",
            thread_id="handoff-stale-2",
            marker=lead_marker,
        )

        assert lead_marker == ["lead_agent"], "a refused card must not keep intercepting"
        assert executed == []
        assert second["messages"][-1].content == "the workspace has 2 files"

    @pytest.mark.asyncio
    async def test_a_card_for_another_cycle_is_not_presented_to_this_one(self):
        """A card that never names its cycle must not be answered by mistake.

        A project runs several cycles at once. Asking a read-only question with
        cycle B selected classifies ordinary and carries no cycle id, so without
        an explicit guard cycle A's Start card is presented — and clicking Start
        would dispatch the wrong cycle's stage.
        """
        executed: list[dict] = []
        lead_marker: list[str] = []
        adapter = self._adapter(executed)
        asked = await self._ask(adapter, thread_id="handoff-other-cycle-ask")

        final = await self._follow_up(
            adapter,
            asked,
            "what did the design meeting decide?",
            thread_id="handoff-other-cycle",
            marker=lead_marker,
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-2",
                explicit_choice=ExplicitChoice.ORDINARY,
            ),
        )

        assert lead_marker == ["lead_agent"]
        assert executed == []
        assert final["messages"][-1].content == "the workspace has 2 files"

    @pytest.mark.asyncio
    async def test_the_card_names_the_cycle_it_would_start(self):
        executed: list[dict] = []
        asked = await self._ask(self._adapter(executed), thread_id="handoff-names-cycle")

        request = asked["messages"][-1].artifact["human_input"]
        assert "cyc-1" in request["context"]

    @pytest.mark.asyncio
    async def test_start_validates_against_the_cards_own_cycle(self):
        """The validator reads `cycle_id`; the card names it `dbtl_cycle_id`.

        Passing the raw card request through sent an empty cycle id, so every
        Start was refused as "no project-owned cycle" — the button never worked.
        The existing dispatch test missed it because its fake adapter ignores
        its arguments, so this one records what it was asked.
        """
        executed: list[dict] = []
        seen: list[dict] = []

        class Adapter:
            async def validate_stage_handoff(self, **kwargs):
                seen.append(kwargs)
                return None

            async def execute(self, **kwargs):
                executed.append(kwargs)
                return SimpleNamespace(
                    stage="build",
                    cycle_id=kwargs["cycle_id"],
                    note="Build dispatch was admitted.",
                    artifact_uri=None,
                    clarification_question=None,
                    produced_usable_evidence=False,
                )

        adapter = Adapter()
        asked = await self._ask(adapter, thread_id="handoff-start-validate-ask")
        request_id = asked["messages"][-1].artifact["human_input"]["request_id"]
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F"),
            stage_adapter=adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        await graph.ainvoke(
            {**FULL_STATE, "messages": [*asked["messages"], self._reply(request_id, "start_next_stage")]},
            config={"configurable": {"thread_id": "handoff-start-validate"}},
        )

        assert [call["cycle_id"] for call in seen] == ["cyc-1", "cyc-1"]
        assert [call["expected_stage"] for call in seen] == ["build", "build"]
        assert executed and executed[0]["cycle_id"] == "cyc-1"

    @pytest.mark.asyncio
    async def test_a_malformed_card_revision_does_not_break_every_later_turn(self):
        """A card is untrusted shape, and the fence runs it on every message.

        Coercing the revision at rebuild time meant a non-integer value raised
        from a code path every subsequent request is forced through, taking the
        whole conversation down rather than one card.
        """
        executed: list[dict] = []
        lead_marker: list[str] = []
        adapter = self._adapter(executed)
        asked = await self._ask(adapter, thread_id="handoff-bad-revision-ask")
        card = asked["messages"][-1]
        broken = ToolMessage(
            id=card.id,
            name=card.name,
            tool_call_id=card.tool_call_id,
            content=card.content,
            artifact={"human_input": {**card.artifact["human_input"], "cycle_revision": "not-a-number"}},
        )

        final = await self._follow_up(
            adapter,
            {"messages": [*asked["messages"][:-1], broken]},
            "go ahead with build",
            thread_id="handoff-bad-revision",
            marker=lead_marker,
        )

        assert lead_marker == []
        assert executed == []
        assert "no stage worker ran" in final["messages"][-1].content.lower()

    @pytest.mark.asyncio
    async def test_the_lead_agent_is_told_what_it_may_not_do(self):
        """Ordinary work carries the governed state, and no authority with it.

        The takeover started with the lead agent not knowing a cycle existed.
        Telling it is worth doing; telling it in a way that reads as permission
        is not, so the block that reaches the model states the limit explicitly.
        """
        from deerflow.agents.middlewares.project_context_middleware import build_dbtl_status_reminder

        executed: list[dict] = []
        seen: list[dict] = []
        adapter = self._adapter(executed)

        class Recording:
            async def validate_stage_handoff(self, **kwargs):
                return None

            async def active_cycle_status(self, *, project_id):
                seen.append({"project_id": project_id})
                return [
                    {
                        "cycle_id": "cyc-1",
                        "title": "Yield GS pilot",
                        "state": "ready_for_build",
                        "parked": False,
                        "stages": {"design": "approved", "build": "in_progress"},
                    }
                ]

            async def execute(self, **kwargs):
                executed.append(kwargs)
                raise AssertionError("ordinary work must not dispatch a stage")

        asked = await self._ask(adapter, thread_id="handoff-status-ask")
        request_id = asked["messages"][-1].artifact["human_input"]["request_id"]
        held = {**asked, "messages": [*asked["messages"], self._reply(request_id, "hold_here")]}

        captured: dict = {}

        def capturing_lead_agent():
            builder = StateGraph(SCHEMA)

            def node(state, config):
                captured.update(request_context(config))
                return {"messages": [AIMessage(content="ok", id="ai-status")]}

            builder.add_node("lead", node)
            builder.add_edge(START, "lead")
            builder.add_edge("lead", END)
            return builder.compile(checkpointer=False)

        graph = build_supervisor_graph(
            lead_agent=capturing_lead_agent(),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                explicit_choice=ExplicitChoice.ORDINARY,
            ),
            stage_adapter=Recording(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())
        await graph.ainvoke(
            {**FULL_STATE, "messages": [*held["messages"], HumanMessage(content="summarise the workspace files")]},
            config={
                "configurable": {"thread_id": "handoff-status-run"},
                "context": {"run_id": "run-status"},
            },
        )

        assert seen == [{"project_id": "proj-1"}]
        snapshot = captured["dbtl_status_snapshot"]
        assert snapshot["cycles"][0]["cycle_id"] == "cyc-1"
        assert snapshot["pending_control"]["answered_with"] == "hold_here"

        block = build_dbtl_status_reminder(snapshot)
        assert "Yield GS pilot" in block
        assert "deliberately not started" in block
        assert "grants you no authority" in block
        assert "must NOT start, run, advance, approve, reject, or record any stage" in block.replace("\n", " ")


class TestCouncilPreflight:
    """The roster is shown before the council convenes, not after."""

    @staticmethod
    def _adapter(plan, executed):
        class Adapter:
            async def preview_council(self, **kwargs):
                return plan

            async def execute(self, **kwargs):
                executed.append(kwargs)
                return SimpleNamespace(
                    stage="design",
                    cycle_id="cyc-1",
                    note="ran",
                    artifact_uri=None,
                    clarification_question=None,
                    produced_usable_evidence=True,
                )

        return Adapter()

    @staticmethod
    def _plan():
        from deerflow.dbtl.agent_selector import AgentCandidate
        from deerflow.dbtl.capabilities import Capability
        from deerflow.dbtl.council import CouncilDepth, plan_council
        from deerflow.dbtl.stage_spec import resolve_stage_spec

        return plan_council(
            resolve_stage_spec("design", domain_profile="generic"),
            (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
            depth=CouncilDepth.MEDIUM,
            model="gpt-5.6-sol",
        )

    @pytest.mark.asyncio
    async def test_no_worker_is_dispatched_before_the_human_sees_the_roster(self):
        executed: list = []
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=self._adapter(self._plan(), executed),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content="Design the drought cycle.", id="h-1")]},
            config={"configurable": {"thread_id": "preflight-1"}},
        )

        # The council is a real spend of time and tokens; offering a choice
        # after dispatching would not be a choice.
        assert executed == []
        card = final["messages"][-1].artifact["human_input"]
        assert card["clarification_type"] == "council_preflight"
        assert card["request_id"].startswith("dbtl-council__")
        # Ordered least-agent-effort first, so declining the council entirely is
        # the option a decisive owner reaches without reading past it.
        # Depths ordered least-agent-effort first, so declining the council
        # entirely is the option a decisive owner reaches without reading past
        # it. "adjust" sits last because it is the one option that starts
        # nothing — it sends the roster back to be redrawn.
        assert [option["id"] for option in card["options"]] == [
            "human_input",
            "light",
            "medium",
            "heavy",
            "adjust",
        ]
        assert card["input_mode"] == "single_choice"
        assert [option["value"] for option in card["options"]] == [
            "human_input",
            "light",
            "medium",
            "heavy",
            "adjust",
        ]
        assert card["council_plan"]["seats"][-1]["role"] == "chair"
        assert card["recommended_depth"] == "medium"
        assert card["recommended_option_id"] == "medium"
        # The text stands alone for anyone who cannot see the card.
        assert "designer" in card["context"]
        assert "gpt-5.6-sol" in card["context"]

    def test_hidden_automatic_kickoff_remains_the_design_request(self):
        state = {
            "messages": [
                HumanMessage(content="Set up a maize simulation.", id="visible"),
                HumanMessage(
                    content="Start the Design council with the owner's recorded answers.",
                    id="kickoff",
                    additional_kwargs={
                        "hide_from_ui": True,
                        "dbtl_design_kickoff": True,
                    },
                ),
            ]
        }

        assert _latest_cycle_request_text(state) == "Start the Design council with the owner's recorded answers."

    @pytest.mark.asyncio
    async def test_preflight_answer_recovers_its_cycle_when_client_selection_is_missing(self):
        """A council card is server-owned scope, even if the browser loses its selection."""
        executed: list = []
        adapter = self._adapter(self._plan(), executed)
        kickoff = "Start the Design council with the owner's recorded answers."
        initial = {
            **FULL_STATE,
            "messages": [
                HumanMessage(content="start a DBTL cycle", id="visible"),
                HumanMessage(
                    content=kickoff,
                    id="kickoff",
                    additional_kwargs={
                        "hide_from_ui": True,
                        "dbtl_design_kickoff": True,
                    },
                ),
            ],
        }
        selected_graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
                explicit_choice=ExplicitChoice.CONTINUE_CYCLE,
            ),
            stage_adapter=adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())
        asked = await selected_graph.ainvoke(
            initial,
            config={
                "configurable": {"thread_id": "preflight-cycle-recovery-ask"},
                "context": {"run_id": "run-1"},
            },
        )
        request_id = asked["messages"][-1].artifact["human_input"]["request_id"]

        # A new graph is built for the answer run, exactly as production does.
        # Deliberately omit selected_cycle_id to reproduce a lost client-side
        # selection after the server has already bound the card to this cycle.
        answer_graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F"),
            stage_adapter=adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())
        final = await answer_graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    *asked["messages"],
                    TestSetupClarificationIsACard.card_reply(request_id, "light"),
                ],
            },
            config={
                "configurable": {"thread_id": "preflight-cycle-recovery-answer"},
                "context": {"run_id": "run-2", "dbtl_council_depth": "light"},
            },
        )

        assert len(executed) == 1
        assert executed[0]["cycle_id"] == "cyc-1"
        assert executed[0]["request_text"] == kickoff
        # The cycle id is gone from the reply on purpose: it identifies the
        # record for a machine and led every sentence a person read.
        content = final["messages"][-1].content
        assert content == "ran\n\nNothing here decides the stage. The design is recorded and waiting for your review."
        assert "cyc-1" not in content

    @staticmethod
    def _human_input_adapter(executed):
        """An adapter that behaves like the live one at Human Input depth."""

        class Adapter:
            async def preview_council(self, **kwargs):
                return TestCouncilPreflight._plan()

            async def execute(self, **kwargs):
                executed.append(kwargs)
                authored = (kwargs.get("authored_design") or "").strip()
                if not authored:
                    return SimpleNamespace(
                        stage="design",
                        cycle_id="cyc-1",
                        note="no agent was consulted",
                        artifact_uri=None,
                        clarification_question=None,
                        authoring_request="Write the design for this cycle.",
                        produced_usable_evidence=False,
                    )
                return SimpleNamespace(
                    stage="design",
                    cycle_id="cyc-1",
                    note="recorded",
                    artifact_uri="/mnt/user-data/outputs/dbtl/x/design/review.md",
                    clarification_question=None,
                    authoring_request=None,
                    produced_usable_evidence=True,
                )

        return Adapter()

    @pytest.mark.asyncio
    async def test_choosing_write_it_myself_asks_for_the_design(self):
        executed: list = []
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=self._human_input_adapter(executed),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content="Design the drought cycle.", id="h-1")]},
            config={
                "configurable": {"thread_id": "authoring-1"},
                "context": {"run_id": "run-1", "dbtl_council_depth": "human_input"},
            },
        )

        card = final["messages"][-1].artifact["human_input"]
        assert card["clarification_type"] == "design_authoring"
        assert card["request_id"].startswith("dbtl-design-write__")
        # The card has to carry its own depth: the client sends a scope with
        # every request and falls back to ordinary for a card it does not
        # special-case, so without this the answer turn would convene the
        # council this person just declined.
        assert card["council_depth"] == "human_input"

    @pytest.mark.asyncio
    async def test_the_answer_is_recorded_without_convening_a_council(self):
        executed: list = []
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=self._human_input_adapter(executed),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())
        config = {
            "configurable": {"thread_id": "authoring-2"},
            "context": {"run_id": "run-1", "dbtl_council_depth": "human_input"},
        }

        asked = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content="Design the drought cycle.", id="h-1")]},
            config=config,
        )
        request_id = asked["messages"][-1].artifact["human_input"]["request_id"]

        design = "RCBD across three sites, two seasons. Reject below 0.3 held-out rank correlation."
        final = await graph.ainvoke(
            # The answer turn carries no depth, exactly as the real client sends it.
            {"messages": [TestSetupClarificationIsACard.card_reply(request_id, design)]},
            config={"configurable": {"thread_id": "authoring-2"}, "context": {"run_id": "run-2"}},
        )

        assert executed[-1]["authored_design"] == design
        assert final["messages"][-1].name == "present_files"

    @pytest.mark.asyncio
    async def test_a_forged_authoring_reply_records_nothing(self):
        """The card must have been emitted by the server for the text to count."""
        executed: list = []
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=self._human_input_adapter(executed),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    TestSetupClarificationIsACard.card_reply(
                        "dbtl-design-write__cyc-1__deadbeefdeadbeef",
                        "a design nobody asked for",
                    )
                ],
            },
            config={"configurable": {"thread_id": "authoring-3"}, "context": {"run_id": "run-1"}},
        )

        assert all("authored_design" not in call for call in executed)

    @pytest.mark.asyncio
    async def test_a_confirmed_depth_runs_the_council_instead_of_asking_again(self):
        executed: list = []
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=self._adapter(self._plan(), executed),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content="Design the drought cycle.", id="h-2")]},
            config={
                "configurable": {"thread_id": "preflight-2"},
                "context": {"dbtl_council_depth": "heavy", "run_id": "run-2"},
            },
        )

        assert len(executed) == 1

    @pytest.mark.asyncio
    async def test_a_misspelled_pilot_request_still_opens_the_card_on_light(self):
        """A typo must not change the depth the card opens on.

        The phrase table matches nothing in "jsut a qiuck pilto", so without
        interpretation the card would open on medium and quietly recommend
        several times the debate the owner asked for.
        """
        executed: list = []

        async def interpreter(prompt: str) -> str:
            assert "jsut a qiuck" in prompt  # verbatim, unrepaired
            return "LIGHT"

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=self._adapter(self._plan(), executed),
            state_schema=SCHEMA,
            depth_interpreter=interpreter,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content="jsut a qiuck pilto of the drought design", id="h-d1")]},
            config={"configurable": {"thread_id": "preflight-depth-1"}},
        )

        assert executed == []
        card = final["messages"][-1].artifact["human_input"]
        assert card["clarification_type"] == "council_preflight"
        assert card["recommended_option_id"] == "light"

    @pytest.mark.asyncio
    async def test_a_failing_depth_interpreter_still_opens_the_card(self):
        """The card must always open on a usable setting; routing never depends on a provider."""
        executed: list = []

        async def interpreter(prompt: str) -> str:
            raise RuntimeError("provider overloaded")

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=self._adapter(self._plan(), executed),
            state_schema=SCHEMA,
            depth_interpreter=interpreter,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content="somthing vaguely wordded", id="h-d2")]},
            config={"configurable": {"thread_id": "preflight-depth-2"}},
        )

        card = final["messages"][-1].artifact["human_input"]
        assert card["clarification_type"] == "council_preflight"
        assert card["recommended_option_id"] == "medium"

    @pytest.mark.asyncio
    async def test_an_undispatchable_council_does_not_raise_a_card_it_cannot_honour(self):
        from deerflow.dbtl.council import CouncilDepth, plan_council
        from deerflow.dbtl.stage_spec import resolve_stage_spec

        empty = plan_council(
            resolve_stage_spec("design", domain_profile="generic"),
            (),
            depth=CouncilDepth.MEDIUM,
            model="m",
        )
        executed: list = []
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=self._adapter(empty, executed),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content="Design the drought cycle.", id="h-3")]},
            config={"configurable": {"thread_id": "preflight-3"}},
        )

        # It falls through to the adapter, which reports the real problem
        # rather than offering a depth for a council that cannot be staffed.
        assert len(executed) == 1
        assert getattr(final["messages"][-1], "artifact", None) is None


class TestLiveStageBranch:
    @pytest.mark.asyncio
    async def test_explicit_degraded_test_retry_recovers_a_fresh_guidance_card(self):
        executed: list[dict] = []

        class Adapter:
            async def recover_evidence_retry(self, **_kwargs):
                return {
                    "cycle_id": "cyc-1",
                    "cycle_revision": 11,
                    "stage": "test",
                    "dossier_hash": "a" * 64,
                    "reason_codes": ["audit_incomplete"],
                    "available_artifacts": [],
                    "initial_hint": "Publish the result at the bound path.",
                }

            async def execute(self, **kwargs):
                executed.append(kwargs)
                return LiveStageResult(
                    stage="test",
                    cycle_id="cyc-1",
                    note="This must not run before guidance.",
                )

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(id="retry-test", content="Retry Test with the recorded guidance.")],
            },
            config={
                "configurable": {"thread_id": "recover-degraded-test-retry"},
                "context": {"run_id": "run-recover-degraded-test-retry"},
            },
        )

        assert executed == []
        request = final["messages"][-1].artifact["human_input"]
        assert request["clarification_type"] == "dbtl_evidence_retry"
        assert request["dossier_hash"] == "a" * 64
        assert request["cycle_revision"] == 11

    @pytest.mark.asyncio
    async def test_degraded_test_retry_uses_the_exception_bound_retry_port(self):
        request_id = "dbtl-evidence-retry__cyc-1__test__dossier"
        request = {
            "version": 1,
            "kind": "human_input_request",
            "source": "ask_clarification",
            "request_id": request_id,
            "clarification_type": "dbtl_evidence_retry",
            "title": "Guide the Test retry",
            "question": "What should change when Test is retried?",
            "context": "The immutable exception dossier remains bound.",
            "input_mode": "free_text",
            "dbtl_cycle_id": "cyc-1",
            "cycle_revision": 11,
            "stage": "test",
            "dossier_hash": "a" * 64,
        }
        recorded: list[dict] = []
        executed: list[dict] = []

        class Adapter:
            async def validate_evidence_retry(self, **_kwargs):
                return True

            async def test_evidence_retry_snapshot(self, **_kwargs):
                return {
                    "stage_attempt_id": "attempt-test",
                    "evidence_hash": "a" * 64,
                    "evidence_exception": {"content_hash": "a" * 64},
                }

            async def record_test_evidence_retry(self, **kwargs):
                recorded.append(kwargs)

            async def execute(self, **kwargs):
                executed.append(kwargs)
                return LiveStageResult(
                    stage="test",
                    cycle_id="cyc-1",
                    note="The guided Test retry ran.",
                )

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    AIMessage(
                        id=f"{request_id}:call",
                        content="",
                        tool_calls=[
                            {
                                "id": request_id,
                                "name": "ask_clarification",
                                "args": {"question": request["question"]},
                                "type": "tool_call",
                            }
                        ],
                    ),
                    ToolMessage(
                        id=request_id,
                        content=request["context"],
                        name="ask_clarification",
                        tool_call_id=request_id,
                        artifact={"human_input": request},
                    ),
                    HumanMessage(
                        id="retry-guidance",
                        content="Publish the hash-bound test result.",
                        additional_kwargs={
                            "hide_from_ui": True,
                            "human_input_response": {
                                "version": 1,
                                "kind": "human_input_response",
                                "source": "ask_clarification",
                                "request_id": request_id,
                                "response_kind": "text",
                                "value": "Publish the hash-bound test result.",
                            },
                        },
                    ),
                ],
            },
            config={
                "configurable": {"thread_id": "degraded-test-retry"},
                "context": {"run_id": "run-degraded-test-retry"},
            },
        )

        assert len(recorded) == 1
        assert recorded[0]["snapshot"]["evidence_hash"] == "a" * 64
        assert len(executed) == 1
        assert executed[0]["request_text"] == "Retry the governed Test stage with human guidance: Publish the hash-bound test result."
        assert "The guided Test retry ran." in final["messages"][-1].content

    @pytest.mark.asyncio
    async def test_unscoped_review_intent_returns_immediately_without_the_lead_or_workers(
        self,
    ):
        lead_calls = []
        worker_calls = []

        class Adapter:
            async def execute(self, **kwargs):
                worker_calls.append(kwargs)
                raise AssertionError("review intent must not dispatch stage workers")

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent(lead_calls),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id=None,
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="I approve the design", id="human-review")],
            },
            config={"configurable": {"thread_id": "unscoped-review-intent"}},
        )

        assert lead_calls == []
        assert worker_calls == []
        assert "chat text cannot record a DBTL review gate" in final["messages"][-1].content

    @pytest.mark.parametrize(
        "text",
        [
            "Okay, I approve it.",
            "Okay, I approve this. can we go to Test stage",
            "approve it!",
            "I approve revision 3",
            "request changes",
            "reject this",
        ],
    )
    @pytest.mark.asyncio
    async def test_review_intent_does_not_rerun_workers_or_create_an_artifact(
        self,
        text,
    ):
        calls = []

        class Adapter:
            async def execute(self, **kwargs):
                calls.append(kwargs)
                raise AssertionError("review intent must not dispatch stage workers")

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content=text, id="human-review")],
            },
            config={"configurable": {"thread_id": f"review-intent-{text}"}},
        )

        assert calls == []
        answer = final["messages"][-1].content
        assert "chat text cannot record a DBTL review gate" in answer
        assert "No meeting participants ran" in answer
        assert final["artifacts"] == FULL_STATE["artifacts"]

    @pytest.mark.parametrize(
        "text",
        [
            "okay, let's Test",
            "can we go to Test stage?",
            "Retry Build using the approved design",
            "Replan the build",
            "Okay, let's start to build following this new plan",
        ],
    )
    @pytest.mark.asyncio
    async def test_unscoped_stage_control_uses_the_unique_live_cycle_without_the_lead_or_workers(
        self,
        text,
    ):
        lead_calls = []
        worker_calls = []

        class Adapter:
            async def active_cycle_status(self, *, project_id):
                assert project_id == "proj-1"
                return [
                    {
                        "cycle_id": "cyc-1",
                        "title": "Maize simulation",
                        "state": "ready_for_build",
                        "parked": False,
                        "stages": {
                            "design": "approved",
                            "build": "in_progress",
                            "test": "locked",
                        },
                    }
                ]

            async def execute(self, **kwargs):
                worker_calls.append(kwargs)
                raise AssertionError("unscoped stage control must not dispatch workers")

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent(lead_calls),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id=None,
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content=text, id="unscoped-stage-control")],
            },
            config={"configurable": {"thread_id": f"unscoped-stage-control-{text}"}},
        )

        assert lead_calls == []
        assert worker_calls == []
        receipt = final["messages"][-1].content.lower()
        assert "maize simulation" in receipt
        assert "build is in progress" in receipt
        assert "test is locked" in receipt
        assert "no stage worker ran" in receipt

    @pytest.mark.parametrize("selected_cycle_id", [None, "cyc-1"])
    @pytest.mark.asyncio
    async def test_explicit_build_command_after_hold_opens_a_new_governed_control(self, selected_cycle_id):
        lead_calls = []
        recovered = []

        class Adapter:
            async def active_cycle_status(self, *, project_id):
                return [
                    {
                        "cycle_id": "cyc-1",
                        "title": "Maize simulation",
                        "state": "ready_for_build",
                        "parked": False,
                        "stages": {"build": "in_progress"},
                    }
                ]

            async def recover_paused_build_control(self, **kwargs):
                recovered.append(kwargs)
                return {
                    "clarification_type": "dbtl_build_control",
                    "request_id": "dbtl-build__resume-1",
                    "collaboration_id": "dbc-new",
                    "build_control_kind": "step_failure",
                    "dbtl_cycle_id": "cyc-1",
                    "cycle_revision": 4,
                    "stage_attempt_id": "sa-1",
                    "workflow_spec_key": "generic:build-workflow:v1",
                    "step_key": "execute_phases",
                    "plan_digest": "plan-1",
                    "input_digest": "resume-after:dbc-old",
                    "error_code": "paused_build_reopened",
                    "question": "This Build was paused. What should happen next?",
                    "rationale": "The earlier Hold remains recorded.",
                    "recommended_option_id": "replan",
                    "input_mode": "single_choice",
                    "options": [
                        {
                            "id": "replan",
                            "label": "Replan the build",
                            "value": "replan_build",
                            "description": "Draw a new plan.",
                        }
                    ],
                }

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent(lead_calls),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id=selected_cycle_id,
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    HumanMessage(
                        content=(
                            "Replan Build to remove every hard-coded DBTL_INPUT_n ordinal from run_calibration.py, "
                            "validate_calibration.py, test_rerun_spec.json, and RUNBOOK.md. Resolve the three immutable "
                            "inputs by logical filename from the server-declared inputs, then preserve the one-phase Build, "
                            "all eight deliverables, exact 2/1/0 results, byte-identical clean outputs, and appended-row "
                            "tamper rejection."
                        ),
                        id="replan-after-hold",
                    )
                ],
            },
            config={
                "configurable": {"thread_id": f"replan-after-hold-{selected_cycle_id}"},
                "context": {"run_id": "run-replan-after-hold"},
            },
        )

        assert lead_calls == []
        assert recovered, final["messages"][-1]
        assert recovered[0]["cycle_id"] == "cyc-1"
        card = final["messages"][-1]
        assert isinstance(card, ToolMessage)
        assert card.artifact["human_input"]["request_id"] == "dbtl-build__resume-1"

    @pytest.mark.asyncio
    async def test_unscoped_test_retry_reopens_the_server_owned_review_control(self):
        lead_calls = []

        class Adapter:
            async def active_cycle_status(self, *, project_id):
                return [
                    {
                        "cycle_id": "cyc-1",
                        "title": "Maize simulation",
                        "state": "test",
                        "parked": False,
                        "stages": {"test": "awaiting_review"},
                    }
                ]

            async def test_review_snapshot(self, *, project_id, cycle_id):
                return {
                    "cycle_id": cycle_id,
                    "evidence_hash": "e" * 64,
                    "evidence_uri": "/mnt/user-data/outputs/test.md",
                    "evaluation": {
                        "outcome": "supported",
                        "validity_pack_key": "generic-predictive:v2",
                        "allowed_recommendations": [
                            "advance_to_learn",
                            "repeat_test",
                            "return_to_reconciliation",
                        ],
                    },
                    "meeting": {"requirement": "skipped"},
                }

            async def execute(self, **kwargs):
                raise AssertionError("reopening a Test control must not dispatch workers")

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent(lead_calls),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id=None,
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content="Retry Test", id="retry-test")]},
            config={"configurable": {"thread_id": "retry-test-control"}},
        )

        assert lead_calls == []
        card = final["messages"][-1]
        assert isinstance(card, ToolMessage)
        request = card.artifact["human_input"]
        assert request["clarification_type"] == "dbtl_test_outcome"
        assert [item["id"] for item in request["options"]] == [
            "advance_to_learn",
            "repeat_test",
        ]

    @pytest.mark.parametrize(
        "text",
        [
            "Let's discuss the Test results",
            "Let's inspect the Build package",
            "continue chatting about the Design",
        ],
    )
    @pytest.mark.asyncio
    async def test_stage_words_in_ordinary_conversation_do_not_become_stage_controls(self, text):
        lead_calls = []

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent(lead_calls),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id=None,
            ),
            stage_adapter=TestPostApprovalStageHandoff()._adapter([]),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content=text, id="ordinary-stage-words")],
            },
            config={"configurable": {"thread_id": f"ordinary-stage-words-{text}"}},
        )

        assert lead_calls
        assert final["messages"][-1].content == "the workspace has 2 files"

    @pytest.mark.asyncio
    async def test_unscoped_stage_control_does_not_resurrect_a_card_when_multiple_cycles_are_live(self):
        lead_calls = []
        executed = []
        handoffs = TestPostApprovalStageHandoff()
        adapter = handoffs._adapter(executed)
        asked = await handoffs._ask(adapter, thread_id="multi-cycle-stage-control-ask")

        async def active_cycle_status(*, project_id):
            assert project_id == "proj-1"
            return [
                {"cycle_id": "cyc-1", "title": "Cycle one", "parked": False, "stages": {"build": "open"}},
                {"cycle_id": "cyc-2", "title": "Cycle two", "parked": False, "stages": {"test": "open"}},
            ]

        adapter.active_cycle_status = active_cycle_status
        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent(lead_calls),
            context=SupervisorContext(project_id="proj-1", project_name="G2F"),
            stage_adapter=adapter,
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [*asked["messages"], HumanMessage(content="let's Test", id="ambiguous-stage-control")],
            },
            config={"configurable": {"thread_id": "multi-cycle-stage-control"}},
        )

        assert lead_calls == []
        assert executed == []
        assert not isinstance(final["messages"][-1], ToolMessage)
        assert "no single active dbtl cycle is selected" in final["messages"][-1].content.lower()

    @pytest.mark.asyncio
    async def test_continuation_awaits_the_live_adapter_with_project_scope(self):
        calls = []

        class Adapter:
            async def execute(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    note="Recorded one bounded Design worker and its review package.",
                    satisfies_gate=False,
                )

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())
        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="run the design stage", id="human-1")],
            },
            config={
                "configurable": {"thread_id": "live-stage"},
                "context": {"run_id": "run-1"},
            },
        )

        assert calls[0]["project_id"] == "proj-1"
        assert calls[0]["cycle_id"] == "cyc-1"
        assert calls[0]["request_text"] == "run the design stage"
        answer = final["messages"][-1].content
        assert "Recorded one bounded Design worker" in answer
        # Says what is true without naming an internal record the reader
        # cannot see.
        assert "waiting for your review" in answer
        assert "server-owned" not in answer

    @pytest.mark.asyncio
    async def test_design_council_clarification_is_a_human_input_card(self):
        class Adapter:
            async def execute(self, **kwargs):
                return SimpleNamespace(
                    note="The Design council needs one project decision.",
                    clarification_question=("Which environments should be held out for validation?"),
                    satisfies_gate=False,
                )

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="start the design council", id="human-1")],
            },
            config={"configurable": {"thread_id": "design-clarification"}},
        )

        message = final["messages"][-1]
        assert isinstance(message, ToolMessage)
        request = message.artifact["human_input"]
        assert message.name == "ask_clarification"
        assert request["source"] == "ask_clarification"
        assert request["question"] == ("Which environments should be held out for validation?")
        assert request["input_mode"] == "free_text"

    @pytest.mark.asyncio
    async def test_design_package_uses_the_existing_present_files_artifact_flow(self):
        class Adapter:
            async def execute(self, **kwargs):
                return SimpleNamespace(
                    note="The Design package is ready for human review.",
                    artifact_uri="/mnt/user-data/outputs/dbtl/design.json",
                    clarification_question=None,
                    satisfies_gate=False,
                )

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="start the design council", id="human-1")],
            },
            config={
                "configurable": {"thread_id": "design-artifact"},
                "context": {"run_id": "run-artifact"},
            },
        )

        presented = final["messages"][-2]
        assert isinstance(presented, AIMessage)
        assert presented.tool_calls[0]["name"] == "present_files"
        assert presented.tool_calls[0]["args"]["filepaths"] == ["/mnt/user-data/outputs/dbtl/design.json"]
        assert final["artifacts"][-1] == "/mnt/user-data/outputs/dbtl/design.json"

    @pytest.mark.asyncio
    async def test_build_package_names_the_presented_human_gate_and_test_handoff(self):
        class Adapter:
            async def execute(self, **kwargs):
                return SimpleNamespace(
                    stage="build",
                    note="The Build package is ready for human review.",
                    artifact_uri="/mnt/user-data/outputs/dbtl/build-review.md",
                    deck_uri="/mnt/user-data/outputs/dbtl/build-slides.html",
                    clarification_question=None,
                    satisfies_gate=False,
                )

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="continue the build", id="human-1")],
            },
            config={
                "configurable": {"thread_id": "build-artifact"},
                "context": {"run_id": "run-build-artifact"},
            },
        )

        presented = final["messages"][-2]
        assert isinstance(presented, AIMessage)
        assert "second presented file—the Build HTML review deck" in presented.content
        assert "Human gate slide" in presented.content
        assert "Submit the package for review" in presented.content
        assert "Start Test / Hold here" in presented.content
        assert presented.tool_calls[0]["args"]["filepaths"] == [
            "/mnt/user-data/outputs/dbtl/build-review.md",
            "/mnt/user-data/outputs/dbtl/build-slides.html",
        ]

    @pytest.mark.asyncio
    async def test_submitted_test_recovers_its_chat_review_card_without_rerunning(self):
        class Adapter:
            async def execute(self, **kwargs):
                return SimpleNamespace(
                    stage="test",
                    note="The test stage is awaiting human review, so it was not run again.",
                    artifact_uri=None,
                    clarification_question=None,
                    satisfies_gate=False,
                )

            async def test_review_snapshot(self, **kwargs):
                return {
                    "evaluation": {
                        "outcome": "supported",
                        "validity_pack_key": "generic-predictive:v2",
                        "allowed_recommendations": ["advance_to_learn", "repeat_test"],
                    },
                    "evidence_uri": "/mnt/user-data/outputs/dbtl/test-review.md",
                    "evidence_hash": "a" * 64,
                    "meeting": {"requirement": "optional"},
                }

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="review the submitted test", id="human-1")],
            },
            config={
                "configurable": {"thread_id": "test-review-recovery"},
                "context": {"run_id": "run-test-review"},
            },
        )

        card = final["messages"][-1]
        assert isinstance(card, ToolMessage)
        request = card.artifact["human_input"]
        assert request["clarification_type"] == "dbtl_test_review"
        assert [option["id"] for option in request["options"]] == [
            "convene_review_meeting",
            "continue_to_outcome",
        ]

    @pytest.mark.asyncio
    async def test_authenticated_deck_meeting_outranks_an_older_test_review_card(self):
        executed: list[dict] = []

        class Adapter:
            async def test_review_snapshot(self, **kwargs):
                return {
                    "evaluation": {
                        "outcome": "supported",
                        "validity_pack_key": "generic-predictive:v2",
                        "allowed_recommendations": ["advance_to_learn", "repeat_test"],
                    },
                    "evidence_uri": "/mnt/user-data/outputs/dbtl/test-review.md",
                    "evidence_hash": "a" * 64,
                    "meeting": {"requirement": "optional"},
                }

            async def execute(self, **kwargs):
                if kwargs.get("review_meeting_stage") != "test":
                    return LiveStageResult(
                        stage="test",
                        cycle_id="cyc-1",
                        note="The Test stage is awaiting human review, so it was not run again.",
                    )
                executed.append(kwargs)
                return LiveStageResult(
                    stage="test",
                    cycle_id="cyc-1",
                    note="The Test review meeting was recorded.",
                    artifact_uri="/mnt/user-data/outputs/dbtl/test-meeting.json",
                    deck_uri="/mnt/user-data/outputs/dbtl/test-meeting.html",
                )

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())
        thread = {"configurable": {"thread_id": "deck-test-review"}}

        await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="review the submitted test", id="human-review")],
            },
            config={**thread, "context": {"run_id": "run-card"}},
        )
        final = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content="Convene the Test review meeting for the recorded evidence.",
                        id="deck-meeting",
                        additional_kwargs={"hide_from_ui": True},
                    )
                ]
            },
            config={
                **thread,
                "context": {
                    "run_id": "run-meeting",
                    "dbtl_review_meeting_stage": "test",
                },
            },
        )

        assert len(executed) == 1
        assert executed[0]["review_meeting_stage"] == "test"
        assert final["messages"][-1].name == "present_files"

    @pytest.mark.asyncio
    async def test_recorded_test_outcome_recovers_start_learn_without_dispatching(self):
        executed: list[dict] = []

        class Adapter:
            async def recover_test_learn_handoff(self, **kwargs):
                return {
                    "cycle_id": kwargs["cycle_id"],
                    "cycle_revision": 16,
                    "approved_stage": "test",
                    "next_stage": "learn",
                    "surface_id": "validity-1",
                }

            async def execute(self, **kwargs):
                executed.append(kwargs)

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="continue the cycle", id="human-1")],
            },
            config={
                "configurable": {"thread_id": "test-learn-handoff-recovery"},
                "context": {"run_id": "run-test-learn-handoff"},
            },
        )

        assert executed == []
        request = final["messages"][-1].artifact["human_input"]
        assert request["clarification_type"] == "dbtl_stage_handoff"
        assert request["approved_stage"] == "test"
        assert request["next_stage"] == "learn"
        assert [option["id"] for option in request["options"]] == [
            "start_next_stage",
            "hold_here",
        ]

    @pytest.mark.asyncio
    async def test_invalidated_test_handoff_keeps_the_red_flag_in_card_copy(self):
        class Adapter:
            async def recover_test_learn_handoff(self, **kwargs):
                return {
                    "cycle_id": kwargs["cycle_id"],
                    "cycle_revision": 16,
                    "approved_stage": "test",
                    "next_stage": "learn",
                    "surface_id": "validity-invalidated",
                    "advanced_with_exception": True,
                }

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="continue the cycle", id="human-1")],
            },
            config={
                "configurable": {"thread_id": "test-invalidated-learn-handoff"},
                "context": {"run_id": "run-test-invalidated-learn-handoff"},
            },
        )

        request = final["messages"][-1].artifact["human_input"]
        assert request["title"] == "Test advanced with red flag"
        assert request["question"] == "Test advanced with a red flag. What should happen next?"
        assert "exception record" in request["context"]

    @pytest.mark.asyncio
    async def test_recorded_test_outcome_replaces_an_answered_older_handoff(self):
        executed: list[dict] = []
        old_request_id = "dbtl-stage-handoff__cyc-1__old"
        old_request = {
            "version": 1,
            "kind": "human_input_request",
            "source": "ask_clarification",
            "request_id": old_request_id,
            "clarification_type": "dbtl_stage_handoff",
            "title": "Test reviewed",
            "question": "Start Learn?",
            "context": "Learn is ready.",
            "input_mode": "single_choice",
            "options": [
                {"id": "start_next_stage", "label": "Start Learn", "value": "start_next_stage"},
                {"id": "hold_here", "label": "Hold here", "value": "hold_here"},
            ],
            "dbtl_cycle_id": "cyc-1",
            "cycle_revision": 15,
            "approved_stage": "test",
            "next_stage": "learn",
            "design_feedback_surface_id": "validity-old",
        }

        class Adapter:
            async def recover_test_learn_handoff(self, **kwargs):
                return {
                    "cycle_id": kwargs["cycle_id"],
                    "cycle_revision": 16,
                    "approved_stage": "test",
                    "next_stage": "learn",
                    "surface_id": "validity-new",
                }

            async def execute(self, **kwargs):
                executed.append(kwargs)

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())
        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    AIMessage(
                        id=f"{old_request_id}:call",
                        content="",
                        tool_calls=[
                            {
                                "id": old_request_id,
                                "name": "ask_clarification",
                                "args": {"question": "Start Learn?"},
                                "type": "tool_call",
                            }
                        ],
                    ),
                    ToolMessage(
                        id=old_request_id,
                        content="Start Learn?",
                        name="ask_clarification",
                        tool_call_id=old_request_id,
                        artifact={"human_input": old_request},
                    ),
                    HumanMessage(
                        id="hold-old",
                        content="Hold here",
                        additional_kwargs={
                            "hide_from_ui": True,
                            "human_input_response": {
                                "version": 1,
                                "kind": "human_input_response",
                                "source": "ask_clarification",
                                "request_id": old_request_id,
                                "response_kind": "option",
                                "option_id": "hold_here",
                                "value": "hold_here",
                            },
                        },
                    ),
                    HumanMessage(id="continue-after-new-outcome", content="continue the cycle"),
                ],
            },
            config={
                "configurable": {"thread_id": "test-learn-recovery-after-hold"},
                "context": {"run_id": "run-test-learn-recovery-after-hold"},
            },
        )

        assert executed == []
        request = final["messages"][-1].artifact["human_input"]
        assert request["clarification_type"] == "dbtl_stage_handoff"
        assert request["cycle_revision"] == 16
        assert request["design_feedback_surface_id"] == "validity-new"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("later_message", "reopens_handoff"),
        [
            ("continue the cycle", False),
            ("Run the governed Learn stage now", True),
        ],
    )
    async def test_recorded_test_outcome_preserves_a_current_hold_until_an_explicit_stage_request(
        self,
        later_message: str,
        reopens_handoff: bool,
    ):
        executed: list[dict] = []
        request_id = "dbtl-stage-handoff__cyc-1__current"
        request = {
            "version": 1,
            "kind": "human_input_request",
            "source": "ask_clarification",
            "request_id": request_id,
            "clarification_type": "dbtl_stage_handoff",
            "title": "Test reviewed",
            "question": "Start Learn?",
            "context": "Learn is ready.",
            "input_mode": "single_choice",
            "options": [
                {"id": "start_next_stage", "label": "Start Learn", "value": "start_next_stage"},
                {"id": "hold_here", "label": "Hold here", "value": "hold_here"},
            ],
            "dbtl_cycle_id": "cyc-1",
            "cycle_revision": 16,
            "approved_stage": "test",
            "next_stage": "learn",
            "design_feedback_surface_id": "validity-current",
        }

        class Adapter:
            async def recover_test_learn_handoff(self, **kwargs):
                return {
                    "cycle_id": kwargs["cycle_id"],
                    "cycle_revision": 16,
                    "approved_stage": "test",
                    "next_stage": "learn",
                    "surface_id": "validity-current",
                }

            async def execute(self, **kwargs):
                executed.append(kwargs)

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())
        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    AIMessage(
                        id=f"{request_id}:call",
                        content="",
                        tool_calls=[{"id": request_id, "name": "ask_clarification", "args": {"question": "Start Learn?"}, "type": "tool_call"}],
                    ),
                    ToolMessage(
                        id=request_id,
                        content="Start Learn?",
                        name="ask_clarification",
                        tool_call_id=request_id,
                        artifact={"human_input": request},
                    ),
                    HumanMessage(
                        id="hold-current",
                        content="Hold here",
                        additional_kwargs={
                            "hide_from_ui": True,
                            "human_input_response": {
                                "version": 1,
                                "kind": "human_input_response",
                                "source": "ask_clarification",
                                "request_id": request_id,
                                "response_kind": "option",
                                "option_id": "hold_here",
                                "value": "hold_here",
                            },
                        },
                    ),
                    HumanMessage(id="later-message", content=later_message),
                ],
            },
            config={"configurable": {"thread_id": "test-learn-current-hold"}},
        )

        assert executed == []
        if reopens_handoff:
            assert isinstance(final["messages"][-1], ToolMessage)
            reopened = final["messages"][-1].artifact["human_input"]
            assert reopened["next_stage"] == "learn"
            assert reopened["design_feedback_surface_id"] == "validity-current"
            assert reopened["request_id"] != request_id
        else:
            assert not isinstance(final["messages"][-1], ToolMessage)
            assert "held" in final["messages"][-1].content.lower()

    @pytest.mark.asyncio
    async def test_the_slide_deck_is_shown_before_the_question_it_needs_answered(self):
        """A person asked to decide first and read second is asked to guess."""

        class Adapter:
            async def execute(self, **kwargs):
                return SimpleNamespace(
                    note="The meeting needs one project decision.",
                    clarification_question="Toy benchmark or credible simulator?",
                    deck_uri="/mnt/user-data/outputs/dbtl/design-slides-rev1-abc123.html",
                    satisfies_gate=False,
                )

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="start the design council", id="human-1")],
            },
            config={
                "configurable": {"thread_id": "design-deck"},
                "context": {"run_id": "run-deck"},
            },
        )

        presented = final["messages"][-4]
        assert presented.tool_calls[0]["args"]["filepaths"] == ["/mnt/user-data/outputs/dbtl/design-slides-rev1-abc123.html"]
        card = final["messages"][-1]
        assert isinstance(card, ToolMessage)
        assert card.artifact["human_input"]["question"] == "Toy benchmark or credible simulator?"
        assert final["artifacts"][-1] == "/mnt/user-data/outputs/dbtl/design-slides-rev1-abc123.html"

    @pytest.mark.asyncio
    async def test_answering_the_meetings_question_is_passed_as_an_answer(self):
        """The adapter resumes the paused meeting rather than convening a new one.

        The answer and the ordinary request text are the same string, so without
        naming it the adapter cannot tell "the owner replied to the chair" from
        "someone typed something in the cycle".
        """
        calls: list[dict] = []

        class Adapter:
            async def execute(self, **kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return SimpleNamespace(
                        note="One decision is required.",
                        clarification_question="Toy benchmark or credible simulator?",
                        satisfies_gate=False,
                    )
                return SimpleNamespace(
                    note="Synthesis complete.",
                    clarification_question=None,
                    artifact_uri="/mnt/user-data/outputs/dbtl/design.md",
                    satisfies_gate=False,
                )

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(
                project_id="proj-1",
                project_name="G2F",
                selected_cycle_id="cyc-1",
            ),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        config = {
            "configurable": {"thread_id": "design-resume"},
            "context": {"run_id": "run-resume"},
        }
        asked = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [HumanMessage(content="start the design council", id="human-1")],
            },
            config=config,
        )
        request_id = asked["messages"][-1].tool_call_id

        await graph.ainvoke(
            {"messages": [TestSetupClarificationIsACard.card_reply(request_id, "A credible simulator.")]},
            config=config,
        )

        assert len(calls) == 2
        assert "clarification_answer" not in calls[0]
        assert calls[1]["clarification_answer"] == "A credible simulator."


class TestAPausedBuildIsNotTalkedPast:
    """The natural reply to "Start the build with this plan?" is "go ahead".

    It names no stage, matches no intent phrase, and would otherwise reach the
    lead agent — which cannot start a build and, before the status snapshot
    covered this, was not even told one was waiting. The fence and the handler
    share one predicate, because a fence that intercepts a request the handler
    then declines falls straight through to stage execution.
    """

    @pytest.mark.asyncio
    async def test_free_text_is_intercepted_while_a_control_waits(self):
        from deerflow.agents.dbtl.supervisor import _build_control_message
        from deerflow.dbtl.branches import BranchDecision
        from deerflow.dbtl.build_control import plan_confirmation_request
        from deerflow.dbtl.build_plan import BuildPhase, BuildPhasePlan, PlanFeasibility
        from deerflow.dbtl.capabilities import Capability
        from deerflow.dbtl.routing import RouteKind, RouteSource, RoutingDecision

        plan = BuildPhasePlan(
            feasibility=PlanFeasibility.PLANNED,
            phases=(
                BuildPhase(phase_key="simulate", title="Simulate", objective="Draw founders.", capability=Capability.SOFTWARE_ENGINEERING),
                BuildPhase(phase_key="fit", title="Fit", objective="Fit the model.", capability=Capability.STATISTICAL_ANALYSIS),
            ),
        )
        card = (
            plan_confirmation_request(
                plan=plan,
                cycle_id="cyc-1",
                stage_attempt_id="sa-1",
                workflow_spec_key="generic:build-workflow:v1",
                cycle_revision=3,
            )
            .bound_to("dbtl-build__cyc-1__deadbeef")
            .as_card()
        )
        decision = BranchDecision(
            branch=SupervisorBranch.CYCLE_CONTINUATION,
            route=RoutingDecision(kind=RouteKind.CYCLE_CONTINUATION, source=RouteSource.SELECTED_CYCLE, cycle_id="cyc-1"),
            cycle_id="cyc-1",
        )
        emitted = list(_build_control_message(decision, card, request_nonce="run-1"))

        marker: list[str] = []
        graph = compile_supervisor(SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"), marker=marker)
        state = {
            **FULL_STATE,
            "messages": [*emitted, HumanMessage(content="yes, looks good, go ahead", id="human-1")],
        }

        final = await graph.ainvoke(state, config={"configurable": {"thread_id": "build-fence"}})

        assert marker == [], "an unanswered build control was talked past into the lead agent"
        # Re-presented rather than answered: both deliveries carry the same
        # durable request/tool-call id, but distinct message ids keep the later
        # card visible after this follow-up and deliverable by the journal.
        cards = [message for message in final["messages"] if isinstance(message, ToolMessage)]
        assert [message.tool_call_id for message in cards] == ["dbtl-build__cyc-1__deadbeef", "dbtl-build__cyc-1__deadbeef"]
        assert len({message.id for message in cards}) == 2

    @pytest.mark.asyncio
    async def test_restart_button_dispatches_instead_of_being_rejected_as_free_text(self):
        from deerflow.agents.dbtl.supervisor import _build_control_message
        from deerflow.dbtl.branches import BranchDecision
        from deerflow.dbtl.build_control import BuildControlAction, step_failure_request
        from deerflow.dbtl.routing import RouteKind, RouteSource, RoutingDecision

        decision = BranchDecision(
            branch=SupervisorBranch.CYCLE_CONTINUATION,
            route=RoutingDecision(kind=RouteKind.CYCLE_CONTINUATION, source=RouteSource.SELECTED_CYCLE, cycle_id="cyc-1"),
            cycle_id="cyc-1",
        )
        request_id = "dbtl-build__cyc-1__restart"
        card = (
            step_failure_request(
                step_key="summarize_results",
                step_label="Summarize results",
                error_code="summary_contract_rejected",
                error_summary="The result was not reviewable.",
                cycle_id="cyc-1",
                stage_attempt_id="sa-1",
                workflow_spec_key="generic:build-workflow:v1",
                cycle_revision=5,
            )
            .bound_to(request_id)
            .as_card()
        )
        reply = HumanMessage(
            content="Restart the build",
            id="restart-answer",
            additional_kwargs={
                "hide_from_ui": True,
                "human_input_response": {
                    "version": 1,
                    "kind": "human_input_response",
                    "source": "ask_clarification",
                    "request_id": request_id,
                    "response_kind": "option",
                    "option_id": "restart",
                    "value": "Restart the build",
                },
            },
        )
        calls = []

        class Adapter:
            async def active_cycle_status(self, *, project_id):
                return [
                    {
                        "cycle_id": "cyc-1",
                        "title": "Linear pilot",
                        "state": "ready_for_build",
                        "parked": False,
                        "stages": {"build": "in_progress"},
                    }
                ]

            async def execute(self, **kwargs):
                calls.append(kwargs)
                return LiveStageResult(stage="build", cycle_id="cyc-1", note="Build restart accepted.")

        graph = build_supervisor_graph(
            lead_agent=fake_lead_agent([]),
            context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id=None),
            stage_adapter=Adapter(),
            state_schema=SCHEMA,
        ).compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {
                **FULL_STATE,
                "messages": [
                    HumanMessage(content="Can I retry the Build stage?", id="recovery-request"),
                    *_build_control_message(decision, card, request_nonce="run-1"),
                    reply,
                ],
            },
            config={"configurable": {"thread_id": "build-restart-button"}},
        )

        assert len(calls) == 1, final["messages"][-1]
        assert calls[0]["build_control"].action is BuildControlAction.RESTART_BUILD
