"""Pure Phase 7 scientific-validity contracts.

Performance and validity are intentionally separate inputs.  A metric may meet
its headline threshold while the assessment is invalidated by leakage, broken
folds, an implausible ceiling, or another failed gate.  Nothing in this module
writes storage; repositories and the UI consume the same deterministic result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class ValidityRefused(ValueError):
    """The supplied validity evidence or requested route is not legal."""


class ValidityCheckName(StrEnum):
    STRUCTURE_NULL = "structure_null"
    FOLD_COMPOSITION = "fold_composition"
    PREDICTIVE_CEILING = "predictive_ceiling"
    DIRECTION = "direction"
    LEAKAGE = "leakage"
    TESTER_HOLDOUT = "tester_holdout"
    WITHIN_GROUP = "within_group"
    DUPLICATES_RELATEDNESS = "duplicates_relatedness"
    REPRODUCIBILITY = "reproducibility"
    RECONCILED_INPUTS = "reconciled_inputs"


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


class ValidityOutcome(StrEnum):
    SUPPORTED = "supported"
    NOT_SUPPORTED = "not_supported"
    INCONCLUSIVE = "inconclusive"
    INVALIDATED = "invalidated"


class WorkflowRecommendation(StrEnum):
    ADVANCE_TO_LEARN = "advance_to_learn"
    LEARN_FROM_INVALIDATED_EVIDENCE = "learn_from_invalidated_evidence"
    REPEAT_TEST = "repeat_test"
    RETURN_TO_BUILD = "return_to_build"
    RETURN_TO_RECONCILIATION = "return_to_reconciliation"
    RETURN_TO_DESIGN = "return_to_design"
    CLOSE_CYCLE = "close_cycle"


@dataclass(frozen=True, slots=True)
class ValidityPack:
    profile: str
    version: int
    title: str
    required_checks: tuple[ValidityCheckName, ...]
    provisional: bool = True

    @property
    def pack_key(self) -> str:
        return f"{self.profile}:v{self.version}"


GENERIC_PREDICTIVE_V1_PACK = ValidityPack(
    profile="generic-predictive",
    version=1,
    title="Generic predictive validity",
    required_checks=tuple(ValidityCheckName),
    # Phase 7 implements the reviewable first pack.  Domain evidence grades and
    # thresholds remain a later human decision rather than being invented here.
    provisional=True,
)


# Version 1 treated every check in the broad cross-domain vocabulary as a gate.
# That made a simple, approved family holdout fail because no pedigree existed,
# even though relatedness was never part of its Design. Version 2 keeps the
# checks that are meaningful for every predictive holdout and leaves
# population-structure, within-group, and relatedness checks to a future
# explicitly selected domain/design pack. A worker may discuss those concerns
# as limitations; it may not silently promote them into required gates.
DEFAULT_VALIDITY_PACK = ValidityPack(
    profile="generic-predictive",
    version=2,
    title="Generic predictive validity",
    required_checks=tuple(
        check
        for check in ValidityCheckName
        if check
        not in {
            ValidityCheckName.STRUCTURE_NULL,
            ValidityCheckName.WITHIN_GROUP,
            ValidityCheckName.DUPLICATES_RELATEDNESS,
        }
    ),
    provisional=True,
)


@dataclass(frozen=True, slots=True)
class HeadlineMetric:
    name: str
    value: float
    threshold: float
    criterion: str = "gte"
    plausible_max: float | None = None
    unit: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidityRefused("A headline metric needs a name.")
        if self.criterion not in {"gte", "lte"}:
            raise ValidityRefused("A headline metric criterion must be 'gte' or 'lte'.")
        for field_name in ("value", "threshold", "plausible_max"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValidityRefused(f"Headline metric {field_name} must be finite.")

    @property
    def meets_threshold(self) -> bool:
        if self.criterion == "gte":
            return self.value >= self.threshold
        return self.value <= self.threshold

    @property
    def exceeds_plausible_ceiling(self) -> bool:
        return self.plausible_max is not None and self.value > self.plausible_max

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "threshold": self.threshold,
            "criterion": self.criterion,
            "plausible_max": self.plausible_max,
            "unit": self.unit,
            "meets_threshold": self.meets_threshold,
        }


@dataclass(frozen=True, slots=True)
class ValidityCheck:
    check: ValidityCheckName | str
    status: CheckStatus | str
    detail: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            parsed_check = ValidityCheckName(self.check)
            parsed_status = CheckStatus(self.status)
        except ValueError as exc:
            raise ValidityRefused(str(exc)) from exc
        if parsed_status in {CheckStatus.PASSED, CheckStatus.FAILED} and not self.detail.strip():
            raise ValidityRefused(f"Validity check {parsed_check.value!r} needs a detail.")
        if parsed_status is CheckStatus.PASSED and not self.evidence_refs:
            raise ValidityRefused(f"Passed validity check {parsed_check.value!r} needs evidence.")
        object.__setattr__(self, "check", parsed_check)
        object.__setattr__(self, "status", parsed_status)
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(dict.fromkeys(item.strip() for item in self.evidence_refs if item.strip())),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "check": self.check.value,
            "status": self.status.value,
            "detail": self.detail.strip(),
            "evidence_refs": list(self.evidence_refs),
        }


_FAILURE_REASON = MappingProxyType(
    {
        ValidityCheckName.STRUCTURE_NULL: "population_structure_artifact",
        ValidityCheckName.FOLD_COMPOSITION: "invalid_fold_composition",
        ValidityCheckName.PREDICTIVE_CEILING: "implausible_high_accuracy",
        ValidityCheckName.DIRECTION: "direction_check_failure",
        ValidityCheckName.LEAKAGE: "train_test_leakage",
        ValidityCheckName.TESTER_HOLDOUT: "holdout_failure",
        ValidityCheckName.WITHIN_GROUP: "within_group_failure",
        ValidityCheckName.DUPLICATES_RELATEDNESS: "duplicates_or_relatedness_artifact",
        ValidityCheckName.REPRODUCIBILITY: "irreproducible_execution",
        ValidityCheckName.RECONCILED_INPUTS: "unreconciled_data",
    }
)

_MISSING_REASON = MappingProxyType({check: f"missing_{check.value}" for check in ValidityCheckName})

_ALLOWED_RECOMMENDATIONS = MappingProxyType(
    {
        ValidityOutcome.SUPPORTED: (
            WorkflowRecommendation.ADVANCE_TO_LEARN,
            WorkflowRecommendation.REPEAT_TEST,
            WorkflowRecommendation.CLOSE_CYCLE,
        ),
        ValidityOutcome.NOT_SUPPORTED: (
            WorkflowRecommendation.ADVANCE_TO_LEARN,
            WorkflowRecommendation.REPEAT_TEST,
            WorkflowRecommendation.CLOSE_CYCLE,
        ),
        ValidityOutcome.INCONCLUSIVE: (
            WorkflowRecommendation.REPEAT_TEST,
            WorkflowRecommendation.RETURN_TO_BUILD,
            WorkflowRecommendation.RETURN_TO_RECONCILIATION,
            WorkflowRecommendation.RETURN_TO_DESIGN,
            WorkflowRecommendation.CLOSE_CYCLE,
        ),
        ValidityOutcome.INVALIDATED: (
            WorkflowRecommendation.LEARN_FROM_INVALIDATED_EVIDENCE,
            WorkflowRecommendation.REPEAT_TEST,
            WorkflowRecommendation.RETURN_TO_BUILD,
            WorkflowRecommendation.RETURN_TO_RECONCILIATION,
            WorkflowRecommendation.RETURN_TO_DESIGN,
            WorkflowRecommendation.CLOSE_CYCLE,
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ValidityEvaluation:
    validity_pack_key: str
    outcome: ValidityOutcome
    reason_codes: tuple[str, ...]
    headline_success: bool
    allowed_recommendations: tuple[WorkflowRecommendation, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "validity_pack_key": self.validity_pack_key,
            "outcome": self.outcome.value,
            "reason_codes": list(self.reason_codes),
            "headline_success": self.headline_success,
            "allowed_recommendations": [item.value for item in self.allowed_recommendations],
        }


def evaluate_validity(
    *,
    metrics: list[HeadlineMetric] | tuple[HeadlineMetric, ...],
    checks: list[ValidityCheck] | tuple[ValidityCheck, ...],
    validity_pack: ValidityPack = DEFAULT_VALIDITY_PACK,
) -> ValidityEvaluation:
    """Derive one fail-closed outcome from metric and validity evidence."""
    by_name: dict[ValidityCheckName, ValidityCheck] = {}
    for item in checks:
        if item.check in by_name:
            raise ValidityRefused(f"Duplicate validity check {item.check.value!r}.")
        by_name[item.check] = item

    unknown = set(by_name) - set(validity_pack.required_checks)
    if unknown:
        raise ValidityRefused("Validity checks are not part of this pack: " + ", ".join(sorted(item.value for item in unknown)))

    headline_success = bool(metrics) and all(item.meets_threshold for item in metrics)
    implausible = any(item.exceeds_plausible_ceiling for item in metrics)
    failed = [check for check in validity_pack.required_checks if check in by_name and by_name[check].status is CheckStatus.FAILED]
    missing = [check for check in validity_pack.required_checks if check not in by_name or by_name[check].status in {CheckStatus.MISSING, CheckStatus.NOT_APPLICABLE}]

    if implausible or failed:
        reasons = ([] if not implausible else ["implausible_high_accuracy"]) + [_FAILURE_REASON[item] for item in failed]
        outcome = ValidityOutcome.INVALIDATED
    elif not metrics or missing:
        reasons = ([] if metrics else ["missing_headline_metrics"]) + [_MISSING_REASON[item] for item in missing]
        outcome = ValidityOutcome.INCONCLUSIVE
    elif headline_success:
        reasons = []
        outcome = ValidityOutcome.SUPPORTED
    else:
        reasons = ["headline_criterion_not_met"]
        outcome = ValidityOutcome.NOT_SUPPORTED

    deduped = tuple(dict.fromkeys(reasons))
    return ValidityEvaluation(
        validity_pack_key=validity_pack.pack_key,
        outcome=outcome,
        reason_codes=deduped,
        headline_success=headline_success,
        allowed_recommendations=_ALLOWED_RECOMMENDATIONS[outcome],
    )


def validate_recommendation(
    evaluation: ValidityEvaluation,
    recommendation: WorkflowRecommendation | str,
) -> WorkflowRecommendation:
    try:
        parsed = WorkflowRecommendation(recommendation)
    except ValueError as exc:
        raise ValidityRefused(str(exc)) from exc
    if parsed not in evaluation.allowed_recommendations:
        raise ValidityRefused(f"Recommendation {parsed.value!r} is not allowed for {evaluation.outcome.value!r} evidence.")
    return parsed
