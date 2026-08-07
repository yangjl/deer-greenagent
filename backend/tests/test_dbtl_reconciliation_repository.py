"""Phase 6: data readiness and reconciliation through durable storage.

The pure rules are covered in ``test_dbtl_reconciliation.py``. What is tested
here is that every *write path* actually goes through them — including the ones
that could be used to get around them — and that an approval records enough to
notice the data moving underneath it afterwards.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl.cycle_state import StageStatus
from deerflow.persistence.dbtl import DbtlCycleRepository, DbtlRevisionConflict, DbtlWorkflowRefused
from deerflow.persistence.dbtl.model import WorkItemRow
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

pytestmark = pytest.mark.asyncio

POLICY = "greenagent-dbtl-v2-draft"
HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest_asyncio.fixture(autouse=True)
async def _close_test_engine():
    yield
    await close_engine()


async def _repos(tmp_path: Path) -> tuple[DbtlCycleRepository, str]:
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    workspaces = WorkspaceRepository(session_factory)
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
    return DbtlCycleRepository(session_factory), "project-1"


async def _cycle(repo: DbtlCycleRepository, project_id: str) -> dict:
    return await repo.create_cycle(
        cycle_id="cycle-1",
        project_id=project_id,
        title="Drought tolerance screen",
        cycle_class="computational",
        research_question="Which lines hold yield under late drought?",
        objective="Rank 200 lines",
        success_criteria="Top decile reproducible across two sites",
        created_by="user-1",
        policy_version=POLICY,
        idempotency_key="create-1",
    )


async def _revision(repo, project_id: str) -> int:
    current = await repo.get_cycle("cycle-1", project_id=project_id)
    assert current is not None
    return int(current["db_revision"])


async def _declare(repo, project_id: str, *, key: str, content_hash: str = HASH_A, **overrides) -> dict:
    payload = {
        "cycle_id": "cycle-1",
        "project_id": project_id,
        "source_key": "yield_trial",
        "uri": "/mnt/user-data/workspace/yield.csv",
        "content_hash": content_hash,
        "recorded_by": "user-1",
        "expected_db_revision": await _revision(repo, project_id),
        "idempotency_key": key,
    }
    payload.update(overrides)
    return await repo.declare_dataset(**payload)


async def _row(repo, project_id: str, *, key: str, check: str = "units_and_encoding", field_name: str = "Yield units", **overrides) -> dict:
    payload = {
        "cycle_id": "cycle-1",
        "project_id": project_id,
        "check": check,
        "field_name": field_name,
        "created_by": "user-1",
        "expected_db_revision": await _revision(repo, project_id),
        "idempotency_key": key,
    }
    payload.update(overrides)
    return await repo.open_reconciliation_row(**payload)


async def _attach(repo, project_id: str, stage: str, *, key: str) -> dict:
    return await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id=project_id,
        stage=stage,
        artifact_type=f"{stage}_report",
        uri=f"/mnt/user-data/workspace/{stage}.json",
        content_hash=HASH_B,
        created_by="user-1",
        expected_db_revision=await _revision(repo, project_id),
        idempotency_key=f"artifact-{key}",
    )


async def _approve_design(repo, project_id: str) -> None:
    await _attach(repo, project_id, "design", key="design")
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id=project_id,
        stage="design",
        expected_db_revision=await _revision(repo, project_id),
        actor_user_id="user-1",
        idempotency_key="submit-design",
    )
    await repo.review_stage(
        cycle_id="cycle-1",
        project_id=project_id,
        stage="design",
        decision="approve",
        rationale="Criteria are operational.",
        expected_db_revision=await _revision(repo, project_id),
        reviewer_user_id="user-2",
        reviewer_project_role="owner",
        idempotency_key="review-design",
    )


class TestDatasetDeclaration:
    async def test_a_declared_dataset_is_readable(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await _declare(repo, project_id, key="d1")
        datasets = await repo.list_datasets("cycle-1", project_id=project_id)
        assert [item["source_key"] for item in datasets] == ["yield_trial"]
        assert datasets[0]["declared_immutable"] is True

    async def test_redeclaring_updates_in_place_rather_than_duplicating(self, tmp_path: Path) -> None:
        # Two rows for one source key would make the fingerprint depend on which
        # one a query happened to read.
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await _declare(repo, project_id, key="d1", content_hash=HASH_A)
        await _declare(repo, project_id, key="d2", content_hash=HASH_B)
        datasets = await repo.list_datasets("cycle-1", project_id=project_id)
        assert len(datasets) == 1
        assert datasets[0]["content_hash"] == HASH_B

    async def test_a_hash_change_is_recorded_in_activity(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await _declare(repo, project_id, key="d1", content_hash=HASH_A)
        await _declare(repo, project_id, key="d2", content_hash=HASH_B)
        events = await repo.list_activity("cycle-1", project_id=project_id)
        latest = [item for item in events if item["event_type"] == "dataset.declared"][-1]
        assert latest["payload"]["previous_content_hash"] == HASH_A

    async def test_a_replayed_declaration_returns_the_same_record(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        # A replay is a retry of the *identical* request, so it resends the
        # revision the caller originally held — a fresh one would be a different
        # request wearing the same key, which the ledger rightly refuses.
        original_revision = await _revision(repo, project_id)
        request = {
            "cycle_id": "cycle-1",
            "project_id": project_id,
            "source_key": "yield_trial",
            "uri": "/mnt/user-data/workspace/yield.csv",
            "content_hash": HASH_A,
            "recorded_by": "user-1",
            "expected_db_revision": original_revision,
            "idempotency_key": "d1",
        }
        first = await repo.declare_dataset(**request)
        after_first = await _revision(repo, project_id)
        second = await repo.declare_dataset(**request)
        assert second["id"] == first["id"]
        assert await _revision(repo, project_id) == after_first

    async def test_reusing_a_key_for_a_different_declaration_is_refused(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await _declare(repo, project_id, key="d1", content_hash=HASH_A)
        with pytest.raises(DbtlWorkflowRefused, match="different workflow action"):
            await _declare(repo, project_id, key="d1", content_hash=HASH_B)

    async def test_a_stale_revision_is_refused(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        with pytest.raises(DbtlRevisionConflict):
            await _declare(repo, project_id, key="d1", expected_db_revision=99)

    async def test_a_malformed_hash_is_refused(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        with pytest.raises(ValueError, match="SHA-256"):
            await _declare(repo, project_id, key="d1", content_hash="nope")

    async def test_an_unknown_role_is_refused(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        with pytest.raises(ValueError, match="Unknown dataset role"):
            await _declare(repo, project_id, key="d1", role="scratch")


class TestMatrixWrites:
    async def test_a_row_is_created_open_and_required(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        row = await _row(repo, project_id, key="r1")
        assert row["payload"]["row_status"] == "open"
        assert row["payload"]["required"] is True
        assert row["status"] == "open"

    async def test_an_unknown_check_is_refused(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        with pytest.raises(ValueError, match="Unknown reconciliation check"):
            await _row(repo, project_id, key="r1", check="vibes")

    async def test_a_human_decision_settles_the_row(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        row = await _row(repo, project_id, key="r1")
        decided = await repo.decide_reconciliation_row(
            row_id=row["id"],
            project_id=project_id,
            status="resolved",
            resolution="bu/ac converted to Mg/ha",
            actor_type="human",
            actor_user_id="user-2",
            expected_db_revision=await _revision(repo, project_id),
            expected_work_item_revision=row["db_revision"],
            idempotency_key="decide-1",
        )
        assert decided["payload"]["row_status"] == "resolved"
        assert decided["payload"]["resolved_by_user_id"] == "user-2"
        assert decided["status"] == "resolved"

    async def test_an_agent_cannot_close_a_judgement_row_through_storage(self, tmp_path: Path) -> None:
        # The pure rule has to hold at the write boundary, not only in the
        # module that states it.
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        row = await _row(repo, project_id, key="r1", check="contradictory_sources", field_name="Treatment coding")
        with pytest.raises(Exception, match="human decision"):
            await repo.decide_reconciliation_row(
                row_id=row["id"],
                project_id=project_id,
                status="resolved",
                resolution="WW maps to 0",
                actor_type="agent",
                actor_user_id=None,
                expected_db_revision=await _revision(repo, project_id),
                expected_work_item_revision=row["db_revision"],
                idempotency_key="decide-1",
            )

    async def test_a_refused_agent_decision_leaves_no_partial_write(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        row = await _row(repo, project_id, key="r1", check="contradictory_sources")
        revision = await _revision(repo, project_id)
        with pytest.raises(Exception):
            await repo.decide_reconciliation_row(
                row_id=row["id"],
                project_id=project_id,
                status="resolved",
                resolution="mine",
                actor_type="agent",
                actor_user_id=None,
                expected_db_revision=revision,
                expected_work_item_revision=row["db_revision"],
                idempotency_key="decide-1",
            )
        assert await _revision(repo, project_id) == revision

    async def test_an_agent_may_propose_on_a_judgement_row(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        row = await _row(repo, project_id, key="r1", check="contradictory_sources")
        decided = await repo.decide_reconciliation_row(
            row_id=row["id"],
            project_id=project_id,
            status="proposed",
            resolution="Source B appears authoritative",
            actor_type="agent",
            actor_user_id=None,
            expected_db_revision=await _revision(repo, project_id),
            expected_work_item_revision=row["db_revision"],
            idempotency_key="decide-1",
        )
        assert decided["payload"]["row_status"] == "proposed"
        # Still open work: a proposal is not a decision.
        assert decided["status"] == "open"

    async def test_the_generic_resolve_path_refuses_a_reconciliation_row(self, tmp_path: Path) -> None:
        # That endpoint takes no actor type, so it cannot apply the rule — which
        # would make it the way around the rule.
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        row = await _row(repo, project_id, key="r1", check="contradictory_sources")
        with pytest.raises(DbtlWorkflowRefused, match="reconciliation endpoint"):
            await repo.resolve_work_item(
                work_item_id=row["id"],
                project_id=project_id,
                resolution="settled",
                actor_user_id="user-2",
                expected_db_revision=await _revision(repo, project_id),
                expected_work_item_revision=row["db_revision"],
                idempotency_key="resolve-1",
            )

    async def test_a_stale_row_revision_is_refused(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        row = await _row(repo, project_id, key="r1")
        with pytest.raises(DbtlRevisionConflict):
            await repo.decide_reconciliation_row(
                row_id=row["id"],
                project_id=project_id,
                status="resolved",
                resolution="converted",
                actor_type="human",
                actor_user_id="user-2",
                expected_db_revision=await _revision(repo, project_id),
                expected_work_item_revision=row["db_revision"] + 5,
                idempotency_key="decide-1",
            )

    async def test_a_row_from_another_project_is_not_found(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        row = await _row(repo, project_id, key="r1")
        with pytest.raises(DbtlWorkflowRefused, match="not found"):
            await repo.decide_reconciliation_row(
                row_id=row["id"],
                project_id="project-other",
                status="resolved",
                resolution="converted",
                actor_type="human",
                actor_user_id="user-2",
                expected_db_revision=1,
                expected_work_item_revision=1,
                idempotency_key="decide-1",
            )


class TestReadinessGate:
    async def test_reconciliation_cannot_be_submitted_with_an_open_required_row(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await _approve_design(repo, project_id)
        await _declare(repo, project_id, key="d1")
        await _row(repo, project_id, key="r1")
        await _attach(repo, project_id, "reconciliation", key="recon")
        with pytest.raises(DbtlWorkflowRefused, match="not ready for review"):
            await repo.submit_stage_for_review(
                cycle_id="cycle-1",
                project_id=project_id,
                stage="reconciliation",
                expected_db_revision=await _revision(repo, project_id),
                actor_user_id="user-1",
                idempotency_key="submit-recon",
            )

    async def test_a_blocked_row_names_the_field_in_the_refusal(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await _approve_design(repo, project_id)
        await _declare(repo, project_id, key="d1")
        row = await _row(repo, project_id, key="r1", check="identifier_integrity", field_name="Hybrid ID")
        await repo.decide_reconciliation_row(
            row_id=row["id"],
            project_id=project_id,
            status="blocked",
            resolution="19 hybrid IDs have no match",
            actor_type="human",
            actor_user_id="user-2",
            blocker_kind="missing_data",
            expected_db_revision=await _revision(repo, project_id),
            expected_work_item_revision=row["db_revision"],
            idempotency_key="decide-1",
        )
        await _attach(repo, project_id, "reconciliation", key="recon")
        with pytest.raises(DbtlWorkflowRefused, match="Hybrid ID"):
            await repo.submit_stage_for_review(
                cycle_id="cycle-1",
                project_id=project_id,
                stage="reconciliation",
                expected_db_revision=await _revision(repo, project_id),
                actor_user_id="user-1",
                idempotency_key="submit-recon",
            )

    async def test_resolving_every_row_reaches_ready_for_build(self, tmp_path: Path) -> None:
        """The phase's exit condition, end to end through storage."""
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await _approve_design(repo, project_id)
        await _declare(repo, project_id, key="d1")
        row = await _row(repo, project_id, key="r1")
        await repo.decide_reconciliation_row(
            row_id=row["id"],
            project_id=project_id,
            status="resolved",
            resolution="bu/ac converted to Mg/ha",
            actor_type="human",
            actor_user_id="user-2",
            expected_db_revision=await _revision(repo, project_id),
            expected_work_item_revision=row["db_revision"],
            idempotency_key="decide-1",
        )
        gate = await repo.evaluate_reconciliation("cycle-1", project_id=project_id)
        assert gate.ready

        await _attach(repo, project_id, "reconciliation", key="recon")
        await repo.submit_stage_for_review(
            cycle_id="cycle-1",
            project_id=project_id,
            stage="reconciliation",
            expected_db_revision=await _revision(repo, project_id),
            actor_user_id="user-1",
            idempotency_key="submit-recon",
        )
        final = await repo.review_stage(
            cycle_id="cycle-1",
            project_id=project_id,
            stage="reconciliation",
            decision="approve",
            rationale="Matrix is settled and inputs are pinned.",
            expected_db_revision=await _revision(repo, project_id),
            reviewer_user_id="user-2",
            reviewer_project_role="owner",
            idempotency_key="review-recon",
        )
        assert final["state"] == "ready_for_build"

    async def test_the_gate_reports_no_declared_sources(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        gate = await repo.evaluate_reconciliation("cycle-1", project_id=project_id)
        assert not gate.ready

    async def test_a_missing_cycle_is_never_ready(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        assert not (await repo.evaluate_reconciliation("cycle-nope", project_id=project_id)).ready

    async def test_an_unreadable_matrix_row_blocks_submission(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await _approve_design(repo, project_id)
        await _declare(repo, project_id, key="d1")
        resolved = await _row(repo, project_id, key="r1")
        await repo.decide_reconciliation_row(
            row_id=resolved["id"],
            project_id=project_id,
            status="resolved",
            resolution="converted",
            actor_type="human",
            actor_user_id="user-2",
            expected_db_revision=await _revision(repo, project_id),
            expected_work_item_revision=resolved["db_revision"],
            idempotency_key="decide-1",
        )

        async with repo._sf() as session:
            session.add(
                WorkItemRow(
                    id="corrupt-row",
                    project_id=project_id,
                    cycle_id="cycle-1",
                    title="Unreadable row",
                    status="open",
                    owner_role=None,
                    payload={
                        "kind": "reconciliation",
                        "check": "not-a-real-check",
                    },
                    db_revision=await _revision(repo, project_id),
                )
            )
            await session.commit()

        gate = await repo.evaluate_reconciliation("cycle-1", project_id=project_id)
        assert not gate.ready
        assert "corrupt-row" in gate.blocking_rows

        await _attach(repo, project_id, "reconciliation", key="recon")
        with pytest.raises(DbtlWorkflowRefused, match="could not be read"):
            await repo.submit_stage_for_review(
                cycle_id="cycle-1",
                project_id=project_id,
                stage="reconciliation",
                expected_db_revision=await _revision(repo, project_id),
                actor_user_id="user-1",
                idempotency_key="submit-corrupt",
            )


class TestApprovalBinding:
    async def _reach_ready(self, tmp_path: Path) -> tuple[DbtlCycleRepository, str]:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await _approve_design(repo, project_id)
        await _declare(repo, project_id, key="d1")
        row = await _row(repo, project_id, key="r1")
        await repo.decide_reconciliation_row(
            row_id=row["id"],
            project_id=project_id,
            status="resolved",
            resolution="converted",
            actor_type="human",
            actor_user_id="user-2",
            expected_db_revision=await _revision(repo, project_id),
            expected_work_item_revision=row["db_revision"],
            idempotency_key="decide-1",
        )
        await _attach(repo, project_id, "reconciliation", key="recon")
        await repo.submit_stage_for_review(
            cycle_id="cycle-1",
            project_id=project_id,
            stage="reconciliation",
            expected_db_revision=await _revision(repo, project_id),
            actor_user_id="user-1",
            idempotency_key="submit-recon",
        )
        await repo.review_stage(
            cycle_id="cycle-1",
            project_id=project_id,
            stage="reconciliation",
            decision="approve",
            rationale="Settled.",
            expected_db_revision=await _revision(repo, project_id),
            reviewer_user_id="user-2",
            reviewer_project_role="owner",
            idempotency_key="review-recon",
        )
        return repo, project_id

    async def test_an_approval_binds_the_declared_inputs(self, tmp_path: Path) -> None:
        repo, project_id = await self._reach_ready(tmp_path)
        view = await repo.reconciliation_view("cycle-1", project_id=project_id)
        assert view["approval_binding"]["stage_spec_key"] == "generic:reconciliation:v1"
        assert view["approval_invalidation"]["invalidated"] is False

    async def test_a_changed_dataset_invalidates_the_approval(self, tmp_path: Path) -> None:
        """ "If a dataset changes, the reconciliation gate is invalidated." """
        repo, project_id = await self._reach_ready(tmp_path)
        await _declare(repo, project_id, key="d2", content_hash=HASH_B)
        cycle = await repo.get_cycle("cycle-1", project_id=project_id)
        assert cycle is not None
        statuses = {item["stage"]: item["status"] for item in cycle["stages"]}
        assert cycle["state"] == "reconciliation"
        assert statuses["reconciliation"] == StageStatus.CHANGES_REQUESTED
        assert statuses["build"] == StageStatus.LOCKED

        view = await repo.reconciliation_view("cycle-1", project_id=project_id)
        assert view["approval_invalidation"]["invalidated"] is True
        assert any("dataset changed" in reason for reason in view["approval_invalidation"]["reasons"])

    async def test_redeclaring_identical_inputs_does_not_reopen_an_approval(self, tmp_path: Path) -> None:
        repo, project_id = await self._reach_ready(tmp_path)
        await _declare(repo, project_id, key="d2", content_hash=HASH_A)
        cycle = await repo.get_cycle("cycle-1", project_id=project_id)
        assert cycle is not None
        assert cycle["state"] == "ready_for_build"

    async def test_an_unapproved_stage_reports_no_invalidation_check(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        view = await repo.reconciliation_view("cycle-1", project_id=project_id)
        assert view["approval_invalidation"] is None


class TestWorkerRuns:
    async def test_a_review_meeting_projects_its_exact_evidence_binding(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        evidence = await _attach(repo, project_id, "design", key="design-evidence")

        await repo.record_worker_runs(
            cycle_id="cycle-1",
            project_id=project_id,
            stage="design",
            stage_spec_key="generic:design:v1",
            results=[],
            actor_user_id="user-1",
            expected_db_revision=await _revision(repo, project_id),
            idempotency_key="design-meeting-1",
            artifact_type="design_review_meeting",
            artifact_uri="/mnt/user-data/outputs/dbtl/design-meeting.json",
            artifact_content_hash=HASH_A,
            reviewed_artifact_id=evidence["id"],
            reviewed_artifact_revision=evidence["revision"],
            reviewed_artifact_content_hash=evidence["content_hash"],
        )

        cycle = await repo.get_cycle("cycle-1", project_id=project_id)
        assert cycle is not None
        meeting = next(item for item in cycle["artifacts"] if item["artifact_type"] == "design_review_meeting")
        assert meeting["reviewed_artifact_id"] == evidence["id"]
        assert meeting["reviewed_artifact_revision"] == evidence["revision"]
        assert meeting["reviewed_artifact_content_hash"] == evidence["content_hash"]

    async def test_a_review_meeting_refuses_a_stale_evidence_hash(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        evidence = await _attach(repo, project_id, "design", key="design-evidence")

        with pytest.raises(DbtlWorkflowRefused, match="evidence binding no longer matches"):
            await repo.record_worker_runs(
                cycle_id="cycle-1",
                project_id=project_id,
                stage="design",
                stage_spec_key="generic:design:v1",
                results=[],
                actor_user_id="user-1",
                expected_db_revision=await _revision(repo, project_id),
                idempotency_key="design-meeting-stale",
                artifact_type="design_review_meeting",
                artifact_uri="/mnt/user-data/outputs/dbtl/design-meeting.json",
                artifact_content_hash=HASH_A,
                reviewed_artifact_id=evidence["id"],
                reviewed_artifact_revision=evidence["revision"],
                reviewed_artifact_content_hash="f" * 64,
            )

    async def test_a_live_fanout_records_its_review_artifact_atomically(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await repo.record_worker_runs(
            cycle_id="cycle-1",
            project_id=project_id,
            stage="design",
            stage_spec_key="generic:design:v1",
            results=[
                {
                    "unit_id": "u1",
                    "capability": "experimental_design",
                    "agent_name": "designer",
                    "status": "completed",
                    "is_trustworthy": True,
                }
            ],
            actor_user_id="user-1",
            expected_db_revision=await _revision(repo, project_id),
            idempotency_key="live-stage-1",
            artifact_type="design_brief",
            artifact_uri="/mnt/user-data/outputs/dbtl/design.json",
            artifact_content_hash=HASH_A,
        )

        cycle = await repo.get_cycle("cycle-1", project_id=project_id)
        assert cycle is not None
        assert cycle["db_revision"] == 2
        assert cycle["artifacts"][0]["artifact_type"] == "design_brief"
        assert cycle["artifacts"][0]["uri"].endswith("/dbtl/design.json")
        replay = await repo.get_stage_execution_replay(
            "cycle-1",
            project_id=project_id,
            idempotency_key="live-stage-1",
        )
        assert replay is not None
        assert replay["worker_count"] == 1
        assert replay["artifact_uri"].endswith("/dbtl/design.json")

    async def test_a_replayed_key_cannot_hide_different_worker_results(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        revision = await _revision(repo, project_id)
        common = {
            "cycle_id": "cycle-1",
            "project_id": project_id,
            "stage": "design",
            "stage_spec_key": "generic:design:v1",
            "actor_user_id": "user-1",
            "expected_db_revision": revision,
            "idempotency_key": "workers-1",
        }
        await repo.record_worker_runs(
            **common,
            results=[
                {
                    "unit_id": "u1",
                    "capability": "experimental_design",
                    "agent_name": "designer",
                    "status": "completed",
                }
            ],
        )
        with pytest.raises(DbtlWorkflowRefused, match="different workflow action"):
            await repo.record_worker_runs(
                **common,
                results=[
                    {
                        "unit_id": "u1",
                        "capability": "experimental_design",
                        "agent_name": "designer",
                        "status": "failed",
                    }
                ],
            )

    async def test_a_contract_for_a_different_stage_is_refused(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        with pytest.raises(ValueError, match="belongs to 'reconciliation'"):
            await repo.record_worker_runs(
                cycle_id="cycle-1",
                project_id=project_id,
                stage="design",
                stage_spec_key="generic:reconciliation:v1",
                results=[],
                actor_user_id="user-1",
                expected_db_revision=await _revision(repo, project_id),
                idempotency_key="workers-wrong-spec",
            )

    async def test_an_approved_stage_cannot_accept_late_worker_evidence(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await _approve_design(repo, project_id)
        with pytest.raises(DbtlWorkflowRefused, match="cannot be recorded"):
            await repo.record_worker_runs(
                cycle_id="cycle-1",
                project_id=project_id,
                stage="design",
                stage_spec_key="generic:design:v1",
                results=[],
                actor_user_id="user-1",
                expected_db_revision=await _revision(repo, project_id),
                idempotency_key="workers-too-late",
            )

    async def test_failed_workers_are_persisted_alongside_successful_ones(self, tmp_path: Path) -> None:
        # A fan-out where a worker crashed must not read afterwards as a tidy
        # run with fewer workers.
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        recorded = await repo.record_worker_runs(
            cycle_id="cycle-1",
            project_id=project_id,
            stage="design",
            stage_spec_key="generic:design:v1",
            results=[
                {"unit_id": "u1", "capability": "experimental_design", "agent_name": "designer", "status": "completed", "is_trustworthy": True},
                {"unit_id": "u2", "capability": "statistical_analysis", "agent_name": "stats", "status": "failed", "is_trustworthy": False},
            ],
            actor_user_id="user-1",
            expected_db_revision=await _revision(repo, project_id),
            idempotency_key="workers-1",
        )
        assert [item["status"] for item in recorded] == ["completed", "failed"]

    async def test_the_attempt_records_which_contract_it_ran_under(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        await repo.record_worker_runs(
            cycle_id="cycle-1",
            project_id=project_id,
            stage="design",
            stage_spec_key="generic:design:v1",
            results=[{"unit_id": "u1", "capability": "experimental_design", "agent_name": "d", "status": "completed", "is_trustworthy": True}],
            actor_user_id="user-1",
            expected_db_revision=await _revision(repo, project_id),
            idempotency_key="workers-1",
        )
        events = await repo.list_activity("cycle-1", project_id=project_id)
        recorded = [item for item in events if item["event_type"] == "stage.workers_recorded"][-1]
        assert recorded["payload"]["stage_spec_key"] == "generic:design:v1"
        assert recorded["payload"]["trustworthy_count"] == 1

    async def test_a_replayed_fan_out_does_not_duplicate_worker_rows(self, tmp_path: Path) -> None:
        repo, project_id = await _repos(tmp_path)
        await _cycle(repo, project_id)
        results = [{"unit_id": "u1", "capability": "experimental_design", "agent_name": "d", "status": "completed", "is_trustworthy": True}]
        revision = await _revision(repo, project_id)
        await repo.record_worker_runs(
            cycle_id="cycle-1",
            project_id=project_id,
            stage="design",
            stage_spec_key="generic:design:v1",
            results=results,
            actor_user_id="user-1",
            expected_db_revision=revision,
            idempotency_key="workers-1",
        )
        again = await repo.record_worker_runs(
            cycle_id="cycle-1",
            project_id=project_id,
            stage="design",
            stage_spec_key="generic:design:v1",
            results=results,
            actor_user_id="user-1",
            expected_db_revision=revision,
            idempotency_key="workers-1",
        )
        assert len(again) == 1
