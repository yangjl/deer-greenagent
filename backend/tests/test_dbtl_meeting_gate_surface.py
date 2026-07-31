"""The convening decision, folded into what a surface may be asked to do.

Until now `meeting_gate()` had no production caller at all: the three
`dbtl.stage_meetings.*` flags reached `/api/features` and nothing else, so
nothing joined an assessment, a flag, and the gate into something a person
could see. These cover the two pure pieces that join them.

The rule that actually changes behaviour is the last one: a *required* meeting
withholds the transition intents until it has happened. Everything else only
changes what is offered.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.stage_meetings import (
    MeetingRequirement,
    apply_meeting_gate,
    surface_meeting_gate,
)


class TestAStageWithoutAMeetingIsLeftAlone:
    def test_design_has_no_review_meeting_gate(self):
        """Design's meeting happens before the evidence, not after it. Asking
        for its review gate is an ordinary answer on the read path, not an
        error — this runs for every surface."""
        assert surface_meeting_gate(stage="design", assessed_difficulty="standard", enabled=True) is None

    def test_design_intents_are_returned_untouched(self):
        intents = ["approve", "request_changes", "park"]

        assert apply_meeting_gate(None, intents) == intents


class TestTheAssessmentDecidesWhetherAMeetingIsOffered:
    @pytest.mark.parametrize(
        ("difficulty", "requirement"),
        [
            ("routine", MeetingRequirement.SKIPPED),
            ("standard", MeetingRequirement.OPTIONAL),
            ("high_stakes", MeetingRequirement.REQUIRED),
        ],
    )
    def test_routine_skips_standard_offers_high_stakes_requires(self, difficulty, requirement):
        gate = surface_meeting_gate(stage="test", assessed_difficulty=difficulty, enabled=True)

        assert gate is not None
        assert gate.requirement is requirement

    def test_the_flag_being_off_skips_the_meeting_whatever_the_assessment(self):
        gate = surface_meeting_gate(stage="test", assessed_difficulty="high_stakes", enabled=False)

        assert gate is not None
        assert gate.requirement is MeetingRequirement.SKIPPED
        assert gate.can_convene is False
        assert gate.transition_routes_locked is False

    @pytest.mark.parametrize("difficulty", [None, "", "  ", "catastrophic", "ROUTINE-ish"])
    def test_an_unreadable_assessment_falls_back_to_standard(self, difficulty):
        """Guessing routine would skip a meeting on missing information and
        guessing high_stakes would lock the gate on it. Standard offers the
        meeting and decides nothing."""
        gate = surface_meeting_gate(stage="test", assessed_difficulty=difficulty, enabled=True)

        assert gate is not None
        assert gate.effective_difficulty.value == "standard"
        assert gate.requirement is MeetingRequirement.OPTIONAL

    def test_a_recorded_human_override_wins(self):
        gate = surface_meeting_gate(
            stage="test",
            assessed_difficulty="high_stakes",
            enabled=True,
            human_override="routine",
        )

        assert gate is not None
        assert gate.requirement is MeetingRequirement.SKIPPED

    def test_a_junk_override_is_ignored_rather_than_obeyed(self):
        gate = surface_meeting_gate(
            stage="test",
            assessed_difficulty="high_stakes",
            enabled=True,
            human_override="whatever",
        )

        assert gate is not None
        assert gate.requirement is MeetingRequirement.REQUIRED

    def test_a_completed_meeting_stops_offering_another(self):
        gate = surface_meeting_gate(
            stage="test",
            assessed_difficulty="high_stakes",
            enabled=True,
            meeting_completed=True,
        )

        assert gate is not None
        assert gate.requirement is MeetingRequirement.COMPLETE
        assert gate.can_convene is False
        assert gate.transition_routes_locked is False


class TestTheGateNarrowsWhatASurfaceMayBeAsked:
    def test_an_optional_meeting_is_added_beside_the_normal_flow(self):
        gate = surface_meeting_gate(stage="test", assessed_difficulty="standard", enabled=True)

        allowed = apply_meeting_gate(gate, ["submit_for_review", "choose_route"])

        assert "convene_review_meeting" in allowed
        # Optional means optional: the ordinary path stays open.
        assert "submit_for_review" in allowed
        assert "choose_route" in allowed

    def test_a_required_meeting_withholds_the_transition_intents(self):
        gate = surface_meeting_gate(stage="test", assessed_difficulty="high_stakes", enabled=True)

        allowed = apply_meeting_gate(gate, ["submit_for_review", "choose_route", "chair_text", "park"])

        assert allowed.count("convene_review_meeting") == 1
        assert "submit_for_review" not in allowed
        assert "choose_route" not in allowed
        # A locked gate must still let a person answer a question and park;
        # otherwise convening the meeting that unlocks it is unreachable.
        assert "chair_text" in allowed
        assert "park" in allowed

    def test_a_skipped_meeting_neither_adds_nor_removes_anything(self):
        gate = surface_meeting_gate(stage="test", assessed_difficulty="routine", enabled=True)

        allowed = apply_meeting_gate(gate, ["submit_for_review", "choose_route"])

        assert allowed == ["submit_for_review", "choose_route"]

    def test_a_completed_meeting_reopens_the_transition_intents(self):
        gate = surface_meeting_gate(
            stage="test",
            assessed_difficulty="high_stakes",
            enabled=True,
            meeting_completed=True,
        )

        allowed = apply_meeting_gate(gate, ["submit_for_review", "choose_route"])

        assert allowed == ["submit_for_review", "choose_route"]
