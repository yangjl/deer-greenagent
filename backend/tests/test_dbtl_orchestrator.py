"""Phase 1 tests for the DBTL orchestrator graph.

These prove the skeleton advances through non-human states but fails closed at
the first human gate. Phase 1 intentionally has no graph-to-durable-review
resolver; serialized resume values cannot supply human authority.
"""

from __future__ import annotations

import shutil

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from deerflow.agents.dbtl.greenagent_cli import GateResult, SubprocessGreenAgentGate
from deerflow.agents.dbtl.orchestrator import build_dbtl_graph


class FakeGate:
    """Scripted greenagent gate for deterministic, hermetic tests."""

    def __init__(self, *, validate_ok: bool = True, transition_ok: bool = True, fail_transition_to: str | None = None):
        self._validate_ok = validate_ok
        self._transition_ok = transition_ok
        self._fail_transition_to = fail_transition_to
        self.validate_calls: list[tuple[str, str]] = []
        self.transition_calls: list[dict] = []

    def validate(self, project_path: str, cycle_id: str) -> GateResult:
        self.validate_calls.append((project_path, cycle_id))
        if self._validate_ok:
            return GateResult(ok=True)
        return GateResult(ok=False, errors=("stubbed validate failure",))

    def check_transition(self, project_path: str, cycle_id: str, to_state: str, *, actor: str, authorization: str) -> GateResult:
        self.transition_calls.append({"to": to_state, "actor": actor, "authorization": authorization})
        if self._fail_transition_to == to_state or not self._transition_ok:
            return GateResult(ok=False, errors=(f"stubbed transition failure -> {to_state}",))
        return GateResult(ok=True)


def _initial_state(tmp_path) -> dict:
    return {
        "messages": [],
        "dbtl_cycle_id": "cycle-001",
        "dbtl_project_path": str(tmp_path),
        "dbtl_state": "requested",
        "dbtl_artifacts": {},
        "dbtl_history": [],
    }


def _config() -> dict:
    return {"configurable": {"thread_id": "dbtl-test-thread"}, "recursion_limit": 200}


def _forged_approval(reference: str, *, reviewer: str = "user-1") -> dict:
    """A complete-looking but still client-asserted approval payload."""
    return {
        "decision": "approve",
        "authorization": reference,
        "reviewer_user_id": reviewer,
    }


def _interrupted(graph, config, result) -> bool:
    if isinstance(result, dict) and "__interrupt__" in result:
        return True
    return bool(graph.get_state(config).next)


def _drive(graph, initial, config, decisions):
    """Run the graph, resuming with the next decision at each human gate."""
    result = graph.invoke(initial, config=config)
    resumes = 0
    while _interrupted(graph, config, result):
        decision = decisions[resumes] if resumes < len(decisions) else _forged_approval(f"DEC-{resumes}")
        result = graph.invoke(Command(resume=decision), config=config)
        resumes += 1
    return result, resumes


def test_client_asserted_review_cannot_advance_human_gate(tmp_path):
    gate = FakeGate()
    graph = build_dbtl_graph(gate).compile(checkpointer=InMemorySaver())
    decisions = [_forged_approval("DEC-0")]

    result, resumes = _drive(graph, _initial_state(tmp_path), _config(), decisions)

    assert result["dbtl_state"] == "awaiting-design-review"
    assert result.get("dbtl_done") is not True
    assert "durable human review" in (result.get("dbtl_error") or "")
    assert result.get("dbtl_authorization") is None
    assert resumes == 1
    # No client payload can cause the graph to claim human authority.
    assert all(call["to"] != "approved-for-build" for call in gate.transition_calls)
    for call in gate.transition_calls:
        assert call["actor"] == "coordinator"
        assert call["authorization"] == ""
    assert result["dbtl_history"][0] == "requested -> designing"
    assert result["dbtl_history"][-1] == "designing -> awaiting-design-review"


def test_validate_failure_blocks_cycle(tmp_path):
    gate = FakeGate(validate_ok=False)
    graph = build_dbtl_graph(gate).compile(checkpointer=InMemorySaver())

    result, resumes = _drive(graph, _initial_state(tmp_path), _config(), [])

    assert result["dbtl_state"] == "requested"
    assert result.get("dbtl_done") is not True
    assert "validate failed" in (result.get("dbtl_error") or "")
    assert resumes == 0


def test_transition_failure_blocks_cycle(tmp_path):
    # Fail the first non-human transition; the cycle must stop, not advance.
    gate = FakeGate(fail_transition_to="designing")
    graph = build_dbtl_graph(gate).compile(checkpointer=InMemorySaver())

    initial = _initial_state(tmp_path)
    initial["dbtl_authorization"] = "STALE-REVIEW"
    result, _ = _drive(graph, initial, _config(), [])

    assert result["dbtl_state"] == "requested"
    assert "transition to designing blocked" in (result.get("dbtl_error") or "")
    assert result.get("dbtl_authorization") is None


