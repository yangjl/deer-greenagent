"""`load_design` is deterministic, and it refuses before it dispatches.

The failure this covers is expensive and quiet: a Build worker used to be handed
the cycle and asked to find its own design, so a wrong guess produced plausible
work against the wrong plan and nothing downstream could tell. These tests pin
the two properties that make the guess unnecessary — the resolution is the
server's, and every way it can go wrong stops the Build *before* a worker runs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from deerflow.agents.dbtl.live_stage.design_input import approved_design_artifact, resolve_build_inputs
from deerflow.dbtl.build_input import BuildInputBundle, BuildInputError, BundleInput, BundleInputKind, bounded_excerpt, dataset_inputs
from deerflow.dbtl.build_workflow import BuildErrorCode

DESIGN_BODY = "# Approved design\n\nFit a genomic prediction model on the 2024 trial.\n"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _project(tmp_path: Path, *, body: str = DESIGN_BODY) -> tuple[str, str]:
    """Write a design package into a project tree; return (root, uri)."""
    outputs = tmp_path / "outputs" / "dbtl" / "cycle-1" / "design"
    outputs.mkdir(parents=True)
    document = outputs / "design-rev1.md"
    document.write_text(body, encoding="utf-8")
    relative = document.relative_to(tmp_path).as_posix()
    return str(tmp_path), f"/mnt/user-data/{relative}"


def _cycle(*, uri: str, content_hash: str, status: str = "approved", revision: int = 1) -> dict[str, Any]:
    return {
        "id": "cycle-1",
        "title": "Cycle 01",
        "stages": [{"stage": "design", "id": "stage-design", "status": status}],
        "artifacts": [
            {
                "stage_attempt_id": "stage-design",
                "uri": uri,
                "content_hash": content_hash,
                "revision": revision,
                "artifact_type": "design_brief.v2",
            }
        ],
    }


class TestTheBundleIsCheckableByConstruction:
    def test_a_design_that_is_not_hash_bound_is_not_a_bundle(self) -> None:
        with pytest.raises(ValueError, match="SHA-256"):
            BuildInputBundle(
                design=BundleInput(kind=BundleInputKind.APPROVED_ARTIFACT, reference="x.md", content_hash="not-a-hash"),
                design_revision=1,
            )

    def test_only_an_approved_artifact_may_be_the_design(self) -> None:
        with pytest.raises(ValueError, match="approved artifact"):
            BuildInputBundle(
                design=BundleInput(kind=BundleInputKind.WORKSPACE_INPUT, reference="x.md", content_hash=_hash("x")),
                design_revision=1,
            )

    def test_a_truncated_excerpt_does_not_move_the_digest(self) -> None:
        """The excerpt is a projection of a document already bound by hash.

        Letting a display boundary change the identity would invalidate a
        perfectly good plan the next time the cap moved.
        """
        design = BundleInput(kind=BundleInputKind.APPROVED_ARTIFACT, reference="d.md", content_hash=_hash(DESIGN_BODY))
        full = BuildInputBundle(design=design, design_revision=1, design_text=DESIGN_BODY)
        clipped = BuildInputBundle(design=design, design_revision=1, design_text=DESIGN_BODY[:10], design_truncated=True)
        assert full.digest == clipped.digest

    def test_a_different_design_revision_is_a_different_bundle(self) -> None:
        design = BundleInput(kind=BundleInputKind.APPROVED_ARTIFACT, reference="d.md", content_hash=_hash(DESIGN_BODY))
        assert BuildInputBundle(design=design, design_revision=1).digest != BuildInputBundle(design=design, design_revision=2).digest

    def test_an_uncheckable_dataset_declaration_is_not_carried(self) -> None:
        """A claim whose whole value is that it is checkable, and is not."""
        assert dataset_inputs([{"source_key": "trial", "content_hash": "short"}]) == ()
        assert dataset_inputs([{"source_key": "", "content_hash": _hash("a")}]) == ()
        (bound,) = dataset_inputs([{"source_key": "trial", "content_hash": _hash("a"), "uri": "/mnt/user-data/t.csv"}])
        assert bound.kind is BundleInputKind.DATASET
        assert bound.reference == "dataset:trial"

    def test_truncation_is_reported_rather_than_silent(self) -> None:
        text, truncated = bounded_excerpt("abcdef", limit=3)
        assert (text, truncated) == ("abc", True)
        assert bounded_excerpt("ab", limit=3) == ("ab", False)


class TestResolutionSucceeds:
    def test_the_approved_design_is_read_and_bound(self, tmp_path: Path) -> None:
        root, uri = _project(tmp_path)
        bundle = resolve_build_inputs(
            _cycle(uri=uri, content_hash=_hash(DESIGN_BODY)),
            project_root=root,
            datasets=[{"source_key": "trial", "content_hash": _hash("a")}],
            manifest=[{"path": "/mnt/user-data/data.csv", "kind": "file", "size_bytes": 12}],
            policy={"reconciliation_required": False},
        )
        assert bundle.design.reference == uri
        assert bundle.design.content_hash == _hash(DESIGN_BODY)
        assert bundle.design_text == DESIGN_BODY
        assert not bundle.design_truncated
        assert [item.reference for item in bundle.inputs] == ["dataset:trial"]
        assert bundle.policy["reconciliation_required"] is False

    def test_the_newest_revision_of_the_approved_attempt_wins(self, tmp_path: Path) -> None:
        root, uri = _project(tmp_path)
        cycle = _cycle(uri=uri, content_hash=_hash(DESIGN_BODY), revision=3)
        cycle["artifacts"].insert(0, {"stage_attempt_id": "stage-design", "uri": "old.md", "content_hash": _hash("old"), "revision": 2})
        assert resolve_build_inputs(cycle, project_root=root, datasets=[], manifest=[], policy={}).design_revision == 3

    def test_a_long_design_is_clipped_and_says_so(self, tmp_path: Path) -> None:
        body = "x" * 100
        root, uri = _project(tmp_path, body=body)
        bundle = resolve_build_inputs(
            _cycle(uri=uri, content_hash=_hash(body)),
            project_root=root,
            datasets=[],
            manifest=[],
            policy={},
            excerpt_limit=10,
        )
        assert bundle.design_truncated
        assert bundle.design_text == "x" * 10
        # The binding is to the whole document, not to the part that was shown.
        assert bundle.design.content_hash == _hash(body)


class TestResolutionRefusesBeforeAnyWorkerRuns:
    def test_no_approved_design_is_refused(self, tmp_path: Path) -> None:
        root, uri = _project(tmp_path)
        with pytest.raises(BuildInputError) as caught:
            resolve_build_inputs(_cycle(uri=uri, content_hash=_hash(DESIGN_BODY), status="in_progress"), project_root=root, datasets=[], manifest=[], policy={})
        assert caught.value.code is BuildErrorCode.DESIGN_MISSING_OR_STALE

    def test_an_approved_design_with_no_artifact_is_refused(self, tmp_path: Path) -> None:
        root, _uri = _project(tmp_path)
        cycle = _cycle(uri="", content_hash="")
        cycle["artifacts"] = []
        with pytest.raises(BuildInputError) as caught:
            resolve_build_inputs(cycle, project_root=root, datasets=[], manifest=[], policy={})
        assert caught.value.code is BuildErrorCode.DESIGN_MISSING_OR_STALE

    def test_a_hash_mismatch_is_stale_not_unreadable(self, tmp_path: Path) -> None:
        """The document moved out from under the approval; that is a governance
        failure, not an IO failure, and the code has to say which."""
        root, uri = _project(tmp_path)
        with pytest.raises(BuildInputError) as caught:
            resolve_build_inputs(_cycle(uri=uri, content_hash=_hash("something else entirely")), project_root=root, datasets=[], manifest=[], policy={})
        assert caught.value.code is BuildErrorCode.DESIGN_MISSING_OR_STALE

    def test_a_missing_file_is_unreadable(self, tmp_path: Path) -> None:
        root, uri = _project(tmp_path)
        Path(uri.replace("/mnt/user-data", root)).unlink()
        with pytest.raises(BuildInputError) as caught:
            resolve_build_inputs(_cycle(uri=uri, content_hash=_hash(DESIGN_BODY)), project_root=root, datasets=[], manifest=[], policy={})
        assert caught.value.code is BuildErrorCode.DESIGN_UNREADABLE

    def test_a_reference_outside_the_project_is_unreadable(self, tmp_path: Path) -> None:
        root, _uri = _project(tmp_path)
        with pytest.raises(BuildInputError) as caught:
            resolve_build_inputs(_cycle(uri="/etc/passwd", content_hash=_hash(DESIGN_BODY)), project_root=root, datasets=[], manifest=[], policy={})
        assert caught.value.code is BuildErrorCode.DESIGN_UNREADABLE

    def test_an_artifact_with_no_usable_hash_is_refused(self, tmp_path: Path) -> None:
        root, uri = _project(tmp_path)
        with pytest.raises(BuildInputError) as caught:
            resolve_build_inputs(_cycle(uri=uri, content_hash="TODO"), project_root=root, datasets=[], manifest=[], policy={})
        assert caught.value.code is BuildErrorCode.DESIGN_MISSING_OR_STALE


class TestTheApprovedArtifactLookupIsShared:
    def test_the_adapter_and_the_step_resolve_the_same_artifact(self, tmp_path: Path) -> None:
        """One lookup, two callers.

        The Build brief the model is shown and the document `load_design` binds
        must be the same file, or a reviewer's approval names one thing and the
        worker implements another.
        """
        from deerflow.agents.dbtl.live_stage.adapter import _approved_design_brief

        root, uri = _project(tmp_path)
        cycle = _cycle(uri=uri, content_hash=_hash(DESIGN_BODY))
        brief = _approved_design_brief(cycle)
        artifact = approved_design_artifact(cycle)
        assert brief is not None and artifact is not None
        assert brief["uri"] == artifact["uri"] == uri
        assert resolve_build_inputs(cycle, project_root=root, datasets=[], manifest=[], policy={}).design.reference == brief["uri"]
