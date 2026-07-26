"""The no-record proposal contract (Phase 4).

The phase's no-go is: *classifier confidence must not bypass confirmation*.
These tests pin that structurally rather than behaviourally — a proposal is a
value object that has no way to express "record created", and building one
touches no repository.
"""

from __future__ import annotations

import inspect

import pytest

from deerflow.dbtl import proposal as proposal_module
from deerflow.dbtl.classifier import ConfidenceBand
from deerflow.dbtl.proposal import (
    CONFIRMATION_REQUIRED_NOTICE,
    NO_RECORD_NOTICE,
    ProposalOutcome,
    UpgradeProposal,
    build_proposal,
    confirmation_summary,
)
from deerflow.dbtl.routing import ExplicitChoice, RoutingRequest, route_request


def _proposal(text: str = "Design and validate a genomic-selection experiment") -> UpgradeProposal | None:
    decision = route_request(RoutingRequest(text=text, project_id="proj-1", selected_cycle_id=None, explicit_choice=None))
    return build_proposal(decision, project_name="G2F")


def test_a_proposal_states_that_no_cycle_exists_yet() -> None:
    """The exact sentence the plan requires on the card."""
    built = _proposal()
    assert built is not None
    assert built.notice == NO_RECORD_NOTICE
    assert NO_RECORD_NOTICE == "No cycle has been created yet."


def test_a_proposal_cannot_express_having_created_a_record() -> None:
    built = _proposal()
    assert built is not None
    assert built.creates_record is False
    with pytest.raises((AttributeError, TypeError)):
        built.creates_record = True  # type: ignore[misc]


def test_the_highest_possible_confidence_still_requires_confirmation() -> None:
    """The no-go, stated directly: confidence never authorizes creation."""
    built = _proposal("Design and validate a genomic-selection experiment across environments, benchmark and compare prediction models, then validate on held-out sites")
    assert built is not None
    assert built.band is ConfidenceBand.HIGH
    assert built.requires_confirmation is True
    assert built.creates_record is False


def test_the_proposal_module_cannot_reach_a_repository() -> None:
    """Structural: no persistence import, so there is nothing to write with."""
    source = inspect.getsource(proposal_module)
    for forbidden in ("persistence", "Repository", "session", "sqlalchemy"):
        assert forbidden not in source, f"the proposal contract must stay pure; found {forbidden!r}"


def test_ordinary_routing_produces_no_proposal_at_all() -> None:
    assert _proposal("Explain this README") is None


def test_an_explicit_ordinary_choice_produces_no_proposal() -> None:
    decision = route_request(
        RoutingRequest(
            text="Design and validate a genomic-selection experiment",
            project_id="proj-1",
            selected_cycle_id=None,
            explicit_choice=ExplicitChoice.ORDINARY,
        )
    )
    assert build_proposal(decision, project_name="G2F") is None


def test_missing_fields_are_carried_into_the_clarification_step() -> None:
    built = _proposal()
    assert built is not None
    assert "target trait" in built.missing_fields


def test_an_explicit_start_collects_the_research_intent_before_confirmation() -> None:
    decision = route_request(
        RoutingRequest(
            text="start a DBTL cycle",
            project_id="proj-1",
            selected_cycle_id=None,
            explicit_choice=None,
        )
    )
    built = build_proposal(decision, project_name="G2F")
    assert built is not None
    assert built.proposed_objective == ""
    assert "research objective" in built.missing_fields
    assert "validation expectation" in built.missing_fields


def test_the_confirmation_summary_names_every_consequence() -> None:
    """The final confirmation must list project, class, objective, gates, effect."""
    built = _proposal()
    assert built is not None
    summary = confirmation_summary(built, cycle_class="computational")
    assert summary.project_name == "G2F"
    assert summary.cycle_class == "computational"
    assert summary.objective == built.proposed_objective
    assert "Design" in summary.required_gates
    assert "Data reconciliation" in summary.required_gates
    assert summary.record_effect
    assert "durable" in summary.record_effect.lower()
    assert summary.notice == CONFIRMATION_REQUIRED_NOTICE


def test_a_continuation_proposal_points_at_the_existing_cycle() -> None:
    decision = route_request(
        RoutingRequest(
            text="add a new model to the comparison",
            project_id="proj-1",
            selected_cycle_id="cycle-9",
            explicit_choice=None,
        )
    )
    built = build_proposal(decision, project_name="G2F")
    assert built is not None
    assert built.cycle_id == "cycle-9"
    assert built.creates_record is False


def test_every_outcome_a_human_can_record_is_enumerated() -> None:
    """Dismissal is a first-class outcome, not the absence of one."""
    assert {outcome.value for outcome in ProposalOutcome} == {
        "start_setup",
        "keep_ordinary",
        "not_sure",
        "dismissed",
        "continue_cycle",
    }
