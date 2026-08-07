"""Phase 7 validity contracts.

Headline performance is deliberately an input to this evaluator, never the
thing that decides scientific validity.  These three fixtures are the Phase 7
demo path and protect that distinction at the pure-contract layer.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.validity import (
    DEFAULT_VALIDITY_PACK,
    CheckStatus,
    HeadlineMetric,
    ValidityCheck,
    ValidityCheckName,
    ValidityOutcome,
    ValidityRefused,
    WorkflowRecommendation,
    evaluate_validity,
    validate_recommendation,
)


def _passing_checks(**overrides: CheckStatus) -> list[ValidityCheck]:
    statuses = {check: CheckStatus.PASSED for check in DEFAULT_VALIDITY_PACK.required_checks}
    statuses.update(overrides)
    return [
        ValidityCheck(
            check=check,
            status=status,
            detail=f"{check.replace('_', ' ')} evidence",
            evidence_refs=(f"artifact://{check}",),
        )
        for check, status in statuses.items()
    ]


def test_high_accuracy_with_leakage_is_invalidated() -> None:
    result = evaluate_validity(
        metrics=[
            HeadlineMetric(
                name="pooled_accuracy",
                value=0.94,
                threshold=0.70,
                criterion="gte",
            )
        ],
        checks=_passing_checks(leakage=CheckStatus.FAILED),
    )

    assert result.outcome is ValidityOutcome.INVALIDATED
    assert "train_test_leakage" in result.reason_codes
    assert result.headline_success is True
    assert WorkflowRecommendation.ADVANCE_TO_LEARN not in result.allowed_recommendations


def test_missing_independent_holdout_is_inconclusive() -> None:
    result = evaluate_validity(
        metrics=[HeadlineMetric(name="rmse", value=0.31, threshold=0.40, criterion="lte")],
        checks=_passing_checks(tester_holdout=CheckStatus.MISSING),
    )

    assert result.outcome is ValidityOutcome.INCONCLUSIVE
    assert result.reason_codes == ("missing_tester_holdout",)
    assert WorkflowRecommendation.ADVANCE_TO_LEARN not in result.allowed_recommendations


def test_valid_negative_result_can_advance_to_learn() -> None:
    result = evaluate_validity(
        metrics=[HeadlineMetric(name="accuracy", value=0.51, threshold=0.70, criterion="gte")],
        checks=_passing_checks(),
    )

    assert result.outcome is ValidityOutcome.NOT_SUPPORTED
    assert result.headline_success is False
    assert WorkflowRecommendation.ADVANCE_TO_LEARN in result.allowed_recommendations
    assert (
        validate_recommendation(
            result,
            WorkflowRecommendation.ADVANCE_TO_LEARN,
        )
        is WorkflowRecommendation.ADVANCE_TO_LEARN
    )


def test_implausibly_high_metric_is_invalid_even_when_checks_claim_to_pass() -> None:
    result = evaluate_validity(
        metrics=[
            HeadlineMetric(
                name="accuracy",
                value=0.99,
                threshold=0.70,
                criterion="gte",
                plausible_max=0.90,
            )
        ],
        checks=_passing_checks(),
    )

    assert result.outcome is ValidityOutcome.INVALIDATED
    assert result.reason_codes == ("implausible_high_accuracy",)


def test_duplicate_checks_are_refused_instead_of_last_write_wins() -> None:
    checks = _passing_checks()
    with pytest.raises(ValidityRefused, match="Duplicate validity check"):
        evaluate_validity(
            metrics=[HeadlineMetric(name="accuracy", value=0.8, threshold=0.7)],
            checks=[*checks, checks[0]],
        )


def test_relatedness_cannot_be_invented_as_a_generic_v2_gate() -> None:
    assert ValidityCheckName.DUPLICATES_RELATEDNESS not in DEFAULT_VALIDITY_PACK.required_checks
    with pytest.raises(ValidityRefused, match="not part of this pack"):
        evaluate_validity(
            metrics=[HeadlineMetric(name="holdout_r2", value=1.0, threshold=0.95)],
            checks=[
                *_passing_checks(),
                ValidityCheck(
                    check=ValidityCheckName.DUPLICATES_RELATEDNESS,
                    status=CheckStatus.FAILED,
                    detail="No pedigree columns were present.",
                ),
            ],
        )


def test_invalidated_result_cannot_be_recommended_for_learn() -> None:
    result = evaluate_validity(
        metrics=[HeadlineMetric(name="accuracy", value=0.94, threshold=0.7)],
        checks=_passing_checks(leakage=CheckStatus.FAILED),
    )
    with pytest.raises(ValidityRefused, match="not allowed"):
        validate_recommendation(result, WorkflowRecommendation.ADVANCE_TO_LEARN)

    assert (
        validate_recommendation(
            result,
            WorkflowRecommendation.LEARN_FROM_INVALIDATED_EVIDENCE,
        )
        is WorkflowRecommendation.LEARN_FROM_INVALIDATED_EVIDENCE
    )
