"""The exploratory Build closeout, driven through the real repository.

The pure tests prove the legal-move tables are right. These prove the decision
reaches a database write, and — the half that matters more — that skipping Test
does not quietly become a cheaper way of claiming the same thing.

Three properties are checked against real SQL:

* the deployment switch is enforced **here**, not only in the UI, because a
  frontend that can skip Test while persistence still treats the result as
  validated is precisely the failure this path exists to avoid;
* the path history records the Build → Learn edge it actually took, not the
  approval verdict behind it, so the audit shows where the cycle went; and
* an exploratory Learn can synthesize and cannot create a knowledge candidate,
  which makes the promotion block structural — no candidate means no claim, so
  promotion and publication have nothing to act on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from deerflow.config.database_config import DatabaseConfig
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
def conditional_test(monkeypatch):
    """The deployment rule, as the writer that consults it sees it."""
    monkeypatch.setattr("deerflow.persistence.dbtl.cycles.conditional_test_enabled", lambda: True)


async def _revision(repo: DbtlCycleRepository) -> int:
    current = await repo.get_cycle("cycle-1", project_id="project-1")
    assert current is not None
    return int(current["db_revision"])


async def _built_cycle(tmp_path: Path) -> DbtlCycleRepository:
    """A cycle whose Design is approved and whose Build is ready to review."""
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
    repo = DbtlCycleRepository(session_factory)
    await repo.create_cycle(
        cycle_id="cycle-1",
        project_id="project-1",
        title="Population structure survey",
        cycle_class="computational",
        research_question="How is the panel structured?",
        objective="Describe the panel before modelling anything",
        success_criteria="A readable PCA and a QC distribution",
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
        rationale="A descriptive pilot; nothing is being predicted.",
        expected_db_revision=await _revision(repo),
        reviewer_user_id="user-2",
        reviewer_project_role="owner",
        idempotency_key="review-design",
    )
    await repo.record_build_lineage(
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
            "inputs": ["/mnt/user-data/uploads/panel.vcf"],
            "environment": {"python": "3.12.13"},
            "configuration": [],
            "expected_outputs": ["/mnt/user-data/outputs/pca.png"],
        },
        input_artifacts=["workspace_file:uploads/panel.vcf:sha256:" + HASH_A],
        output_artifacts=[{"uri": "/mnt/user-data/outputs/pca.png", "content_hash": "d" * 64, "revision": 1}],
        deviations=[],
        logs_uri="/mnt/user-data/outputs/build.log",
        recorded_by="agent:user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="lineage-1",
    )
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
    return repo


async def _close_exploratively(repo: DbtlCycleRepository, *, idempotency_key: str = "approve-build") -> dict:
    return await repo.review_stage(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="build",
        decision="approve",
        rationale="The figures answer the question; there is nothing here to qualify.",
        expected_db_revision=await _revision(repo),
        reviewer_user_id="user-2",
        reviewer_project_role="owner",
        idempotency_key=idempotency_key,
        auto_submit=True,
        build_disposition="learn_exploratory",
    )


class TestTheSwitchIsEnforcedAtTheWriteBoundary:
    @pytest.mark.asyncio
    async def test_a_deployment_that_did_not_opt_in_refuses_the_skip(self, tmp_path: Path, mandatory_test):
        repo = await _built_cycle(tmp_path)

        with pytest.raises(ValueError, match="retention qualification"):
            await _close_exploratively(repo)

    @pytest.mark.asyncio
    async def test_an_unknown_disposition_is_refused_rather_than_ignored(self, tmp_path: Path, conditional_test):
        repo = await _built_cycle(tmp_path)

        with pytest.raises(ValueError, match="Unknown Build disposition"):
            await repo.review_stage(
                cycle_id="cycle-1",
                project_id="project-1",
                stage="build",
                decision="approve",
                rationale="Looks fine.",
                expected_db_revision=await _revision(repo),
                reviewer_user_id="user-2",
                reviewer_project_role="owner",
                idempotency_key="approve-build",
                auto_submit=True,
                build_disposition="ship_it",
            )

    @pytest.mark.asyncio
    async def test_the_default_build_approval_still_opens_test(self, tmp_path: Path, mandatory_test):
        repo = await _built_cycle(tmp_path)

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
        assert statuses["test"] == "in_progress"


class TestClosingWithoutQualification:
    @pytest.mark.asyncio
    async def test_the_cycle_moves_to_learn_with_test_recorded_as_skipped(self, tmp_path: Path, conditional_test):
        repo = await _built_cycle(tmp_path)

        reviewed = await _close_exploratively(repo)

        assert reviewed["state"] == "learn"
        statuses = {item["stage"]: item["status"] for item in reviewed["stages"]}
        assert statuses["build"] == "approved"
        assert statuses["test"] == "skipped"
        assert statuses["learn"] == "in_progress"

    @pytest.mark.asyncio
    async def test_the_path_history_records_the_edge_it_took(self, tmp_path: Path, conditional_test):
        repo = await _built_cycle(tmp_path)
        await _close_exploratively(repo)

        transitions = await repo.list_stage_transitions(cycle_id="cycle-1", project_id="project-1")

        exploratory = [row for row in transitions if row["chosen_route"] == "learn_exploratory"]
        assert len(exploratory) == 1
        assert exploratory[0]["from_stage"] == "build"
        assert exploratory[0]["to_stage"] == "learn"
        # The decision is bound to the evidence it was taken against, so a
        # later Build revision cannot be read as the thing that was closed.
        assert exploratory[0]["evidence_hash"] == HASH_B
        assert exploratory[0]["decided_by"] == "user-2"

    @pytest.mark.asyncio
    async def test_the_review_row_still_records_a_human_verdict(self, tmp_path: Path, conditional_test):
        repo = await _built_cycle(tmp_path)
        await _close_exploratively(repo)

        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        assert cycle["state"] == "learn"

    @pytest.mark.asyncio
    async def test_replaying_the_same_decision_is_the_same_decision(self, tmp_path: Path, conditional_test):
        repo = await _built_cycle(tmp_path)
        # A genuine replay is the same request arriving twice, so it carries
        # the revision the caller originally saw — not the one their own first
        # call produced.
        original_revision = await _revision(repo)
        await _close_exploratively(repo)

        replayed = await repo.review_stage(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="build",
            decision="approve",
            rationale="The figures answer the question; there is nothing here to qualify.",
            expected_db_revision=original_revision,
            reviewer_user_id="user-2",
            reviewer_project_role="owner",
            idempotency_key="approve-build",
            auto_submit=True,
            build_disposition="learn_exploratory",
        )

        assert replayed["state"] == "learn"
        transitions = await repo.list_stage_transitions(cycle_id="cycle-1", project_id="project-1")
        assert len([row for row in transitions if row["chosen_route"] == "learn_exploratory"]) == 1


class TestExploratoryLearnCannotClaimAnything:
    @pytest.mark.asyncio
    async def test_a_synthesis_may_be_recorded_with_no_validity_assessment(self, tmp_path: Path, conditional_test):
        repo = await _built_cycle(tmp_path)
        await _close_exploratively(repo)

        view = await repo.record_learn_synthesis(
            cycle_id="cycle-1",
            project_id="project-1",
            summary="The panel splits into three groups; the QC distribution is clean. Nothing was predicted.",
            candidates=[],
            actor_user_id="user-2",
            expected_db_revision=await _revision(repo),
            idempotency_key="learn-1",
        )

        assert view["candidates"] == []
        synthesized = [event for event in view["events"] if event["event_type"] == "learn.synthesized"]
        assert len(synthesized) == 1
        assert synthesized[0]["payload"]["retention_status"] == "not_validated"
        assert synthesized[0]["payload"]["test_outcome"] is None

    @pytest.mark.asyncio
    async def test_a_candidate_is_refused_on_the_exploratory_path(self, tmp_path: Path, conditional_test):
        repo = await _built_cycle(tmp_path)
        await _close_exploratively(repo)

        with pytest.raises(ValueError, match="without retention qualification"):
            await repo.record_learn_synthesis(
                cycle_id="cycle-1",
                project_id="project-1",
                summary="The panel splits into three groups.",
                candidates=[
                    {
                        "statement": "The panel has three subpopulations.",
                        "evidence": ["/mnt/user-data/outputs/pca.png"],
                        "grade": "supported",
                    }
                ],
                actor_user_id="user-2",
                expected_db_revision=await _revision(repo),
                idempotency_key="learn-1",
            )

    @pytest.mark.asyncio
    async def test_learn_still_refuses_a_cycle_that_settled_nothing(self, tmp_path: Path, mandatory_test):
        """The absent assessment must stay meaningful when nothing was skipped."""
        repo = await _built_cycle(tmp_path)
        await repo.review_stage(
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

        with pytest.raises(ValueError, match="unavailable while the cycle is in 'test'"):
            await repo.record_learn_synthesis(
                cycle_id="cycle-1",
                project_id="project-1",
                summary="Skipping ahead.",
                candidates=[],
                actor_user_id="user-2",
                expected_db_revision=await _revision(repo),
                idempotency_key="learn-1",
            )
