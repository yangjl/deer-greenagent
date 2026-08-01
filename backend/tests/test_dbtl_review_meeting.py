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

EVIDENCE_ID = "evidence-1"
EVIDENCE_REVISION = 3
EVIDENCE_HASH = "a" * 64


def _artifact(
    *,
    attempt: str,
    artifact_type: str,
    reviewed_artifact_id: str | None = None,
    reviewed_artifact_revision: int | None = None,
    reviewed_artifact_content_hash: str | None = None,
) -> dict[str, object]:
    return {
        "id": "art-1",
        "stage_attempt_id": attempt,
        "artifact_type": artifact_type,
        "reviewed_artifact_id": reviewed_artifact_id,
        "reviewed_artifact_revision": reviewed_artifact_revision,
        "reviewed_artifact_content_hash": reviewed_artifact_content_hash,
    }


def _recorded(*, stage: str = "test", attempt: str = "att-1", artifacts: list[dict[str, object]]) -> bool:
    return review_meeting_recorded(
        stage=stage,
        stage_attempt_id=attempt,
        artifacts=artifacts,
        evidence_artifact_id=EVIDENCE_ID,
        evidence_artifact_revision=EVIDENCE_REVISION,
        evidence_content_hash=EVIDENCE_HASH,
    )


class TestAMeetingIsRecordedByItsArtifact:
    def test_no_artifacts_means_no_meeting(self):
        assert _recorded(artifacts=[]) is False

    def test_the_review_meeting_artifact_on_this_attempt_counts(self):
        artifacts = [
            _artifact(
                attempt="att-1",
                artifact_type="test_review_meeting",
                reviewed_artifact_id=EVIDENCE_ID,
                reviewed_artifact_revision=EVIDENCE_REVISION,
                reviewed_artifact_content_hash=EVIDENCE_HASH,
            )
        ]

        assert _recorded(artifacts=artifacts) is True

    def test_a_meeting_without_an_evidence_binding_does_not_count(self):
        artifacts = [_artifact(attempt="att-1", artifact_type="test_review_meeting")]

        assert _recorded(artifacts=artifacts) is False

    @pytest.mark.parametrize(
        ("evidence_id", "evidence_revision", "evidence_hash"),
        [
            ("evidence-2", EVIDENCE_REVISION, EVIDENCE_HASH),
            (EVIDENCE_ID, EVIDENCE_REVISION + 1, EVIDENCE_HASH),
            (EVIDENCE_ID, EVIDENCE_REVISION, "b" * 64),
        ],
    )
    def test_a_meeting_for_stale_evidence_does_not_count(
        self,
        evidence_id: str,
        evidence_revision: int,
        evidence_hash: str,
    ):
        artifacts = [
            _artifact(
                attempt="att-1",
                artifact_type="test_review_meeting",
                reviewed_artifact_id=EVIDENCE_ID,
                reviewed_artifact_revision=EVIDENCE_REVISION,
                reviewed_artifact_content_hash=EVIDENCE_HASH,
            )
        ]

        assert (
            review_meeting_recorded(
                stage="test",
                stage_attempt_id="att-1",
                artifacts=artifacts,
                evidence_artifact_id=evidence_id,
                evidence_artifact_revision=evidence_revision,
                evidence_content_hash=evidence_hash,
            )
            is False
        )

    def test_the_stages_own_evidence_is_not_its_meeting(self):
        """A Test attempt always carries its validity pack. Reading that as a
        completed meeting would mark every Test stage complete before anybody
        met."""
        artifacts = [_artifact(attempt="att-1", artifact_type="test_report")]

        assert _recorded(artifacts=artifacts) is False

    def test_another_attempts_meeting_does_not_count(self):
        """A revised stage attempt gets a new assessment and a new meeting; the
        previous attempt's meeting is not this one's."""
        artifacts = [
            _artifact(
                attempt="att-0",
                artifact_type="test_review_meeting",
                reviewed_artifact_id=EVIDENCE_ID,
                reviewed_artifact_revision=EVIDENCE_REVISION,
                reviewed_artifact_content_hash=EVIDENCE_HASH,
            )
        ]

        assert _recorded(artifacts=artifacts) is False

    def test_another_stages_meeting_does_not_count(self):
        artifacts = [_artifact(attempt="att-1", artifact_type="build_review_meeting")]

        assert _recorded(artifacts=artifacts) is False

    def test_an_unnamed_attempt_matches_nothing(self):
        """Every artifact carries a `stage_attempt_id`; an empty one is a caller
        that could not resolve the attempt, and matching it against artifacts
        whose own id is missing would mark an unrelated meeting complete."""
        artifacts = [
            _artifact(
                attempt="",
                artifact_type="test_review_meeting",
                reviewed_artifact_id=EVIDENCE_ID,
                reviewed_artifact_revision=EVIDENCE_REVISION,
                reviewed_artifact_content_hash=EVIDENCE_HASH,
            )
        ]

        assert _recorded(attempt="", artifacts=artifacts) is False

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
