"""Immutable values crossing the live-stage/supervisor boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LiveStageResult:
    """User-visible result of one supervisor stage request.

    The result can present evidence but can never approve it.  Gate authority
    stays in the typed human-review repository writes.
    """

    stage: str
    cycle_id: str
    note: str
    worker_count: int = 0
    produced_usable_evidence: bool = False
    artifact_uri: str | None = None
    clarification_question: str | None = None
    authoring_request: str | None = None
    deck_uri: str | None = None
    feedback_surface_id: str | None = None
    test_assessment: Mapping[str, Any] | None = None
    review_meeting_requirement: str | None = None
    #: A bound Build control the supervisor should render as a Human Input Card.
    #: Present means the Build is paused on a decision, not that it failed: the
    #: two lead to different words and different next steps, so the caller must
    #: be able to tell them apart without parsing `note`.
    control_request: Mapping[str, Any] | None = None
    #: What the round established, for the one sentence that introduces it in
    #: chat. Carried here rather than re-read by the supervisor, which has no
    #: access to the worker rows and would otherwise summarise a meeting it
    #: cannot see. Absent leaves the reply on its recorded-counts fallback.
    research_question: str = ""
    chair_summary: str = ""
    chair_consensus: Mapping[str, Any] | None = None

    @property
    def satisfies_gate(self) -> bool:
        """Only a typed human review can satisfy a gate."""
        return False
