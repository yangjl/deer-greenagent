"""Requesting changes picks a route before it spends a meeting.

The failure this replaces: clicking "Revise" dispatched a fresh roster, a red
team, and a chair the moment it was clicked — silently, whatever the objection
said. Three of those four workers then died on an expired provider credential
and the only visible symptom was a review card that never came back.

These drive the adapter, so they cover the part the pure
``test_dbtl_revision_intent`` tests cannot: that the chosen route actually
changes who gets dispatched, and that the round says which route it took.
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
        "state": "design",
        "db_revision": 3,
        "title": "Drought yield",
        "research_question": "Which hybrids retain yield under drought?",
        "objective": "Rank the hybrids.",
        "success_criteria": "Held-out-site rank correlation exceeds 0.4.",
        "stages": [
            {"id": "attempt-design", "stage": "design", "status": "changes_requested"},
            {"id": "attempt-reconciliation", "stage": "reconciliation", "status": "locked"},
            {"id": "attempt-build", "stage": "build", "status": "locked"},
            {"id": "attempt-test", "stage": "test", "status": "locked"},
            {"id": "attempt-learn", "stage": "learn", "status": "locked"},
        ],
        "artifacts": [],
    }


def _prior_runs() -> list[dict]:
    """One position and one red team, already argued and recorded."""
    return [
        {
            "unit_id": "dbtl-x-1-experimental_design",
            "capability": "experimental_design",
            "status": "completed",
            "result": {
                "status": "completed",
                "summary": "Argued for 1,000 individuals across two sites.",
                "claims": ["Two sites are enough."],
                "evidence_refs": [{"kind": "external", "reference": "position-1"}],
            },
        },
        {
            "unit_id": "dbtl-x-red-team",
            "capability": "design_red_team",
            "status": "completed",
            "result": {
                "status": "completed",
                "summary": "Attacked the site count.",
                "claims": ["Two sites under-powers the contrast."],
                "evidence_refs": [{"kind": "external", "reference": "red-team"}],
            },
        },
        {
            "unit_id": "dbtl-x-chair",
            "capability": "design_council_chair",
            "status": "completed",
            "result": {"status": "completed", "summary": "Synthesized at 1,000 individuals."},
        },
    ]


OBJECTION = "Use 4,000 individuals, not 1,000."


class _Repo:
    def __init__(self) -> None:
        self.cycle = _cycle()
        self.worker_runs = _prior_runs()
        self.recorded: list[dict] = []
        self.surfaces: list[dict] = []

    async def get_cycle(self, cycle_id: str, *, project_id: str):
        return dict(self.cycle)

    async def list_datasets(self, cycle_id: str, *, project_id: str):
        return []

    async def reconciliation_view(self, cycle_id: str, *, project_id: str):
        return {}

    async def build_test_view(self, cycle_id: str, *, project_id: str):
        return {}

    async def list_worker_runs(self, cycle_id: str, *, project_id: str, stage: str):
        return list(self.worker_runs)

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
        self.recorded.append(kwargs)
        self.cycle["db_revision"] += 1
        return kwargs["results"]

    async def register_stage_feedback_surface(self, **kwargs):
        self.surfaces.append(kwargs)
        return {"surface_id": kwargs["surface_id"], **kwargs}


class _Dispatcher:
    def __init__(self) -> None:
        self.batches: list[tuple] = []

    async def __call__(self, units, *, budget):
        self.batches.append(tuple(units))
        payload = json.dumps(
            {
                "status": "completed",
                "summary": "Revised to 4,000 individuals.",
                "claims": ["The revision answers the reviewer."],
                "evidence_refs": [{"kind": "external", "reference": "position-1"}],
                "limitations": [],
                "quality_checks": [{"name": "answers the objection", "passed": True, "detail": ""}],
                "recommended_next_actions": ["Present for review."],
                "provenance": {"inputs_examined": ["prior positions"]},
            }
        )
        return [DispatchOutcome(unit_id=unit.unit_id, text=payload) for unit in units]

    @property
    def dispatched_units(self) -> list:
        return [unit for batch in self.batches for unit in batch]


def _adapter(repo, dispatcher, *, revision_interpreter=None) -> LiveStageAdapter:
    return LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
        dispatcher=dispatcher,
        revision_interpreter=revision_interpreter,
    )


def _config(tmp_path: Path) -> dict:
    return {
        "configurable": {"thread_id": "thread-1", "run_id": "run-1"},
        "context": {"user_id": "user-1", "project_root": str(tmp_path)},
        "metadata": {},
    }


async def _run(repo, dispatcher, *, revision_interpreter=None, tmp_path: Path):
    return await _adapter(repo, dispatcher, revision_interpreter=revision_interpreter).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Here is the design.",
        state={},
        config=_config(tmp_path),
    )


class TestTheCheapRouteIsTaken:
    @pytest.mark.asyncio
    async def test_no_reader_configured_revises_with_the_chair_alone(self, tmp_path: Path):
        """An unavailable reader must never be the reason four workers run."""
        repo, dispatcher = _Repo(), _Dispatcher()

        result = await _run(repo, dispatcher, tmp_path=tmp_path)

        assert len(dispatcher.dispatched_units) == 1
        assert dispatcher.dispatched_units[0].role == "chair"
        assert result.worker_count == 1

    @pytest.mark.asyncio
    async def test_the_reviewers_words_reach_the_chair_verbatim(self, tmp_path: Path):
        repo, dispatcher = _Repo(), _Dispatcher()

        await _run(repo, dispatcher, tmp_path=tmp_path)

        prompt = dispatcher.dispatched_units[0].prompt
        assert OBJECTION in prompt
        # It is answering the objection, not resuming a question it asked.
        assert "asked for changes" in prompt
        assert "you previously paused" not in prompt.lower()

    @pytest.mark.asyncio
    async def test_the_round_says_which_route_it_took(self, tmp_path: Path):
        """Silence is the bug. The reply has to explain what ran and why."""
        repo, dispatcher = _Repo(), _Dispatcher()

        result = await _run(repo, dispatcher, tmp_path=tmp_path)

        assert "no participants were re-run" in result.note.lower()


class TestReconveningIsStillPossible:
    @pytest.mark.asyncio
    async def test_a_reconvene_verdict_seats_participants_again(self, tmp_path: Path):
        async def reader(_prompt: str) -> str:
            return '{"route": "reconvene", "reason": "Nobody argued population structure.", "roster_note": "Seat someone on population structure."}'

        repo, dispatcher = _Repo(), _Dispatcher()

        result = await _run(repo, dispatcher, revision_interpreter=reader, tmp_path=tmp_path)

        assert len(dispatcher.dispatched_units) > 1
        assert "reconvened" in result.note.lower()
        assert result.worker_count > 1

    @pytest.mark.asyncio
    async def test_the_objection_is_shown_to_the_reader(self, tmp_path: Path):
        prompts: list[str] = []

        async def reader(prompt: str) -> str:
            prompts.append(prompt)
            return '{"route": "chair_only", "reason": "A parameter change."}'

        await _run(_Repo(), _Dispatcher(), revision_interpreter=reader, tmp_path=tmp_path)

        assert prompts and OBJECTION in prompts[0]
        # The positions already argued are what make "already answerable" a
        # judgement rather than a guess.
        assert "1,000 individuals across two sites" in prompts[0]
