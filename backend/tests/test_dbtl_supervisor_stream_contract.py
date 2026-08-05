"""Phase 5 — stream contract for the supervisor's delegated ordinary branch.

Wrapping the lead agent in a parent graph changes what the stream looks like,
and #4399 is the precedent for how badly that can go: a subgraph publishing its
``values`` snapshot as a bare root frame replaces the whole thread view in SDK
clients. These tests pin the three facts the frontend depends on.

They deliberately assert observed framework behaviour rather than our own code,
because that behaviour is the contract. If a LangGraph upgrade changes it, the
frontend breaks and one of these fails first.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime

from deerflow.agents.dbtl.live_stage.types import LiveStageResult
from deerflow.agents.dbtl.supervisor import build_supervisor_graph
from deerflow.dbtl.branches import SupervisorContext


class NoopStageAdapter:
    async def execute(self, *, cycle_id: str | None, **_kwargs) -> LiveStageResult:
        return LiveStageResult(stage="design", cycle_id=cycle_id, note="Stage execution is unavailable.")


class StreamState(TypedDict):
    messages: Annotated[list, add_messages]
    artifacts: Annotated[list, add_messages]


class MessagesState(TypedDict):
    messages: Annotated[list, add_messages]


def streaming_lead_agent():
    """A lead-agent stand-in whose node actually streams model tokens."""
    model = GenericFakeChatModel(messages=iter([AIMessage(content="streamed answer")]))

    async def call_model(state):
        return {"messages": [await model.ainvoke(state["messages"])]}

    builder = StateGraph(MessagesState)
    builder.add_node("call_model", call_model)
    builder.add_edge(START, "call_model")
    builder.add_edge("call_model", END)
    return builder.compile(checkpointer=False)


def supervisor(context: SupervisorContext | None = None):
    return build_supervisor_graph(
        lead_agent=streaming_lead_agent(),
        context=context or SupervisorContext(project_id="proj-1", project_name="G2F"),
        stage_adapter=NoopStageAdapter(),
        state_schema=MessagesState,
    ).compile()


class TestSubgraphFramesDoNotImpersonateRoot:
    @pytest.mark.asyncio
    async def test_root_values_frames_carry_no_namespace(self):
        """The regression #4399 describes: a child snapshot posing as the thread."""
        graph = supervisor()
        frames = [
            item
            async for item in graph.astream(
                {"messages": [HumanMessage(content="list the files", id="h1")]},
                stream_mode=["values"],
                subgraphs=True,
            )
        ]

        root = [(ns, chunk) for ns, mode, chunk in frames if not ns]
        nested = [(ns, chunk) for ns, mode, chunk in frames if ns]

        assert root, "the root graph must still publish its own values frames"
        assert nested, "delegation should produce namespaced child frames"
        # Every nested frame is identifiable as nested, so the worker's
        # `_compose_sse_event` can qualify it instead of publishing bare `values`.
        assert all(ns[0].startswith("ordinary:") for ns, _ in nested)

    @pytest.mark.asyncio
    async def test_delegated_token_streaming_still_reaches_a_root_only_consumer(self):
        """The web frontend does not request subgraph streaming.

        If token frames were namespace-gated away, every ordinary answer would
        stop streaming and appear only at the end of the turn.
        """
        graph = supervisor()
        tokens = [
            chunk
            async for mode, chunk in graph.astream(
                {"messages": [HumanMessage(content="list the files", id="h1")]},
                stream_mode=["values", "messages"],
                subgraphs=False,
            )
            if mode == "messages"
        ]

        assert tokens, "delegated model tokens must survive subgraphs=False"
        assert "streamed answer" in "".join(token.content for token, _meta in tokens)

    @pytest.mark.asyncio
    async def test_delegated_tokens_carry_the_branch_namespace_in_metadata(self):
        """Known consequence, pinned so it is a decision rather than a surprise.

        Token frames from the ordinary branch arrive at a root-only consumer
        (correct — they are the thread's answer) but their *metadata* namespace
        is now ``ordinary:<id>`` instead of empty. The run worker uses that value
        only as part of the large-file-tool batching identity key, never as a
        root-vs-subagent gate, so batching still groups correctly. Anything that
        later treats a non-empty metadata namespace as "not the main thread"
        would misclassify every ordinary answer.
        """
        graph = supervisor()
        namespaces = {
            (meta or {}).get("langgraph_checkpoint_ns", "")
            async for mode, chunk in graph.astream(
                {"messages": [HumanMessage(content="list the files", id="h1")]},
                stream_mode=["messages"],
                subgraphs=False,
            )
            for _token, meta in [chunk]
        }

        assert namespaces
        assert all(ns.startswith("ordinary:") for ns in namespaces)


