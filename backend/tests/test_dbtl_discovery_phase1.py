"""Explicit-only durable discovery and its read-only execution fence."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import MessagesState
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.agents.dbtl.live_stage.types import LiveStageResult
from deerflow.agents.dbtl.supervisor import build_supervisor_graph
from deerflow.agents.middlewares.dbtl_discovery_policy_middleware import (
    DBTL_DISCOVERY_CONTEXT_KEY,
    DISCOVERY_READ_ONLY_TOOLS,
    DbtlDiscoveryPolicyMiddleware,
)
from deerflow.dbtl.branches import SupervisorContext
from deerflow.dbtl.discovery import DiscoveryStatus, DiscoveryTrigger
from deerflow.dbtl.routing import ExplicitChoice, RouteKind, RouteSource, RoutingRequest, route_request
from deerflow.persistence.base import Base
from deerflow.persistence.dbtl import DbtlCycleRepository, DbtlDiscoveryConflict, DbtlDiscoveryRepository


def _route(**updates) -> RoutingRequest:
    values = {
        "text": "start a DBTL cycle",
        "project_id": "project-1",
        "selected_cycle_id": None,
        "explicit_choice": None,
    }
    values.update(updates)
    return RoutingRequest(**values)


def test_flag_off_preserves_the_existing_setup_route() -> None:
    assert route_request(_route()).kind is RouteKind.CYCLE_SETUP


def test_explicit_start_enters_discovery_only_when_enabled() -> None:
    typed = route_request(_route(discovery_enabled=True))
    clicked = route_request(_route(text="anything", explicit_choice=ExplicitChoice.START_CYCLE, discovery_enabled=True))
    assert typed.kind is RouteKind.DISCOVERY
    assert typed.source is RouteSource.EXPLICIT_REQUEST
    assert clicked.kind is RouteKind.DISCOVERY
    assert clicked.source is RouteSource.EXPLICIT_CHOICE


def test_existing_cycle_outranks_an_active_discovery() -> None:
    decision = route_request(
        _route(
            text="continue the design",
            selected_cycle_id="cycle-1",
            active_discovery_id="discovery-1",
            discovery_enabled=True,
        )
    )
    assert decision.kind is RouteKind.CYCLE_CONTINUATION


def test_active_discovery_recovers_after_the_one_shot_selector_resets() -> None:
    decision = route_request(
        _route(
            text="Use a synthetic population",
            active_discovery_id="discovery-1",
            discovery_enabled=True,
        )
    )
    assert decision.kind is RouteKind.DISCOVERY
    assert decision.source is RouteSource.ACTIVE_DISCOVERY


def _tool_request(name: str, *, active: bool = True):
    return SimpleNamespace(
        tool_call={"name": name, "id": "call-1", "args": {}},
        runtime=SimpleNamespace(context={DBTL_DISCOVERY_CONTEXT_KEY: {"active": active}}),
    )


@pytest.mark.parametrize("name", ["write_file", "str_replace", "bash", "task", "memory_add", "mcp__slack__send"])
def test_discovery_blocks_mutation_execution_delegation_and_connector_writes(name: str) -> None:
    called = False

    def handler(_request):
        nonlocal called
        called = True
        return ToolMessage(content="ran", tool_call_id="call-1")

    result = DbtlDiscoveryPolicyMiddleware().wrap_tool_call(_tool_request(name), handler)
    assert called is False
    assert result.status == "error"
    assert "read-only" in str(result.content)


@pytest.mark.parametrize("name", sorted(DISCOVERY_READ_ONLY_TOOLS))
def test_reviewed_read_only_tools_remain_available(name: str) -> None:
    result = DbtlDiscoveryPolicyMiddleware().wrap_tool_call(
        _tool_request(name),
        lambda _request: ToolMessage(content="read", tool_call_id="call-1"),
    )
    assert result.content == "read"


@pytest.mark.asyncio
async def test_repository_binds_one_active_discovery_and_uses_revision_cas() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        repo = DbtlDiscoveryRepository(async_sessionmaker(engine, expire_on_commit=False))
        first = await repo.begin(
            project_id="project-1",
            thread_id="thread-1",
            user_id="user-1",
            policy_version="v1",
            trigger=DiscoveryTrigger.EXPLICIT,
            latest_user_turn="start a cycle",
        )
        replay = await repo.begin(
            project_id="project-1",
            thread_id="thread-1",
            user_id="user-1",
            policy_version="v1",
            trigger=DiscoveryTrigger.EXPLICIT,
            latest_user_turn="start a cycle again",
        )
        assert replay["id"] == first["id"]
        assert replay["revision"] == 1

        second = await repo.record_turn(
            discovery_id=first["id"],
            expected_revision=1,
            latest_user_turn="Use a synthetic population",
        )
        assert second["revision"] == 2
        assert second["draft"]["turn_count"] == 2
        assert second["draft"]["latest_turn_hash"]
        assert "synthetic population" not in str(second["draft"])
        with pytest.raises(DbtlDiscoveryConflict):
            await repo.record_turn(discovery_id=first["id"], expected_revision=1, latest_user_turn="stale")

        declined = await repo.transition(
            discovery_id=first["id"],
            expected_revision=2,
            target=DiscoveryStatus.DECLINED,
        )
        assert declined["status"] == "declined"
        assert await repo.get_active(project_id="project-1", thread_id="thread-1", user_id="user-1") is None
        latest = await repo.get_latest(project_id="project-1", thread_id="thread-1", user_id="user-1")
        assert latest is not None
        assert latest["status"] == "declined"
        outcomes = await repo.list_project_outcomes("project-1")
        assert [item["id"] for item in outcomes] == [first["id"]]
        assert outcomes[0]["turn_count"] == 2
        assert "draft" not in outcomes[0]
        assert "user_id" not in outcomes[0]
        assert await repo.project_outcome_stats("project-1") == {
            "total": 1,
            "classifier_entries": 0,
            "confirmed": 0,
            "declined": 1,
            "active": 0,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_confirmed_offer_creates_cycle_and_link_atomically() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        repo = DbtlDiscoveryRepository(sf)
        started = await repo.begin(
            project_id="project-1",
            thread_id="thread-1",
            user_id="user-1",
            policy_version="server-v1",
            trigger=DiscoveryTrigger.EXPLICIT,
            latest_user_turn="simulate maize",
            structured_draft={"objective": "Simulate maize", "proposed_title": "Maize simulation"},
        )
        ready = await repo.mark_ready(discovery_id=started["id"], expected_revision=1)
        package = {
            "proposed_title": "Maize simulation",
            "objective": "Simulate maize",
            "success_criteria": "A held-out validation succeeds",
            "provenance": {"objective": {"source": "user_turn", "accepted": True}},
        }
        offered = await repo.offer(discovery_id=started["id"], expected_revision=ready["revision"], package=package)
        assert offered["status"] == "ready"
        assert offered["outbox"]["event_type"] == "start_card"
        assert offered["outbox"]["status"] == "pending"
        acknowledged = await repo.acknowledge_offer(
            discovery_id=started["id"],
            expected_revision=offered["revision"],
            package_hash=offered["package_hash"],
            event_id=offered["outbox"]["id"],
            project_id="project-1",
            thread_id="thread-1",
            user_id="user-1",
        )
        assert acknowledged["status"] == "offered"
        confirmed = await repo.confirm_and_create_cycle(
            discovery_id=started["id"],
            expected_revision=offered["revision"],
            package_hash=offered["package_hash"],
            project_id="project-1",
            thread_id="thread-1",
            user_id="user-1",
            submission_id="card-1",
        )
        cycle = await DbtlCycleRepository(sf).get_cycle(confirmed["cycle_id"], project_id="project-1")
        assert confirmed["status"] == "confirmed"
        assert cycle is not None
        assert cycle["originating_thread_id"] == "thread-1"
        assert cycle["discovery_package_hash"] == offered["package_hash"]
        assert cycle["discovery_package"] == package
        assert [stage["stage"] for stage in cycle["stages"]] == ["design", "reconciliation", "build", "test", "learn"]
        assert {item["event_type"] for item in confirmed["outbox"]} == {"creation_receipt", "design_kickoff"}
        for event in confirmed["outbox"]:
            delivered = await repo.mark_outbox_delivered(
                event_id=event["id"],
                event_type=event["event_type"],
                project_id="project-1",
                thread_id="thread-1",
            )
            assert delivered["status"] == "delivered"

        replay = await repo.confirm_and_create_cycle(
            discovery_id=started["id"],
            expected_revision=offered["revision"],
            package_hash=offered["package_hash"],
            project_id="project-1",
            thread_id="thread-1",
            user_id="user-1",
            submission_id="card-1",
        )
        assert replay["cycle_id"] == confirmed["cycle_id"]
    finally:
        await engine.dispose()


class _Store:
    def __init__(self) -> None:
        self.active = None
        self.begin_values = None

    async def get_active(self, **_scope):
        if self.active and self.active.get("status") in {"gathering", "ready", "offered"}:
            return self.active
        return None

    async def get_latest(self, **_scope):
        return self.active

    async def begin(self, **values):
        self.begin_values = values
        self.active = {
            "id": "discovery-1",
            "revision": 1,
            "status": "gathering",
            "trigger": values["trigger"].value,
            "policy_version": values["policy_version"],
            "draft": {"turn_count": 1},
        }
        return self.active

    async def record_turn(self, **values):
        self.active = {**self.active, "revision": values["expected_revision"] + 1}
        return self.active

    async def transition(self, **values):
        self.active = {**self.active, "revision": values["expected_revision"] + 1, "status": values["target"].value}
        return self.active

    async def mark_ready(self, **values):
        return await self.transition(target=DiscoveryStatus.READY, **values)

    async def offer(self, **values):
        self.active = {
            **self.active,
            "status": "offered",
            "package": values["package"],
            "package_hash": "a" * 64,
            "outbox": {"id": "discovery-offer-1", "event_type": "start_card"},
        }
        return self.active

    async def confirm_and_create_cycle(self, **values):
        self.active = {
            **self.active,
            "status": "confirmed",
            "revision": values["expected_revision"] + 1,
            "cycle_id": "cycle-1",
            "package_hash": "a" * 64,
            "outbox": [
                {
                    "id": "discovery-receipt-1",
                    "event_type": "creation_receipt",
                    "payload": {"cycle_id": "cycle-1", "message": "DBTL cycle cycle-1 was created."},
                }
            ],
        }
        return self.active

    async def acknowledge_offer(self, **_values):
        self.active = {**self.active, "status": "offered"}
        return self.active


class _Lead:
    def __init__(self) -> None:
        self.contexts = []

    async def ainvoke(self, _state, config):
        self.contexts.append(config["context"])
        return {"messages": [AIMessage(content="Which population should this use?", id="answer-1")]}


class _NoopStageAdapter:
    def execute(self, *, cycle_id: str | None, **_values) -> LiveStageResult:
        return LiveStageResult(stage="design", cycle_id=cycle_id, note="Stage execution is unavailable.")


class _FailingPreviewAdapter(_NoopStageAdapter):
    def preview_council(self, **_values):
        raise RuntimeError("preview unavailable")


@pytest.mark.asyncio
async def test_classifier_discovery_waits_for_an_explicit_offer_when_auto_offer_is_off() -> None:
    lead = _Lead()
    store = _Store()
    graph = build_supervisor_graph(
        lead_agent=lead,
        context=SupervisorContext(
            project_id="project-1",
            discovery_enabled=True,
            discovery_classifier_entry=True,
            discovery_auto_offer=False,
        ),
        state_schema=MessagesState,
        stage_adapter=_NoopStageAdapter(),
        discovery_store=store,
    ).compile()

    final = await graph.ainvoke(
        {"messages": [HumanMessage(content="Design and validate a genomic-selection experiment", id="human-classifier")]},
        config={"configurable": {"thread_id": "thread-1"}, "context": {"user_id": "user-1"}},
    )

    assert store.begin_values["trigger"] is DiscoveryTrigger.CLASSIFIER
    assert store.active["status"] == "ready"
    assert not any(isinstance(message, ToolMessage) for message in final["messages"])


@pytest.mark.asyncio
async def test_classifier_discovery_auto_offer_is_separately_gated() -> None:
    store = _Store()
    graph = build_supervisor_graph(
        lead_agent=_Lead(),
        context=SupervisorContext(
            project_id="project-1",
            discovery_enabled=True,
            discovery_classifier_entry=True,
            discovery_auto_offer=True,
        ),
        state_schema=MessagesState,
        stage_adapter=_NoopStageAdapter(),
        discovery_store=store,
    ).compile()

    final = await graph.ainvoke(
        {"messages": [HumanMessage(content="Design and validate a genomic-selection experiment", id="human-auto-offer")]},
        config={"configurable": {"thread_id": "thread-1"}, "context": {"user_id": "user-1"}},
    )

    card = next(message for message in final["messages"] if isinstance(message, ToolMessage))
    assert card.artifact["human_input"]["clarification_type"] == "dbtl_discovery_start"


@pytest.mark.asyncio
async def test_explicit_discovery_invokes_lead_once_with_server_owned_read_only_context() -> None:
    lead = _Lead()
    store = _Store()
    graph = build_supervisor_graph(
        lead_agent=lead,
        context=SupervisorContext(project_id="project-1", discovery_enabled=True, policy_version="server-policy-v3"),
        state_schema=MessagesState,
        stage_adapter=_NoopStageAdapter(),
        discovery_store=store,
    ).compile()
    final = await graph.ainvoke(
        {"messages": [HumanMessage(content="start a DBTL cycle", id="human-1")]},
        config={"configurable": {"thread_id": "thread-1"}, "context": {"user_id": "user-1"}},
    )
    assert len(lead.contexts) == 1
    discovery = lead.contexts[0][DBTL_DISCOVERY_CONTEXT_KEY]
    assert discovery["active"] is True
    assert discovery["no_cycle_exists"] is True
    assert discovery["discovery_id"] == "discovery-1"
    assert store.begin_values["policy_version"] == "server-policy-v3"
    assert "Which population" in str(final["messages"][-1].content)


@pytest.mark.asyncio
async def test_project_history_provider_reaches_lead_and_the_versioned_draft() -> None:
    lead = _Lead()
    store = _Store()
    calls = []

    async def provider(**scope):
        calls.append(scope)
        return {
            "version": 1,
            "manifest": [{"path": "/mnt/user-data/trial.csv", "kind": "file", "size_bytes": 20}],
            "prior_threads": [],
            "conflicts": [],
            "source_refs": [
                {
                    "provenance": "project_artifact",
                    "reference": "/mnt/user-data/trial.csv",
                    "revision": "size:20",
                    "scope": "project-1",
                }
            ],
        }

    graph = build_supervisor_graph(
        lead_agent=lead,
        context=SupervisorContext(project_id="project-1", discovery_enabled=True),
        state_schema=MessagesState,
        stage_adapter=_NoopStageAdapter(),
        discovery_store=store,
        discovery_context_provider=provider,
    ).compile()
    await graph.ainvoke(
        {"messages": [HumanMessage(content="start a DBTL cycle", id="human-context")]},
        config={
            "configurable": {"thread_id": "thread-1"},
            "context": {"user_id": "user-1", "project_root": "/tmp/project-1"},
        },
    )

    assert calls == [
        {
            "project_id": "project-1",
            "project_root": "/tmp/project-1",
            "current_thread_id": "thread-1",
            "user_id": "user-1",
            "query": "start a DBTL cycle",
        }
    ]
    assert lead.contexts[0][DBTL_DISCOVERY_CONTEXT_KEY]["evidence"]["manifest"][0]["path"].endswith("trial.csv")
    assert store.begin_values["structured_draft"]["context_refs"][0]["provenance"] == "project_artifact"


@pytest.mark.asyncio
async def test_ready_discovery_emits_server_card_and_start_answer_creates_cycle() -> None:
    lead = _Lead()
    store = _Store()
    graph = build_supervisor_graph(
        lead_agent=lead,
        context=SupervisorContext(project_id="project-1", discovery_enabled=True, policy_version="server-policy-v3"),
        state_schema=MessagesState,
        stage_adapter=_FailingPreviewAdapter(),
        discovery_store=store,
    ).compile()
    config = {"configurable": {"thread_id": "thread-1"}, "context": {"user_id": "user-1"}}
    first = await graph.ainvoke({"messages": [HumanMessage(content="start a DBTL cycle", id="human-1")]}, config=config)
    second = await graph.ainvoke(
        {"messages": [*first["messages"], HumanMessage(content="Simulate a maize population and validate phenotype structure", id="human-2")]},
        config=config,
    )
    card_message = next(message for message in reversed(second["messages"]) if isinstance(message, ToolMessage) and isinstance(message.artifact, dict) and "human_input" in message.artifact)
    request = card_message.artifact["human_input"]
    assert request["clarification_type"] == "dbtl_discovery_start"
    assert request["discovery_revision"] == store.active["revision"]
    assert "selected_option_id" not in request

    response = HumanMessage(
        content="start_cycle",
        id="human-3",
        additional_kwargs={
            "hide_from_ui": True,
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "source": "ask_clarification",
                "request_id": request["request_id"],
                "response_kind": "option",
                "option_id": "start_cycle",
                "value": "start_cycle",
            },
        },
    )
    third = await graph.ainvoke({"messages": [*second["messages"], response]}, config=config)
    assert store.active["status"] == "confirmed"
    assert store.active["cycle_id"] == "cycle-1"
    assert "was created" in str(third["messages"][-1].content)
