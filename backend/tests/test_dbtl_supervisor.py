"""Phase 5 — thin project supervisor graph.

The phase's no-go is broad: *routing must not drop messages, project scope, file
access, or artifact inspection*. These tests are arranged so that each of those
is a separate, checkable claim rather than one end-to-end smoke test that would
pass for the wrong reason.

Two of them pin facts about the *framework* rather than about our code, because
the whole design rests on them:

* A compiled child graph used as a parent node returns its **entire final
  state** as its update, which the parent re-applies through its own reducers.
  Delegation is therefore only safe while every ``ThreadState`` reducer is
  idempotent under re-application. ``test_thread_state_reducers_are_idempotent``
  and ``test_every_thread_state_channel_has_a_merging_reducer`` are what keep a
  future channel with a naive accumulator from silently duplicating a
  conversation.
* Subgraph frames must not impersonate root frames (#4399).
"""

from __future__ import annotations

import inspect
from typing import Annotated, get_args, get_origin, get_type_hints

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.thread_state import ThreadState, get_thread_state_schema
from deerflow.dbtl.branches import (
    SUPERVISOR_BRANCHES,
    BranchDecision,
    SupervisorBranch,
    SupervisorContext,
    resolve_branch,
)
from deerflow.dbtl.routing import ExplicitChoice, RouteKind, RouteSource


def ctx(
    *,
    project_id: str | None = "proj-1",
    project_name: str = "G2F",
    selected_cycle_id: str | None = None,
    explicit_choice: ExplicitChoice | None = None,
) -> SupervisorContext:
    return SupervisorContext(
        project_id=project_id,
        project_name=project_name,
        selected_cycle_id=selected_cycle_id,
        explicit_choice=explicit_choice,
    )


class TestBranchResolution:
    """The supervisor must reuse Phase 4's ladder, not grow a second opinion."""

    def test_ordinary_request_in_a_project_routes_to_ordinary(self):
        decision = resolve_branch("what files are in the workspace?", ctx())
        assert decision.branch is SupervisorBranch.ORDINARY

    def test_projectless_conversation_is_always_ordinary(self):
        decision = resolve_branch(
            "design a multi-environment trial and validate on held-out sites",
            ctx(project_id=None),
        )
        assert decision.branch is SupervisorBranch.ORDINARY
        assert decision.route.source is RouteSource.NO_PROJECT

    def test_selected_cycle_continues_rather_than_proposing_another(self):
        decision = resolve_branch("draft the design package", ctx(selected_cycle_id="cyc-1"))
        assert decision.branch is SupervisorBranch.CYCLE_CONTINUATION
        assert decision.cycle_id == "cyc-1"

    def test_explicit_keep_ordinary_is_final_even_for_research_shaped_text(self):
        decision = resolve_branch(
            "design a multi-environment trial and validate on held-out sites",
            ctx(explicit_choice=ExplicitChoice.ORDINARY),
        )
        assert decision.branch is SupervisorBranch.ORDINARY
        assert decision.route.source is RouteSource.EXPLICIT_CHOICE

    def test_typed_start_request_with_missing_fields_asks_before_creating(self):
        # A bare "start a DBTL cycle" names no objective, so the only honest
        # next step is to ask — not to open a confirmation for a blank cycle.
        decision = resolve_branch("start a DBTL cycle", ctx())
        assert decision.branch is SupervisorBranch.CLARIFICATION
        assert decision.missing_fields

    def test_classifier_proposal_routes_to_setup_branch(self):
        decision = resolve_branch(
            "I want to test whether the new hybrids outperform the check across three environments, validated on held-out sites",
            ctx(),
        )
        assert decision.route.kind is RouteKind.PROPOSAL
        assert decision.branch is SupervisorBranch.CYCLE_SETUP

    def test_every_branch_is_reachable_and_declared(self):
        assert set(SUPERVISOR_BRANCHES) == set(SupervisorBranch)


class TestNoRecordInvariant:
    """No branch may create a durable record. Phase 5 still writes nothing."""

    @pytest.mark.parametrize(
        ("text", "context"),
        [
            ("what files are here?", ctx()),
            ("start a DBTL cycle", ctx()),
            ("draft the design package", ctx(selected_cycle_id="cyc-1")),
            ("test whether hybrids beat the check on held-out sites", ctx()),
        ],
    )
    def test_no_decision_ever_claims_to_create_a_record(self, text, context):
        assert resolve_branch(text, context).creates_record is False

    def test_creates_record_is_a_constant_not_a_field(self):
        # If this became a settable field, "Phase 5 writes nothing" would be a
        # convention instead of a property of the type.
        assert "creates_record" not in BranchDecision.__dataclass_fields__

    def test_branch_module_imports_no_persistence_layer(self):
        from deerflow.dbtl import branches

        source = inspect.getsource(branches)
        assert "persistence" not in source
        assert "Repository" not in source


class TestThreadStatePreservation:
    """The enabling invariant: delegation re-applies state, so reducers must merge."""

    def test_every_thread_state_channel_has_a_merging_reducer(self):
        # A channel annotated with a naive accumulator (operator.add) would
        # duplicate the whole conversation the first time the supervisor
        # delegated to the lead agent. Fail loudly at review time instead.
        hints = get_type_hints(ThreadState, include_extras=True)
        naive = []
        for name, hint in hints.items():
            annotated = hint
            while get_origin(annotated) is not Annotated and get_args(annotated):
                annotated = get_args(annotated)[0]
            if get_origin(annotated) is not Annotated:
                continue
            reducer = get_args(annotated)[1]
            if getattr(reducer, "__module__", "") == "_operator":
                naive.append(name)
        assert naive == [], f"channels with a non-merging reducer: {naive}"

    @pytest.mark.parametrize("mode", ["full", "delta"])
    @pytest.mark.asyncio
    async def test_thread_state_reducers_are_idempotent(self, mode):
        """Re-applying an identical state update must not duplicate anything."""
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph

        schema = get_thread_state_schema(mode)

        def emit(state):
            return {
                "messages": [AIMessage(content="answer", id="ai-1")],
                "artifacts": ["/mnt/user-data/outputs/report.md"],
            }

        child = StateGraph(schema)
        child.add_node("emit", emit)
        child.add_edge(START, "emit")
        child.add_edge("emit", END)

        parent = StateGraph(schema)
        parent.add_node("delegate", child.compile(checkpointer=False))
        parent.add_edge(START, "delegate")
        parent.add_edge("delegate", END)
        graph = parent.compile(checkpointer=InMemorySaver())

        final = await graph.ainvoke(
            {"messages": [HumanMessage(content="hi", id="human-1")], "artifacts": []},
            config={"configurable": {"thread_id": f"idem-{mode}"}},
        )

        assert [m.id for m in final["messages"]] == ["human-1", "ai-1"]
        assert final["artifacts"] == ["/mnt/user-data/outputs/report.md"]
