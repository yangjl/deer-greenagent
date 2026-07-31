"""Stage-graph transition records ride on gate decisions (Phase 0).

Every human-decided boundary appends exactly one row; reconciliation reviews
append none, because reconciliation is a Build-edge precondition rather than a
stage on the path. The path history must survive replayed requests unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl.validity import DEFAULT_VALIDITY_PACK
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.dbtl.model import (
    DbtlReviewRow,
    DbtlStageTransitionImmutable,
    DbtlStageTransitionRow,
)
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
    workspace = await workspaces.create_workspace(workspace_id="ws-1", name="Maize", slug="maize", description=None, created_by="user-1")
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


async def _submit(repo: DbtlCycleRepository, stage: str, key: str) -> None:
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage=stage,
        expected_db_revision=await _revision(repo),
        actor_user_id="user-1",
        idempotency_key=f"submit-{key}",
    )


async def _review(repo: DbtlCycleRepository, stage: str, key: str, decision: str = "approve") -> None:
    await repo.review_stage(
        cycle_id="cycle-1",
        project_id="project-1",
        stage=stage,
        decision=decision,
        rationale=f"{stage}: {decision}.",
        expected_db_revision=await _revision(repo),
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        idempotency_key=f"review-{key}",
    )


async def _approve(repo: DbtlCycleRepository, stage: str, key: str) -> None:
    await _attach(repo, stage, key)
    await _submit(repo, stage, key)
    await _review(repo, stage, key)


async def _reconcile(repo: DbtlCycleRepository) -> None:
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


async def _transitions(repo: DbtlCycleRepository) -> list[dict]:
    return await repo.list_stage_transitions(cycle_id="cycle-1", project_id="project-1")


async def test_design_approval_appends_one_design_to_build_edge(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _approve(repo, "design", "design")

    rows = await _transitions(repo)
    assert len(rows) == 1
    row = rows[0]
    assert (row["seq"], row["from_stage"], row["chosen_route"], row["to_stage"]) == (1, "design", "approve", "build")
    assert row["decided_by"] == "reviewer-1"
    assert row["evidence_hash"] == HASH_B
    assert row["from_attempt"] == 1
    assert row["backfilled"] is False
    assert row["policy_version"] == "greenagent-dbtl-v2-draft"


async def test_review_meeting_annotation_never_replaces_core_verdict_evidence(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    core = await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        artifact_type="design_package",
        uri="/mnt/user-data/outputs/design-package.json",
        content_hash=HASH_B,
        created_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="artifact-design-core",
    )
    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        artifact_type="design_review_meeting",
        uri="/mnt/user-data/outputs/design-review-meeting.json",
        content_hash=HASH_A,
        created_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="artifact-design-meeting",
    )
    meeting = await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        artifact_type="design_review_meeting",
        uri="/mnt/user-data/outputs/design-review-meeting-rev2.json",
        content_hash="c" * 64,
        created_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="artifact-design-meeting-rev2",
    )
    assert meeting["revision"] > core["revision"]

    await _submit(repo, "design", "core-after-meeting")
    await _review(repo, "design", "core-after-meeting")

    sf = get_session_factory()
    assert sf is not None
    async with sf() as session:
        review = await session.scalar(
            select(DbtlReviewRow).where(
                DbtlReviewRow.cycle_id == "cycle-1",
            )
        )
    assert review is not None
    assert review.artifact_id == core["id"]
    assert review.artifact_revision == core["revision"]

    rows = await _transitions(repo)
    assert rows[-1]["evidence_hash"] == HASH_B


async def test_reconciliation_review_appends_no_edge(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _approve(repo, "design", "design")
    await _reconcile(repo)

    rows = await _transitions(repo)
    assert [row["from_stage"] for row in rows] == ["design"]


async def test_changes_requested_records_a_revise_edge(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _attach(repo, "design", "d1")
    await _submit(repo, "design", "d1")
    await _review(repo, "design", "d1", decision="request_changes")

    rows = await _transitions(repo)
    assert len(rows) == 1
    assert (rows[0]["chosen_route"], rows[0]["to_stage"]) == ("request_changes", "design")


async def test_replayed_review_does_not_duplicate_the_edge(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _attach(repo, "design", "design")
    await _submit(repo, "design", "design")
    revision = await _revision(repo)
    for _ in range(2):
        await repo.review_stage(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="design",
            decision="approve",
            rationale="design: approve.",
            expected_db_revision=revision,
            reviewer_user_id="reviewer-1",
            reviewer_project_role="owner",
            idempotency_key="review-design",
        )

    assert len(await _transitions(repo)) == 1


async def test_park_binds_unapproved_evidence_and_a_gate_action_unparks(
    tmp_path: Path,
) -> None:
    repo = await _repo(tmp_path)
    await _attach(repo, "design", "parked-design")
    before_park = await repo.get_cycle("cycle-1", project_id="project-1")
    assert before_park is not None
    artifact_id = before_park["artifacts"][-1]["id"]
    parked = await repo.park_cycle(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        expected_db_revision=await _revision(repo),
        actor_user_id="reviewer-1",
        idempotency_key="park-design",
        decision_surface_id="surface-1",
        assessed_difficulty="routine",
        assessment_rationale="The next action is bounded and reversible.",
        human_override=None,
        offered_routes=["advance", "park"],
    )

    assert parked["parked"] is True
    assert parked["parked_stage"] == "design"
    assert parked["parked_evidence"] == {
        "artifact_id": artifact_id,
        "revision": 1,
        "content_hash": HASH_B,
        "uri": "/mnt/user-data/outputs/design-parked-design.json",
        "approval_status": "unapproved",
    }
    rows = await _transitions(repo)
    assert [(row["chosen_route"], row["to_stage"]) for row in rows] == [("park", "design")]

    resumed = await repo.review_stage(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        decision="approve",
        rationale="Continue after discussion.",
        expected_db_revision=parked["db_revision"],
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        idempotency_key="approve-parked-design",
        progressive_transition={
            "assessed_difficulty": "routine",
            "assessment_rationale": "The next action is bounded and reversible.",
            "human_override": None,
            "offered_routes": ["advance", "park"],
        },
        auto_submit=True,
    )

    assert resumed["parked"] is False
    assert resumed["parked_stage"] is None
    assert resumed["parked_evidence"] is None
    rows = await _transitions(repo)
    assert [row["chosen_route"] for row in rows] == ["park", "approve"]


async def test_transition_rows_refuse_update_and_delete(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _approve(repo, "design", "design")
    sf = get_session_factory()
    assert sf is not None

    async with sf() as session:
        row = await session.scalar(select(DbtlStageTransitionRow))
        assert row is not None
        row.to_stage = "learn"
        with pytest.raises(DbtlStageTransitionImmutable, match="append-only"):
            await session.flush()
        await session.rollback()

    async with sf() as session:
        row = await session.scalar(select(DbtlStageTransitionRow))
        assert row is not None
        await session.delete(row)
        with pytest.raises(DbtlStageTransitionImmutable, match="append-only"):
            await session.flush()
        await session.rollback()

    rows = await _transitions(repo)
    assert [(row["from_stage"], row["to_stage"]) for row in rows] == [("design", "build")]


async def test_test_assessment_appends_a_test_edge_with_the_recommended_route(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _approve(repo, "design", "design")
    await _reconcile(repo)
    await repo.record_build_lineage(
        cycle_id="cycle-1",
        project_id="project-1",
        code_revision="git:1234567",
        config_revision="config:sha256:def",
        environment={"python": "3.12", "platform": "linux"},
        input_artifacts=["artifact://approved-reconciliation"],
        output_artifacts=[{"uri": "/mnt/user-data/outputs/model.bin", "content_hash": HASH_B, "revision": 1}],
        deviations=[],
        logs_uri="/mnt/user-data/outputs/build.log",
        recorded_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="lineage",
    )
    await _approve(repo, "build", "build")
    await _attach(repo, "test", "test")
    await _submit(repo, "test", "test")
    checks = [
        {"check": item.value, "status": "failed" if item.value == "leakage" else "passed", "detail": f"{item.value} evidence", "evidence_refs": [f"artifact://{item.value}"] if item.value != "leakage" else []}
        for item in DEFAULT_VALIDITY_PACK.required_checks
    ]
    await repo.record_validity_assessment(
        cycle_id="cycle-1",
        project_id="project-1",
        metrics=[{"name": "accuracy", "value": 0.94, "threshold": 0.70, "criterion": "gte"}],
        checks=checks,
        recommendation="return_to_build",
        limitations=[],
        rationale="Leakage invalidates the headline result.",
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        expected_db_revision=await _revision(repo),
        idempotency_key="assessment",
    )

    rows = await _transitions(repo)
    assert [(row["from_stage"], row["chosen_route"], row["to_stage"]) for row in rows] == [
        ("design", "approve", "build"),
        ("build", "approve", "test"),
        ("test", "return_to_build", "build"),
    ]
    assert [row["seq"] for row in rows] == [1, 2, 3]


async def test_return_to_design_records_revisit_and_invalidates_forward_stage_statuses(
    tmp_path: Path,
) -> None:
    repo = await _repo(tmp_path)
    await _approve(repo, "design", "design")
    await _reconcile(repo)
    await repo.record_build_lineage(
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
        idempotency_key="lineage-revisit",
    )
    await _approve(repo, "build", "build")
    await _attach(repo, "test", "test-revisit")
    await _submit(repo, "test", "test-revisit")
    checks = [
        {
            "check": item.value,
            "status": "failed" if item.value == "leakage" else "passed",
            "detail": f"{item.value} evidence",
            "evidence_refs": ([f"artifact://{item.value}"] if item.value != "leakage" else []),
        }
        for item in DEFAULT_VALIDITY_PACK.required_checks
    ]
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
        checks=checks,
        recommendation="return_to_design",
        limitations=[],
        rationale="The leakage requires a new design.",
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        expected_db_revision=await _revision(repo),
        idempotency_key="assessment-revisit",
    )

    assert result["cycle"]["state"] == "design"
    statuses = {item["stage"]: item["status"] for item in result["cycle"]["stages"]}
    assert statuses == {
        "design": "changes_requested",
        "reconciliation": "locked",
        "build": "locked",
        "test": "locked",
        "learn": "locked",
    }
    rows = await _transitions(repo)
    assert [(row["from_stage"], row["chosen_route"], row["to_stage"]) for row in rows] == [
        ("design", "approve", "build"),
        ("build", "approve", "test"),
        ("test", "return_to_design", "design"),
    ]


async def test_abandon_records_a_close_edge_from_the_working_stage(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.abandon_cycle(
        cycle_id="cycle-1",
        project_id="project-1",
        expected_db_revision=await _revision(repo),
        actor_user_id="user-1",
        idempotency_key="abandon",
        rationale="Superseded by a new question.",
    )

    rows = await _transitions(repo)
    assert len(rows) == 1
    assert (rows[0]["from_stage"], rows[0]["chosen_route"], rows[0]["to_stage"]) == ("design", "close_cycle", "abandoned")


async def test_an_edge_decided_on_a_deck_names_the_conversation_that_deck_answers(tmp_path: Path) -> None:
    """The timeline's deck link needs a destination. The transition row names
    only the surface; the surface row alone knows which conversation it may
    answer, so the read model joins the two — a client cannot, because the
    surface read endpoint requires a viewer thread the rail does not have."""
    repo = await _repo(tmp_path)
    await _attach(repo, "design", "deck-design")
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    attempt_id = next(item["id"] for item in cycle["stages"] if item["stage"] == "design")
    surface = await repo.register_stage_feedback_surface(
        stage="design",
        project_id="project-1",
        cycle_id="cycle-1",
        stage_attempt_id=attempt_id,
        design_round=1,
        originating_thread_id="thread-1",
        mode="read_only",
        deck_uri="/mnt/user-data/outputs/design-slides.html",
        deck_content_hash="c" * 64,
    )
    await repo.park_cycle(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        expected_db_revision=await _revision(repo),
        actor_user_id="reviewer-1",
        idempotency_key="park-on-deck",
        decision_surface_id=surface["surface_id"],
        assessed_difficulty="routine",
        assessment_rationale="Bounded next action.",
        human_override=None,
        offered_routes=["advance", "park"],
    )

    rows = await _transitions(repo)
    assert rows[-1]["decision_surface_id"] == surface["surface_id"]
    assert rows[-1]["decided_in_thread_id"] == "thread-1"


async def test_an_edge_decided_off_deck_carries_no_thread_link(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _approve(repo, "design", "design")

    rows = await _transitions(repo)
    assert rows[-1]["decision_surface_id"] is None
    assert rows[-1]["decided_in_thread_id"] is None


async def test_history_is_ordered_and_scoped_to_the_cycle(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await _approve(repo, "design", "design")

    assert await repo.list_stage_transitions(cycle_id="cycle-1", project_id="other-project") == []
    assert await repo.list_stage_transitions(cycle_id="missing", project_id="project-1") == []
    rows = await _transitions(repo)
    assert rows == sorted(rows, key=lambda row: row["seq"])
