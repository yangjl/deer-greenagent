from __future__ import annotations

from deerflow.agents.dbtl.live_stage.adapter import _review_meeting_units
from deerflow.config.dbtl_config import DbtlConfig
from deerflow.dbtl.agent_selector import Assignment
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_meetings import (
    MeetingRequirement,
    attach_meeting_to_core_evidence,
    meeting_gate,
)
from deerflow.dbtl.stage_spec import resolve_review_stage_spec


def test_every_review_meeting_seat_receives_the_contract_its_collector_enforces() -> None:
    units = _review_meeting_units(
        stage="build",
        attempt_id="attempt-1",
        assignment=Assignment(capability=Capability.SOFTWARE_ENGINEERING, agent_name="general-purpose", via_generalist=True),
        model="gpt-5.6-sol",
        evidence_uri="/mnt/user-data/outputs/build-review.md",
        evidence_hash="a" * 64,
        context={"cycle_id": "cycle-1", "cycle_title": "Pilot"},
    )

    assert all('"status": "completed" | "needs_input" | "blocked" | "failed"' in unit.prompt for unit in units)
    assert all('"quality_checks"' in unit.prompt for unit in units)


def test_review_specs_are_pinned_for_each_stage() -> None:
    assert resolve_review_stage_spec("test").spec_key == "generic:test-review:v1"
    assert resolve_review_stage_spec("build").spec_key == "generic:build-review:v1"
    assert resolve_review_stage_spec("learn").spec_key == "generic:learn-review:v1"


def test_stage_meeting_flags_default_off_independently() -> None:
    config = DbtlConfig()
    assert config.stage_meetings.model_dump() == {
        "build": False,
        "test": False,
        "learn": False,
    }


def test_routine_skips_standard_offers_and_high_stakes_requires() -> None:
    routine = meeting_gate(stage="test", assessed_difficulty="routine", enabled=True)
    standard = meeting_gate(stage="test", assessed_difficulty="standard", enabled=True)
    high = meeting_gate(stage="test", assessed_difficulty="high_stakes", enabled=True)
    assert routine.requirement is MeetingRequirement.SKIPPED
    assert standard.requirement is MeetingRequirement.OPTIONAL
    assert high.requirement is MeetingRequirement.REQUIRED
    assert high.transition_routes_locked is True


def test_explicit_downward_override_unlocks_high_stakes_meeting() -> None:
    gate = meeting_gate(
        stage="build",
        assessed_difficulty="high_stakes",
        human_override="standard",
        enabled=True,
    )
    assert gate.requirement is MeetingRequirement.OPTIONAL
    assert gate.assessed_difficulty.value == "high_stakes"
    assert gate.effective_difficulty.value == "standard"


def test_test_meeting_cannot_rewrite_computed_outcome() -> None:
    attached = attach_meeting_to_core_evidence(
        stage="test",
        core_evidence={"computed_outcome": "invalidated", "validity_pack_hash": "abc"},
        meeting_output={"computed_outcome": "supported", "recommendation": "repeat_test"},
    )
    assert attached["computed_outcome"] == "invalidated"
    assert "computed_outcome" not in attached["review_meeting"]
    assert attached["review_meeting"]["recommendation"] == "repeat_test"


def test_learn_meeting_cannot_promote_or_publish() -> None:
    attached = attach_meeting_to_core_evidence(
        stage="learn",
        core_evidence={"candidate_ids": ["candidate-1"], "promoted": False},
        meeting_output={
            "recommendation": "candidate-1 merits promotion",
            "promoted": True,
            "published": True,
        },
    )
    assert attached["promoted"] is False
    assert "promoted" not in attached["review_meeting"]
    assert "published" not in attached["review_meeting"]
