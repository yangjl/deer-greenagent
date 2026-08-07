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

from deerflow.agents.dbtl.live_stage.adapter import _write_evidence_exception_package
from deerflow.agents.dbtl.live_stage.test_rerun import TestRerunRecord as RerunRecord
from deerflow.agents.dbtl.live_stage.test_rerun import TestRerunStatus as RerunStatus
from deerflow.agents.dbtl.stage_execution import LiveStageAdapter
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.evidence_exception import EvidenceReason, build_evidence_exception_dossier
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
        self.submissions: list[dict] = []

    async def get_cycle(self, cycle_id: str, *, project_id: str):
        return dict(self.cycle)

    async def list_datasets(self, cycle_id: str, *, project_id: str):
        return []

    async def reconciliation_view(self, cycle_id: str, *, project_id: str):
        return {}

    async def build_test_view(self, cycle_id: str, *, project_id: str):
        return {"cycle_id": cycle_id, "build_lineage": {"id": "lineage-1"}, "validity_assessment": None}

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
                    "stage_attempt_id": "attempt-test",
                    "artifact_type": kwargs["artifact_type"],
                }
            ]
        return kwargs["results"]

    async def attach_artifact(self, **kwargs):
        self.cycle["db_revision"] += 1
        artifact = {
            "id": "artifact-test-exception",
            "revision": 1,
            "uri": kwargs["uri"],
            "content_hash": kwargs["content_hash"],
            "stage": "test",
            "stage_attempt_id": "attempt-test",
            "artifact_type": kwargs["artifact_type"],
        }
        self.cycle["artifacts"].append(artifact)
        return {**artifact, "db_revision": self.cycle["db_revision"]}

    async def register_stage_feedback_surface(self, **kwargs):
        self.surfaces.append(kwargs)
        return {"surface_id": kwargs["surface_id"], **kwargs}

    async def submit_stage_for_review(self, **kwargs):
        self.submissions.append(kwargs)
        self.cycle["db_revision"] += 1
        next(item for item in self.cycle["stages"] if item["stage"] == kwargs["stage"])["status"] = "awaiting_review"
        return dict(self.cycle)


class _Dispatcher:
    def __init__(self, *, invalidated: bool = False) -> None:
        self.invalidated = invalidated

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
                "provenance": {
                    "inputs_examined": ["validity pack"],
                    "validity_assessment": {
                        "metrics": [
                            {
                                "name": "held_out_rank_correlation",
                                "value": 0.46,
                                "threshold": 0.4,
                                "criterion": "gte",
                                "plausible_max": 1.0,
                                "unit": "",
                            }
                        ],
                        "checks": [
                            {
                                "check": check,
                                "status": ("failed" if self.invalidated and check == "reproducibility" else "passed"),
                                "detail": ("The authoritative rerun path was unavailable." if self.invalidated and check == "reproducibility" else f"{check} passed against the recorded Test evidence."),
                                "evidence_refs": ([] if self.invalidated and check == "reproducibility" else ["/mnt/user-data/outputs/validity.json"]),
                            }
                            for check in (
                                "fold_composition",
                                "predictive_ceiling",
                                "direction",
                                "leakage",
                                "tester_holdout",
                                "reproducibility",
                                "reconciled_inputs",
                            )
                        ],
                        "limitations": [],
                        "rationale": "Every required check passed.",
                    },
                },
            }
        )
        return [DispatchOutcome(unit_id=unit.unit_id, text=payload) for unit in units]


def _adapter(
    repo,
    *,
    progressive_gate: bool = True,
    degraded_evidence_continuation: bool = False,
    invalidated: bool = False,
) -> LiveStageAdapter:
    async def assessor(_prompt: str) -> str:
        return json.dumps(
            {
                "difficulty": "high_stakes",
                "rationale": "A headline metric this close to the threshold needs argument.",
            }
        )

    return LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(
            dbtl=SimpleNamespace(
                progressive_gate=progressive_gate,
                degraded_evidence_continuation=degraded_evidence_continuation,
            )
        ),
        candidate_provider=lambda: (AgentCandidate(name="analyst", capabilities=frozenset({Capability.VALIDITY_ASSESSMENT, Capability.STATISTICAL_ANALYSIS})),),
        dispatcher=_Dispatcher(invalidated=invalidated),
        transition_assessor=assessor,
    )


