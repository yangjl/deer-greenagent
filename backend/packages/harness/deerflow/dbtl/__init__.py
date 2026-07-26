"""Durable DBTL workflow contracts (Phase 3).

This package holds the *pure* workflow rules — the state machine a human drives
today and the later Supervisor Graph will drive tomorrow. It deliberately owns
no storage and no agent code, so both callers can share one definition of what
a legal cycle transition is.

Storage lives in ``deerflow.persistence.dbtl``; the experimental orchestrator
graph lives in ``deerflow.agents.dbtl``.
"""

from deerflow.dbtl.classifier import (
    ClassifierDecision,
    ClassifierResult,
    ConfidenceBand,
    RuleHit,
    classify_request,
)
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
from deerflow.dbtl.proposal import (
    CONFIRMATION_REQUIRED_NOTICE,
    NO_RECORD_NOTICE,
    REQUIRED_GATES,
    ConfirmationSummary,
    ProposalOutcome,
    UpgradeProposal,
    build_proposal,
    confirmation_summary,
)
from deerflow.dbtl.routing import (
    ExplicitChoice,
    RouteKind,
    RouteSource,
    RoutingDecision,
    RoutingRequest,
    is_explicit_start_request,
    route_request,
)

__all__ = [
    "CONFIRMATION_REQUIRED_NOTICE",
    "CYCLE_STATES",
    "NO_RECORD_NOTICE",
    "REQUIRED_GATES",
    "STAGE_ORDER",
    "TERMINAL_CYCLE_STATES",
    "ClassifierDecision",
    "ClassifierResult",
    "ConfidenceBand",
    "ConfirmationSummary",
    "CycleClass",
    "ExplicitChoice",
    "ProposalOutcome",
    "ReviewDecision",
    "RouteKind",
    "RouteSource",
    "RoutingDecision",
    "RoutingRequest",
    "RuleHit",
    "StageStatus",
    "TransitionRefused",
    "UpgradeProposal",
    "apply_review",
    "build_proposal",
    "can_enter_stage",
    "classify_request",
    "confirmation_summary",
    "initial_stage_statuses",
    "is_explicit_start_request",
    "is_terminal",
    "next_cycle_state",
    "route_request",
    "stage_for_state",
    "validate_cycle_class",
]
