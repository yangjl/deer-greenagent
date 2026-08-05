"""Project-scoped Design-Build-Test-Learn supervisor graph.

Exposes:
- ``make_project_supervisor``: LangGraph factory for the ``project_supervisor``
  assistant_id (Phase 5), the thin router that delegates ordinary work to the
  real lead agent.
- ``build_supervisor_graph``: the uncompiled supervisor builder, with the lead
  agent and project/cycle context injected so every branch is testable.
"""

from deerflow.agents.dbtl.supervisor import (
    build_supervisor_graph,
    make_project_supervisor,
    supervisor_context_from_config,
)

__all__ = [
    "make_project_supervisor",
    "build_supervisor_graph",
    "supervisor_context_from_config",
]
