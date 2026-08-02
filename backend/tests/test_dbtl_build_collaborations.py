"""The durable record of a paused Build, through real storage.

The two guarantees that make a paused Build safe to resume — one open control
per stage attempt, and an idempotent-but-not-overwritable response — are
database constraints rather than calling-code checks, so these run against a
real SQLite schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl.build_control import plan_confirmation_request, step_failure_request
from deerflow.dbtl.build_plan import BuildPhase, BuildPhasePlan, PlanFeasibility
from deerflow.dbtl.build_workflow import BUILD_WORKFLOW_V1, BuildStepKey, input_digest
from deerflow.dbtl.capabilities import Capability
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.dbtl.collaboration_ops import ANSWERED, HELD, OPEN, SUPERSEDED, DbtlCollaborationConflict
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
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    return str(next(stage for stage in cycle["stages"] if stage["stage"] == "build")["id"])


def _plan() -> BuildPhasePlan:
    return BuildPhasePlan(
        feasibility=PlanFeasibility.PLANNED,
        phases=(
            BuildPhase(phase_key="simulate", title="Simulate", objective="Draw founders.", capability=Capability.SOFTWARE_ENGINEERING),
            BuildPhase(phase_key="fit", title="Fit", objective="Fit the model.", capability=Capability.STATISTICAL_ANALYSIS),
        ),
    )


def _card(request_id: str, stage_attempt_id: str) -> dict:
    card = plan_confirmation_request(
        plan=_plan(),
        cycle_id="cycle-1",
        stage_attempt_id=stage_attempt_id,
        workflow_spec_key=WORKFLOW,
        cycle_revision=3,
    ).as_card()
    card["request_id"] = request_id
    return card


def _failure_card(request_id: str, stage_attempt_id: str) -> dict:
    card = step_failure_request(
        step_key="execute_phases",
        step_label="Run the build",
        error_code="execution_contract_rejected",
        error_summary="Phase two returned prose.",
        cycle_id="cycle-1",
        stage_attempt_id=stage_attempt_id,
        workflow_spec_key=WORKFLOW,
        cycle_revision=4,
    ).as_card()
    card["request_id"] = request_id
    return card


class TestOneControlIsOpenAtATime:
    async def test_the_same_card_replays_instead_of_opening_a_second_row(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        first = await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_card("req-1", stage_attempt_id))
        again = await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_card("req-1", stage_attempt_id))

        assert first["id"] == again["id"]
        assert len(await repo.list_build_collaborations(project_id="project-1", stage_attempt_id=stage_attempt_id)) == 1

    async def test_a_different_control_supersedes_the_open_one_and_keeps_it(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        first = await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_card("req-1", stage_attempt_id))
        second = await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_failure_card("req-2", stage_attempt_id))

        rows = {row["id"]: row for row in await repo.list_build_collaborations(project_id="project-1", stage_attempt_id=stage_attempt_id)}
        assert rows[first["id"]]["lifecycle"] == SUPERSEDED
        assert rows[second["id"]]["lifecycle"] == OPEN
        assert len(rows) == 2

    async def test_a_control_cannot_be_recorded_against_another_projects_stage(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        with pytest.raises(LookupError):
            await repo.open_build_collaboration(project_id="project-2", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_card("req-1", stage_attempt_id))

    async def test_a_control_must_name_the_card_it_records(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        card = _card("", stage_attempt_id)
        with pytest.raises(ValueError):
            await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=card)

    async def test_the_workflow_view_surfaces_the_open_human_control(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        await repo.open_build_collaboration(
            project_id="project-1",
            cycle_id="cycle-1",
            stage_attempt_id=stage_attempt_id,
            request=_card("req-plan", stage_attempt_id),
            originating_thread_id="thread-build",
        )

        view = await repo.build_workflow_view(
            project_id="project-1",
            stage_attempt_id=stage_attempt_id,
            workflow_spec_key=WORKFLOW,
        )

        assert view["waiting_control"] == {
            "kind": "plan_confirmation",
            "request_id": "req-plan",
            "label": "Waiting for you — confirm the Build plan.",
            "originating_thread_id": "thread-build",
        }


class TestTheAnswerIsRecordedOnce:
    async def test_it_keeps_the_persons_words_and_who_answered(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_card("req-1", stage_attempt_id))

        answered = await repo.answer_build_collaboration(
            project_id="project-1",
            request_id="req-1",
            action="change_plan",
            response_text="Split the fitting phase in two.",
            responder_user_id="user-1",
            client_submission_id="sub-1",
        )

        assert answered["lifecycle"] == ANSWERED
        assert answered["action"] == "change_plan"
        assert answered["response_text"] == "Split the fitting phase in two."
        assert answered["responder_user_id"] == "user-1"

    async def test_hold_is_recorded_as_a_decision_rather_than_as_no_answer(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_card("req-1", stage_attempt_id))

        answered = await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="hold_here", client_submission_id="sub-1")

        assert answered["lifecycle"] == HELD
        assert await repo.latest_build_collaboration(project_id="project-1", stage_attempt_id=stage_attempt_id) is None

    async def test_a_replayed_submission_returns_the_recorded_answer(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_card("req-1", stage_attempt_id))
        await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="start_build", client_submission_id="sub-1")
        first = await repo.latest_build_collaboration(project_id="project-1", stage_attempt_id=stage_attempt_id, lifecycle=None)

        again = await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="start_build", client_submission_id="sub-1")

        assert first is not None
        assert again["responded_at"] == first["responded_at"]

    async def test_a_different_answer_under_the_same_submission_conflicts(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_card("req-1", stage_attempt_id))
        await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="start_build", client_submission_id="sub-1")

        with pytest.raises(DbtlCollaborationConflict):
            await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="restart_build", client_submission_id="sub-1")

    async def test_an_unknown_control_is_not_silently_created(self, repo: DbtlCycleRepository) -> None:
        with pytest.raises(LookupError):
            await repo.answer_build_collaboration(project_id="project-1", request_id="never-emitted", action="start_build")

    async def test_an_action_outside_the_vocabulary_is_refused(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_card("req-1", stage_attempt_id))

        with pytest.raises(ValueError):
            await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="approve_the_build")


class TestRestartAndReplanMoveTheDigestChain:
    """A restart that recomputed the same digest would replay what it discards."""

    async def test_an_untouched_build_has_no_epochs(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        assert await repo.build_control_epochs(project_id="project-1", stage_attempt_id=stage_attempt_id) == {"restart": 0, "replan": 0}

    async def test_a_replan_moves_only_the_plan(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_failure_card("req-1", stage_attempt_id))
        await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="replan_build")

        assert await repo.build_control_epochs(project_id="project-1", stage_attempt_id=stage_attempt_id) == {"restart": 0, "replan": 1}

    async def test_a_restart_moves_the_design_read_and_the_plan_beneath_it(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_failure_card("req-1", stage_attempt_id))
        await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="restart_build")

        assert await repo.build_control_epochs(project_id="project-1", stage_attempt_id=stage_attempt_id) == {"restart": 1, "replan": 1}

    async def test_a_hold_moves_nothing(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_failure_card("req-1", stage_attempt_id))
        await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="hold_here")

        assert await repo.build_control_epochs(project_id="project-1", stage_attempt_id=stage_attempt_id) == {"restart": 0, "replan": 0}

    async def test_a_restart_changes_what_the_step_material_binds(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        before = await repo.build_step_material_for(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_failure_card("req-1", stage_attempt_id))
        await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="restart_build")

        after = await repo.build_step_material_for(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        assert input_digest(BuildStepKey.LOAD_DESIGN, material=before["load_design"]) != input_digest(BuildStepKey.LOAD_DESIGN, material=after["load_design"])
        assert input_digest(BuildStepKey.PLAN_BUILD, material=before["plan_build"]) != input_digest(BuildStepKey.PLAN_BUILD, material=after["plan_build"])

    async def test_a_replan_leaves_the_design_read_reusable(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        before = await repo.build_step_material_for(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_failure_card("req-1", stage_attempt_id))
        await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="replan_build")

        after = await repo.build_step_material_for(project_id="project-1", stage_attempt_id=stage_attempt_id, workflow_spec_key=WORKFLOW)

        assert before["load_design"] == after["load_design"]
        assert before["plan_build"] != after["plan_build"]


class TestAnAnswerNamesTheEmissionItAnswers:
    """The card id recurs by design, so the row id is what distinguishes them.

    Without it a redelivered answer to an *earlier* emission settles whichever
    control is open now — and for Replan and Restart that moves the digest chain
    a second time, discarding committed phases nobody asked to discard again.
    """

    async def test_a_redelivered_answer_settles_its_own_row_and_not_the_new_one(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        first = await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_failure_card("req-1", stage_attempt_id))
        await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", collaboration_id=first["id"], action="restart_build")
        # The same pause recurs: same derived id, second row.
        second = await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_failure_card("req-1", stage_attempt_id))
        assert second["id"] != first["id"]

        replayed = await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", collaboration_id=first["id"], action="restart_build")

        assert replayed["id"] == first["id"]
        assert await repo.build_control_epochs(project_id="project-1", stage_attempt_id=stage_attempt_id) == {"restart": 1, "replan": 1}
        assert (await repo.latest_build_collaboration(project_id="project-1", stage_attempt_id=stage_attempt_id))["id"] == second["id"]

    async def test_a_different_answer_to_a_settled_emission_still_conflicts(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        row = await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_failure_card("req-1", stage_attempt_id))
        await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", collaboration_id=row["id"], action="restart_build")

        with pytest.raises(DbtlCollaborationConflict):
            await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", collaboration_id=row["id"], action="replan_build")

    async def test_an_answer_naming_no_emission_still_settles_the_open_control(self, repo: DbtlCycleRepository, stage_attempt_id: str) -> None:
        await repo.open_build_collaboration(project_id="project-1", cycle_id="cycle-1", stage_attempt_id=stage_attempt_id, request=_card("req-1", stage_attempt_id))

        answered = await repo.answer_build_collaboration(project_id="project-1", request_id="req-1", action="start_build")

        assert answered["lifecycle"] == ANSWERED
