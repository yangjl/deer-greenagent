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

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deerflow.agents.dbtl.supervisor import build_supervisor_graph
from deerflow.agents.thread_state import get_thread_state_schema
from deerflow.dbtl.branches import SupervisorBranch, SupervisorContext
from deerflow.dbtl.routing import ExplicitChoice
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


def compile_supervisor(context: SupervisorContext, marker: list[str] | None = None):
    graph = build_supervisor_graph(
        lead_agent=fake_lead_agent(marker if marker is not None else []),
        context=context,
        stage_adapter=ManualStageAdapter(),
        state_schema=SCHEMA,
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
        # Every branch answers exactly once.
        assert len(final["messages"]) == 2

    @pytest.mark.asyncio
    async def test_ordinary_branch_delegates_to_the_real_lead_agent(self):
        marker: list[str] = []
        graph = compile_supervisor(SupervisorContext(project_id="proj-1", project_name="G2F"), marker)

        final = await graph.ainvoke({**FULL_STATE}, config={"configurable": {"thread_id": "delegation"}})

        assert marker == ["lead_agent"], "ordinary work must run the lead agent, not the supervisor"
        assert final["messages"][-1].content == "the workspace has 2 files"

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


class TestLiveStageBranch:
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
