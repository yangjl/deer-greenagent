"""Durable DBTL workflow contracts (Phase 3).

This package holds the *pure* workflow rules — the state machine a human drives
today and the later Supervisor Graph will drive tomorrow. It deliberately owns
no storage and no agent code, so both callers can share one definition of what
a legal cycle transition is.

Storage lives in ``deerflow.persistence.dbtl``; the experimental orchestrator
graph lives in ``deerflow.agents.dbtl``.
"""

from deerflow.dbtl.cycle_state import (
    CYCLE_STATES,
    STAGE_ORDER,
    TERMINAL_CYCLE_STATES,
    CycleClass,
    ReviewDecision,
    StageStatus,
    TransitionRefused,
    apply_review,
    can_enter_stage,
    initial_stage_statuses,
    is_terminal,
    next_cycle_state,
    stage_for_state,
    validate_cycle_class,
)

__all__ = [
    "CYCLE_STATES",
    "STAGE_ORDER",
    "TERMINAL_CYCLE_STATES",
    "CycleClass",
    "ReviewDecision",
    "StageStatus",
    "TransitionRefused",
    "apply_review",
    "can_enter_stage",
    "initial_stage_statuses",
    "is_terminal",
    "next_cycle_state",
    "stage_for_state",
    "validate_cycle_class",
]
