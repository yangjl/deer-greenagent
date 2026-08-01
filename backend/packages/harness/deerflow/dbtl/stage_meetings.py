"""Policy boundary for optional and required post-evidence stage meetings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from deerflow.dbtl.transition_assessment import TransitionDifficulty

REVIEW_MEETING_STAGES = ("build", "test", "learn")

#: What a completed review meeting leaves behind, per stage. These mirror the
#: ``required_artifact_types`` on the pinned ``generic:<stage>-review:v1`` specs,
#: because the artifact a meeting writes is the only durable proof it happened.
REVIEW_MEETING_ARTIFACT_TYPES: Mapping[str, str] = MappingProxyType({stage: f"{stage}_review_meeting" for stage in REVIEW_MEETING_STAGES})


def review_meeting_recorded(
    *,
    stage: str,
    stage_attempt_id: str,
    artifacts: Iterable[Mapping[str, Any]],
    evidence_artifact_id: str,
    evidence_artifact_revision: int,
    evidence_content_hash: str,
) -> bool:
    """Whether this attempt has a meeting for the exact current evidence.

    Derived rather than stored, for the same reason ``is_current`` is derived on
    a feedback surface: a separate boolean could disagree with the evidence, and
    the evidence is what a reviewer actually reads. Scoping to the attempt is
    load-bearing. Stage attempts are long-lived and may accumulate evidence
    revisions after request-changes, so attempt identity alone cannot prove the
    current evidence was ever reviewed.
    """
    expected = REVIEW_MEETING_ARTIFACT_TYPES.get((stage or "").strip().lower())
    attempt = (stage_attempt_id or "").strip()
    evidence_id = (evidence_artifact_id or "").strip()
    evidence_hash = (evidence_content_hash or "").strip()
    if expected is None or not attempt or not evidence_id or evidence_artifact_revision < 1 or not evidence_hash:
        return False
    return any(
        str(item.get("artifact_type") or "") == expected
        and str(item.get("stage_attempt_id") or "") == attempt
        and str(item.get("reviewed_artifact_id") or "") == evidence_id
        and int(item.get("reviewed_artifact_revision") or 0) == evidence_artifact_revision
        and str(item.get("reviewed_artifact_content_hash") or "") == evidence_hash
        for item in artifacts
    )


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


#: Intents that move the cycle along a path edge or end the attempt. These are
#: the ones a *required* meeting withholds until it has happened; the chair
#: intents and ``convene_review_meeting`` are deliberately not here, because a
#: locked gate must still let a person answer a question and convene the
#: meeting that unlocks it.
TRANSITION_INTENTS = frozenset(
    {
        "advance",
        "approve",
        "choose_route",
        "close_without_candidate",
        "recommend_promotion",
        "reject",
        "submit_for_review",
    }
)


def surface_meeting_gate(
    *,
    stage: str,
    assessed_difficulty: str | None,
    enabled: bool,
    meeting_completed: bool = False,
    human_override: str | None = None,
) -> MeetingGate | None:
    """The gate for a surface's stage, or ``None`` when it has no meeting.

    Design has no review meeting, so a Design surface gets ``None`` rather than
    an exception: this runs on the read path for every surface, and a stage
    without a meeting is an ordinary answer there, not an error.

    An absent or unreadable assessment falls back to ``standard``, the same
    fail-safe the transition assessor uses. Guessing ``routine`` would skip a
    meeting on missing information, and guessing ``high_stakes`` would lock the
    gate on it; ``standard`` offers the meeting and decides nothing.
    """
    normalized_stage = (stage or "").strip().lower()
    if normalized_stage not in REVIEW_MEETING_STAGES:
        return None
    difficulty = (assessed_difficulty or "").strip().lower()
    try:
        TransitionDifficulty(difficulty)
    except ValueError:
        difficulty = TransitionDifficulty.STANDARD.value
    override = (human_override or "").strip().lower() or None
    if override is not None:
        try:
            TransitionDifficulty(override)
        except ValueError:
            override = None
    return meeting_gate(
        stage=normalized_stage,
        assessed_difficulty=difficulty,
        enabled=enabled,
        meeting_completed=meeting_completed,
        human_override=override,
    )


def apply_meeting_gate(gate: MeetingGate | None, intents: Sequence[str]) -> list[str]:
    """Fold the convening decision into what a surface may be asked to do.

    Three rules, in the order they matter. A stage with no gate is returned
    untouched, so Design behaves exactly as it did. A gate that *can* convene
    adds ``convene_review_meeting`` — the read model's other branches compute
    verdicts and routes and have no reason to know meetings exist. And a
    **required** meeting withholds the transition intents until it has
    happened, which is the only thing on this path that changes what a person
    can record rather than merely what they are offered.

    Withholding is deliberately not the same as refusing: the server-side
    intent matrix still decides what each stage may ever do, and this narrows
    that set for one surface at one moment.
    """
    if gate is None:
        return list(intents)
    allowed = [intent for intent in intents if not (gate.transition_routes_locked and intent in TRANSITION_INTENTS)]
    if gate.can_convene and "convene_review_meeting" not in allowed:
        allowed.append("convene_review_meeting")
    return allowed


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
