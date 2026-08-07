"""Phase 3: the durable cycle repository.

The outcome the plan asks for is that "restart, replay, or concurrent action
cannot lose or duplicate state". Those are three distinct mechanisms — a
revision check, an idempotency key, and a database constraint — so each is
tested on its own rather than assumed from a happy path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl import StageStatus
from deerflow.dbtl.validity import DEFAULT_VALIDITY_PACK
from deerflow.persistence.dbtl import (
    DbtlCycleRepository,
    DbtlReviewRow,
    DbtlRevisionConflict,
    DbtlWorkflowRefused,
)
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository


@pytest.fixture(autouse=True)
def _shipped_dbtl_gates(build_workflow_steps_off):
    """Pin the optional phased-Build rule for this suite."""


pytestmark = pytest.mark.asyncio

POLICY = "greenagent-dbtl-v2-draft"


@pytest_asyncio.fixture(autouse=True)
async def _close_test_engine():
    yield
    await close_engine()


async def _repos(tmp_path: Path) -> tuple[DbtlCycleRepository, str]:
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    workspaces = WorkspaceRepository(session_factory)
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
    return DbtlCycleRepository(session_factory), "project-1"


async def _cycle(repo: DbtlCycleRepository, project_id: str, **overrides):
    payload = {
        "cycle_id": "cycle-1",
        "project_id": project_id,
        "title": "Drought tolerance screen",
        "cycle_class": "computational",
        "research_question": "Which lines hold yield under late drought?",
        "objective": "Rank 200 lines",
        "success_criteria": "Top decile reproducible across two sites",
        "created_by": "user-1",
        "policy_version": POLICY,
        "idempotency_key": "create-1",
    }
    payload.update(overrides)
    return await repo.create_cycle(**payload)


# --------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------


async def test_a_new_cycle_starts_in_design_with_every_later_stage_locked(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)

    cycle = await _cycle(repo, project_id)

    assert cycle["state"] == "design"
    assert cycle["db_revision"] == 1
    statuses = {stage["stage"]: stage["status"] for stage in cycle["stages"]}
    assert statuses["design"] == StageStatus.IN_PROGRESS
    assert statuses["build"] == StageStatus.LOCKED


async def test_creation_records_the_research_question_and_criteria(tmp_path: Path) -> None:
    """A cycle is a research record; its question must be durable, not UI-only."""
    repo, project_id = await _repos(tmp_path)

    cycle = await _cycle(repo, project_id)

    assert cycle["research_question"] == "Which lines hold yield under late drought?"
    assert cycle["objective"] == "Rank 200 lines"
    assert cycle["success_criteria"] == "Top decile reproducible across two sites"
    assert cycle["cycle_weight"] == "full"


async def test_creation_records_the_originating_conversation_outside_the_governance_hash(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)

    cycle = await _cycle(
        repo,
        project_id,
        originating_thread_id="thread-origin",
    )
    loaded = await repo.get_cycle(cycle["id"], project_id=project_id)

    assert cycle["originating_thread_id"] == "thread-origin"
    assert loaded is not None
    assert loaded["originating_thread_id"] == "thread-origin"


async def test_creation_replay_cannot_rebind_the_originating_conversation(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    await _cycle(repo, project_id, originating_thread_id="thread-origin")

    with pytest.raises(DbtlWorkflowRefused, match="different cycle"):
        await _cycle(
            repo,
            project_id,
            cycle_id="cycle-2",
            originating_thread_id="thread-other",
        )


async def test_project_cycle_summary_distinguishes_new_and_active_projects(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)

    assert await repo.project_cycle_summary(project_id) == {
        "project_cycle_count": 0,
        "has_unfinished_cycles": False,
    }

    await _cycle(repo, project_id)

    assert await repo.project_cycle_summary(project_id) == {
        "project_cycle_count": 1,
        "has_unfinished_cycles": True,
    }


async def test_creation_is_idempotent_under_replay(tmp_path: Path) -> None:
    """A retried "Start a cycle" must not create a second research record."""
    repo, project_id = await _repos(tmp_path)

    first = await _cycle(repo, project_id)
    second = await _cycle(repo, project_id, cycle_id="cycle-2")

    assert second["id"] == first["id"]
    assert len(await repo.list_cycles(project_id)) == 1


async def test_creation_key_cannot_replay_with_a_different_research_record(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    await _cycle(repo, project_id)

    with pytest.raises(DbtlWorkflowRefused, match="different cycle"):
        await _cycle(
            repo,
            project_id,
            cycle_id="cycle-2",
            research_question="A different question",
        )


async def test_a_project_may_run_several_top_level_cycles_at_once(tmp_path: Path) -> None:
    """Parallel cycles are legitimate: different traits, populations, seasons.

    The single-active-cycle rule was dropped in migration 0018. The double-click
    guard it also provided lives on in the create-idempotency index, which the
    test above (`a reused create key ...`) pins separately.
    """
    repo, project_id = await _repos(tmp_path)
    first = await _cycle(repo, project_id)

    second = await _cycle(repo, project_id, cycle_id="cycle-2", idempotency_key="create-2")

    assert second["id"] != first["id"]
    live = [item for item in await repo.list_cycles(project_id) if item["state"] not in {"completed", "abandoned"}]
    assert {item["id"] for item in live} == {first["id"], second["id"]}


async def test_abandoning_a_cycle_retires_it_with_an_audited_revision(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)

    abandoned = await repo.abandon_cycle(
        cycle_id=cycle["id"],
        project_id=project_id,
        expected_db_revision=cycle["db_revision"],
        actor_user_id="researcher-1",
        idempotency_key="abandon-1",
        rationale="Created for a UI routing test.",
    )

    assert abandoned["state"] == "abandoned"
    assert abandoned["db_revision"] == cycle["db_revision"] + 1
    assert await repo.project_cycle_summary(project_id) == {
        "project_cycle_count": 1,
        "has_unfinished_cycles": False,
    }
    events = await repo.list_activity(cycle["id"], project_id=project_id)
    assert events[-1]["event_type"] == "cycle.abandoned"
    assert events[-1]["payload"]["rationale"] == "Created for a UI routing test."


async def test_abandoning_a_cycle_is_idempotent_but_cannot_rewrite_the_reason(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    payload = {
        "cycle_id": cycle["id"],
        "project_id": project_id,
        "expected_db_revision": cycle["db_revision"],
        "actor_user_id": "researcher-1",
        "idempotency_key": "abandon-1",
        "rationale": "Duplicate test cycle.",
    }

    first = await repo.abandon_cycle(**payload)
    replay = await repo.abandon_cycle(**payload)

    assert replay == first
    with pytest.raises(DbtlWorkflowRefused, match="different workflow action"):
        await repo.abandon_cycle(**{**payload, "rationale": "A different reason."})


async def test_a_child_cycle_may_start_beside_a_live_parent(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    parent = await _cycle(repo, project_id, cycle_class="season/program")

    child = await _cycle(
        repo,
        project_id,
        cycle_id="cycle-child",
        idempotency_key="create-child",
        cycle_class="computational",
        parent_cycle_id=parent["id"],
    )

    assert child["parent_cycle_id"] == parent["id"]
    assert {item["id"] for item in await repo.list_cycles(project_id)} == {parent["id"], child["id"]}


async def test_only_computational_children_may_use_a_live_season_parent(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    parent = await _cycle(repo, project_id, cycle_class="season/program")

    with pytest.raises(DbtlWorkflowRefused):
        await _cycle(
            repo,
            project_id,
            cycle_id="cycle-child",
            idempotency_key="create-child",
            cycle_class="other",
            parent_cycle_id=parent["id"],
        )


async def test_a_computational_cycle_cannot_parent_another_cycle(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    parent = await _cycle(repo, project_id)

    with pytest.raises(DbtlWorkflowRefused):
        await _cycle(
            repo,
            project_id,
            cycle_id="cycle-child",
            idempotency_key="create-child",
            parent_cycle_id=parent["id"],
        )


async def test_an_unknown_cycle_class_is_refused(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)

    with pytest.raises(ValueError):
        await _cycle(repo, project_id, cycle_class="vibes")


async def test_a_parent_from_another_project_is_refused(tmp_path: Path) -> None:
    """Cross-project parenting would leak one project's cycle into another."""
    repo, project_id = await _repos(tmp_path)
    await _cycle(repo, project_id)

    with pytest.raises(DbtlWorkflowRefused):
        await _cycle(
            repo,
            project_id,
            cycle_id="cycle-x",
            idempotency_key="create-x",
            parent_cycle_id="cycle-from-elsewhere",
        )


