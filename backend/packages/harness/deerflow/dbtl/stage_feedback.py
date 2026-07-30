"""Stage-owned feedback intent policy.

Rendering controls is not authorization. Every accepted iframe intent is
checked against this declaration at the persistence boundary, after the
surface's server-owned stage has been read. The policy deliberately contains
no promotion or publication write: Learn may record a recommendation, never
turn it into project knowledge.
"""

from __future__ import annotations

from collections.abc import Iterable

STAGE_FEEDBACK_STAGES = ("design", "build", "test", "learn")

_COMMON_CHAIR = frozenset({"chair_option", "chair_text"})
_COMMON_ROUTES = frozenset({"advance", "park"})

STAGE_ALLOWED_INTENTS: dict[str, frozenset[str]] = {
    "design": _COMMON_CHAIR | _COMMON_ROUTES | frozenset({"submit_for_review", "approve", "request_changes", "reject"}),
    "build": _COMMON_CHAIR
    | _COMMON_ROUTES
    | frozenset(
        {
            "submit_for_review",
            "approve",
            "request_changes",
            "reject",
            "convene_review_meeting",
        }
    ),
    "test": _COMMON_CHAIR | _COMMON_ROUTES | frozenset({"submit_for_review", "choose_route", "convene_review_meeting"}),
    "learn": _COMMON_CHAIR
    | _COMMON_ROUTES
    | frozenset(
        {
            "submit_for_review",
            "approve",
            "request_changes",
            "reject",
            "convene_review_meeting",
            "recommend_promotion",
            "close_without_candidate",
        }
    ),
}


def allowed_stage_feedback_intents(stage: str) -> frozenset[str]:
    return STAGE_ALLOWED_INTENTS.get((stage or "").strip().lower(), frozenset())


def validate_stage_feedback_intent(stage: str, intent: str) -> None:
    normalized_stage = (stage or "").strip().lower()
    normalized_intent = (intent or "").strip().lower()
    if normalized_stage not in STAGE_ALLOWED_INTENTS:
        raise ValueError(f"Stage {stage!r} cannot own a feedback surface.")
    if normalized_intent not in STAGE_ALLOWED_INTENTS[normalized_stage]:
        raise ValueError(f"Intent {intent!r} is not allowed for the {normalized_stage.title()} stage.")


def filter_stage_feedback_intents(stage: str, intents: Iterable[str]) -> list[str]:
    allowed = allowed_stage_feedback_intents(stage)
    return [intent for intent in intents if intent in allowed]