class TestRootValuesGranularity:
    """A known cost of delegation, pinned so it cannot regress silently.

    Today the lead agent runs at the root, so a root-only consumer receives a
    ``values`` snapshot after *every* super-step and the UI updates artifacts,
    todos, and the title progressively. Under the supervisor those per-step
    snapshots become namespaced child frames, so a ``subgraphs=False`` consumer
    sees the state only at the delegated node's boundary.

    Nothing is lost — the final snapshot is complete and token streaming is
    unaffected — but progressive state updates arrive at the end of the ordinary
    branch rather than during it. This cost is confined to graph-enabled
    project runs; the thread remains pinned to the ordinary lead-agent
    assistant for state access and rollback.
    """

    @pytest.mark.asyncio
    async def test_root_sees_no_intermediate_snapshot_from_inside_the_branch(self):
        model = GenericFakeChatModel(messages=iter([AIMessage(content="done")]))

        async def step_one(state):
            return {"messages": [AIMessage(content="intermediate", id="mid-1")]}

        async def step_two(state):
            return {"messages": [await model.ainvoke(state["messages"])]}

        child = StateGraph(MessagesState)
        child.add_node("step_one", step_one)
        child.add_node("step_two", step_two)
        child.add_edge(START, "step_one")
        child.add_edge("step_one", "step_two")
        child.add_edge("step_two", END)

        graph = build_supervisor_graph(
            lead_agent=child.compile(checkpointer=False),
            context=SupervisorContext(project_id="proj-1", project_name="G2F"),
            stage_adapter=NoopStageAdapter(),
            state_schema=MessagesState,
        ).compile()

        root_snapshots = [
            chunk
            async for mode, chunk in graph.astream(
                {"messages": [HumanMessage(content="go", id="h1")]},
                stream_mode=["values"],
                subgraphs=False,
            )
        ]

        # Two root frames: the input state, then the state after the whole
        # branch — not one per child super-step.
        assert len(root_snapshots) == 2
        # The complete result is still delivered; only the granularity changed.
        assert [m.id for m in root_snapshots[-1]["messages"]][:2] == ["h1", "mid-1"]


class TestTerminalBranchesStreamAtRoot:
    @pytest.mark.asyncio
    async def test_non_ordinary_branches_emit_root_frames_only(self):
        """The DBTL branches are supervisor nodes, not subgraphs.

        Their answers must therefore reach the thread view directly, with no
        namespace for a client to filter them out by.
        """
        graph = supervisor(SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"))
        frames = [
            (ns, chunk)
            async for ns, mode, chunk in graph.astream(
                {"messages": [HumanMessage(content="run the design stage", id="h1")]},
                stream_mode=["values"],
                subgraphs=True,
            )
        ]

        assert frames
        assert all(not ns for ns, _ in frames), "a terminal branch must not be namespaced"
        final = frames[-1][1]["messages"][-1]
        # The branch reply no longer names the cycle id; what matters to this
        # contract is that the terminal frame carries the branch's own text.
        assert "Stage execution is unavailable" in final.content


class TestSupervisorActivityUsesTheRealNodeConfigShape:
    @pytest.mark.asyncio
    async def test_the_router_emits_activity_after_langgraph_relocates_context(self):
        graph = supervisor()
        runtime = Runtime(context={"run_id": "run-real-node"})
        config = {
            "context": {"run_id": "run-real-node"},
            "configurable": {"__pregel_runtime": runtime},
        }

        frames = [
            chunk
            async for mode, chunk in graph.astream(
                {"messages": [HumanMessage(content="list the files", id="h1")]},
                config=config,
                stream_mode=["custom"],
            )
            if mode == "custom" and chunk.get("type") == "agent_activity"
        ]

        assert [frame["transition"] for frame in frames] == [
            "started",
            "updated",
            "completed",
        ]
        assert {frame["display_name"] for frame in frames} == {"Cycle supervisor"}
        assert {frame["run_id"] for frame in frames} == {"run-real-node"}
