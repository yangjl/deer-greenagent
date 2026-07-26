"""Phase 5 — the supervisor as a per-run project target.

The reserved target remains available for direct/headless use, but interactive
project threads stay pinned to ``lead_agent``. A runtime-only flag selects the
supervisor for one validated project-scoped run, preserving rollback and the
thread's normal state-access graph.
"""

from __future__ import annotations

import pytest

from deerflow.config.dbtl_config import DbtlConfig


def _stub_config(monkeypatch, services, mode: str):
    monkeypatch.setattr(
        services,
        "get_app_config",
        lambda: type("Config", (), {"dbtl": DbtlConfig(mode=mode)})(),
    )


@pytest.mark.parametrize("mode", ["disabled", "audit_only", "manual"])
def test_supervisor_is_fail_closed_until_graph_execution_is_enabled(monkeypatch, mode):
    services = pytest.importorskip("app.gateway.services")
    _stub_config(monkeypatch, services, mode)

    # `manual` enables DBTL *mutations* through the reviewed endpoints; it must
    # not be enough to start running graphs.
    with pytest.raises(services.DbtlExecutionDisabledError):
        services.resolve_agent_factory("project_supervisor")


def test_supervisor_resolves_when_graph_execution_is_enabled(monkeypatch):
    services = pytest.importorskip("app.gateway.services")
    from deerflow.agents.dbtl import make_project_supervisor

    _stub_config(monkeypatch, services, "graph_enabled")

    assert services.resolve_agent_factory("project_supervisor") is make_project_supervisor


def test_ordinary_assistant_ids_never_reach_the_supervisor(monkeypatch):
    services = pytest.importorskip("app.gateway.services")
    from deerflow.agents.lead_agent.agent import make_lead_agent

    _stub_config(monkeypatch, services, "graph_enabled")

    # Base assistant resolution stays unchanged; the per-run resolver performs
    # the separate runtime-context opt-in tested below.
    assert services.resolve_agent_factory(None) is make_lead_agent
    assert services.resolve_agent_factory("lead_agent") is make_lead_agent
    assert services.resolve_agent_factory("some-custom-agent") is make_lead_agent


def test_project_run_selects_supervisor_without_repinning_assistant(monkeypatch):
    services = pytest.importorskip("app.gateway.services")
    from deerflow.agents.dbtl import make_project_supervisor

    _stub_config(monkeypatch, services, "graph_enabled")

    assert (
        services.resolve_run_agent_factory(
            "lead_agent",
            {
                "context": {
                    "project_id": "project-1",
                    "dbtl_supervisor_enabled": True,
                }
            },
        )
        is make_project_supervisor
    )


@pytest.mark.parametrize(
    "config",
    [
        {"context": {"project_id": "project-1"}},
        {"context": {"dbtl_supervisor_enabled": True}},
        {"context": {"project_id": "project-1", "dbtl_supervisor_enabled": False}},
    ],
)
def test_project_run_requires_both_scope_and_explicit_runtime_opt_in(monkeypatch, config):
    services = pytest.importorskip("app.gateway.services")
    from deerflow.agents.lead_agent.agent import make_lead_agent

    _stub_config(monkeypatch, services, "graph_enabled")

    assert services.resolve_run_agent_factory("lead_agent", config) is make_lead_agent


def test_runtime_opt_in_degrades_to_lead_agent_after_rollback(monkeypatch):
    services = pytest.importorskip("app.gateway.services")
    from deerflow.agents.lead_agent.agent import make_lead_agent

    _stub_config(monkeypatch, services, "audit_only")

    assert (
        services.resolve_run_agent_factory(
            "lead_agent",
            {
                "context": {
                    "project_id": "project-1",
                    "dbtl_supervisor_enabled": True,
                }
            },
        )
        is make_lead_agent
    )


def test_run_gate_returns_conflict_before_creating_anything(monkeypatch):
    services = pytest.importorskip("app.gateway.services")
    from fastapi import HTTPException

    _stub_config(monkeypatch, services, "audit_only")

    with pytest.raises(HTTPException) as exc_info:
        services.ensure_dbtl_execution_allowed("project_supervisor")

    assert exc_info.value.status_code == 409


def test_supervisor_rejects_client_shaped_resume_payloads(monkeypatch):
    """Same reasoning as the orchestrator: a resume value is not a review.

    The supervisor's continuation branch has no authority in Phase 5, but the
    gate must already be in place — otherwise the first phase that gives it
    authority inherits an open door.
    """
    services = pytest.importorskip("app.gateway.services")
    from fastapi import HTTPException

    _stub_config(monkeypatch, services, "graph_enabled")

    with pytest.raises(HTTPException) as exc_info:
        services.ensure_dbtl_execution_allowed("project_supervisor", {"resume": {"decision": "approved"}})

    assert exc_info.value.status_code == 409
