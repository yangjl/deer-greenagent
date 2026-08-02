"""Build workflow step attempts through durable storage.

The point of the table is that a failure late in the workflow does not discard
work that succeeded early in it. These tests exercise that through the real
repository and a real SQLite schema, because the two guarantees that make it
work — append-only attempts and one running attempt per step — are enforced by
database constraints rather than by calling code, and a mocked session would
assert nothing about either.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl.build_workflow import BUILD_WORKFLOW_V1, BuildErrorCode, BuildStepKey, StepState
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.dbtl.model import DbtlStageStepRunRow
from deerflow.persistence.dbtl.step_ops import DbtlStepConflict, DbtlStepImmutable
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

pytestmark = pytest.mark.asyncio

WORKFLOW = BUILD_WORKFLOW_V1.spec_key


@pytest_asyncio.fixture(autouse=True)
async def _close_test_engine():
    yield
    await close_engine()


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> DbtlCycleRepository:
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
    repository = DbtlCycleRepository(sf)
    await repository.create_cycle(
        cycle_id="cycle-1",
        project_id="project-1",
        title="Drought model",
        cycle_class="computational",
        research_question="Does the model generalize?",
        objective="Test an independent population",
        success_criteria="Accuracy >= 0.7",
        created_by="user-1",
        policy_version="greenagent-dbtl-v2-draft",
        idempotency_key="create",
    )
    return repository


@pytest_asyncio.fixture
async def stage_attempt_id(repo: DbtlCycleRepository) -> str:
    """The cycle's real Build stage run.

    A step attempt hangs off one by foreign key — that is what keeps a step from
    being recorded against a stage that does not exist — and `create_cycle`
    already materializes the stage rows, so this reads rather than invents one.
    """
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    build = next(stage for stage in cycle["stages"] if stage["stage"] == "build")
    return str(build["id"])


async def _open(repo: DbtlCycleRepository, stage_attempt_id: str, step: BuildStepKey, *, digest: str = "d1", **kwargs):
    return await repo.open_step_attempt(
        project_id="project-1",
        cycle_id="cycle-1",
        stage_attempt_id=stage_attempt_id,
        workflow_spec_key=WORKFLOW,
        step_key=step.value,
        input_digest=digest,
        **kwargs,
    )


async def _succeed(repo: DbtlCycleRepository, step_run_id: str, *, output: str = "out"):
    return await repo.settle_step_attempt(
        step_run_id=step_run_id,
        project_id="project-1",
        status=StepState.SUCCEEDED.value,
        output_digest=output,
    )


class TestAttemptsAppend:
    async def test_an_attempt_starts_running(self, repo, stage_attempt_id):
        attempt, dispatched = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)

        assert dispatched is True
        assert attempt["status"] == StepState.RUNNING.value
        assert attempt["attempt"] == 1

    async def test_a_retry_appends_rather_than_overwriting(self, repo, stage_attempt_id):
        first, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)
        await repo.settle_step_attempt(
            step_run_id=first["id"],
            project_id="project-1",
            status=StepState.FAILED.value,
            error_code=BuildErrorCode.DESIGN_UNREADABLE.value,
            error_summary="The approved Design could not be read.",
        )

        second, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)

        attempts = await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert [row["attempt"] for row in attempts] == [1, 2]
        # The failed record is exactly as it was written: it is what a reviewer
        # reads to understand why the retry happened.
        assert attempts[0]["status"] == StepState.FAILED.value
        assert attempts[0]["error_code"] == BuildErrorCode.DESIGN_UNREADABLE.value
        assert second["supersedes_step_run_id"] == first["id"]

    async def test_only_one_attempt_may_run_at_a_time(self, repo, stage_attempt_id):
        await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)

        with pytest.raises(DbtlStepConflict):
            await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest="d2")

    async def test_concurrent_dispatches_do_not_both_win(self, repo, stage_attempt_id):
        # The database arbitrates, because a check-then-write in the repository
        # would admit two dispatches that both passed the check.
        results = await asyncio.gather(
            _open(repo, stage_attempt_id, BuildStepKey.PLAN_BUILD, digest="a"),
            _open(repo, stage_attempt_id, BuildStepKey.PLAN_BUILD, digest="b"),
            return_exceptions=True,
        )

        conflicts = [entry for entry in results if isinstance(entry, DbtlStepConflict)]
        assert len(conflicts) == 1

    async def test_different_steps_may_run_together(self, repo, stage_attempt_id):
        await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)
        attempt, dispatched = await _open(repo, stage_attempt_id, BuildStepKey.PLAN_BUILD)
        assert dispatched is True
        assert attempt["status"] == StepState.RUNNING.value


class TestASettledAttemptIsImmutable:
    async def test_a_settled_attempt_cannot_be_settled_again(self, repo, stage_attempt_id):
        attempt, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)
        await _succeed(repo, attempt["id"])

        with pytest.raises(DbtlStepImmutable):
            await _succeed(repo, attempt["id"], output="different")

    async def test_a_paused_attempt_is_not_reopened_in_place(self, repo, stage_attempt_id):
        # `needs_input` holds the exact question; reopening the row would erase it.
        attempt, _ = await _open(repo, stage_attempt_id, BuildStepKey.PLAN_BUILD)
        await repo.settle_step_attempt(
            step_run_id=attempt["id"],
            project_id="project-1",
            status=StepState.NEEDS_INPUT.value,
            human_input_request_id="req-1",
        )

        with pytest.raises(DbtlStepImmutable):
            await _succeed(repo, attempt["id"])

        stored = (await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id))[0]
        assert stored["human_input_request_id"] == "req-1"

    async def test_a_non_terminal_status_is_refused(self, repo, stage_attempt_id):
        attempt, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)

        with pytest.raises(ValueError):
            await repo.settle_step_attempt(step_run_id=attempt["id"], project_id="project-1", status=StepState.RUNNING.value)

    async def test_another_project_cannot_settle_this_attempt(self, repo, stage_attempt_id):
        attempt, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)

        # A well-formed settlement, so what is asserted is the ownership check
        # rather than payload validation.
        with pytest.raises(LookupError):
            await repo.settle_step_attempt(step_run_id=attempt["id"], project_id="project-2", status=StepState.SUCCEEDED.value, output_digest="out")


class TestACommittedStepReplaysInsteadOfRunningAgain:
    async def test_the_same_input_digest_returns_the_committed_attempt(self, repo, stage_attempt_id):
        first, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest="d1")
        await _succeed(repo, first["id"])

        replayed, dispatched = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest="d1")

        assert dispatched is False
        assert replayed["id"] == first["id"]

    async def test_a_changed_input_digest_starts_new_work(self, repo, stage_attempt_id):
        first, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest="d1")
        await _succeed(repo, first["id"])

        fresh, dispatched = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest="d2")

        assert dispatched is True
        assert fresh["id"] != first["id"]

    async def test_a_failed_attempt_is_not_replayed(self, repo, stage_attempt_id):
        first, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest="d1")
        await repo.settle_step_attempt(step_run_id=first["id"], project_id="project-1", status=StepState.FAILED.value, error_code=BuildErrorCode.INTERNAL_ERROR.value)

        retried, dispatched = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest="d1")

        assert dispatched is True
        assert retried["id"] != first["id"]


class TestTheWorkflowView:
    async def test_it_reports_every_step_in_spec_order(self, repo, stage_attempt_id):
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        assert [step["key"] for step in view["steps"]] == [key.value for key in BUILD_WORKFLOW_V1.step_order]
        assert view["workflow_spec_key"] == WORKFLOW

    async def test_a_fresh_stage_resumes_at_the_first_step(self, repo, stage_attempt_id):
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        assert view["next_step"] == BuildStepKey.LOAD_DESIGN.value
        assert view["is_complete"] is False

    async def test_a_later_failure_leaves_earlier_successes_selected(self, repo, stage_attempt_id):
        """The whole reason the table exists.

        Digests come from the read model itself rather than a private helper:
        the property under test is that whoever *writes* an attempt and whoever
        *reads* the projection compute the same material, and a test that
        recomputed it independently would keep passing while those two drifted.
        """
        load_digest = await _expected(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)
        load, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest=load_digest)
        await _succeed(repo, load["id"], output="load_design-out")

        plan_digest = await _expected(repo, stage_attempt_id, BuildStepKey.PLAN_BUILD)
        plan, _ = await _open(repo, stage_attempt_id, BuildStepKey.PLAN_BUILD, digest=plan_digest, predecessor_step_run_ids=[load["id"]])
        await repo.settle_step_attempt(step_run_id=plan["id"], project_id="project-1", status=StepState.FAILED.value, error_code=BuildErrorCode.PLAN_CONTRACT_REJECTED.value)

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        by_key = {step["key"]: step for step in view["steps"]}
        assert by_key[BuildStepKey.LOAD_DESIGN.value]["selected_step_run_id"] == load["id"]
        assert by_key[BuildStepKey.PLAN_BUILD.value]["status"] == StepState.FAILED.value
        assert view["next_step"] == BuildStepKey.PLAN_BUILD.value

    async def test_the_view_says_whether_it_checked_staleness(self, repo, stage_attempt_id):
        checked = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)
        assert checked["staleness_checked"] is True

        # No resolvable stage run means no current material, so the view reports
        # what was attempted without claiming any of it is still valid.
        unknown = await repo.build_workflow_view(project_id="project-1", stage_attempt_id="stage-none", workflow_spec_key=WORKFLOW)
        assert unknown["staleness_checked"] is False

    async def test_a_changed_design_invalidates_a_recorded_success(self, repo, stage_attempt_id):
        # The chain has to detect this, or nothing else it does matters.
        digest = await _expected(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)
        load, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest=digest)
        await _succeed(repo, load["id"], output="load_design-out")

        await _attach_design(repo)

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        by_key = {step["key"]: step for step in view["steps"]}
        assert by_key[BuildStepKey.LOAD_DESIGN.value]["selected_step_run_id"] is None
        assert by_key[BuildStepKey.LOAD_DESIGN.value]["status"] == StepState.INVALIDATED.value
        assert view["next_step"] == BuildStepKey.LOAD_DESIGN.value

    async def test_a_step_beyond_the_resume_point_reads_as_waiting(self, repo, stage_attempt_id):
        # "Not started" and "blocked behind something that has not started" look
        # the same in a status column and mean different things.
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        by_key = {step["key"]: step for step in view["steps"]}
        assert by_key[BuildStepKey.LOAD_DESIGN.value]["status"] == StepState.QUEUED.value
        assert by_key[BuildStepKey.RENDER_REVIEW_DECK.value]["status"] == "waiting"

    async def test_every_attempt_is_retained_in_the_view(self, repo, stage_attempt_id):
        first, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest="d1")
        await repo.settle_step_attempt(step_run_id=first["id"], project_id="project-1", status=StepState.FAILED.value, error_code=BuildErrorCode.INTERNAL_ERROR.value)
        await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, digest="d1")

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        by_key = {step["key"]: step for step in view["steps"]}
        assert len(by_key[BuildStepKey.LOAD_DESIGN.value]["attempts"]) == 2

    async def test_another_stage_run_is_not_visible(self, repo, stage_attempt_id):
        await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id="stage-none", workflow_spec_key=WORKFLOW)

        assert all(not step["attempts"] for step in view["steps"])

    async def test_a_phase_records_its_capability_and_who_covered_it(self, repo, stage_attempt_id):
        # A reviewer reading "quantitative genetics: general-purpose" knows what
        # they are looking at; a reviewer reading nothing does not.
        await _open(
            repo,
            stage_attempt_id,
            BuildStepKey.EXECUTE_PHASES,
            digest="phase-1",
            phase_index=1,
            phase_key="simulate_founders",
            plan_digest="plan-1",
            capability="quantitative_genetics",
            agent_name="general-purpose",
            via_generalist=True,
        )

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        assert len(view["phases"]) == 1
        assert view["phases"][0]["capability"] == "quantitative_genetics"
        assert view["phases"][0]["via_generalist"] is True


class TestTheWorkflowKeyIsValidated:
    async def test_an_unknown_workflow_is_refused_before_anything_is_written(self, repo, stage_attempt_id):
        with pytest.raises(LookupError):
            await repo.open_step_attempt(
                project_id="project-1",
                cycle_id="cycle-1",
                stage_attempt_id=stage_attempt_id,
                workflow_spec_key="generic:build-workflow:v99",
                step_key=BuildStepKey.LOAD_DESIGN.value,
                input_digest="d1",
            )

        assert await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id) == []


async def _expected(repo: DbtlCycleRepository, stage_attempt_id: str, step: BuildStepKey) -> str:
    """The digest the read model expects for a step, right now."""
    view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)
    return next(entry for entry in view["steps"] if entry["key"] == step.value)["expected_input_digest"]


async def _attach_design(repo: DbtlCycleRepository) -> None:
    """Record a Design artifact, changing the material Build binds to."""
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        artifact_type="design_brief",
        uri="/outputs/dbtl/design.md",
        content_hash="c" * 64,
        created_by="user-1",
        expected_db_revision=int(cycle["db_revision"]),
        idempotency_key="design-artifact-1",
    )


class TestTheDatabaseIsTheArbiterNotTheFastPath:
    """The repository's pre-check is a courtesy; the index is the guarantee.

    These write rows directly, bypassing `open_step_attempt` entirely, because
    the earlier tests passed on the Python fast path alone: both uniqueness
    rules were keyed on the *nullable* `phase_key`, and SQL compares NULL
    unequal to itself, so neither constrained anything for the four steps that
    have no phase — which is every step but one.
    """

    async def _insert(self, repo, stage_attempt_id, *, id: str, **overrides):
        fields = {
            "id": id,
            "project_id": "project-1",
            "cycle_id": "cycle-1",
            "stage_attempt_id": stage_attempt_id,
            "workflow_spec_key": WORKFLOW,
            "step_key": BuildStepKey.LOAD_DESIGN.value,
            "attempt": 1,
            "status": StepState.RUNNING.value,
            "phase_slot": "",
            "input_digest": "d",
            "predecessor_step_run_ids": [],
            "execution": {},
            "error_summary": "",
            **overrides,
        }
        async with repo._sf() as session:
            async with session.begin():
                session.add(DbtlStageStepRunRow(**fields))

    async def test_two_running_rows_for_one_ordinary_step_are_refused(self, repo, stage_attempt_id):
        await self._insert(repo, stage_attempt_id, id="row-1")

        with pytest.raises(IntegrityError):
            await self._insert(repo, stage_attempt_id, id="row-2", attempt=2)

    async def test_two_rows_with_the_same_attempt_number_are_refused(self, repo, stage_attempt_id):
        await self._insert(repo, stage_attempt_id, id="row-1", status=StepState.FAILED.value)

        with pytest.raises(IntegrityError):
            await self._insert(repo, stage_attempt_id, id="row-2", status=StepState.CANCELLED.value)

    async def test_a_settled_row_does_not_block_the_next_attempt(self, repo, stage_attempt_id):
        # The running index is partial: only one *running* row is excluded.
        await self._insert(repo, stage_attempt_id, id="row-1", status=StepState.FAILED.value)
        await self._insert(repo, stage_attempt_id, id="row-2", attempt=2)

        assert len(await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id)) == 2

    async def test_a_phase_and_an_ordinary_step_do_not_collide(self, repo, stage_attempt_id):
        await self._insert(repo, stage_attempt_id, id="row-1")
        await self._insert(
            repo,
            stage_attempt_id,
            id="row-2",
            step_key=BuildStepKey.EXECUTE_PHASES.value,
            phase_index=1,
            phase_key="simulate",
            phase_slot="simulate",
        )

        assert len(await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id)) == 2

    async def test_two_phases_of_one_plan_do_not_collide(self, repo, stage_attempt_id):
        for index, key in ((1, "simulate"), (2, "derive")):
            await self._insert(
                repo,
                stage_attempt_id,
                id=f"row-{index}",
                step_key=BuildStepKey.EXECUTE_PHASES.value,
                phase_index=index,
                phase_key=key,
                phase_slot=key,
            )

        assert len(await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id)) == 2


class TestTheEvidenceChainIsVerified:
    async def test_a_success_must_record_an_output_digest(self, repo, stage_attempt_id):
        # Every downstream step's identity is computed from it; a success
        # without one would be selected as a predecessor and contribute nothing.
        attempt, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)

        with pytest.raises(ValueError, match="output digest"):
            await repo.settle_step_attempt(step_run_id=attempt["id"], project_id="project-1", status=StepState.SUCCEEDED.value)

    async def test_an_unknown_predecessor_is_refused(self, repo, stage_attempt_id):
        with pytest.raises(LookupError, match="predecessor"):
            await _open(repo, stage_attempt_id, BuildStepKey.PLAN_BUILD, predecessor_step_run_ids=["dss_nonexistent"])

    async def test_an_unsuccessful_predecessor_is_refused(self, repo, stage_attempt_id):
        failed, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)
        await repo.settle_step_attempt(step_run_id=failed["id"], project_id="project-1", status=StepState.FAILED.value)

        with pytest.raises(LookupError, match="predecessor"):
            await _open(repo, stage_attempt_id, BuildStepKey.PLAN_BUILD, predecessor_step_run_ids=[failed["id"]])

    async def test_a_real_predecessor_is_accepted_and_recorded(self, repo, stage_attempt_id):
        load, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)
        await _succeed(repo, load["id"])

        plan, _ = await _open(repo, stage_attempt_id, BuildStepKey.PLAN_BUILD, predecessor_step_run_ids=[load["id"]])

        assert plan["predecessor_step_run_ids"] == [load["id"]]

    async def test_a_stage_run_from_another_project_is_refused(self, repo):
        # The foreign key accepts it; project ownership is what makes it wrong.
        with pytest.raises(LookupError, match="stage run"):
            await repo.open_step_attempt(
                project_id="project-1",
                cycle_id="cycle-1",
                stage_attempt_id="stage-from-nowhere",
                workflow_spec_key=WORKFLOW,
                step_key=BuildStepKey.LOAD_DESIGN.value,
                input_digest="d",
            )


class TestTheReadModelSaysWhereTheBuildActuallyIs:
    async def test_a_running_step_reads_as_running(self, repo, stage_attempt_id):
        await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        assert _step(view, BuildStepKey.LOAD_DESIGN)["status"] == StepState.RUNNING.value

    async def test_a_failed_step_reads_as_failed_and_names_its_reason(self, repo, stage_attempt_id):
        attempt, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)
        await repo.settle_step_attempt(
            step_run_id=attempt["id"],
            project_id="project-1",
            status=StepState.FAILED.value,
            error_code=BuildErrorCode.DESIGN_UNREADABLE.value,
        )

        step = _step(await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW), BuildStepKey.LOAD_DESIGN)
        assert step["status"] == StepState.FAILED.value
        assert step["error_code"] == BuildErrorCode.DESIGN_UNREADABLE.value

    async def test_a_paused_step_reads_as_waiting_for_a_person(self, repo, stage_attempt_id):
        attempt, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)
        await repo.settle_step_attempt(step_run_id=attempt["id"], project_id="project-1", status=StepState.NEEDS_INPUT.value, human_input_request_id="req-1")

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        assert _step(view, BuildStepKey.LOAD_DESIGN)["status"] == StepState.NEEDS_INPUT.value

    async def test_a_cancelled_step_is_not_reported_as_queued(self, repo, stage_attempt_id):
        attempt, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)
        await repo.settle_step_attempt(step_run_id=attempt["id"], project_id="project-1", status=StepState.CANCELLED.value)

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        assert _step(view, BuildStepKey.LOAD_DESIGN)["status"] == StepState.CANCELLED.value

    async def test_a_step_nobody_has_attempted_still_reads_as_queued(self, repo, stage_attempt_id):
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        assert _step(view, BuildStepKey.LOAD_DESIGN)["status"] == StepState.QUEUED.value


class TestTheReadModelStaysBounded:
    async def test_an_oversized_error_summary_is_capped_when_written(self, repo, stage_attempt_id):
        attempt, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN)

        await repo.settle_step_attempt(
            step_run_id=attempt["id"],
            project_id="project-1",
            status=StepState.FAILED.value,
            error_code=BuildErrorCode.SANDBOX_EXECUTION_FAILED.value,
            error_summary="x" * 50_000,
        )

        stored = (await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id))[0]
        assert len(stored["error_summary"]) <= 2_000

    async def test_execution_metadata_keeps_scalars_and_drops_the_rest(self, repo, stage_attempt_id):
        # A nested object is where an unreviewed payload — a transcript, an
        # environment dump, a set of headers — rides into a durable record.
        attempt, _ = await _open(
            repo,
            stage_attempt_id,
            BuildStepKey.LOAD_DESIGN,
            execution={"duration_ms": 1200, "model": "gpt", "raw_log": {"stdout": "secret"}, "prompt": ["a", "b"]},
        )

        assert attempt["execution"] == {"duration_ms": 1200, "model": "gpt"}

    async def test_a_long_execution_value_is_truncated(self, repo, stage_attempt_id):
        attempt, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, execution={"command": "y" * 5_000})

        assert len(attempt["execution"]["command"]) <= 512


def _step(view: dict, key: BuildStepKey) -> dict:
    return next(step for step in view["steps"] if step["key"] == key.value)


class TestAStepLeftRunningByADeadProcessIsReclaimed:
    """The one-running-attempt rule has no expiry, so somebody must give it one.

    Without this a Gateway killed mid-phase leaves a row that blocks its step
    forever: every later Build collides with a process that no longer exists.
    Reclamation prevents that stale row from triggering the recorder's visible,
    fail-closed refusal on every later Build.
    """

    async def test_a_stale_attempt_from_another_run_is_cancelled_and_the_step_reopens(self, repo, stage_attempt_id) -> None:
        first, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, parent_run_id="run-1")
        await _age_step(repo, first["id"], hours=8)

        second, dispatched = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, parent_run_id="run-2")

        assert dispatched is True
        assert second["attempt"] == 2
        rows = await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id)
        reclaimed = next(row for row in rows if row["id"] == first["id"])
        assert reclaimed["status"] == StepState.CANCELLED.value
        assert reclaimed["error_code"] == BuildErrorCode.CANCELLED.value

    async def test_a_live_attempt_from_another_run_is_still_refused(self, repo, stage_attempt_id) -> None:
        await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, parent_run_id="run-1")

        with pytest.raises(DbtlStepConflict):
            await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, parent_run_id="run-2")

    async def test_this_runs_own_attempt_is_never_reclaimed_from_under_it(self, repo, stage_attempt_id) -> None:
        first, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, parent_run_id="run-1")
        await _age_step(repo, first["id"], hours=8)

        with pytest.raises(DbtlStepConflict):
            await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, parent_run_id="run-1")

    async def test_a_known_interrupted_run_is_cancelled_immediately(self, repo, stage_attempt_id) -> None:
        first, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, parent_run_id="run-1")

        settled = await repo.cancel_running_step_attempts(
            project_id="project-1",
            cycle_id="cycle-1",
            parent_run_id="run-1",
            summary="The owning run was interrupted.",
        )
        second, dispatched = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, parent_run_id="run-2")

        assert settled == 1
        assert dispatched is True
        assert second["attempt"] == 2
        rows = await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id)
        interrupted = next(row for row in rows if row["id"] == first["id"])
        assert interrupted["status"] == StepState.CANCELLED.value
        assert interrupted["error_code"] == BuildErrorCode.CANCELLED.value
        assert interrupted["completed_at"] is not None

    async def test_interrupt_cleanup_cannot_cancel_another_runs_step(self, repo, stage_attempt_id) -> None:
        first, _ = await _open(repo, stage_attempt_id, BuildStepKey.LOAD_DESIGN, parent_run_id="run-1")

        settled = await repo.cancel_running_step_attempts(
            project_id="project-1",
            cycle_id="cycle-1",
            parent_run_id="run-2",
            summary="The owning run was interrupted.",
        )

        assert settled == 0
        rows = await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert next(row for row in rows if row["id"] == first["id"])["status"] == StepState.RUNNING.value


async def _age_step(repo, step_run_id: str, *, hours: int) -> None:
    """Backdate an attempt's start, standing in for a process that died then."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    async with repo._sf() as session:
        async with session.begin():
            await session.execute(
                update(DbtlStageStepRunRow).where(DbtlStageStepRunRow.id == step_run_id).values(started_at=datetime.now(UTC) - timedelta(hours=hours)),
            )
