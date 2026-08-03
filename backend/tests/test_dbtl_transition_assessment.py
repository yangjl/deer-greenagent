from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.live_stage.adapter import (
    LiveStageAdapter,
    _reconciliation_ready_after_design_approval,
)
from deerflow.dbtl.transition_assessment import (
    DEFAULT_STANDARD_RATIONALE,
    TransitionDifficulty,
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


def test_design_click_may_satisfy_only_the_design_approval_gate_reason() -> None:
    assert _reconciliation_ready_after_design_approval(
        {
            "gate": {
                "ready": False,
                "blocking_rows": [],
                "reasons": ["The Design stage has not been approved, so there is nothing to reconcile against."],
            }
        }
    )
    assert not _reconciliation_ready_after_design_approval(
        {
            "gate": {
                "ready": False,
                "blocking_rows": [],
                "reasons": [
                    "The Design stage has not been approved, so there is nothing to reconcile against.",
                    "No data sources have been declared, so nothing can be reconciled.",
                ],
            }
        }
    )