# --------------------------------------------------------------------------
# Stage review and the two-approval gate
# --------------------------------------------------------------------------


async def _attach(repo, project_id, cycle, stage, *, key):
    """Every stage review needs evidence; attach a fixture artifact first."""
    current = await repo.get_cycle(cycle["id"], project_id=project_id)
    assert current is not None
    return await repo.attach_artifact(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage=stage,
        artifact_type=f"{stage}_package",
        uri=f"/mnt/user-data/workspace/{stage}.json",
        content_hash=key.ljust(64, "0")[:64],
        created_by="user-1",
        expected_db_revision=current["db_revision"],
        idempotency_key=f"artifact-{stage}-{key}",
    )


async def _declare_dataset(repo, project_id, cycle, *, key, content_hash=None):
    """Phase 6: reconciliation cannot be reviewed with nothing declared."""
    current = await repo.get_cycle(cycle["id"], project_id=project_id)
    assert current is not None
    return await repo.declare_dataset(
        cycle_id=cycle["id"],
        project_id=project_id,
        source_key="yield_trial",
        uri="/mnt/user-data/workspace/yield.csv",
        content_hash=content_hash or "a" * 64,
        recorded_by="user-1",
        expected_db_revision=current["db_revision"],
        idempotency_key=f"dataset-{key}",
    )


