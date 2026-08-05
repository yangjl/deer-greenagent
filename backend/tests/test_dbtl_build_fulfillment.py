from __future__ import annotations

import pytest

from deerflow.dbtl.build_fulfillment import (
    BuildFulfillmentRejected,
    FulfillmentStatus,
    derive_build_fulfillment,
    parse_build_fulfillment,
)
from deerflow.dbtl.deliverables import parse_deliverable_manifest

SHA = "a" * 64


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


def _item(deliverable_id: str, **overrides: object) -> dict[str, object]:
    path = "outputs/report.md" if deliverable_id == "analysis-report" else "outputs/summary.csv"
    item: dict[str, object] = {
        "deliverable_id": deliverable_id,
        "status": "delivered",
        "artifacts": [{"path": path, "content_hash": SHA}],
        "attempt_evidence": [],
        "notes": "Produced and hash-bound by the server.",
    }
    item.update(overrides)
    return item


def test_every_planned_deliverable_has_one_typed_fulfillment() -> None:
    result = parse_build_fulfillment(
        {"version": 1, "items": [_item("analysis-report"), _item("summary-table")]},
        manifest=_manifest(),
    )

    assert [item.deliverable_id for item in result.items] == ["analysis-report", "summary-table"]
    assert result.items[0].artifacts[0].content_hash == SHA
    assert result.all_delivered is True
    assert result.reviewable is True


@pytest.mark.parametrize(
    "items",
    [
        [_item("analysis-report")],
        [_item("analysis-report"), _item("analysis-report")],
        [_item("analysis-report"), _item("summary-table"), _item("unknown")],
    ],
)
def test_missing_duplicate_and_unknown_ids_are_rejected(items: list[dict[str, object]]) -> None:
    with pytest.raises(BuildFulfillmentRejected):
        parse_build_fulfillment({"version": 1, "items": items}, manifest=_manifest())


def test_delivered_requires_hash_bound_evidence_at_an_expected_path() -> None:
    with pytest.raises(BuildFulfillmentRejected):
        parse_build_fulfillment(
            {
                "version": 1,
                "items": [
                    _item("analysis-report", artifacts=[]),
                    _item("summary-table"),
                ],
            },
            manifest=_manifest(),
        )

    with pytest.raises(BuildFulfillmentRejected):
        parse_build_fulfillment(
            {
                "version": 1,
                "items": [
                    _item(
                        "analysis-report",
                        artifacts=[{"path": "outputs/other.md", "content_hash": "not-a-hash"}],
                    ),
                    _item("summary-table"),
                ],
            },
            manifest=_manifest(),
        )


@pytest.mark.parametrize("status", ["attempted_failed", "blocked"])
def test_failed_or_blocked_work_requires_attempt_evidence(status: str) -> None:
    with pytest.raises(BuildFulfillmentRejected):
        parse_build_fulfillment(
            {
                "version": 1,
                "items": [
                    _item(
                        "analysis-report",
                        status=status,
                        artifacts=[],
                        attempt_evidence=[],
                    ),
                    _item("summary-table"),
                ],
            },
            manifest=_manifest(),
        )


def test_all_statuses_are_explicit_and_not_attempted_is_not_reviewable() -> None:
    result = parse_build_fulfillment(
        {
            "version": 1,
            "items": [
                _item(
                    "analysis-report",
                    status="attempted_failed",
                    artifacts=[],
                    attempt_evidence=["outputs/logs/report.stderr.txt"],
                ),
                _item(
                    "summary-table",
                    status="not_attempted",
                    artifacts=[],
                    notes="Time budget expired before this item began.",
                ),
            ],
        },
        manifest=_manifest(),
    )

    assert result.items[0].status is FulfillmentStatus.ATTEMPTED_FAILED
    assert result.reviewable is False
    assert result.all_delivered is False


def test_prose_cannot_satisfy_the_fulfillment_contract() -> None:
    with pytest.raises(BuildFulfillmentRejected):
        parse_build_fulfillment("Everything was delivered.", manifest=_manifest())


def test_server_derives_delivered_and_attempted_items_from_published_bytes() -> None:
    fulfillment = derive_build_fulfillment(
        _manifest(),
        published=[
            {
                "source_path": "outputs/report.md",
                "uri": "/mnt/user-data/outputs/dbtl/hash-report.md",
                "content_hash": SHA,
            }
        ],
        declarations=[
            {
                "deliverable_id": "summary-table",
                "status": "attempted_failed",
                "artifacts": [],
                "attempt_evidence": ["logs/summary.stderr.txt"],
                "notes": "The source file had no cohort column.",
            }
        ],
    )

    assert fulfillment.items[0].status is FulfillmentStatus.DELIVERED
    assert fulfillment.items[0].artifacts[0].path == "outputs/report.md"
    assert fulfillment.items[0].artifacts[0].content_hash == SHA
    assert fulfillment.items[1].status is FulfillmentStatus.ATTEMPTED_FAILED
    assert fulfillment.reviewable is True


def test_server_marks_an_unaccounted_missing_item_not_attempted() -> None:
    fulfillment = derive_build_fulfillment(_manifest(), published=[], declarations=[])

    assert all(item.status is FulfillmentStatus.NOT_ATTEMPTED for item in fulfillment.items)
    assert fulfillment.reviewable is False
