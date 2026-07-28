"""The design a human approved, carried into the work that implements it.

Build, Test, and Learn workers received the datasets, the reconciliation matrix,
and the build/test view — everything except the design those things exist to
serve. So a Build worker was told what data it had and never what study it was
building, and had to re-derive the intent from the cycle title.

Carrying it is not just convenience. The approval bound a specific document by
content hash, and a stage that quietly worked from a *later* design revision
would have escaped the gate that approval represents. So the hash travels with
the text, and only an approved design travels at all.
"""

from __future__ import annotations

import pytest

from deerflow.agents.dbtl.stage_execution import _approved_design_brief


def _cycle(*, design_status: str = "approved", with_artifact: bool = True) -> dict:
    return {
        "id": "cycle-1",
        "project_id": "project-1",
        "stages": [
            {"id": "attempt-design", "stage": "design", "status": design_status},
            {"id": "attempt-build", "stage": "build", "status": "in_progress"},
        ],
        "artifacts": (
            [
                {
                    "id": "art-1",
                    "stage_attempt_id": "attempt-design",
                    "artifact_type": "design_brief",
                    "revision": 1,
                    "content_hash": "a" * 64,
                    "uri": "/mnt/user-data/outputs/dbtl/x/design/review-1.md",
                },
                {
                    "id": "art-2",
                    "stage_attempt_id": "attempt-design",
                    "artifact_type": "design_brief",
                    "revision": 2,
                    "content_hash": "b" * 64,
                    "uri": "/mnt/user-data/outputs/dbtl/x/design/review-2.md",
                },
            ]
            if with_artifact
            else []
        ),
    }


class TestCarryingIt:
    def test_an_approved_design_travels_with_its_hash(self):
        brief = _approved_design_brief(_cycle())

        assert brief is not None
        # The approval bound a document by hash. A downstream stage that named
        # only the path could silently work from a different revision of it.
        assert brief["content_hash"] == "b" * 64
        assert brief["uri"].endswith("review-2.md")

    def test_the_latest_revision_wins(self):
        assert _approved_design_brief(_cycle())["revision"] == 2


class TestRefusingToCarryIt:
    @pytest.mark.parametrize("status", ["in_progress", "awaiting_review", "changes_requested", "locked"])
    def test_an_unapproved_design_does_not_travel(self, status):
        """Build cannot start before Design is approved, but Test can re-run.

        Handing an unapproved design to a later stage would let work proceed
        from something no person has agreed to, which is the gate this whole
        workflow is built around.
        """
        assert _approved_design_brief(_cycle(design_status=status)) is None

    def test_an_approved_stage_with_no_artifact_carries_nothing(self):
        assert _approved_design_brief(_cycle(with_artifact=False)) is None

    def test_a_cycle_with_no_design_stage_is_handled(self):
        assert _approved_design_brief({"stages": [], "artifacts": []}) is None

    def test_a_malformed_cycle_does_not_raise(self):
        # Reached on every Build/Test/Learn request, so it must never be the
        # reason a stage cannot run.
        assert _approved_design_brief({}) is None
        assert _approved_design_brief({"stages": "nonsense", "artifacts": None}) is None

    def test_another_stages_artifact_is_not_mistaken_for_the_design(self):
        cycle = _cycle()
        cycle["artifacts"].append(
            {
                "id": "art-9",
                "stage_attempt_id": "attempt-build",
                "artifact_type": "build_package",
                "revision": 7,
                "content_hash": "c" * 64,
                "uri": "/mnt/user-data/outputs/dbtl/x/build/review-7.md",
            }
        )

        assert _approved_design_brief(cycle)["uri"].endswith("design/review-2.md")
