"""Phase 0 contracts for conversational DBTL discovery."""

from __future__ import annotations

import inspect

import pytest

from deerflow.dbtl import discovery as discovery_module
from deerflow.dbtl.discovery import (
    DISCOVERY_CARD_TYPE,
    DISCOVERY_NO_RECORD_NOTICE,
    DiscoveryAction,
    DiscoveryDraft,
    DiscoveryProvenance,
    DiscoveryStatus,
    DiscoveryTrigger,
    DiscoveryValue,
    assess_discovery_readiness,
    can_transition_discovery,
    discovery_card_request,
)


def _draft(**overrides: object) -> DiscoveryDraft:
    values = {
        "discovery_id": "discovery-1",
        "project_id": "project-1",
        "thread_id": "thread-1",
        "user_id": "user-1",
        "trigger": DiscoveryTrigger.EXPLICIT,
        "policy_version": "dbtl-v1",
        "revision": 3,
    }
    values.update(overrides)
    return DiscoveryDraft(**values)  # type: ignore[arg-type]


def _value(value: str, provenance: DiscoveryProvenance = DiscoveryProvenance.USER_TURN) -> DiscoveryValue:
    return DiscoveryValue(value=value, provenance=provenance)


def test_discovery_cannot_claim_to_be_a_cycle_or_governance_record() -> None:
    draft = _draft()
    assert draft.creates_record is False
    assert not hasattr(draft, "cycle_id")
    assert not hasattr(draft, "stage")
    assert not hasattr(draft, "gate_status")
    with pytest.raises((AttributeError, TypeError)):
        draft.creates_record = True  # type: ignore[misc]


def test_discovery_contract_has_no_persistence_dependency() -> None:
    source = inspect.getsource(discovery_module)
    for forbidden in ("persistence", "Repository", "sqlalchemy", "session"):
        assert forbidden not in source


def test_provenance_and_acceptance_are_independent() -> None:
    value = DiscoveryValue(
        value="Use family holdout",
        provenance=DiscoveryProvenance.MODEL_SUGGESTION,
        source_ref="turn-4",
        accepted=True,
    )
    assert value.provenance is DiscoveryProvenance.MODEL_SUGGESTION
    assert value.accepted is True


def test_readiness_requires_only_the_deterministic_minimum() -> None:
    incomplete = _draft(objective=_value("Simulate a maize population"))
    assert assess_discovery_readiness(incomplete).missing == (
        "DBTL rationale",
        "intended output or decision",
    )

    ready = _draft(
        objective=_value("Simulate a maize population"),
        rationale=_value("The design and validation assumptions need iterative review"),
        intended_outputs=(_value("A validated synthetic dataset"),),
        open_questions=("Which family holdout threshold should Design use?",),
    )
    assert assess_discovery_readiness(ready).ready is True


def test_offered_can_be_confirmed_but_terminal_states_cannot_reopen() -> None:
    assert can_transition_discovery(DiscoveryStatus.READY, DiscoveryStatus.OFFERED)
    assert can_transition_discovery(DiscoveryStatus.OFFERED, DiscoveryStatus.CONFIRMED)
    assert can_transition_discovery(DiscoveryStatus.OFFERED, DiscoveryStatus.GATHERING)
    for terminal in (
        DiscoveryStatus.CONFIRMED,
        DiscoveryStatus.DECLINED,
        DiscoveryStatus.SUPERSEDED,
        DiscoveryStatus.EXPIRED,
    ):
        assert not can_transition_discovery(terminal, DiscoveryStatus.GATHERING)


def test_start_card_is_revision_bound_and_has_no_default_answer() -> None:
    card = discovery_card_request(_draft(), proposal_hash="a" * 64)
    assert card["clarification_type"] == DISCOVERY_CARD_TYPE
    assert card["discovery_id"] == "discovery-1"
    assert card["discovery_revision"] == 3
    assert card["project_id"] == "project-1"
    assert card["thread_id"] == "thread-1"
    assert card["proposal_hash"] == "a" * 64
    assert card["notice"] == DISCOVERY_NO_RECORD_NOTICE
    assert card["input_mode"] == "single_choice"
    assert "selected_option_id" not in card
    assert [option["id"] for option in card["options"]] == [
        DiscoveryAction.START.value,
        DiscoveryAction.KEEP_DISCUSSING.value,
        DiscoveryAction.CONTINUE_ORDINARY.value,
    ]


def test_start_card_keeps_bound_assumptions_out_of_the_decision_control() -> None:
    draft = _draft(
        objective=_value("Rank drought-tolerant hybrids"),
        rationale=_value("Use governed iteration", DiscoveryProvenance.MODEL_SUGGESTION),
        known_inputs=(_value("2025 site trials"),),
        intended_outputs=(_value("Reviewed hybrid ranking", DiscoveryProvenance.MODEL_SUGGESTION),),
        success_criteria=(_value("Held-out correlation above 0.4"),),
        rejection_criteria=(_value("Site leakage"),),
    )

    card = discovery_card_request(draft, proposal_hash="b" * 64)

    assert card["context"] == DISCOVERY_NO_RECORD_NOTICE
    assert card["proposal_hash"] == "b" * 64
    assert card["discovery_revision"] == draft.revision


@pytest.mark.parametrize("proposal_hash", ["", "not-a-hash", "a" * 63, "g" * 64])
def test_start_card_refuses_an_unbound_proposal(proposal_hash: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        discovery_card_request(_draft(), proposal_hash=proposal_hash)


def test_start_card_refuses_a_nonpositive_revision() -> None:
    with pytest.raises(ValueError, match="positive revision"):
        discovery_card_request(_draft(revision=0), proposal_hash="a" * 64)


def test_deterministic_package_keeps_model_suggestions_distinct_from_user_facts() -> None:
    from deerflow.dbtl.discovery import build_discovery_package

    package = build_discovery_package("Simulate a maize population and validate the generated phenotype structure")
    assert package["objective"]
    assert package["provenance"]["objective"] == {"source": "user_turn", "accepted": True}
    assert package["provenance"]["rationale"] == {"source": "model_suggestion", "accepted": False}


def test_detailed_start_request_keeps_its_objective_but_bare_start_does_not() -> None:
    from deerflow.dbtl.discovery import build_discovery_package

    detailed = build_discovery_package("Start a DBTL cycle to simulate maize and validate phenotype structure")
    bare = build_discovery_package("Start a DBTL cycle")

    assert detailed["objective"] == "simulate maize and validate phenotype structure"
    assert bare["objective"] == ""
