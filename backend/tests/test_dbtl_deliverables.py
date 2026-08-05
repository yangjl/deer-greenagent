from __future__ import annotations

import json

import pytest

from deerflow.dbtl.cycle_state import CycleClass
from deerflow.dbtl.deliverables import (
    DeliverableManifestRejected,
    parse_deliverable_manifest,
)


def _item(item_id: str = "analysis-report", **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": item_id,
        "title": "Analysis report",
        "kind": "report",
        "required": True,
        "expected_paths": [f"outputs/{item_id}.md"],
        "acceptance_criteria": ["States the result and limitations"],
        "validation": "Open the Markdown and check each required section.",
        "capabilities": ["technical-writing"],
    }
    item.update(overrides)
    return item


def _notebook() -> dict[str, object]:
    return _item(
        "rerun-playbook",
        title="Human rerun playbook",
        kind="notebook",
        expected_paths=["outputs/rerun.ipynb"],
        acceptance_criteria=["Runs linearly from pinned inputs"],
        validation="Open the notebook and run its cells in order.",
        capabilities=["software-development", "data-analysis"],
    )


def _manifest(*items: dict[str, object]) -> dict[str, object]:
    return {"deliverables": list(items)}


def test_parses_one_human_facing_deliverable() -> None:
    parsed = parse_deliverable_manifest(
        _manifest(_item()),
        cycle_class=CycleClass.OTHER,
    )

    assert parsed.as_dict() == {
        "version": 1,
        "cycle_class": "other",
        "deliverables": [
            {
                "id": "analysis-report",
                "title": "Analysis report",
                "kind": "report",
                "required": True,
                "expected_paths": ["outputs/analysis-report.md"],
                "acceptance_criteria": ["States the result and limitations"],
                "validation": "Open the Markdown and check each required section.",
                "capabilities": ["technical-writing"],
            }
        ],
    }


def test_accepts_ten_deliverables_but_rejects_zero_or_eleven() -> None:
    ten = [_item(f"report-{index}") for index in range(10)]
    assert len(parse_deliverable_manifest(_manifest(*ten), cycle_class="other").deliverables) == 10

    with pytest.raises(DeliverableManifestRejected, match="between 1 and 10"):
        parse_deliverable_manifest(_manifest(), cycle_class="other")
    with pytest.raises(DeliverableManifestRejected, match="between 1 and 10"):
        parse_deliverable_manifest(_manifest(*ten, _item("report-10")), cycle_class="other")


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"id": "Not Stable"}, "stable lowercase"),
        ({"title": "  "}, "title"),
        ({"kind": "dashboard"}, "kind"),
        ({"required": "yes"}, "required"),
        ({"expected_paths": []}, "expected_paths"),
        ({"acceptance_criteria": []}, "acceptance_criteria"),
        ({"acceptance_criteria": [" "]}, "acceptance_criteria"),
        ({"validation": ""}, "validation"),
        ({"capabilities": []}, "capabilities"),
        ({"capabilities": [""]}, "capabilities"),
    ],
)
def test_rejects_incomplete_or_unsupported_deliverable_fields(
    override: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(DeliverableManifestRejected, match=message):
        parse_deliverable_manifest(_manifest(_item(**override)), cycle_class="other")


def test_accepts_every_initial_kind() -> None:
    kinds = (
        "figure",
        "table",
        "image",
        "equation",
        "software",
        "notebook",
        "report",
        "dataset",
        "other",
    )
    items = [
        _item(
            f"item-{kind}",
            kind=kind,
            expected_paths=[f"outputs/item-{kind}.ipynb" if kind == "notebook" else f"outputs/item-{kind}.txt"],
        )
        for kind in kinds
    ]

    parsed = parse_deliverable_manifest(_manifest(*items), cycle_class="other")

    assert [item.kind.value for item in parsed.deliverables] == list(kinds)


def test_rejects_duplicate_deliverable_ids() -> None:
    with pytest.raises(DeliverableManifestRejected, match="duplicate.*same-id"):
        parse_deliverable_manifest(
            _manifest(_item("same-id"), _item("same-id")),
            cycle_class="other",
        )


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/tmp/result.png",
        "../result.png",
        "outputs/../../result.png",
        r"C:\results\result.png",
        "~/result.png",
        "outputs//result.png",
        "outputs/./result.png",
        "https://example.com/result.png",
        "outputs/result.png\x00ignored",
    ],
)
def test_rejects_unsafe_expected_paths(unsafe_path: str) -> None:
    with pytest.raises(DeliverableManifestRejected, match="safe project-relative"):
        parse_deliverable_manifest(
            _manifest(_item(expected_paths=[unsafe_path])),
            cycle_class="other",
        )


def test_computational_cycle_requires_exactly_one_notebook() -> None:
    with pytest.raises(DeliverableManifestRejected, match="exactly one.*notebook"):
        parse_deliverable_manifest(_manifest(_item()), cycle_class="computational")

    parsed = parse_deliverable_manifest(
        _manifest(_item(), _notebook()),
        cycle_class="computational",
    )
    assert parsed.notebook.id == "rerun-playbook"

    with pytest.raises(DeliverableManifestRejected, match="exactly one.*notebook"):
        parse_deliverable_manifest(
            _manifest(
                _notebook(),
                _item(
                    "second-notebook",
                    kind="notebook",
                    expected_paths=["outputs/second.Rmd"],
                ),
            ),
            cycle_class="computational",
        )


@pytest.mark.parametrize("path", ["outputs/playbook.ipynb", "outputs/playbook.Rmd"])
def test_computational_notebook_accepts_both_supported_formats(path: str) -> None:
    parsed = parse_deliverable_manifest(
        _manifest(_item(), _notebook() | {"expected_paths": [path]}),
        cycle_class="computational",
    )

    assert parsed.notebook.expected_paths == (path,)


def test_rejects_notebook_with_no_supported_notebook_path() -> None:
    with pytest.raises(DeliverableManifestRejected, match=r"\.ipynb or \.Rmd"):
        parse_deliverable_manifest(
            _manifest(_notebook() | {"expected_paths": ["outputs/playbook.py"]}),
            cycle_class="computational",
        )


def test_canonical_json_and_hash_are_stable_across_mapping_order_and_whitespace() -> None:
    first_item = _item()
    second_item = {
        "capabilities": [" technical-writing "],
        "validation": " Open the Markdown and check each required section. ",
        "acceptance_criteria": [" States the result and limitations "],
        "expected_paths": ["outputs/analysis-report.md"],
        "required": True,
        "kind": "report",
        "title": " Analysis report ",
        "id": "analysis-report",
    }

    first = parse_deliverable_manifest(_manifest(first_item), cycle_class="other")
    second = parse_deliverable_manifest(_manifest(second_item), cycle_class=CycleClass.OTHER)

    assert first.canonical_json == second.canonical_json
    assert json.loads(first.canonical_json) == first.as_dict()
    assert first.content_hash == second.content_hash
    assert len(first.content_hash) == 64


def test_rejects_malformed_manifest_and_cycle_class() -> None:
    with pytest.raises(DeliverableManifestRejected, match="object"):
        parse_deliverable_manifest([], cycle_class="other")
    with pytest.raises(DeliverableManifestRejected, match="cycle_class"):
        parse_deliverable_manifest(_manifest(_item()), cycle_class="wet-lab")
