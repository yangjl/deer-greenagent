"""Degraded evidence may move through governance without becoming approval."""

from __future__ import annotations

import pytest

from deerflow.agents.dbtl.live_stage.adapter import (
    _learn_synthesis_payload,
    _write_evidence_exception_deck,
    _write_evidence_exception_package,
)
from deerflow.dbtl.evidence_exception import (
    EvidenceCondition,
    EvidenceExceptionRefused,
    EvidenceReason,
    ScientificEffect,
    build_evidence_exception_dossier,
    invalidated_test_exception_facts,
)


def test_path_variance_stays_an_observation_without_an_exception() -> None:
    dossier = build_evidence_exception_dossier(
        stage="build",
        stage_attempt_id="attempt-build-1",
        reason_codes=[EvidenceReason.CONTRACT_BOOKKEEPING_VARIANCE],
    )

    assert dossier is None


def test_missing_peripheral_deliverable_builds_a_hash_bound_degraded_dossier() -> None:
    payload = {
        "stage": "build",
        "stage_attempt_id": "attempt-build-1",
        "reason_codes": [EvidenceReason.DELIVERABLE_ATTEMPT_FAILED],
        "verified_facts": ["The executable completed with exit code 0."],
        "affected_deliverables": [{"id": "notebook", "status": "attempted_failed"}],
        "available_artifacts": [{"path": "outputs/results.json", "content_hash": "a" * 64}],
    }

    first = build_evidence_exception_dossier(**payload)
    second = build_evidence_exception_dossier(**payload)

    assert first is not None
    assert first.condition is EvidenceCondition.DEGRADED_VERIFIED
    assert first.scientific_effect is ScientificEffect.LIMITS_SCOPE
    assert first.recovery_options == ("retry_with_guidance", "continue_to_test_with_red_flag", "hold", "close_cycle")
    assert first.content_hash == second.content_hash
    assert first.as_dict()["content_hash"] == first.content_hash


@pytest.mark.parametrize(
    "reason",
    [
        EvidenceReason.EXECUTION_ABSENT,
        EvidenceReason.INPUT_CHANGED,
        EvidenceReason.CORE_OUTPUT_MISSING,
        EvidenceReason.CORE_OUTPUT_UNREADABLE,
        EvidenceReason.HASH_MISMATCH,
    ],
)
def test_core_trust_failures_are_untrusted_and_cannot_support_science(reason: EvidenceReason) -> None:
    dossier = build_evidence_exception_dossier(
        stage="test",
        stage_attempt_id="attempt-test-1",
        reason_codes=[reason],
        untrusted_claims=["The worker reported a passing metric."],
    )

    assert dossier is not None
    assert dossier.condition is EvidenceCondition.UNTRUSTED
    assert dossier.scientific_effect is ScientificEffect.INVALIDATES_SUPPORT
    assert "learn_from_invalidated_evidence" in dossier.recovery_options


def test_unknown_reason_or_unsupported_stage_fails_closed() -> None:
    with pytest.raises(EvidenceExceptionRefused, match="Unknown evidence reason"):
        build_evidence_exception_dossier(
            stage="build",
            stage_attempt_id="attempt-build-1",
            reason_codes=["made_up"],
        )


def test_invalidated_reproducibility_check_becomes_a_typed_exception_fact() -> None:
    reasons, failed_checks = invalidated_test_exception_facts(
        {
            "evaluation": {
                "outcome": "invalidated",
                "reason_codes": ["irreproducible_execution"],
            },
            "checks": [
                {
                    "check": "reproducibility",
                    "status": "failed",
                    "detail": ("The authoritative rerun exited 2 because the recorded content-addressed script path was unavailable."),
                },
                {
                    "check": "leakage",
                    "status": "passed",
                    "detail": "The frozen model did not read holdout labels.",
                },
            ],
        }
    )

    assert reasons == (EvidenceReason.RERUN_UNAVAILABLE,)
    assert failed_checks == (
        {
            "check": "reproducibility",
            "status": "failed",
            "detail": ("The authoritative rerun exited 2 because the recorded content-addressed script path was unavailable."),
        },
    )


