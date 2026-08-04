"""The optional-reconciliation flag, driven through the real repository.

The pure tests prove the legal-move tables are right. These prove the switch
reaches a database write: that an approved Design opens Build with no matrix
settled, and — the half that matters more — that the data guarantees
reconciliation used to carry did not leave with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl.validity import DEFAULT_VALIDITY_PACK, ValidityCheckName
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

POLICY = "greenagent-dbtl-v2-draft"
HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest_asyncio.fixture(autouse=True)
async def _close_test_engine():
    yield
    await close_engine()


@pytest.fixture
def no_reconciliation(monkeypatch):
    """The deployment rule, as the two writers that consult it see it."""
    monkeypatch.setattr("deerflow.persistence.dbtl.cycles.reconciliation_required", lambda: False)
    monkeypatch.setattr("deerflow.persistence.dbtl.build_test_ops.reconciliation_required", lambda: False)


async def _repo(tmp_path: Path) -> DbtlCycleRepository:
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
    return DbtlCycleRepository(session_factory)


async def _revision(repo: DbtlCycleRepository) -> int:
    current = await repo.get_cycle("cycle-1", project_id="project-1")
    assert current is not None
    return int(current["db_revision"])


async def _approved_design(tmp_path: Path) -> DbtlCycleRepository:
    repo = await _repo(tmp_path)
    await repo.create_cycle(
        cycle_id="cycle-1",
        project_id="project-1",
        title="Drought tolerance screen",
        cycle_class="computational",
        research_question="Which lines hold yield under late drought?",
        objective="Rank 200 lines",
        success_criteria="Top decile reproducible across two sites",
        created_by="user-1",
        policy_version=POLICY,
        idempotency_key="create-1",
    )
    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        artifact_type="design_report",
        uri="/mnt/user-data/workspace/design.json",
        content_hash=HASH_B,
        created_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="artifact-design",
    )
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        expected_db_revision=await _revision(repo),
        actor_user_id="user-1",
        idempotency_key="submit-design",
    )
    await repo.review_stage(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        decision="approve",
        rationale="Criteria are operational.",
        expected_db_revision=await _revision(repo),
        reviewer_user_id="user-2",
        reviewer_project_role="owner",
        idempotency_key="review-design",
    )
    return repo


class TestDesignApprovalOpensBuild:
    @pytest.mark.asyncio
    async def test_an_approved_design_reaches_ready_for_build_with_no_matrix(self, tmp_path: Path, no_reconciliation):
        repo = await _approved_design(tmp_path)

        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        assert cycle["state"] == "ready_for_build"
        statuses = {item["stage"]: item["status"] for item in cycle["stages"]}
        assert statuses["build"] == "in_progress"
        # Skipped, not worked and not deleted.
        assert statuses["reconciliation"] == "locked"

    @pytest.mark.asyncio
    async def test_the_gate_still_holds_when_the_flag_is_left_alone(self, tmp_path: Path, strict_reconciliation):
        # "Left alone" means the shipped default, which is pinned here rather
        # than read from the ambient config: a developer who opted out locally
        # would otherwise turn this into a second copy of the test above.
        repo = await _approved_design(tmp_path)

        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        assert cycle["state"] == "reconciliation"
        statuses = {item["stage"]: item["status"] for item in cycle["stages"]}
        assert statuses["build"] == "locked"


class TestBuildOwnsInputBinding:
    async def _record_lineage(self, repo: DbtlCycleRepository, *, input_artifacts: list[str] | None = None):
        return await repo.record_build_lineage(
            cycle_id="cycle-1",
            project_id="project-1",
            code_revision="git:abc123",
            config_revision="config:sha256:" + "c" * 64,
            environment={"python": "3.12.13"},
            rerun_spec={
                "version": 1,
                "entry_point": "/mnt/user-data/build.py",
                "command": "python build.py",
                "seed": "",
                "inputs": ["/mnt/user-data/uploads/yield.csv"],
                "environment": {"python": "3.12.13"},
                "configuration": [],
                "expected_outputs": ["/mnt/user-data/outputs/model.pkl"],
            },
            input_artifacts=input_artifacts if input_artifacts is not None else ["workspace_file:uploads/yield.csv:sha256:" + HASH_A],
            output_artifacts=[{"uri": "/mnt/user-data/outputs/model.pkl", "content_hash": "d" * 64, "revision": 1}],
            deviations=[],
            logs_uri="/mnt/user-data/outputs/build.log",
            recorded_by="agent:user-1",
            expected_db_revision=await _revision(repo),
            idempotency_key="lineage-1",
        )

    @pytest.mark.asyncio
    async def test_build_accepts_a_server_bound_input_without_a_dataset_declaration(self, tmp_path: Path, no_reconciliation):
        repo = await _approved_design(tmp_path)

        lineage = await self._record_lineage(repo)

        assert len(lineage["dataset_fingerprint"]) == 64
        assert lineage["input_artifacts"] == ["workspace_file:uploads/yield.csv:sha256:" + HASH_A]

    @pytest.mark.asyncio
    async def test_build_refuses_an_input_without_a_server_content_hash(self, tmp_path: Path, no_reconciliation):
        repo = await _approved_design(tmp_path)

        with pytest.raises(ValueError, match="server-computed SHA-256"):
            await self._record_lineage(repo, input_artifacts=["workspace_file:uploads/yield.csv"])

    @pytest.mark.asyncio
    async def test_build_refuses_to_record_a_result_that_names_no_data(self, tmp_path: Path, no_reconciliation):
        repo = await _approved_design(tmp_path)

        with pytest.raises(ValueError, match="at least one input file"):
            await self._record_lineage(repo, input_artifacts=[])

    @pytest.mark.asyncio
    async def test_one_click_build_approval_opens_test_from_ready_for_build(self, tmp_path: Path, no_reconciliation):
        repo = await _approved_design(tmp_path)
        await self._record_lineage(repo)
        await repo.attach_artifact(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="build",
            artifact_type="build_package",
            uri="/mnt/user-data/outputs/build-package.json",
            content_hash=HASH_B,
            created_by="agent:user-1",
            expected_db_revision=await _revision(repo),
            idempotency_key="build-package",
        )

        reviewed = await repo.review_stage(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="build",
            decision="approve",
            rationale="The execution package is reproducible.",
            expected_db_revision=await _revision(repo),
            reviewer_user_id="user-2",
            reviewer_project_role="owner",
            idempotency_key="approve-build",
            auto_submit=True,
        )

        assert reviewed["state"] == "test"
        statuses = {item["stage"]: item["status"] for item in reviewed["stages"]}
        assert statuses["build"] == "approved"
        assert statuses["test"] == "in_progress"

        view = await repo.build_test_view("cycle-1", project_id="project-1")
        assert view["validity_pack"]["pack_key"] == "generic-predictive:v2"
        assert ValidityCheckName.DUPLICATES_RELATEDNESS.value not in view["validity_pack"]["required_checks"]

        await repo.attach_artifact(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="test",
            artifact_type="validity_report",
            uri="/mnt/user-data/outputs/test-report.json",
            content_hash=HASH_A,
            created_by="agent:user-1",
            expected_db_revision=await _revision(repo),
            idempotency_key="test-package",
        )
        await repo.submit_stage_for_review(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="test",
            expected_db_revision=await _revision(repo),
            actor_user_id="user-1",
            idempotency_key="submit-test",
        )
        checks = [
            {
                "check": check.value,
                # A client cannot override the server's durable provenance
                # fact with stale Build prose.
                "status": "failed" if check is ValidityCheckName.RECONCILED_INPUTS else "passed",
                "detail": "Stale worker claim." if check is ValidityCheckName.RECONCILED_INPUTS else f"{check.value} passed.",
                "evidence_refs": [] if check is ValidityCheckName.RECONCILED_INPUTS else [f"artifact://{check.value}"],
            }
            for check in DEFAULT_VALIDITY_PACK.required_checks
        ]
        assessed = await repo.record_validity_assessment(
            cycle_id="cycle-1",
            project_id="project-1",
            metrics=[{"name": "holdout_r2", "value": 1.0, "threshold": 0.95, "criterion": "gte"}],
            checks=checks,
            recommendation="advance_to_learn",
            limitations=[],
            rationale="The approved holdout criterion and its required validity checks passed.",
            reviewer_user_id="user-2",
            reviewer_project_role="owner",
            expected_db_revision=await _revision(repo),
            idempotency_key="assess-test",
        )

        assert assessed["cycle"]["state"] == "learn"
        assert assessed["validity_assessment"]["outcome"] == "supported"
        recorded_checks = {item["check"]: item for item in assessed["validity_assessment"]["checks"]}
        assert recorded_checks[ValidityCheckName.RECONCILED_INPUTS.value]["status"] == "passed"
        assert recorded_checks[ValidityCheckName.RECONCILED_INPUTS.value]["evidence_refs"] == ["server://dbtl/build-lineage"]
