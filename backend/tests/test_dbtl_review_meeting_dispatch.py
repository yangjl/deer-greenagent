"""Phase 3A: convening the Test validity meeting actually runs one.

The pre-meeting page shipped first, so a person could see the gate and press
nothing. This is the other half: a request that convenes the meeting dispatches
a real debate over the stage's *own recorded evidence*, records it, and replaces
the pre-meeting deck with one carrying the argument.

Two rules carry the weight here. The meeting is a **reader**, not a re-run: it
never re-executes the stage, so a Test attempt awaiting review is exactly when it
is allowed to happen. And its output is attached review evidence — it cannot
restate the computed outcome, which is what `sanitize_meeting_attachment`
enforces and what the last class checks reaches the recorded package.
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

PACKAGE_URI = "/mnt/user-data/outputs/dbtl/cycle-1/test/test-report-rev1-abc.md"
PACKAGE_HASH = "d" * 64


def _cycle(*, test_status: str = "awaiting_review") -> dict:
    return {
        "id": "cycle-1",
        "project_id": "project-1",
        "state": "test",
        "db_revision": 9,
        "title": "Drought yield",
        "research_question": "Which hybrids retain yield under drought?",
        "objective": "Rank the hybrids.",
        "success_criteria": "Held-out-site rank correlation exceeds 0.4.",
        "stages": [
            {"id": "attempt-design", "stage": "design", "status": "approved"},
            {"id": "attempt-reconciliation", "stage": "reconciliation", "status": "approved"},
            {"id": "attempt-build", "stage": "build", "status": "approved"},
            {"id": "attempt-test", "stage": "test", "status": test_status},
            {"id": "attempt-learn", "stage": "learn", "status": "locked"},
        ],
        "artifacts": [
            {
                "id": "artifact-test-1",
                "stage_attempt_id": "attempt-test",
                "artifact_type": "test_report",
                "revision": 1,
                "uri": PACKAGE_URI,
                "content_hash": PACKAGE_HASH,
            }
        ],
    }


class _Repo:
    def __init__(self, *, test_status: str = "awaiting_review") -> None:
        self.cycle = _cycle(test_status=test_status)
        self.surfaces: list[dict] = []
        self.recorded: list[dict] = []

    async def get_cycle(self, cycle_id: str, *, project_id: str):
        return dict(self.cycle)

    async def list_datasets(self, cycle_id: str, *, project_id: str):
        return []

    async def reconciliation_view(self, cycle_id: str, *, project_id: str):
        return {}

    async def build_test_view(self, cycle_id: str, *, project_id: str):
        return {
            "cycle_id": cycle_id,
            "build_lineage": None,
            "validity_assessment": {"outcome": "supported", "recommendation": "advance_to_learn"},
        }

    async def list_worker_runs(self, cycle_id: str, *, project_id: str, stage: str):
        return []

    async def list_activity(self, cycle_id: str, *, project_id: str):
        return []

    async def get_stage_execution_replay(self, cycle_id: str, *, project_id: str, idempotency_key: str):
        return None

    async def record_worker_runs(self, **kwargs):
        self.recorded.append(kwargs)
        self.cycle["db_revision"] += 1
        if kwargs.get("artifact_uri"):
            self.cycle["artifacts"] = [
                *self.cycle["artifacts"],
                {
                    "id": "artifact-meeting-1",
                    "stage_attempt_id": "attempt-test",
                    "artifact_type": kwargs["artifact_type"],
                    "revision": 1,
                    "uri": kwargs["artifact_uri"],
                    "content_hash": kwargs["artifact_content_hash"],
                },
            ]
        return kwargs["results"]

    async def register_stage_feedback_surface(self, **kwargs):
        self.surfaces.append(kwargs)
        return {"surface_id": kwargs["surface_id"], **kwargs}


class _Dispatcher:
    """Answers every seat with a contract-valid report, recording what it saw."""

    def __init__(self) -> None:
        self.units: list = []

    async def __call__(self, units, *, budget):
        self.units.extend(units)
        outcomes = []
        for unit in units:
            payload = json.dumps(
                {
                    "status": "completed",
                    "summary": f"{unit.role} read the validity pack.",
                    "claims": ["The held-out split is family-disjoint."],
                    "evidence_refs": [{"kind": "workspace_file", "reference": PACKAGE_URI}],
                    "limitations": [],
                    "quality_checks": [{"name": "folds inspected", "passed": True, "detail": ""}],
                    "recommended_next_actions": ["Record a validity decision."],
                    "provenance": {"inputs_examined": [PACKAGE_URI]},
                    # A meeting may not restate what the validity pack computes.
                    "computed_outcome": "invalidated",
                }
            )
            outcomes.append(DispatchOutcome(unit_id=unit.unit_id, text=payload))
        return outcomes


def _adapter(repo, dispatcher) -> LiveStageAdapter:
    async def assessor(_prompt: str) -> str:
        return json.dumps({"difficulty": "high_stakes", "rationale": "The metric sits near the threshold."})

    return LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(dbtl=SimpleNamespace(progressive_gate=True)),
        candidate_provider=lambda: (
            AgentCandidate(
                name="analyst",
                capabilities=frozenset({Capability.VALIDITY_ASSESSMENT, Capability.STATISTICAL_ANALYSIS}),
            ),
        ),
        dispatcher=dispatcher,
        transition_assessor=assessor,
    )


async def _convene(repo, tmp_path: Path, *, dispatcher=None, stage: str = "test"):
    dispatcher = dispatcher or _Dispatcher()
    result = await _adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Convene the Test review meeting.",
        state={},
        config={
            "configurable": {"thread_id": "thread-1", "run_id": "run-1"},
            "context": {"user_id": "user-1", "project_root": str(tmp_path)},
            "metadata": {},
        },
        review_meeting_stage=stage,
    )
    return result, dispatcher


class TestConveningRunsAMeetingRatherThanTheStage:
    @pytest.mark.asyncio
    async def test_an_awaiting_review_stage_is_exactly_when_a_meeting_may_run(self, tmp_path: Path):
        """Ordinary execution refuses an `awaiting_review` stage, because
        re-running it would replace evidence a person is reading. A review
        meeting reads that evidence, so the same status must not refuse it."""
        repo = _Repo(test_status="awaiting_review")

        result, dispatcher = await _convene(repo, tmp_path)

        assert dispatcher.units, "convening dispatched nobody"
        assert result.worker_count > 0

    @pytest.mark.asyncio
    async def test_it_runs_under_the_pinned_review_contract(self, tmp_path: Path):
        repo = _Repo()

        await _convene(repo, tmp_path)

        assert repo.recorded[0]["stage_spec_key"] == "generic:test-review:v1"

    @pytest.mark.asyncio
    async def test_the_debate_has_a_position_a_red_team_and_a_chair(self, tmp_path: Path):
        """A single reader is not a review meeting. The same
        disagreement-before-synthesis shape the Design council uses applies
        here, or the deck has nothing to show but one opinion."""
        repo = _Repo()

        _result, dispatcher = await _convene(repo, tmp_path)

        roles = {unit.role for unit in dispatcher.units}
        assert {"position", "red_team", "chair"} <= roles

    @pytest.mark.asyncio
    async def test_every_seat_is_told_which_evidence_it_is_reviewing(self, tmp_path: Path):
        repo = _Repo()

        _result, dispatcher = await _convene(repo, tmp_path)

        assert all(PACKAGE_URI in unit.prompt for unit in dispatcher.units)

    @pytest.mark.asyncio
    async def test_the_stages_own_evidence_is_not_replaced(self, tmp_path: Path):
        """The meeting attaches review evidence beside the core result; the
        validity pack it argues about stays exactly where it was."""
        repo = _Repo()

        await _convene(repo, tmp_path)

        core = [item for item in repo.cycle["artifacts"] if item["artifact_type"] == "test_report"]
        assert len(core) == 1
        assert core[0]["content_hash"] == PACKAGE_HASH


class TestTheMeetingRecordsItsOwnEvidence:
    @pytest.mark.asyncio
    async def test_it_writes_a_review_meeting_artifact(self, tmp_path: Path):
        repo = _Repo()

        await _convene(repo, tmp_path)

        assert repo.recorded[0]["artifact_type"] == "test_review_meeting"
        assert repo.recorded[0]["artifact_uri"]

    @pytest.mark.asyncio
    async def test_a_chair_cannot_restate_the_computed_outcome(self, tmp_path: Path):
        """`sanitize_meeting_attachment` is the rule; this checks it is actually
        applied to what gets recorded rather than merely available."""
        repo = _Repo()

        await _convene(repo, tmp_path)

        recorded = json.dumps(repo.recorded[0]["results"])
        assert "computed_outcome" not in recorded

    @pytest.mark.asyncio
    async def test_it_registers_a_review_surface_for_the_same_attempt(self, tmp_path: Path):
        repo = _Repo()

        await _convene(repo, tmp_path)

        assert repo.surfaces, "the meeting produced no deck a person can answer"
        surface = repo.surfaces[-1]
        assert surface["stage"] == "test"
        assert surface["stage_attempt_id"] == "attempt-test"
        assert surface["mode"] == "stage_review"


class TestItRefusesWhatItCannotReview:
    @pytest.mark.asyncio
    async def test_design_has_no_review_meeting(self, tmp_path: Path):
        repo = _Repo()

        result, dispatcher = await _convene(repo, tmp_path, stage="design")

        assert not dispatcher.units
        assert "review meeting" in result.note.lower()

    @pytest.mark.asyncio
    async def test_a_stage_with_no_recorded_evidence_convenes_nobody(self, tmp_path: Path):
        """There is nothing to argue about before the stage has produced its
        pack, and a meeting held over nothing would still write a deck saying it
        happened."""
        repo = _Repo()
        repo.cycle["artifacts"] = []

        result, dispatcher = await _convene(repo, tmp_path)

        assert not dispatcher.units
        assert not repo.recorded
        assert "evidence" in result.note.lower()
