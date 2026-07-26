"""DBTL orchestrator: a greenagent-gated Design-Build-Test-Learn run target.

Exposes:
- ``make_dbtl_orchestrator``: LangGraph factory registered under the
  ``dbtl_orchestrator`` assistant_id (Phase 1 stage-machine skeleton).
- ``build_dbtl_graph``: the uncompiled graph builder (gate-injectable, testable).
- ``make_project_supervisor``: LangGraph factory for the ``project_supervisor``
  assistant_id (Phase 5), the thin router that delegates ordinary work to the
  real lead agent.
- ``build_supervisor_graph``: the uncompiled supervisor builder, with the lead
  agent and project/cycle context injected so every branch is testable.
- greenagent gate types for validation/transition gating.
"""

from deerflow.agents.dbtl.greenagent_cli import (
    GateResult,
    GreenAgentGate,
    GreenAgentUnavailableError,
    SubprocessGreenAgentGate,
)
from deerflow.agents.dbtl.orchestrator import build_dbtl_graph, make_dbtl_orchestrator
from deerflow.agents.dbtl.state import DBTLState
from deerflow.agents.dbtl.supervisor import (
    build_supervisor_graph,
    make_project_supervisor,
    supervisor_context_from_config,
)

__all__ = [
    "make_dbtl_orchestrator",
    "build_dbtl_graph",
    "DBTLState",
    "make_project_supervisor",
    "build_supervisor_graph",
    "supervisor_context_from_config",
    "GreenAgentGate",
    "GateResult",
    "SubprocessGreenAgentGate",
    "GreenAgentUnavailableError",
]