async def _approve(repo, project_id, cycle, stage, *, key):
    await _attach(repo, project_id, cycle, stage, key=key)
    if stage == "reconciliation":
        await _declare_dataset(repo, project_id, cycle, key=key)
        current = await repo.get_cycle(cycle["id"], project_id=project_id)
        assert current is not None
        row = await repo.open_reconciliation_row(
            cycle_id=cycle["id"],
            project_id=project_id,
            check="units_and_encoding",
            field_name="Yield units",
            created_by="user-1",
            expected_db_revision=current["db_revision"],
            idempotency_key=f"row-{key}",
        )
        current = await repo.get_cycle(cycle["id"], project_id=project_id)
        assert current is not None
        await repo.decide_reconciliation_row(
            row_id=row["id"],
            project_id=project_id,
            status="resolved",
            resolution="Verified as Mg/ha.",
            actor_type="human",
            actor_user_id="user-2",
            expected_db_revision=current["db_revision"],
            expected_work_item_revision=row["db_revision"],
            idempotency_key=f"decide-row-{key}",
        )
    current = await repo.get_cycle(cycle["id"], project_id=project_id)
    assert current is not None
    submitted = await repo.submit_stage_for_review(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage=stage,
        expected_db_revision=current["db_revision"],
        actor_user_id="user-1",
        idempotency_key=f"submit-{key}",
    )
    return await repo.review_stage(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage=stage,
        decision="approve",
        rationale="Looks sound.",
        expected_db_revision=submitted["db_revision"],
        reviewer_user_id="user-2",
        reviewer_project_role="owner",
        idempotency_key=f"review-{key}",
    )


async def test_design_approval_opens_build_without_reconciliation(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)

    after = await _approve(repo, project_id, cycle, "design", key="design")

    statuses = {stage["stage"]: stage["status"] for stage in after["stages"]}
    assert after["state"] == "ready_for_build"
    assert statuses["reconciliation"] == StageStatus.LOCKED
    assert statuses["build"] == StageStatus.IN_PROGRESS


async def test_reconciliation_review_does_not_gate_or_advance_build(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)

    after_design = await _approve(repo, project_id, cycle, "design", key="design")
    assert after_design["state"] == "ready_for_build"

    after_reconciliation = await _approve(repo, project_id, after_design, "reconciliation", key="recon")
    assert after_reconciliation["state"] == "ready_for_build"


