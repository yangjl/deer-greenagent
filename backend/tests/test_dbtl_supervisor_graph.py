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

import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deerflow.agents.dbtl.supervisor import (
    _latest_cycle_request_text,
    _make_llm_question_writer,
    build_supervisor_graph,
)
from deerflow.agents.thread_state import get_thread_state_schema
from deerflow.dbtl.branches import SupervisorBranch, SupervisorContext
from deerflow.dbtl.routing import ExplicitChoice
from deerflow.dbtl.setup_questions import SetupQuestion
from deerflow.dbtl.stage_stub import ManualStageAdapter, StageStubResult

MODE = "full"
SCHEMA = get_thread_state_schema(MODE)

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
        stage_adapter=ManualStageAdapter(),
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
        assert "review the Design stage" in answer.content
        assert marker == []

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
            stage_adapter=ManualStageAdapter(),
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


class TestStageStubCannotDoScience:
    """ "Keep stage execution behind a controlled manual/stub adapter."""

    def test_stub_never_claims_a_scientific_result_or_a_gate(self):
        result = ManualStageAdapter().execute(stage="design", cycle_id="cyc-1")
        assert isinstance(result, StageStubResult)
        assert result.writes_scientific_result is False
        assert result.satisfies_gate is False

    def test_those_properties_are_constants_not_fields(self):
        fields = StageStubResult.__dataclass_fields__
        assert "writes_scientific_result" not in fields
        assert "satisfies_gate" not in fields

    def test_stub_module_reaches_no_persistence_and_no_greenagent_gate(self):
        from deerflow.dbtl import stage_stub

        source = inspect.getsource(stage_stub)
        assert "persistence" not in source
        assert "Repository" not in source
        assert "greenagent_cli" not in source
        assert "check_transition" not in source

    @pytest.mark.asyncio
    async def test_continuation_branch_says_it_recorded_nothing(self):
        graph = compile_supervisor(SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"))

        final = await graph.ainvoke(
            {**FULL_STATE, "messages": [HumanMessage(content="run the design stage", id="human-1")]},
            config={"configurable": {"thread_id": "stub-note"}},
        )

        answer = final["messages"][-1].content
        assert "cyc-1" in answer
        # The user must not be able to read this as work having been done.
        assert "nothing has been recorded" in answer.lower()


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
    @pytest.mark.parametrize(
        "text",
        [
            "Okay, I approve it.",
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
        assert "cannot satisfy a review gate" in answer

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
