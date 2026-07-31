"""A cycle awaiting changes does not argue with everything you say to it.

Observed in manual testing: a cycle sitting in ``changes_requested`` convened a
meeting for the word "hello". The hold rule skipped that status entirely, on the
grounds that a reviewer asking for changes *is* the request to argue again —
true of the verdict, and not true of every message that arrives after it.

The reviewer's round is dispatched once, by the review endpoint's own kickoff.
A person typing later has to ask.
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

OBJECTION = "Use 4,000 individuals, not 1,000."


def _cycle(status: str = "changes_requested") -> dict:
    return {
        "id": "cycle-1",
        "project_id": "project-1",
        "state": "design",
        "db_revision": 5,
        "title": "Drought yield",
        "research_question": "Which hybrids retain yield under drought?",
        "objective": "Rank the hybrids.",
        "success_criteria": "Held-out rank correlation exceeds 0.4.",
        "stages": [
            {"id": "attempt-design", "stage": "design", "status": status},
            {"id": "attempt-reconciliation", "stage": "reconciliation", "status": "locked"},
            {"id": "attempt-build", "stage": "build", "status": "locked"},
            {"id": "attempt-test", "stage": "test", "status": "locked"},
            {"id": "attempt-learn", "stage": "learn", "status": "locked"},
        ],
        "artifacts": [
            {
                "id": "artifact-1",
                "stage_attempt_id": "attempt-design",
                "artifact_type": "design_brief",
                "revision": 1,
                "content_hash": "b" * 64,
                "uri": "/mnt/user-data/outputs/design-review-rev1.md",
            }
        ],
    }


class _Repo:
    def __init__(self, cycle: dict) -> None:
        self.cycle = cycle

    async def get_cycle(self, cycle_id: str, *, project_id: str):
        return dict(self.cycle)

    async def list_datasets(self, cycle_id: str, *, project_id: str):
        return []

    async def reconciliation_view(self, cycle_id: str, *, project_id: str):
        return {}

    async def build_test_view(self, cycle_id: str, *, project_id: str):
        return {}

    async def list_worker_runs(self, cycle_id: str, *, project_id: str, stage: str):
        return [
            {
                "unit_id": "dbtl-x-1-experimental_design",
                "capability": "experimental_design",
                "status": "completed",
                "result": {"status": "completed", "summary": "Argued for 1,000 individuals."},
            }
        ]

    async def list_activity(self, cycle_id: str, *, project_id: str):
        return [
            {
                "event_type": "stage.reviewed",
                "payload": {"stage": "design", "decision": "request_changes", "rationale": OBJECTION},
            }
        ]

    async def get_stage_execution_replay(self, cycle_id: str, *, project_id: str, idempotency_key: str):
        return None

    async def record_worker_runs(self, **kwargs):
        self.cycle["db_revision"] += 1
        return kwargs["results"]

    async def register_stage_feedback_surface(self, **kwargs):
        return {"surface_id": kwargs["surface_id"], **kwargs}


class _Dispatcher:
    def __init__(self) -> None:
        self.batches: list[tuple] = []

    async def __call__(self, units, *, budget):
        self.batches.append(tuple(units))
        payload = json.dumps(
            {
                "status": "completed",
                "summary": "Revised.",
                "claims": ["It answers the reviewer."],
                "evidence_refs": [{"kind": "external", "reference": "position-1"}],
                "limitations": [],
                "quality_checks": [{"name": "answers", "passed": True, "detail": ""}],
                "recommended_next_actions": ["Review."],
                "provenance": {"inputs_examined": ["positions"]},
            }
        )
        return [DispatchOutcome(unit_id=unit.unit_id, text=payload) for unit in units]

    @property
    def dispatched(self) -> int:
        return sum(len(batch) for batch in self.batches)


async def _run(text: str, *, status: str = "changes_requested", tmp_path: Path):
    dispatcher = _Dispatcher()
    adapter = LiveStageAdapter(
        repo=_Repo(_cycle(status)),
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
        dispatcher=dispatcher,
    )
    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text=text,
        state={},
        config={
            "configurable": {"thread_id": "thread-1", "run_id": "run-1"},
            "context": {"user_id": "user-1", "project_root": str(tmp_path)},
            "metadata": {},
        },
    )
    return result, dispatcher


class TestAnIdleMessageConvenesNobody:
    @pytest.mark.asyncio
    async def test_a_greeting_does_not_convene_a_meeting(self, tmp_path: Path):
        result, dispatcher = await _run("hello", tmp_path=tmp_path)

        assert dispatcher.dispatched == 0
        assert result.worker_count == 0
        assert "no new meeting was convened" in result.note

    @pytest.mark.asyncio
    async def test_a_question_about_the_design_convenes_nobody_either(self, tmp_path: Path):
        _result, dispatcher = await _run("what does the design say about heritability?", tmp_path=tmp_path)

        assert dispatcher.dispatched == 0


class TestTheReviewersRoundStillRuns:
    @pytest.mark.asyncio
    async def test_the_servers_own_refinement_kickoff_always_convenes(self, tmp_path: Path):
        """Deterministic, so an unavailable interpreter cannot cost a reviewer
        the round their verdict asked for."""
        _result, dispatcher = await _run(
            f"Refine the approved Design candidate for the reviewer's written objection.\n\n{OBJECTION}",
            tmp_path=tmp_path,
        )

        assert dispatcher.dispatched >= 1

    @pytest.mark.asyncio
    async def test_asking_in_words_still_convenes(self, tmp_path: Path):
        _result, dispatcher = await _run("run the meeting again", tmp_path=tmp_path)

        assert dispatcher.dispatched >= 1


class TestTheInProgressHoldIsUnchanged:
    @pytest.mark.asyncio
    async def test_a_settled_package_still_holds(self, tmp_path: Path):
        result, dispatcher = await _run("hello", status="in_progress", tmp_path=tmp_path)

        assert dispatcher.dispatched == 0
        assert "no new meeting was convened" in result.note
