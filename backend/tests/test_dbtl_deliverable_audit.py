from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.live_stage.adapter import _validated_deliverable_audit
from deerflow.dbtl.deliverable_audit import (
    AuditCompleteness,
    DeliverableAuditRejected,
    DeliverableAuditVerdict,
    parse_deliverable_audit,
)
from deerflow.dbtl.deliverables import parse_deliverable_manifest

SHA = "b" * 64


def _manifest():
    return parse_deliverable_manifest(
        {
            "deliverables": [
                {
                    "id": "analysis-report",
                    "title": "Analysis report",
                    "kind": "report",
                    "required": True,
                    "expected_paths": ["outputs/report.md"],
                    "acceptance_criteria": ["States the result", "Names limitations"],
                    "validation": "Inspect the Markdown.",
                    "capabilities": ["technical-writing"],
                },
                {
                    "id": "summary-table",
                    "title": "Summary table",
                    "kind": "table",
                    "required": True,
                    "expected_paths": ["outputs/summary.csv"],
                    "acceptance_criteria": ["Has one row per cohort"],
                    "validation": "Read the CSV.",
                    "capabilities": ["data-analysis"],
                },
            ]
        },
        cycle_class="other",
    )


def _audit_item(deliverable_id: str, *, verdict: str = "pass") -> dict[str, object]:
    if deliverable_id == "analysis-report":
        path = "outputs/report.md"
        criteria = ["States the result", "Names limitations"]
    else:
        path = "outputs/summary.csv"
        criteria = ["Has one row per cohort"]
    return {
        "deliverable_id": deliverable_id,
        "verdict": verdict,
        "observed_artifacts": [{"path": path, "content_hash": SHA}],
        "criteria": [{"criterion": criterion, "verdict": verdict, "detail": "Independently inspected."} for criterion in criteria],
        "notes": "Tester evidence, not Build prose.",
    }


def test_test_audit_binds_each_expected_path_and_criterion_to_observed_hashes() -> None:
    audit = parse_deliverable_audit(
        {
            "version": 1,
            "items": [_audit_item("analysis-report"), _audit_item("summary-table")],
        },
        manifest=_manifest(),
    )

    assert audit.completeness is AuditCompleteness.COMPLETE
    assert audit.items[0].expected_paths == ("outputs/report.md",)
    assert audit.items[0].observed_artifacts[0].content_hash == SHA
    assert [check.criterion for check in audit.items[0].criteria] == [
        "States the result",
        "Names limitations",
    ]


def test_test_audit_accepts_the_design_manifest_field_names_used_by_workers() -> None:
    items = [_audit_item("analysis-report"), _audit_item("summary-table")]
    for item in items:
        item["id"] = item.pop("deliverable_id")
        item["acceptance_criteria"] = item.pop("criteria")
        for criterion in item["acceptance_criteria"]:
            criterion["status"] = criterion.pop("verdict")
        for artifact in item["observed_artifacts"]:
            artifact["sha256"] = artifact.pop("content_hash")

    audit = parse_deliverable_audit(
        {"version": 1, "items": items},
        manifest=_manifest(),
    )

    assert audit.completeness is AuditCompleteness.COMPLETE
    assert [item.deliverable_id for item in audit.items] == ["analysis-report", "summary-table"]


def test_server_maps_governed_artifact_uris_back_to_manifest_paths() -> None:
    items = [_audit_item("analysis-report"), _audit_item("summary-table")]
    outputs = []
    for item in items:
        source_path = item["observed_artifacts"][0]["path"]
        governed_uri = f"/mnt/user-data/outputs/dbtl/build/hash-{source_path.rsplit('/', 1)[-1]}"
        outputs.append({"source_path": source_path, "uri": governed_uri, "content_hash": SHA})
        item["observed_artifacts"] = [{"path": governed_uri, "sha256": SHA}]

    audit, refusal = _validated_deliverable_audit(
        [SimpleNamespace(provenance={"deliverable_audit": {"version": 1, "items": items}})],
        manifest=_manifest(),
        build_test={"build_lineage": {"output_artifacts": outputs}},
    )

    assert refusal == ""
    assert audit is not None
    assert audit.items[0].observed_artifacts[0].path == "outputs/report.md"


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("fail", AuditCompleteness.INCOMPLETE),
        ("not_testable", AuditCompleteness.NOT_TESTABLE),
    ],
)
def test_overall_completeness_is_computed_from_typed_item_audits(
    verdict: str,
    expected: AuditCompleteness,
) -> None:
    audit = parse_deliverable_audit(
        {
            "version": 1,
            "items": [
                _audit_item("analysis-report", verdict=verdict),
                _audit_item("summary-table"),
            ],
        },
        manifest=_manifest(),
    )

    assert audit.completeness is expected
    assert audit.items[0].verdict is DeliverableAuditVerdict(verdict)


@pytest.mark.parametrize(
    "raw",
    [
        "All deliverables look good.",
        {"version": 1, "items": [_audit_item("analysis-report")]},
        {
            "version": 1,
            "items": [
                _audit_item("analysis-report"),
                _audit_item("analysis-report"),
                _audit_item("summary-table"),
            ],
        },
        {
            "version": 1,
            "items": [
                _audit_item("analysis-report"),
                _audit_item("summary-table"),
                _audit_item("unknown"),
            ],
        },
    ],
)
def test_prose_missing_duplicate_and_unknown_items_are_rejected(raw: object) -> None:
    with pytest.raises(DeliverableAuditRejected):
        parse_deliverable_audit(raw, manifest=_manifest())


def test_pass_cannot_hide_a_missing_artifact_or_acceptance_check() -> None:
    missing_artifact = _audit_item("analysis-report")
    missing_artifact["observed_artifacts"] = []
    with pytest.raises(DeliverableAuditRejected):
        parse_deliverable_audit(
            {"version": 1, "items": [missing_artifact, _audit_item("summary-table")]},
            manifest=_manifest(),
        )

    missing_check = _audit_item("analysis-report")
    missing_check["criteria"] = list(missing_check["criteria"])[1:]
    with pytest.raises(DeliverableAuditRejected):
        parse_deliverable_audit(
            {"version": 1, "items": [missing_check, _audit_item("summary-table")]},
            manifest=_manifest(),
        )


def test_observed_artifacts_require_expected_paths_and_sha256() -> None:
    bad = _audit_item("analysis-report")
    bad["observed_artifacts"] = [{"path": "outputs/other.md", "content_hash": "not-a-hash"}]

    with pytest.raises(DeliverableAuditRejected):
        parse_deliverable_audit(
            {"version": 1, "items": [bad, _audit_item("summary-table")]},
            manifest=_manifest(),
        )