def test_non_invalidated_test_assessment_needs_no_exception() -> None:
    reasons, failed_checks = invalidated_test_exception_facts(
        {
            "evaluation": {"outcome": "supported", "reason_codes": []},
            "checks": [],
        }
    )

    assert reasons == ()
    assert failed_checks == ()

    with pytest.raises(EvidenceExceptionRefused, match="Build or Test"):
        build_evidence_exception_dossier(
            stage="design",
            stage_attempt_id="attempt-design-1",
            reason_codes=[EvidenceReason.EXECUTION_ABSENT],
        )


def test_scientific_invalidation_does_not_become_an_evidence_exception() -> None:
    reasons, failed_checks = invalidated_test_exception_facts(
        {
            "evaluation": {
                "outcome": "invalidated",
                "reason_codes": ["train_test_leakage", "holdout_failure"],
            },
            "checks": [
                {
                    "check": "leakage",
                    "status": "failed",
                    "detail": "The fitted feature matrix included holdout labels.",
                },
                {
                    "check": "tester_holdout",
                    "status": "failed",
                    "detail": "The independent holdout missed its acceptance criterion.",
                },
            ],
        }
    )

    assert reasons == ()
    assert failed_checks == ()


def test_learn_candidates_inherit_the_persisted_exception_limitation() -> None:
    _summary, candidates = _learn_synthesis_payload(
        [
            {
                "is_trustworthy": True,
                "summary": "The supported result is bounded.",
                "claims": ["The model met the held-out threshold."],
                "evidence_refs": ["artifact://validity-report"],
                "limitations": ["One environment was held out."],
            }
        ],
        test_outcome="supported",
        fallback_summary="",
        required_limitations=["Evidence exception abc123 limits the supported claim's scope."],
    )

    assert candidates[0]["limitations"] == [
        "One environment was held out.",
        "Evidence exception abc123 limits the supported claim's scope.",
    ]


def test_dossier_artifact_hash_and_deck_are_bound_without_quarantined_bytes(
    tmp_path,
) -> None:
    dossier = build_evidence_exception_dossier(
        stage="build",
        stage_attempt_id="attempt-build-1",
        reason_codes=[EvidenceReason.HASH_MISMATCH],
        verified_facts=["The server observed a different SHA-256."],
        untrusted_claims=["SECRET-QUARANTINED-BYTES"],
        failed_checks=[{"check": "output_hash", "status": "failed"}],
    )
    assert dossier is not None
    cycle = {
        "id": "cycle-1",
        "title": "Linear pilot",
        "db_revision": 7,
    }

    uri, content_hash, _digest = _write_evidence_exception_package(
        project_root=str(tmp_path),
        cycle=cycle,
        dossier=dossier,
    )
    package = (tmp_path / "outputs" / uri.removeprefix("/mnt/user-data/outputs/")).read_bytes()
    assert content_hash == dossier.content_hash
    assert __import__("hashlib").sha256(package).hexdigest() == dossier.content_hash

    deck = _write_evidence_exception_deck(
        project_root=str(tmp_path),
        cycle=cycle,
        dossier=dossier,
        package_path=uri,
        surface_id="dfs-exception-1",
        transition_gate={"evidence_exception": dossier.as_dict()},
    )
    assert deck is not None
    html = (tmp_path / "outputs" / deck.uri.removeprefix("/mnt/user-data/outputs/")).read_text(encoding="utf-8")
    assert "Continue with red flag" in html
    assert "Dossier SHA-256" in html
    assert "headline result" not in html.lower()
    # The deck may name a quarantined claim, but must never read or embed the
    # artifact bytes themselves. This sentinel represents those bytes, not a
    # safe server-authored label, so it must stay in the JSON dossier only.
    assert "SECRET-QUARANTINED-BYTES" not in html
