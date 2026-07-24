"""State schema and DBTL topology for the orchestrator graph.

The orchestrator carries a lean, ``messages``-bearing state (runtime/streaming
compatible). Full ``ThreadState`` / ``CheckpointStateAccessor`` integration is a
Phase 4 hardening item and intentionally out of scope for the Phase 1 skeleton.

The state topology mirrors greenagent's ``lib/dbtl.js`` so the two stay in step,
but greenagent remains the *authority*: the orchestrator only proposes the next
state, and ``greenagent dbtl check-transition`` is what actually permits it.
"""

from __future__ import annotations

import operator
from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

# Golden happy path through the DBTL state machine. This only tells the stub
# driver which state to attempt next; illegal moves are still rejected by
# greenagent's own TRANSITIONS table via ``check_transition``.
HAPPY_PATH: dict[str, str] = {
    "requested": "designing",
    "designing": "awaiting-design-review",
    "awaiting-design-review": "approved-for-build",
    "approved-for-build": "build-planning",
    "build-planning": "building",
    "building": "awaiting-builder-handoff",
    "awaiting-builder-handoff": "test-planning",
    "test-planning": "testing",
    "testing": "pass",
    "pass": "awaiting-evidence-review",
    "awaiting-evidence-review": "learning",
    "learning": "awaiting-knowledge-review",
    "awaiting-knowledge-review": "completed",
}

TERMINAL_STATE = "completed"

# Transitions greenagent requires ``actor=human`` plus a documented
# authorization reference for (mirrors ``dbtl.js::transitionNeedsHuman``).
HUMAN_GATED_TARGETS: frozenset[str] = frozenset({"approved-for-build", "learning", "completed"})

# Artifacts each target state requires (mirrors ``dbtl.js::ARTIFACT_REQUIREMENTS``).
# The Phase 1 stub "produces" placeholders for these so the shape is faithful;
# Phase 2 role agents produce schema-valid artifacts instead.
ARTIFACT_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "awaiting-design-review": ("design_package", "designer_debate"),
    "approved-for-build": ("design_package", "designer_debate"),
    "build-planning": ("design_package",),
    "building": ("design_package", "phase_action_plan"),
    "awaiting-builder-handoff": ("design_package", "phase_action_plan", "implementation_drift"),
    "test-planning": ("design_package", "phase_action_plan", "implementation_drift", "builder_handoff"),
    "testing": ("test_plan",),
    "pass": ("test_plan", "test_result"),
    "awaiting-evidence-review": ("test_plan", "test_result"),
    "learning": ("test_result",),
    "awaiting-knowledge-review": ("test_result", "knowledge_candidate"),
    "completed": ("test_result", "knowledge_candidate"),
}


class DBTLState(TypedDict):
    """Lean orchestrator state.

    ``messages`` keeps the graph streaming-compatible with the deer-flow
    runtime; the ``dbtl_*`` channels carry the cycle's control state.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    dbtl_cycle_id: str
    dbtl_project_path: str
    dbtl_state: str
    dbtl_artifacts: dict[str, str]
    dbtl_pending_target: NotRequired[str | None]
    dbtl_authorization: NotRequired[str | None]
    dbtl_history: Annotated[list[str], operator.add]
    dbtl_error: NotRequired[str | None]
    dbtl_done: NotRequired[bool]
