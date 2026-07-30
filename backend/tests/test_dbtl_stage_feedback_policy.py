from __future__ import annotations

import pytest

from deerflow.dbtl.stage_feedback import (
    STAGE_ALLOWED_INTENTS,
    validate_stage_feedback_intent,
)


@pytest.mark.parametrize(
    ("stage", "intent"),
    [
        ("design", "approve"),
        ("build", "convene_review_meeting"),
        ("test", "choose_route"),
        ("learn", "recommend_promotion"),
    ],
)
def test_declared_stage_intents_are_accepted(stage: str, intent: str) -> None:
    validate_stage_feedback_intent(stage, intent)


@pytest.mark.parametrize(
    ("stage", "intent"),
    [
        ("design", "recommend_promotion"),
        ("build", "choose_route"),
        ("test", "approve"),
        ("learn", "choose_route"),
        ("reconciliation", "approve"),
    ],
)
def test_disallowed_intents_fail_closed(stage: str, intent: str) -> None:
    with pytest.raises(ValueError):
        validate_stage_feedback_intent(stage, intent)


def test_no_stage_surface_can_publish_or_promote() -> None:
    for intents in STAGE_ALLOWED_INTENTS.values():
        assert "publish" not in intents
        assert "promote" not in intents
