"""Phase 3A: the Test stage registers a review page of its own.

Until now only Design produced a deck, so the convening decision had nowhere to
appear — a Test stage finished, wrote a package, and offered a person nothing to
open. This is the pre-meeting surface the plan calls for: rendered from the
stage's own evidence, because no meeting has happened yet.

The rule that matters most here is what it must *not* carry. A Test outcome is
computed at review time from the validity pack, so a deck rendered before that
review must offer no route menu at all — a menu here would pre-empt the outcome
computation, which is the one thing Phase 7 forbids.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.stage_execution import LiveStageAdapter
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_runner import DispatchOutcome


def _cycle() -> dict:
    return {
        "id": "cycle-1",
        "project_id": "project-1",
        "state": "test",
        "db_revision": 6,
        "title": "Drought yield",
        "research_question": "Which hybrids retain yield under drought?",
        "objective": "Rank the hybrids.",
        "success_criteria": "Held-out-site rank correlation exceeds 0.4.",
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
    def __init__(self) -> None:
        self.cycle = _cycle()
        self.surfaces: list[dict] = []
        self.recorded: list[dict] = []

    async def get_cycle(self, cycle_id: str, *, project_id: str):
        return dict(self.cycle)

    async def list_datasets(self, cycle_id: str, *, project_id: str):
        return []

    async def reconciliation_view(self, cycle_id: str, *, project_id: str):
        return {}

    async def build_test_view(self, cycle_id: str, *, project_id: str):
        return {"cycle_id": cycle_id, "build_lineage": None, "validity_assessment": None}

    async def list_worker_runs(self, cycle_id: str, *, project_id: str, stage: str):
        return []

    async def list_activity(self, cycle_id: str, *, project_id: str):
        return []

    async def get_stage_execution_replay(self, cycle_id: str, *, project_id: str, idempotency_key: str):
        return None

    async def record_worker_runs(self, **kwargs):
        self.recorded.append(kwargs)
        self.cycle["db_revision"] += 1
        # The artifact the deck binds to has to exist on the cycle by the time
        # the surface is planned, exactly as the production repository does.
        if kwargs.get("artifact_uri"):
            self.cycle["artifacts"] = [
                {
                    "id": "artifact-test-1",
                    "revision": 1,
                    "uri": kwargs["artifact_uri"],
                    "content_hash": kwargs["artifact_content_hash"],
                    "stage": "test",
                }
            ]
        return kwargs["results"]

    async def register_stage_feedback_surface(self, **kwargs):
        self.surfaces.append(kwargs)
        return {"surface_id": kwargs["surface_id"], **kwargs}


class _Dispatcher:
    async def __call__(self, units, *, budget):
        payload = json.dumps(
            {
                "status": "completed",
                "summary": "Held-out rank correlation 0.46 across 20 replicates.",
                "claims": ["No family crossed the train/test split."],
                "evidence_refs": [{"kind": "workspace_file", "reference": "/mnt/user-data/outputs/validity.json"}],
                "limitations": [],
                "quality_checks": [{"name": "no leakage", "passed": True, "detail": ""}],
                "recommended_next_actions": ["Review the validity pack."],
                "provenance": {"inputs_examined": ["validity pack"]},
            }
        )
        return [DispatchOutcome(unit_id=unit.unit_id, text=payload) for unit in units]


def _adapter(repo, *, progressive_gate: bool = True) -> LiveStageAdapter:
    async def assessor(_prompt: str) -> str:
        return json.dumps(
            {
                "difficulty": "high_stakes",
                "rationale": "A headline metric this close to the threshold needs argument.",
            }
        )

    return LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(dbtl=SimpleNamespace(progressive_gate=progressive_gate)),
        candidate_provider=lambda: (AgentCandidate(name="analyst", capabilities=frozenset({Capability.VALIDITY_ASSESSMENT, Capability.STATISTICAL_ANALYSIS})),),
        dispatcher=_Dispatcher(),
        transition_assessor=assessor,
    )


async def _run(repo, tmp_path: Path, *, progressive_gate: bool = True):
    return await _adapter(repo, progressive_gate=progressive_gate).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Run the validity checks.",
        state={},
        config={
            "configurable": {"thread_id": "thread-1", "run_id": "run-1"},
            "context": {"user_id": "user-1", "project_root": str(tmp_path)},
            "metadata": {},
        },
    )


class TestTheTestStageGetsAReviewPage:
    @pytest.mark.asyncio
    async def test_a_test_surface_is_registered_against_the_test_attempt(self, tmp_path: Path):
        repo = _Repo()

        result = await _run(repo, tmp_path)

        assert result.produced_usable_evidence
        assert repo.surfaces, "Test produced evidence but registered no review surface"
        surface = repo.surfaces[0]
        assert surface["stage"] == "test"
        assert surface["stage_attempt_id"] == "attempt-test"
        assert surface["mode"] == "stage_review"

    @pytest.mark.asyncio
    async def test_the_deck_is_written_under_the_test_stage(self, tmp_path: Path):
        repo = _Repo()

        result = await _run(repo, tmp_path)

        assert result.deck_uri
        assert "/test/" in result.deck_uri
        assert "test-slides" in result.deck_uri


class TestItCarriesAnAssessmentButNoRoutes:
    @pytest.mark.asyncio
    async def test_the_assessment_travels_so_the_meeting_gate_can_read_it(self, tmp_path: Path):
        repo = _Repo()

        await _run(repo, tmp_path)

        gate = repo.surfaces[0]["decision_request"]["transition_gate"]
        assert gate["stage"] == "test"
        assert gate["assessment"]["difficulty"] == "high_stakes"

    @pytest.mark.asyncio
    async def test_no_route_menu_is_offered_before_the_outcome_is_computed(self, tmp_path: Path):
        """A Test outcome is computed at review time from the validity pack. A
        route menu rendered before that would pre-empt the computation."""
        repo = _Repo()

        await _run(repo, tmp_path)

        assert repo.surfaces[0]["decision_request"]["transition_gate"]["routes"] == []

    @pytest.mark.asyncio
    async def test_the_flag_being_off_still_registers_the_page_without_a_gate(self, tmp_path: Path):
        repo = _Repo()

        await _run(repo, tmp_path, progressive_gate=False)

        assert repo.surfaces, "the review page is not the progressive gate and must not depend on it"
        assert repo.surfaces[0]["decision_request"] is None
