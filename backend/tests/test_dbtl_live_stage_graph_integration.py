"""Supervisor → live stage adapter → durable Phase 6 evidence."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deerflow.agents.dbtl.stage_execution import LiveStageAdapter
from deerflow.agents.dbtl.supervisor import build_supervisor_graph
from deerflow.agents.thread_state import get_thread_state_schema
from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.branches import SupervisorContext
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_runner import DispatchOutcome
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.engine import (
    close_engine,
    get_session_factory,
    init_engine_from_config,
)
from deerflow.persistence.workspaces import WorkspaceRepository

SCHEMA = get_thread_state_schema("full")


@pytest_asyncio.fixture(autouse=True)
async def _close_test_engine():
    yield
    await close_engine()


def _lead():
    builder = StateGraph(SCHEMA)
    builder.add_node(
        "answer",
        lambda state: {"messages": [AIMessage(content="ordinary")]},
    )
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=False)


async def _repo(tmp_path: Path) -> DbtlCycleRepository:
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    sf = get_session_factory()
    assert sf is not None
    workspaces = WorkspaceRepository(sf)
    await workspaces.create_workspace(
        workspace_id="ws-1",
        name="Maize",
        slug="maize",
        description=None,
        created_by="user-1",
    )
    await workspaces.create_project(
        project_id="project-1",
        workspace_id="ws-1",
        name="Drought",
        slug="drought",
        description=None,
        crop_profile="maize",
        created_by="user-1",
    )
    repo = DbtlCycleRepository(sf)
    await repo.create_cycle(
        cycle_id="cycle-1",
        project_id="project-1",
        title="Drought tolerance",
        cycle_class="computational",
        research_question="Which lines retain yield?",
        objective="Rank lines",
        success_criteria="Held-out correlation exceeds 0.4",
        created_by="user-1",
        policy_version="greenagent-dbtl-v2-draft",
        idempotency_key="create-1",
    )
    return repo


async def _dispatcher(units, *, budget):
    text = """{
      "status": "completed",
      "summary": "Prepared the operational Design evidence.",
      "claims": ["The success criterion is measurable."],
      "evidence_refs": [{"kind": "artifact", "reference": "design-package", "description": "stage package"}],
      "limitations": [],
      "quality_checks": [{"name": "threshold stated", "passed": true, "detail": ""}],
      "recommended_next_actions": ["Submit for human review."],
      "provenance": {"inputs_examined": ["cycle metadata"]}
    }"""
    return [DispatchOutcome(unit_id=unit.unit_id, text=text) for unit in units]


@pytest.mark.asyncio
async def test_selected_cycle_runs_workers_and_persists_review_evidence(
    tmp_path: Path,
) -> None:
    repo = await _repo(tmp_path)
    run_config = {
        "configurable": {"thread_id": "thread-1"},
        "context": {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "user_id": "user-1",
            "project_id": "project-1",
            "project_root": str(tmp_path),
        },
    }
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
        ),
        dispatcher=_dispatcher,
        runtime_config=run_config,
    )
    graph = build_supervisor_graph(
        lead_agent=_lead(),
        context=SupervisorContext(
            project_id="project-1",
            project_name="Drought",
            selected_cycle_id="cycle-1",
        ),
        state_schema=SCHEMA,
        stage_adapter=adapter,
    ).compile(checkpointer=InMemorySaver())

    final = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="Run the Design stage.", id="human-1")],
            "artifacts": [],
        },
        config=run_config,
    )

    workers = await repo.list_worker_runs(
        "cycle-1",
        project_id="project-1",
        stage="design",
    )
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert len(workers) == 3, final["messages"][-1].content
    assert {item["capability"] for item in workers} >= {
        "design_red_team",
        "design_council_chair",
    }
    assert workers[0]["status"] == "completed"
    assert cycle is not None
    assert cycle["artifacts"][0]["artifact_type"] == "design_brief"
    assert "recorded their structured results" in final["messages"][-2].content
    assert final["messages"][-2].tool_calls[0]["name"] == "present_files"
    assert final["artifacts"][-1] == cycle["artifacts"][0]["uri"]
    assert "cannot satisfy a review gate" in final["messages"][-2].content
