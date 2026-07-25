import json
from pathlib import Path

from app.gateway.dbtl_readiness import (
    DBTL_TEST_OUTCOMES,
    KNOWLEDGE_CANDIDATE_STATUSES,
    scan_dbtl_readiness,
)
from deerflow.config.dbtl_config import DbtlConfig


def _write_json(path: Path, body: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_scanner_classifies_legacy_records_without_writing(tmp_path: Path) -> None:
    compatible = tmp_path / ".greenagent/dbtl-cycles/compatible.json"
    repairable = tmp_path / ".greenagent/dbtl-cycles/repairable.json"
    invalid = tmp_path / ".greenagent/handoffs/invalid.json"
    superseded = tmp_path / ".greenagent/handoffs/superseded.json"
    _write_json(
        compatible,
        {
            "schema_version": 1,
            "id": "compatible",
            "state": "testing",
            "history": [],
            "artifacts": {},
        },
    )
    _write_json(
        repairable,
        {
            "schema_version": 1,
            "id": "repairable",
            "state": "awaiting-human-review",
            "history": [],
            "artifacts": {},
        },
    )
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text("{not-json", encoding="utf-8")
    _write_json(
        superseded,
        {
            "schema_version": 1,
            "id": "superseded",
            "work_item_id": "work",
            "from_role": "builder",
            "to_role": "tester",
            "evidence": [],
            "authorization": "review-1",
            "superseded_by": "new-handoff",
        },
    )
    before = {path: path.read_bytes() for path in (compatible, repairable, invalid, superseded)}

    report = scan_dbtl_readiness(tmp_path, DbtlConfig())

    assert report.mode == "audit_only"
    assert report.mutations_enabled is False
    assert report.graph_execution_enabled is False
    assert report.counts == {
        "compatible": 1,
        "repairable": 1,
        "invalid_or_ambiguous": 1,
        "safe_to_supersede": 1,
    }
    assert [item.path for item in report.items] == sorted(item.path for item in report.items)
    assert all(not item.path.startswith("/") for item in report.items)
    assert {path: path.read_bytes() for path in before} == before


def test_scanner_is_deterministic_and_handles_missing_greenagent_dir(tmp_path: Path) -> None:
    first = scan_dbtl_readiness(tmp_path, DbtlConfig())
    second = scan_dbtl_readiness(tmp_path, DbtlConfig())

    assert first == second
    assert first.items == []
    assert first.counts == {
        "compatible": 0,
        "repairable": 0,
        "invalid_or_ambiguous": 0,
        "safe_to_supersede": 0,
    }


def test_phase_zero_vocabulary_is_explicit() -> None:
    assert DBTL_TEST_OUTCOMES == ("supported", "not_supported", "inconclusive", "invalidated")
    assert KNOWLEDGE_CANDIDATE_STATUSES == ("candidate", "promoted", "rejected", "superseded")
