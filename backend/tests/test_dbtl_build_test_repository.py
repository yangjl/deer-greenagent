"""Phase 7 Build lineage and Test validity through durable storage."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl.validity import DEFAULT_VALIDITY_PACK, ValidityRefused
from deerflow.persistence.dbtl import DbtlCycleRepository, DbtlWorkflowRefused
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

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


async def _record_lineage(repo: DbtlCycleRepository):
    return await repo.record_build_lineage(
        cycle_id="cycle-1",
        project_id="project-1",
        code_revision="git:1234567",
        config_revision="config:sha256:def",
        environment={"python": "3.12", "platform": "linux"},
        input_artifacts=["artifact://approved-reconciliation"],
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
    assert lineage["output_artifacts"][0]["content_hash"] == HASH_B
    view = await repo.build_test_view("cycle-1", project_id="project-1")
    assert view["build_lineage"]["id"] == lineage["id"]


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


async def test_legacy_return_to_reconciliation_is_refused_by_stage_graph(
    tmp_path: Path,
) -> None:
    repo = await _repo(tmp_path)
    await _awaiting_test_review(repo)
    revision = await _revision(repo)

    with pytest.raises(
        ValidityRefused,
        match="Reconciliation is not a cycle-stage destination",
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
