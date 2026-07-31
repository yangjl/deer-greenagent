"""Data Reconciliation as an optional gate, not a deleted stage.

A deployment may set ``dbtl.reconciliation_required = false``, after which an
approved Design opens Build directly. The stage is *skipped*, never removed: its
rows, endpoints, and matrix stay available for projects that use them.

Two properties matter more than the switch itself. The default is the strict
behaviour, so a caller that has not been taught about the flag keeps the gate.
And a cycle already mid-flight must stay advanceable when an operator flips the
switch under it — in either direction.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.cycle_state import (
    ReviewDecision,
    StageStatus,
    TransitionRefused,
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


class TestTheDefaultIsStillTheStrictGate:
    def test_design_approval_opens_reconciliation_not_build(self):
        updated = apply_review(_statuses(design=StageStatus.AWAITING_REVIEW), "design", ReviewDecision.APPROVE)

        assert updated["reconciliation"] is StageStatus.IN_PROGRESS
        assert updated["build"] is StageStatus.LOCKED

    def test_build_cannot_be_entered_on_design_approval_alone(self):
        statuses = _statuses(design=StageStatus.APPROVED, reconciliation=StageStatus.IN_PROGRESS)

        assert can_enter_stage("build", "reconciliation", statuses) is False

    def test_a_route_menu_still_blocks_the_build_edge_by_default(self):
        routes = compute_stage_routes(RouteContext(stage="design", outcome="approved"))

        advance = next(route for route in routes if route.slug == "advance")
        assert advance.blocked is True


class TestSkippingReconciliation:
    def test_design_approval_opens_build_directly(self):
        updated = apply_review(
            _statuses(design=StageStatus.AWAITING_REVIEW),
            "design",
            ReviewDecision.APPROVE,
            reconciliation_required=False,
        )

        assert updated["build"] is StageStatus.IN_PROGRESS
        # Stepped over, not opened: a stage nothing waits for would show as
        # permanent outstanding work on every cycle.
        assert updated["reconciliation"] is StageStatus.LOCKED

    def test_build_may_be_entered_once_design_is_approved(self):
        statuses = _statuses(design=StageStatus.APPROVED, build=StageStatus.IN_PROGRESS)

        assert can_enter_stage("build", "ready_for_build", statuses, reconciliation_required=False) is True

    def test_the_cycle_advances_from_design_to_ready_for_build(self):
        statuses = _statuses(design=StageStatus.APPROVED)

        assert next_cycle_state("design", statuses, reconciliation_required=False) == "ready_for_build"

    def test_the_build_edge_is_no_longer_blocked(self):
        routes = compute_stage_routes(
            RouteContext(stage="design", outcome="approved", reconciliation_required=False),
        )

        advance = next(route for route in routes if route.slug == "advance")
        assert advance.blocked is False
        assert advance.blocked_reason == ""
        assert advance.to_stage == "build"

    def test_a_repeat_build_edge_after_test_is_open_too(self):
        routes = compute_stage_routes(
            RouteContext(stage="test", outcome="inconclusive", reconciliation_required=False),
        )

        repeat = next(route for route in routes if route.slug == "return_to_build")
        assert repeat.blocked is False


class TestACycleMidFlightStaysAdvanceable:
    def test_a_cycle_already_in_reconciliation_can_still_move_forward(self):
        """An operator flipping the switch must not strand a cycle that was
        already working the matrix."""
        statuses = _statuses(design=StageStatus.APPROVED, reconciliation=StageStatus.IN_PROGRESS)

        assert next_cycle_state("reconciliation", statuses, reconciliation_required=False) == "ready_for_build"

    def test_a_cycle_that_settled_the_matrix_is_unaffected_by_turning_the_gate_back_on(self):
        statuses = _statuses(design=StageStatus.APPROVED, reconciliation=StageStatus.APPROVED)

        assert next_cycle_state("reconciliation", statuses) == "ready_for_build"
        assert can_enter_stage("build", "ready_for_build", statuses) is True

    def test_turning_the_gate_back_on_re_blocks_a_cycle_that_skipped_it(self):
        """Not a regression: the whole point of the flag being reversible is
        that the stricter rule reasserts itself."""
        statuses = _statuses(design=StageStatus.APPROVED, build=StageStatus.IN_PROGRESS)

        assert can_enter_stage("build", "ready_for_build", statuses) is False
        with pytest.raises(TransitionRefused):
            next_cycle_state("ready_for_build", statuses)


class TestReconciliationRemainsAvailable:
    def test_the_stage_is_skipped_not_deleted(self):
        """Its row still exists and a project may still work it deliberately."""
        statuses = _statuses(design=StageStatus.APPROVED)

        assert "reconciliation" in statuses
        assert can_enter_stage("reconciliation", "design", statuses, reconciliation_required=False) is True
