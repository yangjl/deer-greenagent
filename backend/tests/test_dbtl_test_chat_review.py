"""Test's human gate lives in chat and binds typed server-evaluated evidence."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from deerflow.agents.dbtl.live_stage.test_rerun import TestRerunRecord as RerunRecord
from deerflow.agents.dbtl.live_stage.test_rerun import TestRerunStatus as RerunStatus
from deerflow.agents.dbtl.live_stage.test_review import TestReviewService
from deerflow.agents.dbtl.live_stage.test_review import validated_test_assessment as _validated_test_assessment
from deerflow.agents.dbtl.supervisor import (
    _test_card_messages,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    answered_cycle_card_id as _answered_cycle_card_id,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    answered_test_card as _answered_test_card,
)
from deerflow.agents.dbtl.supervisor_support.human_input_protocol import TEST_OUTCOME_PREFIX, TEST_REVIEW_PREFIX
from deerflow.dbtl.branches import BranchDecision, SupervisorBranch
from deerflow.dbtl.worker_result import StageWorkerResult, WorkerStatus
from deerflow.persistence.dbtl import DbtlCycleRepository


def _decision() -> BranchDecision:
    return BranchDecision(
        branch=SupervisorBranch.CYCLE_CONTINUATION,
        route=SimpleNamespace(source="explicit", confidence=1.0),
        cycle_id="cycle-1",
    )


def _assessment_result() -> StageWorkerResult:
    checks = [
        "fold_composition",
        "predictive_ceiling",
        "direction",
        "leakage",
        "tester_holdout",
        "reproducibility",
        # The worker is not the authority for this one in optional mode; the
        # server removes it and reconstructs it from Build lineage below.
        "reconciled_inputs",
    ]
    return StageWorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="The locked model passed its family holdout.",
        capability="validity_assessment",
        agent_name="analyst",
        provenance={
            "validity_assessment": {
                "metrics": [
                    {
                        "name": "holdout_r2",
                        "value": 1.0,
                        "threshold": 0.95,
                        "criterion": "gte",
                        "plausible_max": None,
                        "unit": "",
                    }
                ],
                "checks": [
                    {
                        "check": check,
                        "status": "passed",
                        "detail": f"{check} was verified.",
                        "evidence_refs": [f"evidence:{check}"],
                    }
                    for check in checks
                ],
                "limitations": ["Tiny synthetic holdout."],
                "rationale": "All checks in the pinned pack passed.",
            }
        },
    )


def test_server_computes_supported_from_complete_typed_test_evidence(monkeypatch):
    monkeypatch.setattr(
        "deerflow.agents.dbtl.live_stage.test_review.reconciliation_required",
        lambda: False,
    )
    snapshot = _validated_test_assessment(
        [_assessment_result()],
        build_test={"build_lineage": {"id": "lineage-1"}},
    )

    assert snapshot is not None
    assert snapshot["evaluation"]["outcome"] == "supported"
    provenance = next(item for item in snapshot["checks"] if item["check"] == "reconciled_inputs")
    assert provenance["evidence_refs"] == ["lineage-1"]


def test_server_reloads_its_derived_metric_fields(monkeypatch):
    monkeypatch.setattr(
        "deerflow.agents.dbtl.live_stage.test_review.reconciliation_required",
        lambda: False,
    )
    result = _assessment_result()
    result.provenance["validity_assessment"]["metrics"][0]["meets_threshold"] = True

    snapshot = _validated_test_assessment(
        [result],
        build_test={"build_lineage": {"id": "lineage-1"}},
    )

    assert snapshot is not None
    assert snapshot["evaluation"]["outcome"] == "supported"


def test_a_prose_pass_without_typed_test_evidence_is_not_reviewable():
    result = StageWorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="PASS",
        capability="validity_assessment",
        agent_name="analyst",
    )

    assert _validated_test_assessment([result], build_test={}) is None


def test_server_rerun_failure_overrides_worker_authored_reproducibility_pass(monkeypatch):
    monkeypatch.setattr(
        "deerflow.agents.dbtl.live_stage.test_review.reconciliation_required",
        lambda: False,
    )
    rerun = RerunRecord(
        status=RerunStatus.FAILED,
        command="python fit.py",
        reason="The rerun output hash changed.",
        exit_status=0,
        inputs_verified=True,
    )

    snapshot = _validated_test_assessment(
        [_assessment_result()],
        build_test={"build_lineage": {"id": "lineage-1"}},
        rerun=rerun,
    )

    assert snapshot is not None
    reproducibility = next(item for item in snapshot["checks"] if item["check"] == "reproducibility")
    assert reproducibility["status"] == "failed"
    assert snapshot["evaluation"]["outcome"] == "invalidated"


@pytest.mark.asyncio
async def test_review_snapshot_uses_the_newest_test_retry(monkeypatch):
    monkeypatch.setattr(
        "deerflow.agents.dbtl.live_stage.test_review.reconciliation_required",
        lambda: False,
    )
    assessment = _assessment_result().as_dict()
    failed_rerun = StageWorkerResult(
        status=WorkerStatus.FAILED,
        summary="The old rerun failed.",
        capability="reproducibility_rerun",
        agent_name="server",
        provenance={
            "rerun_execution": {
                "status": "failed",
                "command": "python fit.py",
                "reason": "old failure",
                "inputs_verified": False,
            }
        },
    ).as_dict()
    passed_rerun = StageWorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="The latest rerun passed.",
        capability="reproducibility_rerun",
        agent_name="server",
        provenance={
            "rerun_execution": {
                "status": "passed",
                "command": "python fit.py",
                "reason": "latest pass",
                "exit_status": 0,
                "inputs_verified": True,
                "logs": [{"path": "/mnt/user-data/outputs/rerun.stdout.log"}],
                "outputs": [],
            }
        },
    ).as_dict()

    class Repo:
        async def get_cycle(self, cycle_id: str, *, project_id: str):
            return {
                "id": cycle_id,
                "db_revision": 9,
                "stages": [{"id": "attempt-test", "stage": "test", "status": "awaiting_review"}],
                "artifacts": [
                    {
                        "id": "artifact-test",
                        "stage_attempt_id": "attempt-test",
                        "artifact_type": "validity_report",
                        "revision": 1,
                        "uri": "/mnt/user-data/outputs/test.md",
                        "content_hash": "a" * 64,
                    }
                ],
            }

        async def list_worker_runs(self, cycle_id: str, *, project_id: str, stage: str):
            return [
                {"unit_id": "old-build-rerun", "result": failed_rerun},
                {"unit_id": "old-validity", "result": assessment},
                {"unit_id": "new-build-rerun", "result": passed_rerun},
                {"unit_id": "new-validity", "result": assessment},
            ]

        async def build_test_view(self, cycle_id: str, *, project_id: str):
            return {"build_lineage": {"id": "lineage-1"}}

        async def latest_stage_feedback_surface(self, **kwargs):
            return {
                "id": "dfs-test-review",
                "decision_request": None,
            }

    service = TestReviewService(
        repo=Repo(),
        app_config=SimpleNamespace(dbtl=SimpleNamespace(stage_meetings=SimpleNamespace(test=False))),
        runtime_reader=lambda _config: {},
    )

    snapshot = await service.snapshot(project_id="project-1", cycle_id="cycle-1")

    assert snapshot is not None
    assert snapshot["evaluation"]["outcome"] == "supported"
    assert snapshot["feedback_surface_id"] == "dfs-test-review"
    reproducibility = next(item for item in snapshot["checks"] if item["check"] == "reproducibility")
    assert reproducibility["detail"] == "latest pass"


@pytest.mark.asyncio
async def test_review_snapshot_never_pairs_a_new_rerun_with_an_older_assessment(monkeypatch):
    monkeypatch.setattr(
        "deerflow.agents.dbtl.live_stage.test_review.reconciliation_required",
        lambda: False,
    )
    old_assessment = _assessment_result().as_dict()
    passed_rerun = StageWorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="The latest rerun passed.",
        capability="reproducibility_rerun",
        agent_name="server",
        provenance={
            "rerun_execution": {
                "status": "passed",
                "command": "python fit.py",
                "reason": "latest pass",
                "exit_status": 0,
                "inputs_verified": True,
                "logs": [{"path": "/mnt/user-data/outputs/rerun.log"}],
                "outputs": [],
            }
        },
    ).as_dict()
    malformed_new_assessment = _assessment_result().as_dict()
    # Reject the newest assessment at the typed worker-result boundary. The
    # review service must not silently pair this attempt's rerun with the older
    # attempt's otherwise valid assessment.
    malformed_new_assessment["status"] = "not-a-worker-status"

    class Repo:
        async def get_cycle(self, cycle_id: str, *, project_id: str):
            return {
                "id": cycle_id,
                "db_revision": 9,
                "stages": [
                    {
                        "id": "attempt-test",
                        "stage": "test",
                        "status": "awaiting_review",
                        "stage_spec_key": "generic:test:v4",
                    }
                ],
                "artifacts": [
                    {
                        "id": "new-test-report",
                        "stage_attempt_id": "attempt-test",
                        "artifact_type": "validity_report",
                        "revision": 1,
                        "uri": "/mnt/user-data/outputs/test.md",
                        "content_hash": "a" * 64,
                    }
                ],
            }

        async def list_worker_runs(self, cycle_id: str, *, project_id: str, stage: str):
            return [
                {"unit_id": "dbtl-old-build-rerun", "result": passed_rerun},
                {"unit_id": "dbtl-old-0-validity_assessment", "result": old_assessment},
                {"unit_id": "dbtl-new-build-rerun", "result": passed_rerun},
                {"unit_id": "dbtl-new-0-validity_assessment", "result": malformed_new_assessment},
            ]

        async def build_test_view(self, cycle_id: str, *, project_id: str):
            return {"build_lineage": {"id": "lineage-1"}}

        async def latest_stage_feedback_surface(self, **kwargs):
            return None

    service = TestReviewService(
        repo=Repo(),
        app_config=SimpleNamespace(dbtl=SimpleNamespace(stage_meetings=SimpleNamespace(test=False))),
        runtime_reader=lambda _config: {},
    )

    assert await service.snapshot(project_id="project-1", cycle_id="cycle-1") is None


def test_chat_review_card_recovers_cycle_and_carries_bound_snapshot():
    snapshot = {
        "evaluation": {
            "outcome": "supported",
            "validity_pack_key": "generic-predictive:v2",
            "allowed_recommendations": ["advance_to_learn", "repeat_test"],
        },
        "evidence_uri": "/mnt/user-data/outputs/test.md",
        "evidence_hash": "a" * 64,
        "meeting": {"requirement": "optional"},
    }
    _call, card = _test_card_messages(_decision(), snapshot, request_nonce="run-1", outcome=False)
    request = card.artifact["human_input"]
    reply = HumanMessage(
        content="Continue to outcome decision",
        additional_kwargs={
            "hide_from_ui": True,
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "source": "ask_clarification",
                "request_id": request["request_id"],
                "response_kind": "option",
                "option_id": "continue_to_outcome",
                "value": "continue_to_outcome",
            },
        },
    )

    assert request["request_id"].startswith(TEST_REVIEW_PREFIX)
    assert request["test_review_snapshot"]["evidence_hash"] == "a" * 64
    assert _answered_cycle_card_id({"messages": [card, reply]}) == "cycle-1"


def test_chat_review_uses_the_server_emitted_option_value():
    snapshot = {
        "evaluation": {
            "outcome": "supported",
            "validity_pack_key": "generic-predictive:v2",
            "allowed_recommendations": ["advance_to_learn", "repeat_test"],
        },
        "evidence_hash": "a" * 64,
        "meeting": {"requirement": "optional"},
    }
    _call, card = _test_card_messages(_decision(), snapshot, request_nonce="run-1", outcome=False)
    request = card.artifact["human_input"]
    reply = HumanMessage(
        content="Continue",
        additional_kwargs={
            "hide_from_ui": True,
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "source": "ask_clarification",
                "request_id": request["request_id"],
                "response_kind": "option",
                "option_id": "continue_to_outcome",
                "value": "convene_review_meeting",
            },
        },
    )

    answered = _answered_test_card({"messages": [card, reply]}, TEST_REVIEW_PREFIX)

    assert answered is not None
    assert answered[1] == "continue_to_outcome"


def test_outcome_card_offers_only_server_allowed_routes():
    snapshot = {
        "evaluation": {
            "outcome": "supported",
            "validity_pack_key": "generic-predictive:v2",
            "allowed_recommendations": ["advance_to_learn", "repeat_test"],
        },
        "evidence_hash": "b" * 64,
        "meeting": {"requirement": "complete"},
    }

    _call, card = _test_card_messages(_decision(), snapshot, request_nonce="run-2", outcome=True)
    request = card.artifact["human_input"]

    assert request["request_id"].startswith(TEST_OUTCOME_PREFIX)
    assert [item["id"] for item in request["options"]] == [
        "advance_to_learn",
        "repeat_test",
    ]


def test_outcome_card_binds_the_deck_that_owns_its_route_control():
    snapshot = {
        "evaluation": {
            "outcome": "supported",
            "validity_pack_key": "generic-predictive:v2",
            "allowed_recommendations": ["advance_to_learn"],
        },
        "evidence_hash": "b" * 64,
        "feedback_surface_id": "dfs-test-review",
        "meeting": {"requirement": "complete"},
    }

    _call, card = _test_card_messages(_decision(), snapshot, request_nonce="run-3", outcome=True)

    assert card.artifact["human_input"]["design_feedback_surface_id"] == "dfs-test-review"


def test_rollout_cursor_projects_build_approved_test_active_as_test():
    cycle = SimpleNamespace(
        id="cycle-1",
        project_id="project-1",
        parent_cycle_id=None,
        originating_thread_id="thread-1",
        title="Tiny holdout",
        cycle_class="computational",
        state="build",
        policy_version="v1",
        db_revision=14,
        projection_hash="hash",
        projection_json={},
        created_by="user-1",
        created_at=None,
        updated_at=None,
    )
    statuses = {
        "design": "approved",
        "reconciliation": "locked",
        "build": "approved",
        "test": "awaiting_review",
        "learn": "locked",
    }
    stages = [
        SimpleNamespace(
            id=f"attempt-{stage}",
            stage=stage,
            status=status,
            attempt_number=1,
            db_revision=14,
            updated_at=None,
        )
        for stage, status in statuses.items()
    ]

    payload = DbtlCycleRepository._cycle_payload(cycle, stages)

    assert payload["state"] == "test"
