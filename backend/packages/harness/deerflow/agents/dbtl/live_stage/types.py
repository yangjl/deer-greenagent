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

    @property
    def satisfies_gate(self) -> bool:
        """Only a typed human review can satisfy a gate."""
        return False
