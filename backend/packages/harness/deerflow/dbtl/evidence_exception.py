"""Server-owned classification for evidence that cannot take the clean path."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum


class EvidenceExceptionRefused(ValueError):
    """The supplied facts cannot form a bounded evidence exception."""


class EvidenceCondition(StrEnum):
    OBSERVED_VARIANCE = "observed_variance"
    DEGRADED_VERIFIED = "degraded_verified"
    UNTRUSTED = "untrusted"


class ScientificEffect(StrEnum):
    NONE = "none"
    LIMITS_SCOPE = "limits_scope"
    INVALIDATES_SUPPORT = "invalidates_support"


class EvidenceReason(StrEnum):
    EXECUTION_ABSENT = "execution_absent"
    INPUT_CHANGED = "input_changed"
    CORE_OUTPUT_MISSING = "core_output_missing"
    CORE_OUTPUT_UNREADABLE = "core_output_unreadable"
    HASH_MISMATCH = "hash_mismatch"
    DELIVERABLE_NOT_ATTEMPTED = "deliverable_not_attempted"
    DELIVERABLE_ATTEMPT_FAILED = "deliverable_attempt_failed"
    AUDIT_INCOMPLETE = "audit_incomplete"
    RERUN_UNAVAILABLE = "rerun_unavailable"
    CONTRACT_BOOKKEEPING_VARIANCE = "contract_bookkeeping_variance"


_UNTRUSTED_REASONS = frozenset(
    {
        EvidenceReason.EXECUTION_ABSENT,
        EvidenceReason.INPUT_CHANGED,
        EvidenceReason.CORE_OUTPUT_MISSING,
        EvidenceReason.CORE_OUTPUT_UNREADABLE,
        EvidenceReason.HASH_MISMATCH,
    }
)
_DEGRADED_REASONS = frozenset(
    {
        EvidenceReason.DELIVERABLE_NOT_ATTEMPTED,
        EvidenceReason.DELIVERABLE_ATTEMPT_FAILED,
        EvidenceReason.AUDIT_INCOMPLETE,
        EvidenceReason.RERUN_UNAVAILABLE,
    }
)


def invalidated_test_exception_facts(
    assessment: Mapping[str, object] | None,
) -> tuple[tuple[EvidenceReason, ...], tuple[dict[str, object], ...]]:
    """Translate a typed invalidated Test assessment into dossier facts.

    The validity pack decides whether Test is invalidated. This helper only
    maps its failed checks onto the smaller evidence-exception taxonomy; it
    never re-evaluates metrics or accepts worker-authored classifications.
    """
    if not isinstance(assessment, Mapping):
        return (), ()
    evaluation = assessment.get("evaluation")
    if not isinstance(evaluation, Mapping) or evaluation.get("outcome") != "invalidated":
        return (), ()
    checks = assessment.get("checks")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        return (), ()

    reasons: list[EvidenceReason] = []
    failed: list[dict[str, object]] = []
    for raw in checks:
        if not isinstance(raw, Mapping) or raw.get("status") not in {"failed", "missing"}:
            continue
        check = str(raw.get("check") or "")[:160]
        status = str(raw.get("status") or "")[:24]
        detail = str(raw.get("detail") or "")[:2_000]
        lowered = detail.lower()
        if "hash" in lowered and any(word in lowered for word in ("changed", "disagree", "mismatch")):
            reason = EvidenceReason.HASH_MISMATCH
        elif "input" in lowered and any(word in lowered for word in ("changed", "missing", "unavailable")):
            reason = EvidenceReason.INPUT_CHANGED
        elif check == "reproducibility":
            reason = EvidenceReason.RERUN_UNAVAILABLE
        else:
            # Leakage, holdout failure, broken folds, and other scientific
            # validity failures belong to the ordinary invalidated-Test route
            # menu. They are not evidence-integrity exceptions.
            continue
        reasons.append(reason)
        failed.append({"check": check, "status": status, "detail": detail})
    return tuple(dict.fromkeys(reasons)), tuple(failed[:100])


def _bounded_strings(values: Sequence[object], *, limit: int = 100) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip()[:2_000] for value in values if str(value).strip()))[:limit]


def _bounded_records(values: Sequence[Mapping[str, object]], *, limit: int = 100) -> tuple[dict[str, object], ...]:
    return tuple(dict(item) for item in values[:limit])


@dataclass(frozen=True, slots=True)
class EvidenceExceptionDossier:
    version: int
    condition: EvidenceCondition
    stage: str
    stage_attempt_id: str
    reason_codes: tuple[EvidenceReason, ...]
    verified_facts: tuple[str, ...]
    untrusted_claims: tuple[str, ...]
    affected_inputs: tuple[dict[str, object], ...]
    affected_deliverables: tuple[dict[str, object], ...]
    failed_checks: tuple[dict[str, object], ...]
    available_artifacts: tuple[dict[str, object], ...]
    scientific_effect: ScientificEffect
    recovery_options: tuple[str, ...]

    def _payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "condition": self.condition.value,
            "stage": self.stage,
            "stage_attempt_id": self.stage_attempt_id,
            "reason_codes": [item.value for item in self.reason_codes],
            "verified_facts": list(self.verified_facts),
            "untrusted_claims": list(self.untrusted_claims),
            "affected_inputs": list(self.affected_inputs),
            "affected_deliverables": list(self.affected_deliverables),
            "failed_checks": list(self.failed_checks),
            "available_artifacts": list(self.available_artifacts),
            "scientific_effect": self.scientific_effect.value,
            "recovery_options": list(self.recovery_options),
        }

    @property
    def content_hash(self) -> str:
        encoded = json.dumps(self._payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {**self._payload(), "content_hash": self.content_hash}


def build_evidence_exception_dossier(
    *,
    stage: str,
    stage_attempt_id: str,
    reason_codes: Sequence[EvidenceReason | str],
    verified_facts: Sequence[object] = (),
    untrusted_claims: Sequence[object] = (),
    affected_inputs: Sequence[Mapping[str, object]] = (),
    affected_deliverables: Sequence[Mapping[str, object]] = (),
    failed_checks: Sequence[Mapping[str, object]] = (),
    available_artifacts: Sequence[Mapping[str, object]] = (),
    continuation_route: str | None = None,
) -> EvidenceExceptionDossier | None:
    """Classify server-observed facts and return no dossier for path variance."""
    normalized_stage = stage.strip().lower()
    if normalized_stage not in {"build", "test"}:
        raise EvidenceExceptionRefused("Evidence exceptions apply only to Build or Test.")
    if not stage_attempt_id.strip():
        raise EvidenceExceptionRefused("An evidence exception needs a stage attempt id.")
    try:
        reasons = tuple(dict.fromkeys(EvidenceReason(item) for item in reason_codes))
    except ValueError as exc:
        raise EvidenceExceptionRefused(f"Unknown evidence reason: {exc}.") from exc
    if not reasons:
        raise EvidenceExceptionRefused("An evidence exception needs at least one reason code.")

    if any(reason in _UNTRUSTED_REASONS for reason in reasons):
        condition = EvidenceCondition.UNTRUSTED
        effect = ScientificEffect.INVALIDATES_SUPPORT
    elif any(reason in _DEGRADED_REASONS for reason in reasons):
        condition = EvidenceCondition.DEGRADED_VERIFIED
        effect = ScientificEffect.LIMITS_SCOPE
    else:
        return None

    continuation = continuation_route or ("continue_to_test_with_red_flag" if normalized_stage == "build" else "learn_from_invalidated_evidence")
    return EvidenceExceptionDossier(
        version=1,
        condition=condition,
        stage=normalized_stage,
        stage_attempt_id=stage_attempt_id.strip()[:96],
        reason_codes=reasons,
        verified_facts=_bounded_strings(verified_facts),
        untrusted_claims=_bounded_strings(untrusted_claims),
        affected_inputs=_bounded_records(affected_inputs),
        affected_deliverables=_bounded_records(affected_deliverables),
        failed_checks=_bounded_records(failed_checks),
        available_artifacts=_bounded_records(available_artifacts),
        scientific_effect=effect,
        recovery_options=("retry_with_guidance", continuation, "hold", "close_cycle"),
    )