async def test_the_full_manual_cycle_reaches_completed_without_state_lag(tmp_path: Path) -> None:
    """Phase 7 uses lineage and typed Test validity without introducing state lag."""
    repo, project_id = await _repos(tmp_path)
    current = await _cycle(repo, project_id)

    current = await _approve(repo, project_id, current, "design", key="design")
    assert current["state"] == "ready_for_build"
    current = await _approve(
        repo,
        project_id,
        current,
        "reconciliation",
        key="reconciliation",
    )
    assert current["state"] == "ready_for_build"

    current = await repo.get_cycle(current["id"], project_id=project_id)
    assert current is not None
    await repo.record_build_lineage(
        cycle_id=current["id"],
        project_id=project_id,
        code_revision="git:1234567",
        config_revision="config:abc",
        environment={"python": "3.12"},
        rerun_spec={
            "version": 1,
            "entry_point": "/mnt/user-data/build.py",
            "command": "python build.py",
            "seed": "",
            "inputs": ["/mnt/user-data/reconciliation.json"],
            "environment": {"python": "3.12"},
            "configuration": [],
            "expected_outputs": ["/mnt/user-data/outputs/model.bin"],
        },
        input_artifacts=[f"workspace_file:reconciliation.json:sha256:{'a' * 64}"],
        output_artifacts=[
            {
                "uri": "/mnt/user-data/outputs/model.bin",
                "content_hash": "b" * 64,
                "revision": 1,
            }
        ],
        deviations=[],
        logs_uri="/mnt/user-data/outputs/build.log",
        recorded_by="user-1",
        expected_db_revision=current["db_revision"],
        idempotency_key="lineage-build",
    )
    current = await _approve(repo, project_id, current, "build", key="build")
    assert current["state"] == "test"

    artifact = await _attach(repo, project_id, current, "test", key="test")
    current = await repo.submit_stage_for_review(
        cycle_id=current["id"],
        project_id=project_id,
        stage="test",
        expected_db_revision=artifact["db_revision"],
        actor_user_id="user-1",
        idempotency_key="submit-test",
    )
    assessed = await repo.record_validity_assessment(
        cycle_id=current["id"],
        project_id=project_id,
        metrics=[
            {
                "name": "accuracy",
                "value": 0.51,
                "threshold": 0.70,
                "criterion": "gte",
            }
        ],
        checks=[
            {
                "check": check.value,
                "status": "passed",
                "detail": f"{check.value} evidence",
                "evidence_refs": [f"artifact://{check.value}"],
            }
            for check in DEFAULT_VALIDITY_PACK.required_checks
        ],
        recommendation="advance_to_learn",
        limitations=[],
        rationale="A valid negative should proceed to synthesis.",
        reviewer_user_id="user-2",
        reviewer_project_role="owner",
        expected_db_revision=current["db_revision"],
        idempotency_key="validity-test",
    )
    current = assessed["cycle"]
    assert current["state"] == "learn"
    current = await _approve(repo, project_id, current, "learn", key="learn")
    assert current["state"] == "completed"


