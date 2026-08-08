"""Phase 7 Build lineage and Test validity through durable storage."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl.validity import DEFAULT_VALIDITY_PACK, ValidityRefused
from deerflow.persistence.dbtl import DbtlCycleRepository, DbtlWorkflowRefused, DesignFeedbackConflict
from deerflow.persistence.dbtl.model import DbtlStageAttemptRow
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository


@pytest.fixture(autouse=True)
def _shipped_dbtl_gates(build_workflow_steps_off):
    """Pin the optional phased-Build rule for this suite."""


pytestmark = pytest.mark.asyncio

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest_asyncio.fixture(autouse=True)
async def _close_test_engine():
    yield
    await close_engine()


async def _repo(tmp_path: Path) -> DbtlCycleRepository:
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    sf = get_session_factory()
    assert sf is not None
    workspaces = WorkspaceRepository(sf)
    workspace = await workspaces.create_workspace(
        workspace_id="ws-1",
        name="Maize",
        slug="maize",
        description=None,
        created_by="user-1",
    )
    await workspaces.create_project(
        project_id="project-1",
        workspace_id=workspace["id"],
        name="Drought",
        slug="drought",
        description=None,
        crop_profile="maize",
        created_by="user-1",
    )
    repo = DbtlCycleRepository(sf)
    await repo.create_cycle(
        cycle_id="cycle-1",
        project_id="project-1",
        title="Drought model",
        cycle_class="computational",
        research_question="Does the model generalize?",
        objective="Test an independent population",
        success_criteria="Accuracy >= 0.7 with every validity gate passing",
        created_by="user-1",
        policy_version="greenagent-dbtl-v2-draft",
        idempotency_key="create",
    )
    return repo


async def _revision(repo: DbtlCycleRepository) -> int:
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    return int(cycle["db_revision"])


async def _attach(repo: DbtlCycleRepository, stage: str, key: str) -> None:
    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage=stage,
        artifact_type=f"{stage}_package",
        uri=f"/mnt/user-data/outputs/{stage}-{key}.json",
        content_hash=HASH_B,
        created_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key=f"artifact-{key}",
    )


async def _approve(repo: DbtlCycleRepository, stage: str, key: str) -> None:
    await _attach(repo, stage, key)
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage=stage,
        expected_db_revision=await _revision(repo),
        actor_user_id="user-1",
        idempotency_key=f"submit-{key}",
    )
    await repo.review_stage(
        cycle_id="cycle-1",
        project_id="project-1",
        stage=stage,
        decision="approve",
        rationale=f"{stage} evidence is complete.",
        expected_db_revision=await _revision(repo),
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        idempotency_key=f"review-{key}",
    )


async def _ready_for_build(repo: DbtlCycleRepository) -> None:
    await _approve(repo, "design", "design")
    await repo.declare_dataset(
        cycle_id="cycle-1",
        project_id="project-1",
        source_key="yield",
        uri="/mnt/user-data/workspace/yield.csv",
        content_hash=HASH_A,
        recorded_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="dataset",
    )
    row = await repo.open_reconciliation_row(
        cycle_id="cycle-1",
        project_id="project-1",
        check="units_and_encoding",
        field_name="Yield units",
        created_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="row",
    )
    await repo.decide_reconciliation_row(
        row_id=row["id"],
        project_id="project-1",
        status="resolved",
        resolution="Converted to Mg/ha.",
        actor_type="human",
        actor_user_id="reviewer-1",
        expected_db_revision=await _revision(repo),
        expected_work_item_revision=row["db_revision"],
        idempotency_key="row-decision",
    )
    await _approve(repo, "reconciliation", "reconciliation")


async def _record_lineage(repo: DbtlCycleRepository, *, rerun_spec: dict | None = None, typed: bool = True):
    if typed and rerun_spec is None:
        rerun_spec = {
            "version": 1,
            "entry_point": "/mnt/user-data/fit.py",
            "command": "uv run python fit.py --seed 7",
            "seed": 7,
            "inputs": ["/mnt/user-data/yield.csv"],
            "environment": {"python": "3.12", "uv": "pinned lockfile"},
            "configuration": ["/mnt/user-data/pyproject.toml"],
            "expected_outputs": ["/mnt/user-data/outputs/model.bin"],
        }
    return await repo.record_build_lineage(
        cycle_id="cycle-1",
        project_id="project-1",
        code_revision="git:1234567",
        config_revision="config:sha256:def",
        environment={"python": "3.12", "platform": "linux"},
        rerun_spec=rerun_spec,
        input_artifacts=[f"workspace_file:yield.csv:sha256:{HASH_A}"],
        output_artifacts=[
            {
                "uri": "/mnt/user-data/outputs/model.bin",
                "content_hash": HASH_B,
                "revision": 1,
            }
        ],
        deviations=[],
        logs_uri="/mnt/user-data/outputs/build.log",
        recorded_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="lineage",
    )


async def _awaiting_test_review(repo: DbtlCycleRepository) -> None:
    await _ready_for_build(repo)
    await _record_lineage(repo)
    await _approve(repo, "build", "build")
    await _attach(repo, "test", "test")
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        expected_db_revision=await _revision(repo),
        actor_user_id="user-1",
        idempotency_key="submit-test",
    )


async def _advance_build_with_exception(repo: DbtlCycleRepository) -> None:
    await _ready_for_build(repo)
    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="build",
        artifact_type="evidence_exception",
        uri="/mnt/user-data/outputs/build-exception.json",
        content_hash=HASH_B,
        created_by="server",
        expected_db_revision=await _revision(repo),
        idempotency_key="build-exception-artifact",
    )
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="build",
        expected_db_revision=await _revision(repo),
        actor_user_id="user-1",
        idempotency_key="submit-build-exception",
    )
    await repo.review_stage(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="build",
        decision="advanced_with_exception",
        rationale="Continue only to record and evaluate the failed evidence.",
        expected_db_revision=await _revision(repo),
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        idempotency_key="review-build-exception",
        design_feedback_provenance={
            "input_source": "stage_deck",
            "evidence_exception": {"content_hash": HASH_B},
        },
    )


async def test_exception_intent_refusals_do_not_consume_the_action_ledger(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _ready_for_build(repo)
    artifact = await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="build",
        artifact_type="evidence_exception",
        uri="/mnt/user-data/outputs/build-exception.json",
        content_hash=HASH_B,
        created_by="server",
        expected_db_revision=await _revision(repo),
        idempotency_key="build-exception-for-surface",
    )
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    build_attempt = next(item for item in cycle["stages"] if item["stage"] == "build")
    surface = await repo.register_stage_feedback_surface(
        stage="build",
        project_id="project-1",
        cycle_id="cycle-1",
        stage_attempt_id=build_attempt["id"],
        design_round=1,
        originating_thread_id="thread-1",
        mode="stage_review",
        deck_uri="/mnt/user-data/outputs/build-exception.html",
        deck_content_hash=HASH_A,
        decision_request={
            "transition_gate": {
                "stage": "build",
                "evidence_exception": {
                    "content_hash": HASH_B,
                    "reason_codes": ["deliverable_attempt_failed"],
                },
            }
        },
        evidence_artifact_id=artifact["id"],
        evidence_artifact_revision=artifact["revision"],
        evidence_content_hash=HASH_B,
    )
    base = {
        "project_id": "project-1",
        "cycle_id": "cycle-1",
        "surface_id": surface["surface_id"],
        "originating_thread_id": "thread-1",
        "action_kind": "continue_with_red_flag",
        "selected_card_ids": [],
        "expected_db_revision": int(cycle["db_revision"]),
        "expected_evidence": {
            "artifact_id": artifact["id"],
            "revision": artifact["revision"],
            "content_hash": HASH_B,
        },
        "expected_deck_hash": HASH_A,
    }

    with pytest.raises(DesignFeedbackConflict, match="disabled"):
        await repo.reserve_stage_feedback_action(
            **base,
            human_comment="Continue with this limitation.",
            client_submission_id="exception-disabled",
            degraded_evidence_continuation=False,
        )
    assert await repo.stage_feedback_actions(surface["surface_id"], project_id="project-1") == []

    with pytest.raises(DesignFeedbackConflict, match="written rationale"):
        await repo.reserve_stage_feedback_action(
            **base,
            human_comment="",
            client_submission_id="exception-no-rationale",
            degraded_evidence_continuation=True,
        )
    assert await repo.stage_feedback_actions(surface["surface_id"], project_id="project-1") == []

    legacy = await repo.register_stage_feedback_surface(
        stage="build",
        project_id="project-1",
        cycle_id="cycle-1",
        stage_attempt_id=build_attempt["id"],
        design_round=2,
        originating_thread_id="thread-1",
        mode="stage_review",
        deck_uri="/mnt/user-data/outputs/build-legacy.html",
        deck_content_hash="d" * 64,
        decision_request={"transition_gate": {"stage": "build"}},
        evidence_artifact_id=artifact["id"],
        evidence_artifact_revision=artifact["revision"],
        evidence_content_hash=HASH_B,
    )
    with pytest.raises(DesignFeedbackConflict, match="active evidence exception"):
        await repo.reserve_stage_feedback_action(
            **{**base, "surface_id": legacy["surface_id"], "expected_deck_hash": "d" * 64},
            human_comment="Continue with this limitation.",
            client_submission_id="exception-legacy",
            degraded_evidence_continuation=True,
        )
    assert await repo.stage_feedback_actions(legacy["surface_id"], project_id="project-1") == []


async def test_supported_test_preserves_the_degraded_build_limitation_into_learn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "deerflow.persistence.dbtl.cycles.degraded_evidence_continuation_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "deerflow.persistence.dbtl.build_test_ops.degraded_evidence_continuation_enabled",
        lambda: True,
    )
    repo = await _repo(tmp_path)
    await _ready_for_build(repo)
    await _record_lineage(repo)
    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="build",
        artifact_type="evidence_exception",
        uri="/mnt/user-data/outputs/build-exception.json",
        content_hash=HASH_B,
        created_by="server",
        expected_db_revision=await _revision(repo),
        idempotency_key="supported-build-exception",
    )
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="build",
        expected_db_revision=await _revision(repo),
        actor_user_id="server",
        idempotency_key="submit-supported-build-exception",
    )
    await repo.review_stage(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="build",
        decision="advanced_with_exception",
        rationale="The numeric result is trustworthy, but the notebook deliverable failed.",
        expected_db_revision=await _revision(repo),
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        idempotency_key="review-supported-build-exception",
        design_feedback_provenance={
            "input_source": "stage_deck",
            "evidence_exception": {"content_hash": HASH_B},
        },
    )
    await _attach(repo, "test", "supported-test")
    test_exception = await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        artifact_type="evidence_exception",
        uri="/mnt/user-data/outputs/test-inherited-exception.json",
        content_hash=HASH_A,
        created_by="server",
        expected_db_revision=await _revision(repo),
        idempotency_key="supported-test-exception",
    )
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        expected_db_revision=await _revision(repo),
        actor_user_id="server",
        idempotency_key="submit-supported-test",
    )

    result = await repo.record_validity_assessment(
        cycle_id="cycle-1",
        project_id="project-1",
        metrics=[{"name": "accuracy", "value": 0.8, "threshold": 0.7, "criterion": "gte"}],
        checks=_checks(),
        recommendation="advance_to_learn",
        limitations=[],
        rationale="The human accepted the supported Test outcome with the inherited Build limitation.",
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        expected_db_revision=await _revision(repo),
        idempotency_key="supported-test-to-learn",
    )

    assessment = result["validity_assessment"]
    assert assessment["outcome"] == "supported"
    assert assessment["evidence_exception_artifact_id"] == test_exception["id"]
    assert assessment["evidence_exception_hash"] == HASH_A
    assert any(HASH_A in item and "limits" in item for item in assessment["limitations"])
    assert result["cycle"]["state"] == "learn"


def _checks(**overrides: str) -> list[dict]:
    statuses = {item.value: "passed" for item in DEFAULT_VALIDITY_PACK.required_checks}
    statuses.update(overrides)
    return [
        {
            "check": check,
            "status": status,
            "detail": f"{check} evidence",
            "evidence_refs": [f"artifact://{check}"] if status == "passed" else [],
        }
        for check, status in statuses.items()
    ]


async def test_build_lineage_binds_inputs_environment_and_outputs(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _ready_for_build(repo)
    lineage = await _record_lineage(repo)

    assert lineage["dataset_fingerprint"]
    assert lineage["code_revision"] == "git:1234567"
    assert lineage["environment"]["python"] == "3.12"
    assert lineage["rerun_status"] == "verified"
    assert lineage["rerun_spec"]["command"] == "uv run python fit.py --seed 7"
    assert lineage["output_artifacts"][0]["content_hash"] == HASH_B
    view = await repo.build_test_view("cycle-1", project_id="project-1")
    assert view["build_lineage"]["id"] == lineage["id"]
    assert view["build_lineage"]["rerun_spec"] == lineage["rerun_spec"]


async def test_invalid_typed_rerun_record_is_refused_before_persistence(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _ready_for_build(repo)

    with pytest.raises(ValueError, match="invalid structured rerun"):
        await _record_lineage(repo, rerun_spec={"command": "python fit.py"})


async def test_current_build_contract_refuses_missing_typed_rerun_record(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _ready_for_build(repo)

    # Version-agnostic on purpose: the assertion is that the refusal names the
    # contract the attempt is pinned to, not that the default is any one
    # version — pinning the number here breaks on every default bump and says
    # nothing about the behaviour under test.
    with pytest.raises(ValueError, match=r"generic:build:v\d+ requires"):
        await _record_lineage(repo, typed=False)


async def test_build_cannot_be_submitted_without_lineage(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _ready_for_build(repo)
    await _attach(repo, "build", "build")
    with pytest.raises(DbtlWorkflowRefused, match="lineage"):
        await repo.submit_stage_for_review(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="build",
            expected_db_revision=await _revision(repo),
            actor_user_id="user-1",
            idempotency_key="submit-build",
        )


async def test_exception_dossiers_advance_build_and_invalidated_test_without_lineage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "deerflow.persistence.dbtl.cycles.degraded_evidence_continuation_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "deerflow.persistence.dbtl.build_test_ops.degraded_evidence_continuation_enabled",
        lambda: True,
    )
    repo = await _repo(tmp_path)
    await _advance_build_with_exception(repo)

    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    statuses = {item["stage"]: item["status"] for item in cycle["stages"]}
    assert statuses["build"] == "advanced_with_exception"
    assert statuses["test"] == "in_progress"

    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        artifact_type="evidence_exception",
        uri="/mnt/user-data/outputs/test-exception.json",
        content_hash=HASH_A,
        created_by="server",
        expected_db_revision=await _revision(repo),
        idempotency_key="test-exception-artifact",
    )
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        expected_db_revision=await _revision(repo),
        actor_user_id="server",
        idempotency_key="submit-test-exception",
    )
    result = await repo.record_validity_assessment(
        cycle_id="cycle-1",
        project_id="project-1",
        metrics=[
            {
                "name": "scientific_support",
                "value": 0.0,
                "threshold": 1.0,
                "criterion": "gte",
            }
        ],
        checks=_checks(reproducibility="failed"),
        recommendation="learn_from_invalidated_evidence",
        limitations=["No trusted Build execution exists."],
        rationale="Record the invalidated result and carry only process lessons into Learn.",
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        expected_db_revision=await _revision(repo),
        idempotency_key="invalidated-exception-assessment",
        review_provenance={
            "input_source": "stage_deck",
            "feedback_surface_id": "dfs-test-exception",
            "selected_action": "continue_with_red_flag",
        },
    )

    assessment = result["validity_assessment"]
    assert assessment["outcome"] == "invalidated"
    assert assessment["build_lineage_id"] is None
    assert assessment["evidence_exception_hash"] == HASH_A
    assert assessment["evidence_exception_artifact_id"]
    assert result["cycle"]["state"] == "learn"
    statuses = {item["stage"]: item["status"] for item in result["cycle"]["stages"]}
    assert statuses["test"] == "advanced_with_exception"
    assert statuses["learn"] == "in_progress"
    transitions = await repo.list_stage_transitions(
        cycle_id="cycle-1",
        project_id="project-1",
    )
    assert transitions[-1]["decision_surface_id"] == "dfs-test-exception"


async def test_invalidated_test_binds_a_supplemental_exception_beside_the_validity_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "deerflow.persistence.dbtl.build_test_ops.degraded_evidence_continuation_enabled",
        lambda: True,
    )
    repo = await _repo(tmp_path)
    await _ready_for_build(repo)
    await _record_lineage(repo)
    await _approve(repo, "build", "build")
    await _attach(repo, "test", "test")
    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        artifact_type="evidence_exception",
        uri="/mnt/user-data/outputs/test-exception.json",
        content_hash=HASH_A,
        created_by="server",
        expected_db_revision=await _revision(repo),
        idempotency_key="supplemental-test-exception",
    )
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        expected_db_revision=await _revision(repo),
        actor_user_id="server",
        idempotency_key="submit-supplemental-test-exception",
    )

    result = await repo.record_validity_assessment(
        cycle_id="cycle-1",
        project_id="project-1",
        metrics=[
            {
                "name": "holdout_mae",
                "value": 0.0,
                "threshold": 0.0,
                "criterion": "lte",
            }
        ],
        checks=_checks(reproducibility="failed"),
        recommendation="learn_from_invalidated_evidence",
        limitations=["The server-owned rerun path was unavailable."],
        rationale="Keep the typed validity pack and its red-flag dossier together.",
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        expected_db_revision=await _revision(repo),
        idempotency_key="supplemental-invalidated-assessment",
    )

    assessment = result["validity_assessment"]
    assert assessment["outcome"] == "invalidated"
    assert assessment["build_lineage_id"]
    assert assessment["evidence_exception_hash"] == HASH_A
    assert assessment["evidence_exception_artifact_id"]


async def test_invalidated_exception_can_request_a_human_guided_test_retry_without_lineage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "deerflow.persistence.dbtl.cycles.degraded_evidence_continuation_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "deerflow.persistence.dbtl.build_test_ops.degraded_evidence_continuation_enabled",
        lambda: True,
    )
    repo = await _repo(tmp_path)
    await _advance_build_with_exception(repo)
    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        artifact_type="evidence_exception",
        uri="/mnt/user-data/outputs/test-exception.json",
        content_hash=HASH_A,
        created_by="server",
        expected_db_revision=await _revision(repo),
        idempotency_key="test-exception-artifact",
    )
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        expected_db_revision=await _revision(repo),
        actor_user_id="server",
        idempotency_key="submit-test-exception",
    )

    result = await repo.record_validity_assessment(
        cycle_id="cycle-1",
        project_id="project-1",
        metrics=[{"name": "scientific_support", "value": 0.0, "threshold": 1.0, "criterion": "gte"}],
        checks=_checks(reproducibility="failed"),
        recommendation="repeat_test",
        limitations=["No trusted Build execution exists."],
        rationale="Retry after the human supplies environment guidance.",
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        expected_db_revision=await _revision(repo),
        idempotency_key="invalidated-exception-retry",
    )

    assessment = result["validity_assessment"]
    assert assessment["outcome"] == "invalidated"
    assert assessment["build_lineage_id"] is None
    assert assessment["evidence_exception_hash"] == HASH_A
    assert result["cycle"]["state"] == "test"
    statuses = {item["stage"]: item["status"] for item in result["cycle"]["stages"]}
    assert statuses["test"] == "changes_requested"


async def test_high_accuracy_and_leakage_routes_back_to_build(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _awaiting_test_review(repo)
    result = await repo.record_validity_assessment(
        cycle_id="cycle-1",
        project_id="project-1",
        metrics=[
            {
                "name": "accuracy",
                "value": 0.94,
                "threshold": 0.70,
                "criterion": "gte",
                # A refreshed Test deck sends the server's own projected
                # metric, including this derived field.
                "meets_threshold": True,
            }
        ],
        checks=_checks(leakage="failed"),
        recommendation="return_to_build",
        limitations=["Pooled metric is misleading."],
        rationale="Leakage invalidates the headline result.",
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        expected_db_revision=await _revision(repo),
        idempotency_key="assessment",
    )

    assert result["validity_assessment"]["outcome"] == "invalidated"
    assert result["cycle"]["state"] == "build"
    statuses = {item["stage"]: item["status"] for item in result["cycle"]["stages"]}
    assert statuses["build"] == "changes_requested"
    assert statuses["test"] == "locked"


async def test_legacy_return_to_reconciliation_is_not_an_allowed_recommendation(
    tmp_path: Path,
) -> None:
    repo = await _repo(tmp_path)
    await _awaiting_test_review(repo)
    revision = await _revision(repo)

    with pytest.raises(
        ValidityRefused,
        match="is not a valid WorkflowRecommendation",
    ):
        await repo.record_validity_assessment(
            cycle_id="cycle-1",
            project_id="project-1",
            metrics=[
                {
                    "name": "accuracy",
                    "value": 0.94,
                    "threshold": 0.70,
                    "criterion": "gte",
                }
            ],
            checks=_checks(leakage="failed"),
            recommendation="return_to_reconciliation",
            limitations=[],
            rationale="The input matrix should be checked again.",
            reviewer_user_id="reviewer-1",
            reviewer_project_role="owner",
            expected_db_revision=revision,
            idempotency_key="legacy-reconciliation-route",
        )

    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    assert cycle["state"] == "test"
    assert cycle["db_revision"] == revision
    assert (
        await repo.list_stage_transitions(
            cycle_id="cycle-1",
            project_id="project-1",
        )
    )[-1]["to_stage"] == "test"


async def test_missing_holdout_is_inconclusive_and_can_close(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _awaiting_test_review(repo)
    result = await repo.record_validity_assessment(
        cycle_id="cycle-1",
        project_id="project-1",
        metrics=[{"name": "rmse", "value": 0.31, "threshold": 0.40, "criterion": "lte"}],
        checks=_checks(tester_holdout="missing"),
        recommendation="close_cycle",
        limitations=["No independent tester holdout."],
        rationale="Close without a scientific claim.",
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        expected_db_revision=await _revision(repo),
        idempotency_key="assessment",
    )
    assert result["validity_assessment"]["outcome"] == "inconclusive"
    assert result["cycle"]["state"] == "completed"


async def test_valid_negative_is_eligible_for_learn(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _awaiting_test_review(repo)
    result = await repo.record_validity_assessment(
        cycle_id="cycle-1",
        project_id="project-1",
        metrics=[{"name": "accuracy", "value": 0.51, "threshold": 0.70, "criterion": "gte"}],
        checks=_checks(),
        recommendation="advance_to_learn",
        limitations=[],
        rationale="The well-powered negative result is informative.",
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        expected_db_revision=await _revision(repo),
        idempotency_key="assessment",
    )
    assert result["validity_assessment"]["outcome"] == "not_supported"
    assert result["cycle"]["state"] == "learn"


async def test_generic_test_review_is_refused(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _awaiting_test_review(repo)
    with pytest.raises(DbtlWorkflowRefused, match="validity assessment"):
        await repo.review_stage(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="test",
            decision="approve",
            rationale="Looks good.",
            expected_db_revision=await _revision(repo),
            reviewer_user_id="reviewer-1",
            reviewer_project_role="owner",
            idempotency_key="generic-test-review",
        )


async def test_test_review_meeting_evidence_can_be_recorded_while_awaiting_review(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _ready_for_build(repo)
    await _record_lineage(repo)
    await _approve(repo, "build", "build")
    await repo.record_worker_runs(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        stage_spec_key="generic:test:v4",
        results=[],
        actor_user_id="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="core-test-workers",
        artifact_type="test_package",
        artifact_uri="/mnt/user-data/outputs/test-package.json",
        artifact_content_hash=HASH_B,
    )
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        expected_db_revision=await _revision(repo),
        actor_user_id="user-1",
        idempotency_key="submit-recorded-test",
    )
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    attempt = next(item for item in cycle["stages"] if item["stage"] == "test")
    evidence = next(item for item in cycle["artifacts"] if item["stage_attempt_id"] == attempt["id"] and item["artifact_type"] == "test_package")

    await repo.record_worker_runs(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="test",
        stage_spec_key="generic:test-review:v1",
        results=[],
        actor_user_id="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="test-review-meeting",
        artifact_type="test_review_meeting",
        artifact_uri="/mnt/user-data/outputs/test-review-meeting.json",
        artifact_content_hash=HASH_A,
        reviewed_artifact_id=evidence["id"],
        reviewed_artifact_revision=evidence["revision"],
        reviewed_artifact_content_hash=evidence["content_hash"],
    )

    refreshed = await repo.get_cycle("cycle-1", project_id="project-1")
    assert refreshed is not None
    refreshed_attempt = next(item for item in refreshed["stages"] if item["stage"] == "test")
    assert refreshed_attempt["status"] == "awaiting_review"
    sf = get_session_factory()
    assert sf is not None
    async with sf() as session:
        recorded_attempt = await session.scalar(select(DbtlStageAttemptRow).where(DbtlStageAttemptRow.id == refreshed_attempt["id"]))
    assert recorded_attempt is not None
    assert recorded_attempt.stage_spec_key == "generic:test:v4"
    meeting = next(item for item in refreshed["artifacts"] if item["artifact_type"] == "test_review_meeting")
    assert meeting["reviewed_artifact_id"] == evidence["id"]


async def test_ordinary_test_workers_remain_blocked_while_awaiting_review(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _awaiting_test_review(repo)

    with pytest.raises(DbtlWorkflowRefused, match="awaiting_review"):
        await repo.record_worker_runs(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="test",
            stage_spec_key="generic:test:v4",
            results=[],
            actor_user_id="user-1",
            expected_db_revision=await _revision(repo),
            idempotency_key="ordinary-test-workers-after-submit",
        )


async def test_failed_review_meeting_can_retry_under_the_same_action_id(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _awaiting_test_review(repo)
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    attempt = next(item for item in cycle["stages"] if item["stage"] == "test")
    evidence = next(item for item in cycle["artifacts"] if item["stage_attempt_id"] == attempt["id"] and item["artifact_type"] == "test_package")
    surface = await repo.register_stage_feedback_surface(
        stage="test",
        project_id="project-1",
        cycle_id="cycle-1",
        stage_attempt_id=attempt["id"],
        design_round=1,
        originating_thread_id="thread-1",
        mode="stage_review",
        deck_uri="/mnt/user-data/outputs/test-slides.html",
        deck_content_hash=HASH_A,
        evidence_artifact_id=evidence["id"],
        evidence_artifact_revision=evidence["revision"],
        evidence_content_hash=evidence["content_hash"],
        decision_request={
            "transition_gate": {
                "stage": "test",
                "assessment": {"difficulty": "high_stakes"},
                "routes": [],
            }
        },
    )
    common = {
        "project_id": "project-1",
        "cycle_id": "cycle-1",
        "surface_id": surface["surface_id"],
        "originating_thread_id": "thread-1",
        "action_kind": "convene_review_meeting",
        "selected_card_ids": [],
        "client_submission_id": "review-meeting-retry",
        "expected_db_revision": int(cycle["db_revision"]),
        "expected_evidence": {
            "artifact_id": evidence["id"],
            "revision": evidence["revision"],
            "content_hash": evidence["content_hash"],
        },
        "expected_deck_hash": HASH_A,
    }
    _surface, first, replayed = await repo.reserve_stage_feedback_action(
        **common,
        human_comment="Check the holdout evidence.",
    )
    assert replayed is False
    await repo.update_stage_feedback_action(
        first["client_submission_id"],
        project_id="project-1",
        status="failed",
        run_id="run-failed-meeting",
        failure_code="resume_no_feedback_surface",
    )

    _surface, retried, reused = await repo.reserve_stage_feedback_action(
        **common,
        human_comment="Check the holdout and fold evidence.",
    )

    assert reused is True
    assert retried["status"] == "pending"
    assert retried["run_id"] is None
    assert retried["human_comment"] == "Check the holdout and fold evidence."
    assert retried["receipt"]["failed_attempts"][-1]["run_id"] == "run-failed-meeting"
