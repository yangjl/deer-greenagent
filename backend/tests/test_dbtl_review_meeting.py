"""Convening a stage's review meeting, and knowing when it has happened.

Until now `meeting_completed` was a parameter nothing ever passed as true: the
read model looked for a `review_meeting_recorded` key on the stage projection
that no writer produced, so a gate could offer a meeting forever and a
*required* one could never be satisfied. The meeting's output is an artifact, so
that artifact is what answers the question — a separate boolean column could
disagree with the evidence, and the evidence is what a reviewer reads.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.stage_meetings import (
    REVIEW_MEETING_ARTIFACT_TYPES,
    MeetingRequirement,
    review_meeting_recorded,
    surface_meeting_gate,
)


def _artifact(*, attempt: str, artifact_type: str) -> dict[str, object]:
    return {"id": "art-1", "stage_attempt_id": attempt, "artifact_type": artifact_type}


class TestAMeetingIsRecordedByItsArtifact:
    def test_no_artifacts_means_no_meeting(self):
        assert review_meeting_recorded(stage="test", stage_attempt_id="att-1", artifacts=[]) is False

    def test_the_review_meeting_artifact_on_this_attempt_counts(self):
        artifacts = [_artifact(attempt="att-1", artifact_type="test_review_meeting")]

        assert review_meeting_recorded(stage="test", stage_attempt_id="att-1", artifacts=artifacts) is True

    def test_the_stages_own_evidence_is_not_its_meeting(self):
        """A Test attempt always carries its validity pack. Reading that as a
        completed meeting would mark every Test stage complete before anybody
        met."""
        artifacts = [_artifact(attempt="att-1", artifact_type="test_report")]

        assert review_meeting_recorded(stage="test", stage_attempt_id="att-1", artifacts=artifacts) is False

    def test_another_attempts_meeting_does_not_count(self):
        """A revised stage attempt gets a new assessment and a new meeting; the
        previous attempt's meeting is not this one's."""
        artifacts = [_artifact(attempt="att-0", artifact_type="test_review_meeting")]

        assert review_meeting_recorded(stage="test", stage_attempt_id="att-1", artifacts=artifacts) is False

    def test_another_stages_meeting_does_not_count(self):
        artifacts = [_artifact(attempt="att-1", artifact_type="build_review_meeting")]

        assert review_meeting_recorded(stage="test", stage_attempt_id="att-1", artifacts=artifacts) is False

    def test_an_unnamed_attempt_matches_nothing(self):
        """Every artifact carries a `stage_attempt_id`; an empty one is a caller
        that could not resolve the attempt, and matching it against artifacts
        whose own id is missing would mark an unrelated meeting complete."""
        artifacts = [_artifact(attempt="", artifact_type="test_review_meeting")]

        assert review_meeting_recorded(stage="test", stage_attempt_id="", artifacts=artifacts) is False

    def test_design_has_no_review_meeting_artifact(self):
        assert "design" not in REVIEW_MEETING_ARTIFACT_TYPES

    @pytest.mark.parametrize("stage", ["build", "test", "learn"])
    def test_every_meeting_stage_names_its_artifact(self, stage):
        assert REVIEW_MEETING_ARTIFACT_TYPES[stage] == f"{stage}_review_meeting"


class TestACompletedMeetingClosesTheGate:
    def test_a_required_meeting_stops_being_required_once_it_has_happened(self):
        gate = surface_meeting_gate(
            stage="test",
            assessed_difficulty="high_stakes",
            enabled=True,
            meeting_completed=True,
        )

        assert gate is not None
        assert gate.requirement is MeetingRequirement.COMPLETE
        assert gate.transition_routes_locked is False
        assert gate.can_convene is False

    def test_an_optional_meeting_is_not_offered_twice(self):
        gate = surface_meeting_gate(
            stage="test",
            assessed_difficulty="standard",
            enabled=True,
            meeting_completed=True,
        )

        assert gate is not None
        assert gate.can_convene is False
