"""Progressive-gate Phase 3: a feedback surface belongs to a stage, not to Design.

Every review meeting (Build, Test, Learn) reuses the deck and feedback-surface
machinery the Design council already has. That machinery hardcoded ``"design"``
in three places — the stage attempt it bound to, the stage it registered under,
and the directory it wrote into — so a Test meeting could not have registered a
surface at all.

Two properties matter more than the generalization itself. A surface must bind
the attempt of *its own* stage, because a Test verdict recorded against the
Design attempt is a verdict on the wrong document. And Design must come through
byte-identical: the surface id is derived so a retried turn re-registers onto
the same row, and captured Design decks in the manual-test scenarios carry ids
computed by the old code.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.stage_execution import LiveStageAdapter


def _cycle() -> dict:
    return {
        "id": "cycle-1",
        "project_id": "project-1",
        "state": "test",
        "db_revision": 7,
        "title": "Drought yield",
        "stages": [
            {"id": "attempt-design", "stage": "design", "status": "approved"},
            {"id": "attempt-reconciliation", "stage": "reconciliation", "status": "approved"},
            {"id": "attempt-build", "stage": "build", "status": "approved"},
            {"id": "attempt-test", "stage": "test", "status": "in_progress"},
            {"id": "attempt-learn", "stage": "learn", "status": "locked"},
        ],
        "artifacts": [],
    }


class _Repo:
    """Only the two calls surface planning and registration actually make."""

    def __init__(self, cycle: dict | None = None) -> None:
        self.cycle = cycle if cycle is not None else _cycle()
        self.registered: list[dict] = []

    async def get_cycle(self, cycle_id: str, *, project_id: str):
        if self.cycle is None or cycle_id != self.cycle["id"] or project_id != self.cycle["project_id"]:
            return None
        return dict(self.cycle)

    async def register_stage_feedback_surface(self, **kwargs):
        self.registered.append(kwargs)
        return {"surface_id": kwargs["surface_id"], **kwargs}

    async def register_design_feedback_surface(self, **kwargs):  # pragma: no cover - legacy path
        return await self.register_stage_feedback_surface(stage="design", **kwargs)


def _adapter(repo: _Repo) -> LiveStageAdapter:
    return LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (),
        dispatcher=None,
    )


def _expected_surface_id(execution_key: str, mode: str, round_number: int) -> str:
    digest = hashlib.sha256("\x1f".join((execution_key, mode, str(round_number))).encode("utf-8")).hexdigest()
    return f"dfs-{digest[:32]}"


class TestASurfaceBindsItsOwnStagesAttempt:
    @pytest.mark.anyio
    async def test_a_test_surface_binds_the_test_attempt(self):
        repo = _Repo()
        plan = await _adapter(repo)._plan_feedback_surface(
            stage="test",
            cycle_id="cycle-1",
            project_id="project-1",
            execution_key="dbtl-stage:run-1:cycle-1",
            round_number=1,
            originating_thread_id="thread-1",
            paused=True,
            artifact_uri="",
            artifact_hash="",
        )

        assert plan is not None
        assert plan.stage == "test"
        assert plan.stage_attempt_id == "attempt-test"

    @pytest.mark.anyio
    async def test_a_stage_with_no_attempt_row_registers_nothing(self):
        cycle = _cycle()
        cycle["stages"] = [item for item in cycle["stages"] if item["stage"] != "test"]
        plan = await _adapter(_Repo(cycle))._plan_feedback_surface(
            stage="test",
            cycle_id="cycle-1",
            project_id="project-1",
            execution_key="dbtl-stage:run-1:cycle-1",
            round_number=1,
            originating_thread_id="thread-1",
            paused=True,
            artifact_uri="",
            artifact_hash="",
        )

        assert plan is None

    @pytest.mark.anyio
    async def test_design_planning_is_unchanged(self):
        """The derived id is what makes a retry idempotent; it must not move."""
        repo = _Repo()
        plan = await _adapter(repo)._plan_feedback_surface(
            stage="design",
            cycle_id="cycle-1",
            project_id="project-1",
            execution_key="dbtl-stage:run-1:cycle-1",
            round_number=2,
            originating_thread_id="thread-1",
            paused=True,
            artifact_uri="",
            artifact_hash="",
        )

        assert plan is not None
        assert plan.stage == "design"
        assert plan.stage_attempt_id == "attempt-design"
        assert plan.surface_id == _expected_surface_id("dbtl-stage:run-1:cycle-1", "chair_feedback", 2)


class TestRegistrationCarriesTheStage:
    @pytest.mark.anyio
    async def test_the_registered_row_records_the_surfaces_own_stage(self):
        repo = _Repo()
        adapter = _adapter(repo)
        plan = await adapter._plan_feedback_surface(
            stage="test",
            cycle_id="cycle-1",
            project_id="project-1",
            execution_key="dbtl-stage:run-1:cycle-1",
            round_number=1,
            originating_thread_id="thread-1",
            paused=True,
            artifact_uri="",
            artifact_hash="",
        )
        assert plan is not None

        await adapter._register_feedback_surface(
            plan,
            SimpleNamespace(uri="/mnt/user-data/outputs/test-slides.html", content_hash="d" * 64),
            cycle_id="cycle-1",
            project_id="project-1",
        )

        assert repo.registered[0]["stage"] == "test"
        assert repo.registered[0]["stage_attempt_id"] == "attempt-test"
        # The repository column is still named for Design during the
        # compatibility window; the value is the round of whatever stage this is.
        assert repo.registered[0]["design_round"] == 1
