"""Phases 3B and 3C: Build and Learn register review pages of their own.

Test got one first, so the pattern is settled: a page rendered from the stage's
*own* recorded evidence rather than from a chair result, because no meeting has
happened when it is written. It carries the transition assessment so the meeting
gate has a difficulty to read.

The rule these share with Test is what they must *not* carry. Build's verdict
and Learn's promotion are decisions taken at review time against the evidence; a
route menu rendered before that would pre-empt them. Learn's page is the one
where this matters most — nothing on it may promote or publish, ever.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.stage_execution import LiveStageAdapter
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_feedback import allowed_stage_feedback_intents
from deerflow.dbtl.stage_runner import DispatchOutcome


@pytest.fixture(autouse=True)
def _shipped_dbtl_gates(build_workflow_steps_off):
    """These cases are about the review *page*, not the Build workflow.

    ``build_workflow_steps_enabled()`` reads the ambient ``config.yaml``, so a
    developer running the phased Build workflow sent every Build case here down
    the phased path, where the fake dispatcher's payload carries no phase
    manifest and the stage correctly produces no usable evidence. The failure
    then looks like a review-page regression and reproduces on one machine and
    not in CI. State the switch rather than inheriting it.
    """


_STATES = {
    "build": "build",
    "learn": "learn",
}


def _cycle(stage: str) -> dict:
    statuses = {
        "design": "approved",
        "reconciliation": "approved",
        "build": "approved" if stage == "learn" else "in_progress",
        "test": "approved" if stage == "learn" else "locked",
        "learn": "in_progress" if stage == "learn" else "locked",
    }
    return {
        "id": "cycle-1",
        "project_id": "project-1",
        "state": _STATES[stage],
        "db_revision": 7,
        "title": "Drought yield",
        "research_question": "Which hybrids retain yield under drought?",
        "objective": "Rank the hybrids.",
        "success_criteria": "Held-out-site rank correlation exceeds 0.4.",
        "stages": [{"id": f"attempt-{name}", "stage": name, "status": status} for name, status in statuses.items()],
        "artifacts": [],
    }


class _Repo:
    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.cycle = _cycle(stage)
        self.surfaces: list[dict] = []
        self.recorded: list[dict] = []
        self.lineage: list[dict] = []
        self.syntheses: list[dict] = []

    async def get_cycle(self, cycle_id: str, *, project_id: str):
        return dict(self.cycle)

    async def list_datasets(self, cycle_id: str, *, project_id: str):
        return [
            {
                "source_key": "genotypes",
                "content_hash": "e" * 64,
                "uri": "/mnt/user-data/workspace/genotypes.csv",
                "declared_immutable": True,
                "role": "raw",
            }
        ]

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

    async def knowledge_view(self, project_id: str, *, cycle_id: str):
        return {"events": [], "candidates": [], "claims": []}

    async def record_learn_synthesis(self, **kwargs):
        self.syntheses.append(kwargs)
        self.cycle["db_revision"] += 1
        return {}

    async def record_build_lineage(self, **kwargs):
        self.lineage.append(kwargs)
        self.cycle["db_revision"] += 1
        return {}

    async def record_worker_runs(self, **kwargs):
        self.recorded.append(kwargs)
        self.cycle["db_revision"] += 1
        if kwargs.get("artifact_uri"):
            self.cycle["artifacts"] = [
                {
                    "id": f"artifact-{self.stage}-1",
                    "stage_attempt_id": f"attempt-{self.stage}",
                    "artifact_type": kwargs["artifact_type"],
                    "revision": 1,
                    "uri": kwargs["artifact_uri"],
                    "content_hash": kwargs["artifact_content_hash"],
                }
            ]
        return kwargs["results"]

    async def register_stage_feedback_surface(self, **kwargs):
        self.surfaces.append(kwargs)
        return {"surface_id": kwargs["surface_id"], **kwargs}


class _Dispatcher:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    async def __call__(self, units, *, budget):
        outcomes = []
        for index, unit in enumerate(units):
            artifact_refs: list[str] = []
            evidence_refs = [{"kind": "workspace_file", "reference": "/mnt/user-data/outputs/run.json"}]
            if "Project context:\n" in unit.prompt:
                context_text = unit.prompt.split("Project context:\n", 1)[1].split("\n\nReading files:", 1)[0]
                context = json.loads(context_text)
                stage_workspace = str((context.get("stage_workspace") or {}).get("path") or "")
                if stage_workspace.startswith("/mnt/user-data/"):
                    artifact_uri = f"{stage_workspace}/artifacts/result.json"
                    artifact_path = self.project_root / artifact_uri.removeprefix("/mnt/user-data/")
                    artifact_path.parent.mkdir(parents=True, exist_ok=True)
                    artifact_path.write_text('{"status":"ok"}\n', encoding="utf-8")
                    artifact_refs = [artifact_uri]
                    evidence_refs = [{"kind": "workspace_file", "reference": artifact_uri}]
            payload = json.dumps(
                {
                    "status": "completed",
                    "summary": "Recorded the run and its outputs.",
                    "artifact_refs": artifact_refs,
                    "claims": ["Every declared input was consumed as declared."],
                    "evidence_refs": evidence_refs,
                    "limitations": [],
                    "quality_checks": [{"name": "outputs versioned", "passed": True, "detail": ""}],
                    "recommended_next_actions": ["Review the record."],
                    "provenance": {
                        "inputs_examined": ["run log"],
                        # Build's pinned contract requires a structured rerun
                        # record from v8 on — Test re-executes it, so a Build
                        # without one cannot be reviewed. These cases are about
                        # the review page, but they still have to satisfy the
                        # contract the stage actually runs under.
                        **(
                            {
                                "rerun_spec": {
                                    "version": 1,
                                    "entry_point": f"{stage_workspace}/artifacts/result.json",
                                    "command": "uv run python run.py --seed 7",
                                    "seed": 7,
                                    # Non-empty by contract: a rerun that names
                                    # nothing it consumes is not re-executable.
                                    "inputs": ["/mnt/user-data/outputs/run.json"],
                                    "environment": {"python": "3.12"},
                                    "configuration": [],
                                    "expected_outputs": [f"{stage_workspace}/artifacts/result.json"],
                                }
                            }
                            # One Build, one rerun record. Every worker
                            # declaring its own would remap to its own
                            # published path and conflict, which the merge
                            # correctly refuses — but that is a different
                            # test than this file's.
                            if artifact_refs and index == 0
                            else {}
                        ),
                    },
                }
            )
            outcomes.append(DispatchOutcome(unit_id=unit.unit_id, text=payload))
        return outcomes


async def _run(stage: str, tmp_path: Path, *, progressive_gate: bool = True):
    repo = _Repo(stage)

    async def assessor(_prompt: str) -> str:
        return json.dumps({"difficulty": "high_stakes", "rationale": "This one deserves an argument."})

    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(dbtl=SimpleNamespace(progressive_gate=progressive_gate)),
        candidate_provider=lambda: (
            AgentCandidate(
                name="analyst",
                capabilities=frozenset(
                    {
                        Capability.SOFTWARE_ENGINEERING,
                        Capability.DATA_RECONCILIATION,
                        Capability.STATISTICAL_ANALYSIS,
                        Capability.VALIDITY_ASSESSMENT,
                        Capability.KNOWLEDGE_SYNTHESIS,
                        Capability.SCIENTIFIC_REPORTING,
                    }
                ),
            ),
        ),
        dispatcher=_Dispatcher(tmp_path),
        transition_assessor=assessor,
    )
    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text=f"Run the {stage} stage.",
        state={},
        config={
            "configurable": {"thread_id": "thread-1", "run_id": "run-1"},
            "context": {"user_id": "user-1", "project_root": str(tmp_path)},
            "metadata": {},
        },
    )
    return repo, result


@pytest.mark.parametrize("stage", ["build", "learn"])
class TestEachStageGetsItsOwnReviewPage:
    @pytest.mark.asyncio
    async def test_a_surface_is_registered_against_that_stages_attempt(self, stage, tmp_path: Path):
        repo, result = await _run(stage, tmp_path)

        assert result.produced_usable_evidence
        assert repo.surfaces, f"{stage} produced evidence but registered no review page"
        surface = repo.surfaces[0]
        assert surface["stage"] == stage
        assert surface["stage_attempt_id"] == f"attempt-{stage}"
        assert surface["mode"] == "stage_review"

    @pytest.mark.asyncio
    async def test_the_deck_is_written_under_that_stage(self, stage, tmp_path: Path):
        _repo, result = await _run(stage, tmp_path)

        assert result.deck_uri
        assert f"/{stage}/" in result.deck_uri
        assert f"{stage}-slides" in result.deck_uri

    @pytest.mark.asyncio
    async def test_the_assessment_travels_so_the_meeting_gate_can_read_it(self, stage, tmp_path: Path):
        repo, _result = await _run(stage, tmp_path)

        gate = repo.surfaces[0]["decision_request"]["transition_gate"]
        assert gate["stage"] == stage
        assert gate["assessment"]["difficulty"] == "high_stakes"

    @pytest.mark.asyncio
    async def test_no_route_menu_is_offered_before_the_review(self, stage, tmp_path: Path):
        """The verdict is taken at review time against this evidence. A menu
        rendered before it would pre-empt the decision it exists to record."""
        repo, _result = await _run(stage, tmp_path)

        assert repo.surfaces[0]["decision_request"]["transition_gate"]["routes"] == []

    @pytest.mark.asyncio
    async def test_the_page_does_not_depend_on_the_progressive_gate(self, stage, tmp_path: Path):
        repo, _result = await _run(stage, tmp_path, progressive_gate=False)

        assert repo.surfaces, "the review page is not the progressive gate"
        request = repo.surfaces[0]["decision_request"]
        assert request.get("transition_gate") is None
        assert request["commentable_slides"]


class TestLearnsPageCannotPromoteOrPublish:
    def test_no_promotion_or_publication_intent_is_reachable_from_a_deck(self):
        """Learn may record a recommendation. Turning one into project
        knowledge is a separate human act with its own audit record, and no
        deck intent may stand in for it."""
        allowed = allowed_stage_feedback_intents("learn")

        assert "recommend_promotion" in allowed
        assert not {"promote", "publish", "publication"} & allowed