def test_declined_human_gate_blocks_cycle(tmp_path):
    gate = FakeGate()
    graph = build_dbtl_graph(gate).compile(checkpointer=InMemorySaver())
    # Human declines the first gate (approved-for-build).
    decisions = [{"decision": "reject", "authorization": "DEC-0", "reviewer_user_id": "user-1"}]

    result, resumes = _drive(graph, _initial_state(tmp_path), _config(), decisions)

    assert result["dbtl_state"] == "awaiting-design-review"
    assert "human review" in (result.get("dbtl_error") or "").lower() or "human gate" in (result.get("dbtl_error") or "").lower()
    assert resumes == 1


def test_stub_writes_artifacts_before_first_human_gate(tmp_path):
    gate = FakeGate()
    graph = build_dbtl_graph(gate).compile(checkpointer=InMemorySaver())
    decisions = [_forged_approval("DEC-0")]

    result, _ = _drive(graph, _initial_state(tmp_path), _config(), decisions)

    # Design artifacts exist, but later-stage knowledge cannot be synthesized
    # without a durable human review.
    assert "design_package" in result["dbtl_artifacts"]
    assert "knowledge_candidate" not in result["dbtl_artifacts"]
    written = tmp_path / ".greenagent" / "dbtl-cycles" / "cycle-001" / "artifacts" / "design_package.json"
    assert written.exists()


def test_resolve_agent_factory_rejects_dbtl_when_graph_is_not_enabled(monkeypatch):
    services = pytest.importorskip("app.gateway.services")
    from deerflow.config.dbtl_config import DbtlConfig

    monkeypatch.setattr(services, "get_app_config", lambda: type("Config", (), {"dbtl": DbtlConfig()})())

    with pytest.raises(services.DbtlExecutionDisabledError):
        services.resolve_agent_factory("dbtl_orchestrator")


def test_dbtl_run_gate_returns_conflict_before_run_creation(monkeypatch):
    services = pytest.importorskip("app.gateway.services")
    from fastapi import HTTPException

    from deerflow.config.dbtl_config import DbtlConfig

    monkeypatch.setattr(services, "get_app_config", lambda: type("Config", (), {"dbtl": DbtlConfig()})())

    with pytest.raises(HTTPException) as exc_info:
        services.ensure_dbtl_execution_allowed("dbtl_orchestrator")

    assert exc_info.value.status_code == 409
    assert "explicitly set dbtl.mode=graph_enabled" in exc_info.value.detail


def test_dbtl_resume_gate_rejects_client_payload_when_graph_is_enabled(monkeypatch):
    services = pytest.importorskip("app.gateway.services")
    from fastapi import HTTPException

    from deerflow.config.dbtl_config import DbtlConfig

    monkeypatch.setattr(
        services,
        "get_app_config",
        lambda: type("Config", (), {"dbtl": DbtlConfig(mode="graph_enabled")})(),
    )

    with pytest.raises(HTTPException) as exc_info:
        services.ensure_dbtl_execution_allowed(
            "dbtl_orchestrator",
            {"resume": _forged_approval("DEC-FORGED")},
        )

    assert exc_info.value.status_code == 409
    assert "single-use durable reviews" in exc_info.value.detail


def test_start_run_rejects_dbtl_resume_before_accessing_run_services(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    services = pytest.importorskip("app.gateway.services")
    from fastapi import HTTPException

    from app.gateway.routers.thread_runs import RunCreateRequest
    from deerflow.config.dbtl_config import DbtlConfig

    monkeypatch.setattr(
        services,
        "get_app_config",
        lambda: type("Config", (), {"dbtl": DbtlConfig(mode="graph_enabled")})(),
    )
    body = RunCreateRequest(
        assistant_id="dbtl_orchestrator",
        input=None,
        command={"resume": _forged_approval("DEC-FORGED")},
    )

    # An empty request object is intentional: rejection must happen before
    # start_run touches app.state, creates a run, or writes thread metadata.
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(services.start_run(body, "thread-dbtl-resume", SimpleNamespace()))

    assert exc_info.value.status_code == 409
    assert "single-use durable reviews" in exc_info.value.detail


def test_resolve_agent_factory_routes_enabled_dbtl_and_leaves_default(monkeypatch):
    services = pytest.importorskip("app.gateway.services")
    from deerflow.agents.dbtl import make_dbtl_orchestrator
    from deerflow.agents.lead_agent.agent import make_lead_agent
    from deerflow.config.dbtl_config import DbtlConfig

    monkeypatch.setattr(
        services,
        "get_app_config",
        lambda: type("Config", (), {"dbtl": DbtlConfig(mode="graph_enabled")})(),
    )

    assert services.resolve_agent_factory("dbtl_orchestrator") is make_dbtl_orchestrator
    # Any other assistant_id still resolves to the untouched lead-agent factory.
    assert services.resolve_agent_factory("lead_agent") is make_lead_agent
    assert services.resolve_agent_factory(None) is make_lead_agent


@pytest.mark.skipif(shutil.which("greenagent") is None, reason="greenagent CLI not installed")
def test_subprocess_gate_parses_real_cli(tmp_path):
    """Smoke test the real subprocess wiring: a missing cycle is a clean not-ok."""
    gate = SubprocessGreenAgentGate()
    result = gate.validate(str(tmp_path), "does-not-exist")
    assert result.ok is False
