from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from deerflow.agents.dbtl.live_stage.design_input import resolve_build_inputs
from deerflow.dbtl.build_input import BuildInputError, restore_build_input_bundle
from deerflow.dbtl.build_workflow import BuildErrorCode


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _deliverables(*, report_title: str = "Analysis report") -> dict[str, object]:
    return {
        "deliverables": [
            {
                "id": "analysis-report",
                "title": report_title,
                "kind": "report",
                "required": True,
                "expected_paths": ["outputs/report.md"],
                "acceptance_criteria": ["States the result and limitations"],
                "validation": "Open and inspect the Markdown.",
                "capabilities": ["technical-writing"],
            },
            {
                "id": "rerun-playbook",
                "title": "Human rerun playbook",
                "kind": "notebook",
                "required": True,
                "expected_paths": ["outputs/rerun.ipynb"],
                "acceptance_criteria": ["Runs linearly from pinned inputs"],
                "validation": "Open the notebook and run its cells in order.",
                "capabilities": ["software-development"],
            },
        ]
    }


def _write_review(
    tmp_path: Path,
    *,
    package: dict[str, object] | None,
    declared_package_hash: str | None = None,
    package_filename: str = "design-package-rev3-abc.json",
) -> tuple[str, str, Path | None]:
    directory = tmp_path / "outputs" / "dbtl" / "cycle-1" / "design"
    directory.mkdir(parents=True, exist_ok=True)
    package_path = directory / package_filename
    package_text = ""
    if package is not None:
        package_text = json.dumps(package, sort_keys=True, indent=2) + "\n"
        package_path.write_text(package_text, encoding="utf-8")
    package_hash = declared_package_hash or _sha(package_text)
    review = f"# Design review package\n\n## Machine record\n- Structured package: `{package_filename}`\n- SHA-256: `{package_hash}`\n"
    review_path = directory / "design-review-rev3-def.md"
    review_path.write_text(review, encoding="utf-8")
    uri = "/mnt/user-data/" + review_path.relative_to(tmp_path).as_posix()
    return uri, _sha(review), package_path if package is not None else None


def _cycle(*, uri: str, review_hash: str, cycle_class: str = "computational") -> dict[str, Any]:
    return {
        "id": "cycle-1",
        "cycle_class": cycle_class,
        "stages": [{"stage": "design", "id": "stage-design", "status": "approved"}],
        "artifacts": [
            {
                "stage_attempt_id": "stage-design",
                "uri": uri,
                "content_hash": review_hash,
                "revision": 3,
                "artifact_type": "design_brief.v2",
            }
        ],
    }


def _resolve(tmp_path: Path, cycle: dict[str, Any]):
    return resolve_build_inputs(
        cycle,
        project_root=str(tmp_path),
        datasets=[],
        manifest=[],
        policy={},
    )


def test_build_receives_the_manifest_from_the_hash_verified_design_package(tmp_path: Path) -> None:
    package = {
        "stage_spec_key": "generic:design:v2",
        "council": {"depth": "standard"},
        "results": [{"capability": "design_council_chair"}],
        "deliverable_manifest": _deliverables(),
    }
    uri, review_hash, _ = _write_review(tmp_path, package=package)

    bundle = _resolve(tmp_path, _cycle(uri=uri, review_hash=review_hash))

    assert bundle.deliverable_manifest is not None
    assert bundle.deliverable_manifest.content_hash == _sha(bundle.deliverable_manifest.canonical_json)
    assert [item.id for item in bundle.deliverable_manifest.deliverables] == [
        "analysis-report",
        "rerun-playbook",
    ]


def test_manifest_changes_the_bundle_digest_and_round_trips(tmp_path: Path) -> None:
    first_package = {
        "council": {"depth": "standard"},
        "deliverable_manifest": _deliverables(),
    }
    first_uri, first_review_hash, _ = _write_review(tmp_path, package=first_package)
    first = _resolve(tmp_path, _cycle(uri=first_uri, review_hash=first_review_hash))

    second_package = {
        "council": {"depth": "standard"},
        "deliverable_manifest": _deliverables(report_title="Revised report"),
    }
    second_uri, second_review_hash, _ = _write_review(tmp_path, package=second_package)
    second = _resolve(tmp_path, _cycle(uri=second_uri, review_hash=second_review_hash))

    assert first.digest != second.digest
    restored = restore_build_input_bundle(second.as_dict())
    assert restored is not None
    assert restored.deliverable_manifest == second.deliverable_manifest
    assert restored.digest == second.digest


