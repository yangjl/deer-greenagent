"""Phase 1 tests for the DBTL orchestrator graph.

These prove the skeleton: a single stubbed DBTL cycle is driven from
``requested`` to ``completed``, gated at every step by an injectable
greenagent gate, interrupting at greenagent's three human-gated transitions.
Execution inside each stage is stubbed (no LLM); real role agents replace the
stub in Phase 2.
"""

from __future__ import annotations

import shutil

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from deerflow.agents.dbtl.greenagent_cli import GateResult, SubprocessGreenAgentGate
from deerflow.agents.dbtl.orchestrator import build_dbtl_graph
from deerflow.agents.dbtl.state import HUMAN_GATED_TARGETS, TERMINAL_STATE


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


def _interrupted(graph, config, result) -> bool:
    if isinstance(result, dict) and "__interrupt__" in result:
        return True
    return bool(graph.get_state(config).next)


def _drive(graph, initial, config, decisions):
    """Run the graph, resuming with the next decision at each human gate."""
    result = graph.invoke(initial, config=config)
    resumes = 0
    while _interrupted(graph, config, result):
        decision = decisions[resumes] if resumes < len(decisions) else {"approved": True, "authorization": f"DEC-{resumes}"}
        result = graph.invoke(Command(resume=decision), config=config)
        resumes += 1
    return result, resumes


def test_happy_path_drives_requested_to_completed(tmp_path):
    gate = FakeGate()
    graph = build_dbtl_graph(gate).compile(checkpointer=InMemorySaver())
    decisions = [{"approved": True, "authorization": f"DEC-{i}"} for i in range(3)]

    result, resumes = _drive(graph, _initial_state(tmp_path), _config(), decisions)

    assert result["dbtl_state"] == TERMINAL_STATE
    assert result.get("dbtl_done") is True
    assert result.get("dbtl_error") is None
    # greenagent's three human-gated transitions each paused the graph.
    assert resumes == 3
    # Every human-gated transition carried actor=human + a documented authorization.
    human_calls = [c for c in gate.transition_calls if c["to"] in HUMAN_GATED_TARGETS]
    assert len(human_calls) == 3
    for call in human_calls:
        assert call["actor"] == "human"
        assert call["authorization"].startswith("DEC-")
    # Non-human transitions used the coordinator actor with no authorization.
    for call in gate.transition_calls:
        if call["to"] not in HUMAN_GATED_TARGETS:
            assert call["actor"] == "coordinator"
            assert call["authorization"] == ""
    # History records every advance.
    assert result["dbtl_history"][0] == "requested -> designing"
    assert result["dbtl_history"][-1] == "awaiting-knowledge-review -> completed"


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

    result, _ = _drive(graph, _initial_state(tmp_path), _config(), [])

    assert result["dbtl_state"] == "requested"
    assert "transition to designing blocked" in (result.get("dbtl_error") or "")


def test_declined_human_gate_blocks_cycle(tmp_path):
    gate = FakeGate()
    graph = build_dbtl_graph(gate).compile(checkpointer=InMemorySaver())
    # Human declines the first gate (approved-for-build).
    decisions = [{"approved": False, "authorization": ""}]

    result, resumes = _drive(graph, _initial_state(tmp_path), _config(), decisions)

    assert result["dbtl_state"] == "awaiting-design-review"
    assert "human review" in (result.get("dbtl_error") or "").lower() or "human gate" in (result.get("dbtl_error") or "").lower()
    assert resumes == 1


def test_stub_writes_artifact_placeholders(tmp_path):
    gate = FakeGate()
    graph = build_dbtl_graph(gate).compile(checkpointer=InMemorySaver())
    decisions = [{"approved": True, "authorization": f"DEC-{i}"} for i in range(3)]

    result, _ = _drive(graph, _initial_state(tmp_path), _config(), decisions)

    # Required artifacts were recorded and their placeholder files written.
    assert "design_package" in result["dbtl_artifacts"]
    assert "knowledge_candidate" in result["dbtl_artifacts"]
    written = tmp_path / ".greenagent" / "dbtl-cycles" / "cycle-001" / "artifacts" / "design_package.json"
    assert written.exists()


def test_resolve_agent_factory_routes_dbtl_and_leaves_default():
    services = pytest.importorskip("app.gateway.services")
    from deerflow.agents.dbtl import make_dbtl_orchestrator
    from deerflow.agents.lead_agent.agent import make_lead_agent

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
