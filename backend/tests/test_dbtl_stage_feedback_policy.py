from __future__ import annotations

import pytest

from deerflow.dbtl.stage_feedback import (
    STAGE_ALLOWED_INTENTS,
    filter_stage_feedback_intents,
    is_core_review_artifact,
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


def test_only_build_and_test_accept_the_evidence_exception_intent() -> None:
    assert filter_stage_feedback_intents("build", ["continue_with_red_flag"]) == ["continue_with_red_flag"]
    assert filter_stage_feedback_intents("test", ["continue_with_red_flag"]) == ["continue_with_red_flag"]
    assert filter_stage_feedback_intents("design", ["continue_with_red_flag"]) == []
    assert filter_stage_feedback_intents("learn", ["continue_with_red_flag"]) == []


def test_supplemental_exception_dossier_does_not_replace_core_review_evidence() -> None:
    assert is_core_review_artifact("build", "build_package") is True
    assert is_core_review_artifact("build", "build_package.v2") is True
    assert is_core_review_artifact("build", "evidence_exception") is False