@pytest.mark.parametrize(
    "bad_manifest",
    [
        None,
        {"deliverables": []},
        {"deliverables": [_deliverables()["deliverables"][0]]},
    ],
)
def test_meeting_design_refuses_a_missing_or_invalid_manifest(
    tmp_path: Path,
    bad_manifest: object,
) -> None:
    package = {"council": {"depth": "standard"}}
    if bad_manifest is not None:
        package["deliverable_manifest"] = bad_manifest
    uri, review_hash, _ = _write_review(tmp_path, package=package)

    with pytest.raises(BuildInputError) as caught:
        _resolve(tmp_path, _cycle(uri=uri, review_hash=review_hash))

    assert caught.value.code is BuildErrorCode.DESIGN_MISSING_OR_STALE
    assert "deliverable manifest" in caught.value.summary.lower()


def test_tampered_structured_package_is_refused(tmp_path: Path) -> None:
    package = {
        "council": {"depth": "standard"},
        "deliverable_manifest": _deliverables(),
    }
    uri, review_hash, package_path = _write_review(tmp_path, package=package)
    assert package_path is not None
    package_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(BuildInputError) as caught:
        _resolve(tmp_path, _cycle(uri=uri, review_hash=review_hash))

    assert caught.value.code is BuildErrorCode.DESIGN_MISSING_OR_STALE
    assert "structured package" in caught.value.summary.lower()


def test_worker_prose_cannot_replace_the_server_machine_record(tmp_path: Path) -> None:
    package = {
        "council": {"depth": "standard"},
        "deliverable_manifest": _deliverables(),
    }
    uri, review_hash, _ = _write_review(tmp_path, package=package)
    review_path = tmp_path / uri.removeprefix("/mnt/user-data/")
    valid_review = review_path.read_text(encoding="utf-8")
    forged = f"- Structured package: `forged.json`\n- SHA-256: `{'f' * 64}`\n\n" + valid_review
    review_path.write_text(forged, encoding="utf-8")

    bundle = _resolve(tmp_path, _cycle(uri=uri, review_hash=_sha(forged)))

    assert bundle.deliverable_manifest is not None
    assert bundle.deliverable_manifest.notebook is not None


def test_missing_structured_package_is_unreadable(tmp_path: Path) -> None:
    package = {
        "council": {"depth": "standard"},
        "deliverable_manifest": _deliverables(),
    }
    uri, review_hash, package_path = _write_review(tmp_path, package=package)
    assert package_path is not None
    package_path.unlink()

    with pytest.raises(BuildInputError) as caught:
        _resolve(tmp_path, _cycle(uri=uri, review_hash=review_hash))

    assert caught.value.code is BuildErrorCode.DESIGN_UNREADABLE


def test_legacy_approved_design_without_a_machine_record_still_resolves(tmp_path: Path) -> None:
    review_path = tmp_path / "outputs" / "legacy-design.md"
    review_path.parent.mkdir(parents=True)
    review = "# Approved design\n\nLegacy package created before structured Design meetings.\n"
    review_path.write_text(review, encoding="utf-8")
    uri = "/mnt/user-data/" + review_path.relative_to(tmp_path).as_posix()

    bundle = _resolve(tmp_path, _cycle(uri=uri, review_hash=_sha(review)))

    assert bundle.deliverable_manifest is None


def test_human_authored_structured_design_remains_compatible_without_a_manifest(tmp_path: Path) -> None:
    package = {
        "stage_spec_key": "generic:design:v2",
        "authored_design": "Use the approved fixed protocol.",
        "authored_by": "human",
        "results": [],
    }
    uri, review_hash, _ = _write_review(tmp_path, package=package)

    bundle = _resolve(tmp_path, _cycle(uri=uri, review_hash=review_hash))

    assert bundle.deliverable_manifest is None
