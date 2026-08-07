"""Phase 3: the durable DBTL cycle state machine.

The plan's central rule is that Build readiness requires **two** independent
approvals — Design and Data Reconciliation — so this is tested as a property of
the machine rather than of any one endpoint. Everything here is pure: the same
contract the later Supervisor Graph will call.
"""

from __future__ import annotations

import pytest

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


def _statuses(**overrides: StageStatus) -> dict[str, StageStatus]:
    statuses = dict(initial_stage_statuses())
    statuses.update(overrides)
    return statuses


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


def test_stage_order_matches_the_product_surface() -> None:
    assert STAGE_ORDER == ("design", "reconciliation", "build", "test", "learn")


def test_cycle_states_include_the_explicit_build_readiness_state() -> None:
    """`ready_for_build` is a state, not an inference — it is what gets approved."""
    assert "ready_for_build" in CYCLE_STATES
    assert TERMINAL_CYCLE_STATES == frozenset({"completed", "abandoned"})
    assert is_terminal("completed") and is_terminal("abandoned")
    assert not is_terminal("design")


def test_every_stage_starts_locked_except_design() -> None:
    statuses = initial_stage_statuses()

    assert statuses["design"] is StageStatus.IN_PROGRESS
    assert all(statuses[stage] is StageStatus.LOCKED for stage in STAGE_ORDER[1:])


def test_cycle_classes_are_constrained() -> None:
    assert validate_cycle_class("computational") is CycleClass.COMPUTATIONAL
    assert validate_cycle_class("season/program") is CycleClass.SEASON_PROGRAM
    assert validate_cycle_class("other") is CycleClass.OTHER
    with pytest.raises(ValueError):
        validate_cycle_class("whatever")


def test_state_maps_back_to_the_stage_being_worked() -> None:
    assert stage_for_state("design") == "design"
    assert stage_for_state("reconciliation") == "reconciliation"
    # Build readiness is a checkpoint between reconciliation and build, so it
    # is not itself a stage anyone works in.
    assert stage_for_state("ready_for_build") is None
    assert stage_for_state("completed") is None


# --------------------------------------------------------------------------
# The two-approval gate
# --------------------------------------------------------------------------


def test_reconciliation_opens_only_after_design_is_approved() -> None:
    assert not can_enter_stage("reconciliation", "design", _statuses())
    approved = _statuses(design=StageStatus.APPROVED)
    assert can_enter_stage("reconciliation", "design", approved)


def test_build_requires_both_design_and_reconciliation_approval() -> None:
    """The Phase 3 rule, stated directly."""
    design_only = _statuses(design=StageStatus.APPROVED, reconciliation=StageStatus.IN_PROGRESS)
    both = _statuses(design=StageStatus.APPROVED, reconciliation=StageStatus.APPROVED)

    assert not can_enter_stage("build", "reconciliation", design_only)
    assert can_enter_stage("build", "ready_for_build", both)


def test_reconciliation_approval_alone_does_not_open_build() -> None:
    """Approving out of order must not substitute for the missing approval."""
    reconciliation_only = _statuses(reconciliation=StageStatus.APPROVED)

    assert not can_enter_stage("build", "ready_for_build", reconciliation_only)


def test_changes_requested_on_design_closes_build_again() -> None:
    """A re-opened Design must retract the readiness it previously granted."""
    reopened = _statuses(design=StageStatus.CHANGES_REQUESTED, reconciliation=StageStatus.APPROVED)

    assert not can_enter_stage("build", "ready_for_build", reopened)


# --------------------------------------------------------------------------
# Transitions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "statuses", "expected"),
    [
        ("design", {"design": StageStatus.APPROVED}, "reconciliation"),
        (
            "reconciliation",
            {"design": StageStatus.APPROVED, "reconciliation": StageStatus.APPROVED},
            "ready_for_build",
        ),
        (
            "ready_for_build",
            {"design": StageStatus.APPROVED, "reconciliation": StageStatus.APPROVED},
            "build",
        ),
    ],
)
def test_allowed_forward_transitions(current: str, statuses: dict, expected: str) -> None:
    assert next_cycle_state(current, _statuses(**statuses)) == expected