async def test_one_idempotency_key_cannot_mean_submit_and_review(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    artifact = await _attach(repo, project_id, cycle, "design", key="design")
    submitted = await repo.submit_stage_for_review(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        expected_db_revision=artifact["db_revision"],
        actor_user_id="user-1",
        idempotency_key="same-key",
    )

    with pytest.raises(DbtlWorkflowRefused, match="different workflow action"):
        await repo.review_stage(
            cycle_id=cycle["id"],
            project_id=project_id,
            stage="design",
            decision="approve",
            rationale="Looks sound.",
            expected_db_revision=submitted["db_revision"],
            reviewer_user_id="user-2",
            reviewer_project_role="owner",
            idempotency_key="same-key",
        )


async def test_a_stage_cannot_be_submitted_while_locked(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)

    with pytest.raises(DbtlWorkflowRefused):
        await repo.submit_stage_for_review(
            cycle_id=cycle["id"],
            project_id=project_id,
            stage="build",
            expected_db_revision=cycle["db_revision"],
            actor_user_id="user-1",
            idempotency_key="submit-build",
        )


async def test_requesting_changes_returns_the_stage_to_work(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    artifact = await _attach(repo, project_id, cycle, "design", key="design")
    submitted = await repo.submit_stage_for_review(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        expected_db_revision=artifact["db_revision"],
        actor_user_id="user-1",
        idempotency_key="submit-1",
    )

    after = await repo.review_stage(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        decision="request_changes",
        rationale="Add the control population.",
        expected_db_revision=submitted["db_revision"],
        reviewer_user_id="user-2",
        reviewer_project_role="member",
        idempotency_key="review-1",
    )

    statuses = {stage["stage"]: stage["status"] for stage in after["stages"]}
    assert statuses["design"] == StageStatus.CHANGES_REQUESTED
    assert after["state"] == "design"


async def test_a_review_requires_a_rationale(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    artifact = await _attach(repo, project_id, cycle, "design", key="design")
    submitted = await repo.submit_stage_for_review(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        expected_db_revision=artifact["db_revision"],
        actor_user_id="user-1",
        idempotency_key="submit-1",
    )

    with pytest.raises(ValueError):
        await repo.review_stage(
            cycle_id=cycle["id"],
            project_id=project_id,
            stage="design",
            decision="approve",
            rationale="   ",
            expected_db_revision=submitted["db_revision"],
            reviewer_user_id="user-2",
            reviewer_project_role="member",
            idempotency_key="review-1",
        )


# --------------------------------------------------------------------------
# Concurrency, replay, restart
# --------------------------------------------------------------------------


async def test_a_stale_revision_is_refused(tmp_path: Path) -> None:
    """Two reviewers acting on the same screen: the second must lose."""
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    artifact = await _attach(repo, project_id, cycle, "design", key="design")
    stale_revision = artifact["db_revision"]
    await repo.submit_stage_for_review(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        expected_db_revision=stale_revision,
        actor_user_id="user-1",
        idempotency_key="submit-1",
    )

    with pytest.raises(DbtlRevisionConflict):
        await repo.submit_stage_for_review(
            cycle_id=cycle["id"],
            project_id=project_id,
            stage="design",
            expected_db_revision=stale_revision,
            actor_user_id="user-1",
            idempotency_key="submit-2",
        )


async def test_a_replayed_review_returns_the_same_state_without_reapplying(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    artifact = await _attach(repo, project_id, cycle, "design", key="design")
    submitted = await repo.submit_stage_for_review(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        expected_db_revision=artifact["db_revision"],
        actor_user_id="user-1",
        idempotency_key="submit-1",
    )
    payload = {
        "cycle_id": cycle["id"],
        "project_id": project_id,
        "stage": "design",
        "decision": "approve",
        "rationale": "Sound.",
        "expected_db_revision": submitted["db_revision"],
        "reviewer_user_id": "user-2",
        "reviewer_project_role": "member",
        "idempotency_key": "review-1",
    }

    first = await repo.review_stage(**payload)
    second = await repo.review_stage(**payload)

    assert first["db_revision"] == second["db_revision"]
    assert len([event for event in await repo.list_activity(cycle["id"], project_id=project_id) if event["event_type"] == "stage.reviewed"]) == 1


async def test_review_key_cannot_replay_with_a_different_verdict(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    artifact = await _attach(repo, project_id, cycle, "design", key="design")
    submitted = await repo.submit_stage_for_review(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        expected_db_revision=artifact["db_revision"],
        actor_user_id="user-1",
        idempotency_key="submit-1",
    )
    payload = {
        "cycle_id": cycle["id"],
        "project_id": project_id,
        "stage": "design",
        "decision": "approve",
        "rationale": "Sound.",
        "expected_db_revision": submitted["db_revision"],
        "reviewer_user_id": "user-2",
        "reviewer_project_role": "member",
        "idempotency_key": "review-1",
    }
    await repo.review_stage(**payload)

    with pytest.raises(DbtlWorkflowRefused, match="different workflow action"):
        await repo.review_stage(**{**payload, "decision": "reject"})


async def test_state_survives_a_fresh_repository_instance(tmp_path: Path) -> None:
    """The restart test: nothing lives in process memory."""
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    await _approve(repo, project_id, cycle, "design", key="design")

    reopened = DbtlCycleRepository(get_session_factory())
    restored = await reopened.get_cycle(cycle["id"], project_id=project_id)

    assert restored is not None
    assert restored["state"] == "ready_for_build"
    assert restored["research_question"] == "Which lines hold yield under late drought?"


# --------------------------------------------------------------------------
# Artifacts, blockers, activity
# --------------------------------------------------------------------------


async def test_artifacts_are_revisioned_per_stage(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)

    first = await repo.attach_artifact(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        artifact_type="design_package",
        uri="/mnt/user-data/workspace/design.json",
        content_hash="a" * 64,
        created_by="user-1",
        expected_db_revision=cycle["db_revision"],
        idempotency_key="artifact-1",
    )
    second = await repo.attach_artifact(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        artifact_type="design_package",
        uri="/mnt/user-data/workspace/design.json",
        content_hash="b" * 64,
        created_by="user-1",
        expected_db_revision=first["db_revision"],
        idempotency_key="artifact-2",
    )

    assert first["revision"] == 1
    assert second["revision"] == 2


async def test_artifact_replay_returns_the_original_revision(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    payload = {
        "cycle_id": cycle["id"],
        "project_id": project_id,
        "stage": "design",
        "artifact_type": "design_package",
        "uri": "/mnt/user-data/workspace/design.json",
        "content_hash": "a" * 64,
        "created_by": "user-1",
        "expected_db_revision": cycle["db_revision"],
        "idempotency_key": "artifact-1",
    }

    first = await repo.attach_artifact(**payload)
    second = await repo.attach_artifact(**payload)

    assert second["id"] == first["id"]
    assert second["revision"] == first["revision"] == 1
    detail = await repo.get_cycle(cycle["id"], project_id=project_id)
    assert detail is not None
    assert len(detail["artifacts"]) == 1


async def test_artifact_key_cannot_replay_with_different_evidence(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    payload = {
        "cycle_id": cycle["id"],
        "project_id": project_id,
        "stage": "design",
        "artifact_type": "design_package",
        "uri": "/mnt/user-data/workspace/design.json",
        "content_hash": "a" * 64,
        "created_by": "user-1",
        "expected_db_revision": cycle["db_revision"],
        "idempotency_key": "artifact-1",
    }
    await repo.attach_artifact(**payload)

    with pytest.raises(DbtlWorkflowRefused, match="different workflow action"):
        await repo.attach_artifact(**{**payload, "content_hash": "b" * 64})


async def test_locked_or_approved_stages_cannot_accept_new_evidence(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)

    with pytest.raises(DbtlWorkflowRefused):
        await _attach(repo, project_id, cycle, "build", key="locked")

    approved = await _approve(repo, project_id, cycle, "design", key="design")
    with pytest.raises(DbtlWorkflowRefused):
        await _attach(repo, project_id, approved, "design", key="late")


async def test_a_blocker_is_recorded_and_resolved_as_a_work_item(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)

    blocker = await repo.create_work_item(
        cycle_id=cycle["id"],
        project_id=project_id,
        title="Genotype file missing 12 entries",
        kind="blocker",
        created_by="user-1",
        expected_db_revision=cycle["db_revision"],
        idempotency_key="work-1",
    )
    current = await repo.get_cycle(cycle["id"], project_id=project_id)
    assert current is not None
    resolved = await repo.resolve_work_item(
        work_item_id=blocker["id"],
        project_id=project_id,
        resolution="Re-exported from the source LIMS.",
        actor_user_id="user-1",
        expected_db_revision=current["db_revision"],
        expected_work_item_revision=blocker["db_revision"],
        idempotency_key="resolve-1",
    )

    assert blocker["status"] == "open"
    assert resolved["status"] == "resolved"
    assert resolved["payload"]["resolution"] == "Re-exported from the source LIMS."


async def test_a_stale_work_item_resolution_is_refused(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    blocker = await repo.create_work_item(
        cycle_id=cycle["id"],
        project_id=project_id,
        title="Missing genotypes",
        kind="blocker",
        created_by="user-1",
        expected_db_revision=cycle["db_revision"],
        idempotency_key="work-1",
    )

    with pytest.raises(DbtlRevisionConflict):
        await repo.resolve_work_item(
            work_item_id=blocker["id"],
            project_id=project_id,
            resolution="Fixed.",
            actor_user_id="user-1",
            expected_db_revision=blocker["db_revision"] - 1,
            expected_work_item_revision=blocker["db_revision"],
            idempotency_key="resolve-1",
        )


async def test_every_action_appears_in_activity_with_actor_and_revision(tmp_path: Path) -> None:
    """The human exit review checks exactly this."""
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    await _approve(repo, project_id, cycle, "design", key="design")

    events = await repo.list_activity(cycle["id"], project_id=project_id)

    types = [event["event_type"] for event in events]
    assert types == ["cycle.created", "artifact.attached", "stage.submitted", "stage.reviewed"]
    assert all(event["actor_user_id"] for event in events)
    assert all(event["payload"].get("db_revision") for event in events)
    assert [event["sequence"] for event in events] == [1, 2, 3, 4]


async def test_activity_is_scoped_to_its_project(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)

    assert await repo.list_activity(cycle["id"], project_id="another-project") == []
    assert await repo.get_cycle(cycle["id"], project_id="another-project") is None


async def test_created_at_is_serialized_as_an_iso_string(tmp_path: Path) -> None:
    repo, project_id = await _repos(tmp_path)

    cycle = await _cycle(repo, project_id)

    assert isinstance(cycle["created_at"], str)
    assert datetime.fromisoformat(cycle["created_at"]).tzinfo is not None
    assert datetime.fromisoformat(cycle["created_at"]) <= datetime.now(UTC)


async def test_a_stage_without_evidence_cannot_be_submitted(tmp_path: Path) -> None:
    """A review with nothing to review would be an approval on faith."""
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)

    with pytest.raises(DbtlWorkflowRefused):
        await repo.submit_stage_for_review(
            cycle_id=cycle["id"],
            project_id=project_id,
            stage="design",
            expected_db_revision=cycle["db_revision"],
            actor_user_id="user-1",
            idempotency_key="submit-1",
        )


async def test_a_review_is_bound_to_the_evidence_revision_it_saw(tmp_path: Path) -> None:
    """A later artifact revision must not inherit an earlier approval."""
    repo, project_id = await _repos(tmp_path)
    cycle = await _cycle(repo, project_id)
    await _attach(repo, project_id, cycle, "design", key="v1")
    second = await _attach(repo, project_id, cycle, "design", key="v2")
    current = await repo.get_cycle(cycle["id"], project_id=project_id)
    assert current is not None
    submitted = await repo.submit_stage_for_review(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        expected_db_revision=current["db_revision"],
        actor_user_id="user-1",
        idempotency_key="submit-1",
    )

    bound_projection_hash = submitted["projection_hash"]
    await repo.review_stage(
        cycle_id=cycle["id"],
        project_id=project_id,
        stage="design",
        decision="approve",
        rationale="Second revision resolves the blocker.",
        expected_db_revision=submitted["db_revision"],
        reviewer_user_id="user-2",
        reviewer_project_role="owner",
        idempotency_key="review-1",
    )

    detail = await repo.get_cycle(cycle["id"], project_id=project_id)
    assert detail is not None
    assert second["revision"] == 2
    assert [item["revision"] for item in detail["artifacts"]] == [1, 2]
    session_factory = get_session_factory()
    assert session_factory is not None
    async with session_factory() as session:
        review = await session.scalar(select(DbtlReviewRow).where(DbtlReviewRow.idempotency_key == "review-1"))
    assert review is not None
    assert review.bound_projection_hash == bound_projection_hash
    assert review.reviewer_project_role == "owner"
