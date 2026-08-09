from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.live_stage.adapter import (
    LiveStageAdapter,
    make_llm_transition_assessor,
)
from deerflow.dbtl.transition_assessment import (
    DEFAULT_STANDARD_RATIONALE,
    TransitionDifficulty,
    build_transition_assessment_prompt,
    parse_transition_assessment,
    standard_assessment,
)


def test_parses_a_bounded_model_assessment() -> None:
    assessment = parse_transition_assessment('{"difficulty":"high_stakes","rationale":"The field intervention is irreversible."}')

    assert assessment.difficulty is TransitionDifficulty.HIGH_STAKES
    assert assessment.rationale == "The field intervention is irreversible."
    assert assessment.source == "model"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"difficulty":"easy","rationale":"Looks fine."}',
        '{"difficulty":"routine","rationale":""}',
        "[]",
    ],
)
def test_malformed_assessments_are_refused_for_fail_safe_fallback(raw: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        parse_transition_assessment(raw)


def test_standard_fallback_never_silently_becomes_routine() -> None:
    assessment = standard_assessment()

    assert assessment.difficulty is TransitionDifficulty.STANDARD
    assert assessment.rationale == DEFAULT_STANDARD_RATIONALE
    assert assessment.source == "fallback"


def test_review_disclaimer_and_superseded_history_are_not_transition_risks() -> None:
    prompt = build_transition_assessment_prompt(
        stage="learn",
        cycle={"id": "cycle-1", "title": "Bounded fixture"},
        evidence_summary=(
            "> This package **does not satisfy** the review gate. A person must read it and decide; nothing here advances the cycle on its own.\n\nThe current authoritative rerun passed; an earlier failed rerun remains in history."
        ),
    )

    assert "not evidence that the stage failed" in prompt
    assert "superseded or historical evidence is not materially disputed current work by itself" in prompt


@pytest.mark.asyncio
async def test_transition_assessor_reuses_the_shared_helper_model(monkeypatch) -> None:
    app_config = SimpleNamespace(
        dbtl=SimpleNamespace(setup_draft_model_name="shared-helper-model"),
    )
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: app_config)

    async def run_oneshot_llm(**kwargs) -> str:
        return kwargs["model_name"]

    monkeypatch.setattr("deerflow.utils.oneshot_llm.run_oneshot_llm", run_oneshot_llm)

    assessor = make_llm_transition_assessor()

    assert assessor is not None
    assert await assessor("Assess the remaining work.") == "shared-helper-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["null", "outage", "malformed"])
async def test_live_assessor_failures_degrade_to_standard(mode: str) -> None:
    async def outage(_prompt: str) -> str:
        raise RuntimeError("provider unavailable")

    assessor = {
        "null": None,
        "outage": outage,
        "malformed": lambda _prompt: '{"difficulty":"routine"}',
    }[mode]
    adapter = LiveStageAdapter(
        repo=None,
        app_config=SimpleNamespace(
            dbtl=SimpleNamespace(progressive_gate=True),
        ),
        transition_assessor=assessor,
    )

    result = await adapter._assess_transition(
        stage="design",
        cycle={"id": "cycle-1", "title": "Trial"},
        evidence_summary="Bound evidence.",
    )

    assert result.difficulty is TransitionDifficulty.STANDARD