async def _run(
    repo,
    tmp_path: Path,
    *,
    progressive_gate: bool = True,
    degraded_evidence_continuation: bool = False,
    invalidated: bool = False,
):
    return await _adapter(
        repo,
        progressive_gate=progressive_gate,
        degraded_evidence_continuation=degraded_evidence_continuation,
        invalidated=invalidated,
    ).execute(
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
    async def test_complete_typed_evidence_is_submitted_so_chat_can_render_its_human_gate(self, tmp_path: Path):
        repo = _Repo()

        result = await _run(repo, tmp_path)

        assert result.produced_usable_evidence
        assert repo.submissions == [
            {
                "cycle_id": "cycle-1",
                "project_id": "project-1",
                "stage": "test",
                "expected_db_revision": 7,
                "actor_user_id": "user-1",
                "idempotency_key": "dbtl-stage:run-1:cycle-1:test-auto-submit",
            }
        ]
        assert next(item for item in repo.cycle["stages"] if item["stage"] == "test")["status"] == "awaiting_review"

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

    @pytest.mark.asyncio
    async def test_the_test_deck_carries_its_registered_feedback_bridge(self, tmp_path: Path):
        repo = _Repo()

        result = await _run(repo, tmp_path)

        relative = result.deck_uri.removeprefix("/mnt/user-data/outputs/")
        html = (tmp_path / "outputs" / relative).read_text(encoding="utf-8")
        assert repo.surfaces, "the deck must be registered before it can accept human input"
        assert repo.surfaces[0]["surface_id"] in html
        assert "deerflow-design-deck" in html


class TestItCarriesTheComputedAssessmentAndRoutes:
    @pytest.mark.asyncio
    async def test_a_successful_test_preserves_the_upstream_build_exception(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        repo = _Repo()
        monkeypatch.setattr(
            "deerflow.agents.dbtl.live_stage.adapter._validated_test_assessment",
            lambda *_args, **_kwargs: {
                "metrics": [],
                "checks": [],
                "limitations": [],
                "rationale": "The server-computed Test outcome is supported.",
                "evaluation": {
                    "outcome": "supported",
                    "allowed_recommendations": ["advance_to_learn", "repeat_test", "close_cycle"],
                },
            },
        )
        build_attempt = next(item for item in repo.cycle["stages"] if item["stage"] == "build")
        build_attempt["status"] = "advanced_with_exception"
        dossier = build_evidence_exception_dossier(
            stage="build",
            stage_attempt_id="attempt-build",
            reason_codes=[EvidenceReason.DELIVERABLE_ATTEMPT_FAILED],
            verified_facts=["The numeric result and figure were server-readable."],
        )
        assert dossier is not None
        uri, content_hash, _digest = _write_evidence_exception_package(
            project_root=str(tmp_path),
            cycle=repo.cycle,
            dossier=dossier,
        )
        repo.cycle["artifacts"] = [
            {
                "id": "artifact-build-exception",
                "revision": 1,
                "uri": uri,
                "content_hash": content_hash,
                "stage": "build",
                "stage_attempt_id": "attempt-build",
                "artifact_type": "evidence_exception",
            }
        ]

        result = await _run(
            repo,
            tmp_path,
            degraded_evidence_continuation=True,
        )

        assert result.produced_usable_evidence
        assert [item["artifact_type"] for item in repo.cycle["artifacts"]] == [
            "validity_report",
            "evidence_exception",
        ]
        propagated = repo.surfaces[0]["decision_request"]["transition_gate"]["evidence_exception"]
        assert propagated["condition"] == "degraded_verified"
        assert propagated["scientific_effect"] == "limits_scope"
        assert propagated["reason_codes"] == ["deliverable_attempt_failed"]
        assert "advance_to_learn" in propagated["recovery_options"]
        assert "learn_from_invalidated_evidence" not in propagated["recovery_options"]
        assert any(content_hash in fact for fact in propagated["verified_facts"])
        relative = result.deck_uri.removeprefix("/mnt/user-data/outputs/")
        html = (tmp_path / "outputs" / relative).read_text(encoding="utf-8")
        assert "Red flag: this evidence remains scope-limited." in html

    @pytest.mark.asyncio
    async def test_invalidated_typed_test_keeps_its_report_and_adds_a_red_flag_dossier(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        repo = _Repo()
        monkeypatch.setattr(
            "deerflow.agents.dbtl.live_stage.adapter.prepare_test_rerun",
            lambda *_args, **_kwargs: RerunRecord(
                status=RerunStatus.FAILED,
                command="python fit.py",
                reason="The authoritative rerun path was unavailable.",
                exit_status=2,
                inputs_verified=True,
            ),
        )

        result = await _run(
            repo,
            tmp_path,
            degraded_evidence_continuation=True,
            invalidated=True,
        )

        assert result.produced_usable_evidence
        assert [item["artifact_type"] for item in repo.cycle["artifacts"]] == [
            "validity_report",
            "evidence_exception",
        ]
        gate = repo.surfaces[0]["decision_request"]["transition_gate"]
        assert gate["evidence_exception"]["condition"] == "degraded_verified"
        assert gate["evidence_exception"]["reason_codes"] == ["rerun_unavailable"]
        assert [item["slug"] for item in gate["routes"]][0] == ("learn_from_invalidated_evidence")
        relative = result.deck_uri.removeprefix("/mnt/user-data/outputs/")
        html = (tmp_path / "outputs" / relative).read_text(encoding="utf-8")
        assert "Red flag: this evidence remains failed or untrusted." in html

    @pytest.mark.asyncio
    async def test_the_assessment_travels_so_the_meeting_gate_can_read_it(self, tmp_path: Path):
        repo = _Repo()

        await _run(repo, tmp_path)

        gate = repo.surfaces[0]["decision_request"]["transition_gate"]
        assert gate["stage"] == "test"
        assert gate["assessment"]["difficulty"] == "high_stakes"

    @pytest.mark.asyncio
    async def test_the_route_menu_comes_from_the_computed_outcome(self, tmp_path: Path):
        repo = _Repo()

        await _run(repo, tmp_path)

        routes = repo.surfaces[0]["decision_request"]["transition_gate"]["routes"]
        assert routes
        assert {item["slug"] for item in routes} == {
            "repeat_test",
            "return_to_build",
            "return_to_reconciliation",
            "return_to_design",
            "close_cycle",
        }

    @pytest.mark.asyncio
    async def test_the_flag_being_off_still_registers_the_page_without_a_gate(self, tmp_path: Path):
        repo = _Repo()

        await _run(repo, tmp_path, progressive_gate=False)

        assert repo.surfaces, "the review page is not the progressive gate and must not depend on it"
        request = repo.surfaces[0]["decision_request"]
        assert request["transition_gate"]["routes"]
        assert request["commentable_slides"]
