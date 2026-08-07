"""Data Reconciliation never gates the Design → Build path.

The reconciliation records remain available as evidence, but there is no
deployment switch and no alternate state machine.
"""

from deerflow.dbtl.cycle_state import (
    ReviewDecision,
    StageStatus,
    apply_review,
    can_enter_stage,
    initial_stage_statuses,
    next_cycle_state,
)
from deerflow.dbtl.stage_routes import RouteContext, compute_stage_routes


def _statuses(**overrides: StageStatus) -> dict[str, StageStatus]:
    statuses = initial_stage_statuses()
    statuses.update(overrides)
    return statuses


def test_design_approval_opens_build_directly() -> None:
    updated = apply_review(
        _statuses(design=StageStatus.AWAITING_REVIEW),
        "design",
        ReviewDecision.APPROVE,
    )

    assert updated["build"] is StageStatus.IN_PROGRESS
    assert updated["reconciliation"] is StageStatus.LOCKED


def test_build_may_be_entered_once_design_is_approved() -> None:
    statuses = _statuses(design=StageStatus.APPROVED, build=StageStatus.IN_PROGRESS)

    assert can_enter_stage("build", "ready_for_build", statuses) is True
    assert next_cycle_state("design", statuses) == "ready_for_build"


def test_design_and_repeat_build_routes_are_never_blocked_by_reconciliation() -> None:
    design_routes = compute_stage_routes(RouteContext(stage="design", outcome="approved"))
    repeat_routes = compute_stage_routes(RouteContext(stage="test", outcome="inconclusive"))

    design_advance = next(route for route in design_routes if route.slug == "advance")
    repeat_build = next(route for route in repeat_routes if route.slug == "return_to_build")
    assert design_advance.to_stage == "build"
    assert repeat_build.to_stage == "build"


def test_an_existing_reconciliation_state_can_still_advance() -> None:
    statuses = _statuses(
        design=StageStatus.APPROVED,
        reconciliation=StageStatus.IN_PROGRESS,
    )

    assert next_cycle_state("reconciliation", statuses) == "ready_for_build"