@pytest.mark.parametrize(
    ("current", "statuses"),
    [
        ("design", {}),
        ("design", {"design": StageStatus.AWAITING_REVIEW}),
        ("reconciliation", {"design": StageStatus.APPROVED}),
        ("completed", {}),
        ("abandoned", {}),
    ],
)
def test_forbidden_transitions_refuse_rather_than_guess(current: str, statuses: dict) -> None:
    with pytest.raises(TransitionRefused):
        next_cycle_state(current, _statuses(**statuses))


def test_an_unknown_state_is_refused_not_treated_as_the_start() -> None:
    with pytest.raises(TransitionRefused):
        next_cycle_state("not-a-state", initial_stage_statuses())


def test_entering_a_stage_out_of_order_is_refused() -> None:
    everything_approved = _statuses(
        design=StageStatus.APPROVED,
        reconciliation=StageStatus.APPROVED,
        build=StageStatus.APPROVED,
    )
    # Test may open after Build, but Learn may not skip Test.
    assert can_enter_stage("test", "build", everything_approved)
    assert not can_enter_stage("learn", "build", everything_approved)


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------


def test_approve_moves_a_stage_from_awaiting_review_to_approved() -> None:
    statuses = _statuses(design=StageStatus.AWAITING_REVIEW)

    updated = apply_review(statuses, "design", ReviewDecision.APPROVE)

    assert updated["design"] is StageStatus.APPROVED
    # Immutability: the caller's dict is not touched.
    assert statuses["design"] is StageStatus.AWAITING_REVIEW


def test_build_exception_opens_only_test_without_becoming_approval() -> None:
    statuses = _statuses(
        design=StageStatus.APPROVED,
        reconciliation=StageStatus.APPROVED,
        build=StageStatus.AWAITING_REVIEW,
    )

    updated = apply_review(statuses, "build", ReviewDecision.ADVANCE_WITH_EXCEPTION)

    assert updated["build"] is StageStatus.ADVANCED_WITH_EXCEPTION
    assert updated["build"] is not StageStatus.APPROVED
    assert updated["test"] is StageStatus.IN_PROGRESS
    assert updated["learn"] is StageStatus.LOCKED
    assert can_enter_stage("test", "test", updated)
    assert not can_enter_stage("learn", "test", updated)


def test_only_build_and_test_accept_an_exception_transition() -> None:
    with pytest.raises(TransitionRefused, match="Build or Test"):
        apply_review(
            _statuses(design=StageStatus.AWAITING_REVIEW),
            "design",
            ReviewDecision.ADVANCE_WITH_EXCEPTION,
        )


def test_request_changes_returns_the_stage_to_work() -> None:
    updated = apply_review(_statuses(design=StageStatus.AWAITING_REVIEW), "design", ReviewDecision.REQUEST_CHANGES)

    assert updated["design"] is StageStatus.CHANGES_REQUESTED


def test_reject_is_distinct_from_request_changes() -> None:
    updated = apply_review(_statuses(design=StageStatus.AWAITING_REVIEW), "design", ReviewDecision.REJECT)

    assert updated["design"] is StageStatus.REJECTED


def test_a_stage_that_is_not_awaiting_review_cannot_be_reviewed() -> None:
    """Otherwise a locked or in-progress stage could be approved out of band."""
    for status in (StageStatus.LOCKED, StageStatus.IN_PROGRESS, StageStatus.APPROVED):
        with pytest.raises(TransitionRefused):
            apply_review(_statuses(design=status), "design", ReviewDecision.APPROVE)


def test_reviewing_an_unknown_stage_is_refused() -> None:
    with pytest.raises(TransitionRefused):
        apply_review(initial_stage_statuses(), "nonsense", ReviewDecision.APPROVE)


def test_approving_a_stage_unlocks_only_the_next_one() -> None:
    approved = apply_review(_statuses(design=StageStatus.AWAITING_REVIEW), "design", ReviewDecision.APPROVE)

    assert approved["reconciliation"] is StageStatus.IN_PROGRESS
    assert approved["build"] is StageStatus.LOCKED
    assert approved["test"] is StageStatus.LOCKED
