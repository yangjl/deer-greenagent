"""Policy boundary for optional and required post-evidence stage meetings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from deerflow.dbtl.transition_assessment import TransitionDifficulty

REVIEW_MEETING_STAGES = ("build", "test", "learn")


class MeetingRequirement(StrEnum):
    SKIPPED = "skipped"
    OPTIONAL = "optional"
    REQUIRED = "required"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class MeetingGate:
    stage: str
    assessed_difficulty: TransitionDifficulty
    effective_difficulty: TransitionDifficulty
    requirement: MeetingRequirement
    transition_routes_locked: bool
    can_convene: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "assessed_difficulty": self.assessed_difficulty.value,
            "effective_difficulty": self.effective_difficulty.value,
            "requirement": self.requirement.value,
            "transition_routes_locked": self.transition_routes_locked,
            "can_convene": self.can_convene,
        }


def meeting_gate(
    *,
    stage: str,
    assessed_difficulty: str,
    enabled: bool,
    meeting_completed: bool = False,
    human_override: str | None = None,
) -> MeetingGate:
    """Compute convening policy without reading meeting output.

    This is intentionally a one-way function of core evidence assessment,
    rollout config, completion, and an explicit human override. A chair's
    recommendation is not an input, preventing recursive reassessment.
    """
    normalized_stage = (stage or "").strip().lower()
    if normalized_stage not in REVIEW_MEETING_STAGES:
        raise ValueError(f"Stage {stage!r} has no review-meeting gate.")
    assessed = TransitionDifficulty(assessed_difficulty)
    effective = TransitionDifficulty(human_override) if human_override else assessed
    if not enabled or effective is TransitionDifficulty.ROUTINE:
        requirement = MeetingRequirement.SKIPPED
    elif meeting_completed:
        requirement = MeetingRequirement.COMPLETE
    elif effective is TransitionDifficulty.HIGH_STAKES:
        requirement = MeetingRequirement.REQUIRED
    else:
        requirement = MeetingRequirement.OPTIONAL
    return MeetingGate(
        stage=normalized_stage,
        assessed_difficulty=assessed,
        effective_difficulty=effective,
        requirement=requirement,
        transition_routes_locked=requirement is MeetingRequirement.REQUIRED,
        can_convene=requirement in {MeetingRequirement.OPTIONAL, MeetingRequirement.REQUIRED},
    )


def sanitize_meeting_attachment(stage: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep a chair annotation while stripping fields it cannot own."""
    normalized_stage = (stage or "").strip().lower()
    if normalized_stage not in REVIEW_MEETING_STAGES:
        raise ValueError(f"Stage {stage!r} has no review meeting.")
    forbidden = {
        "computed_outcome",
        "outcome",
        "validity_outcome",
        "promoted",
        "promotion",
        "published",
        "publication",
    }
    return {str(key): value for key, value in payload.items() if str(key) not in forbidden}


def attach_meeting_to_core_evidence(
    *,
    stage: str,
    core_evidence: Mapping[str, Any],
    meeting_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach bounded review evidence without mutating the core result."""
    return {
        **dict(core_evidence),
        "review_meeting": sanitize_meeting_attachment(stage, meeting_output),
    }
