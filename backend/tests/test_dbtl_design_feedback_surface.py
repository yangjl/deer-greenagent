"""The durable descriptor that proves a deck came from the Design workflow.

A registered surface is what separates the deck DeerFlow rendered from any
other HTML an agent produced: it binds one file's exact bytes to one project,
cycle, attempt, round, and originating conversation. Nothing here makes a deck
interactive — that needs the authenticated parent bridge — but everything the
bridge will check is decided here, so these tests are mostly about what the
descriptor must *refuse*.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.dbtl import DbtlCycleRepository, DbtlWorkflowRefused
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

pytestmark = pytest.mark.asyncio

DECK_HASH = "a" * 64
OTHER_DECK_HASH = "b" * 64
EVIDENCE_HASH = "c" * 64
DECK_URI = "/mnt/user-data/outputs/dbtl/cycle/design/design-slides-rev1-aaaaaa.html"


@pytest_asyncio.fixture(autouse=True)
async def _close_test_engine():
    yield
    await close_engine()


async def _repo(tmp_path: Path, *, projects: tuple[str, ...] = ("project-1",)) -> DbtlCycleRepository:
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
    for index, project_id in enumerate(projects):
        await workspaces.create_project(
            project_id=project_id,
            workspace_id=workspace["id"],
            name=f"Project {index}",
            slug=f"project-{index}",
            description=None,
            crop_profile="maize",
            created_by="user-1",
        )
    repo = DbtlCycleRepository(sf)
    for index, project_id in enumerate(projects):
        await repo.create_cycle(
            cycle_id=f"cycle-{index + 1}",
            project_id=project_id,
            title="Drought model",
            cycle_class="computational",
            research_question="Does the model generalize?",
            objective="Test an independent population",
            success_criteria="Accuracy >= 0.7",
            created_by="user-1",
            policy_version="greenagent-dbtl-v2-draft",
            idempotency_key=f"create-{index}",
        )
    return repo


async def _attempt_id(repo: DbtlCycleRepository, *, cycle_id: str = "cycle-1", project_id: str = "project-1", stage: str = "design") -> str:
    cycle = await repo.get_cycle(cycle_id, project_id=project_id)
    assert cycle is not None
    return next(item["id"] for item in cycle["stages"] if item["stage"] == stage)


async def _register(
    repo: DbtlCycleRepository,
    *,
    cycle_id: str = "cycle-1",
    project_id: str = "project-1",
    mode: str = "chair_feedback",
    deck_content_hash: str = DECK_HASH,
    design_round: int = 1,
    thread_id: str = "thread-1",
    **overrides: object,
) -> dict:
    return await repo.register_design_feedback_surface(
        project_id=project_id,
        cycle_id=cycle_id,
        stage_attempt_id=await _attempt_id(repo, cycle_id=cycle_id, project_id=project_id),
        design_round=design_round,
        originating_thread_id=thread_id,
        mode=mode,
        deck_uri=DECK_URI,
        deck_content_hash=deck_content_hash,
        **overrides,
    )


class TestRegistrationBindsTheDeckToItsOrigin:
    async def test_a_registered_surface_records_where_it_came_from(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)

        surface = await _register(repo)

        assert surface["surface_id"]
        assert surface["project_id"] == "project-1"
        assert surface["cycle_id"] == "cycle-1"
        assert surface["originating_thread_id"] == "thread-1"
        assert surface["mode"] == "chair_feedback"
        assert surface["deck_content_hash"] == DECK_HASH
        assert surface["design_round"] == 1
        assert surface["superseded_by_surface_id"] is None

    async def test_the_binding_revisions_are_server_owned(self, tmp_path: Path) -> None:
        """A caller cannot assert which revision its deck was rendered against."""
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None

        surface = await _register(repo)

        assert surface["bound_db_revision"] == cycle["db_revision"]
        assert surface["projection_hash"] == cycle["projection_hash"]
        assert surface["policy_version"] == cycle["policy_version"]

    async def test_a_paused_meeting_binds_its_chair_request(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)

        surface = await _register(repo, human_input_request_id="dbtl-design__abc__def", chair_worker_run_id="run-9")

        assert surface["human_input_request_id"] == "dbtl-design__abc__def"
        assert surface["chair_worker_run_id"] == "run-9"


class TestLookupIsScoped:
    async def test_a_surface_is_readable_by_its_own_project(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        surface = await _register(repo)

        found = await repo.get_design_feedback_surface(surface["surface_id"], project_id="project-1")

        assert found is not None
        assert found["surface_id"] == surface["surface_id"]

    async def test_another_project_cannot_read_it(self, tmp_path: Path) -> None:
        """The descriptor is the addressing rule; a wrong project is not a hint."""
        repo = await _repo(tmp_path, projects=("project-1", "project-2"))
        surface = await _register(repo)

        found = await repo.get_design_feedback_surface(surface["surface_id"], project_id="project-2")

        assert found is None

    async def test_an_unknown_surface_id_is_simply_absent(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)

        assert await repo.get_design_feedback_surface("forged-surface-id", project_id="project-1") is None

    async def test_the_latest_surface_for_an_attempt_is_addressable(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        await _register(repo)
        second = await _register(repo, deck_content_hash=OTHER_DECK_HASH, design_round=2)

        latest = await repo.latest_design_feedback_surface(project_id="project-1", cycle_id="cycle-1")

        assert latest is not None
        assert latest["surface_id"] == second["surface_id"]


class TestRegenerationSupersedesRatherThanMutates:
    async def test_a_new_deck_supersedes_the_previous_surface(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        first = await _register(repo)

        second = await _register(repo, deck_content_hash=OTHER_DECK_HASH, design_round=2)

        stale = await repo.get_design_feedback_surface(first["surface_id"], project_id="project-1")
        assert stale is not None
        assert stale["superseded_by_surface_id"] == second["surface_id"]
        assert stale["is_current"] is False
        assert second["is_current"] is True

    async def test_a_superseded_surface_stays_readable(self, tmp_path: Path) -> None:
        """It is the audit record of what somebody was shown."""
        repo = await _repo(tmp_path)
        first = await _register(repo)
        await _register(repo, deck_content_hash=OTHER_DECK_HASH, design_round=2)

        stale = await repo.get_design_feedback_surface(first["surface_id"], project_id="project-1")

        assert stale is not None
        assert stale["deck_content_hash"] == DECK_HASH
        assert stale["deck_uri"] == DECK_URI

    async def test_re_registering_the_same_deck_returns_the_same_surface(self, tmp_path: Path) -> None:
        """A retried turn must not mint a second descriptor for one file."""
        repo = await _repo(tmp_path)
        first = await _register(repo)

        again = await _register(repo)

        assert again["surface_id"] == first["surface_id"]
        assert again["is_current"] is True


class TestRefusals:
    async def test_a_review_surface_must_name_the_evidence_it_shows(self, tmp_path: Path) -> None:
        """A verdict binds to a document; a review deck with none binds to nothing."""
        repo = await _repo(tmp_path)

        with pytest.raises(DbtlWorkflowRefused, match="evidence"):
            await _register(repo, mode="stage_review")

    async def test_a_paused_meeting_needs_no_evidence(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)

        surface = await _register(repo, mode="chair_feedback")

        assert surface["evidence_artifact_id"] is None

    async def test_an_unknown_mode_is_refused(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)

        with pytest.raises(DbtlWorkflowRefused, match="mode"):
            await _register(repo, mode="approve_everything")

    async def test_a_deck_hash_that_is_not_a_sha256_is_refused(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)

        with pytest.raises(DbtlWorkflowRefused, match="hash"):
            await _register(repo, deck_content_hash="not-a-hash")

    async def test_an_uppercase_hash_is_refused_rather_than_normalized(self, tmp_path: Path) -> None:
        """Normalizing would make two spellings of one hash compare unequal later."""
        repo = await _repo(tmp_path)

        with pytest.raises(DbtlWorkflowRefused, match="hash"):
            await _register(repo, deck_content_hash=DECK_HASH.upper())

    async def test_a_surface_needs_an_originating_conversation(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)

        with pytest.raises(DbtlWorkflowRefused, match="conversation"):
            await _register(repo, thread_id="  ")

    async def test_a_foreign_cycle_is_refused(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path, projects=("project-1", "project-2"))
        attempt = await _attempt_id(repo, cycle_id="cycle-2", project_id="project-2")

        with pytest.raises(DbtlWorkflowRefused, match="cycle"):
            await repo.register_design_feedback_surface(
                project_id="project-1",
                cycle_id="cycle-2",
                stage_attempt_id=attempt,
                design_round=1,
                originating_thread_id="thread-1",
                mode="chair_feedback",
                deck_uri=DECK_URI,
                deck_content_hash=DECK_HASH,
            )

    async def test_an_attempt_from_another_cycle_is_refused(self, tmp_path: Path) -> None:
        """The attempt is what the review binds to; it must belong to this cycle."""
        repo = await _repo(tmp_path, projects=("project-1", "project-2"))
        foreign_attempt = await _attempt_id(repo, cycle_id="cycle-2", project_id="project-2")

        with pytest.raises(DbtlWorkflowRefused, match="attempt"):
            await repo.register_design_feedback_surface(
                project_id="project-1",
                cycle_id="cycle-1",
                stage_attempt_id=foreign_attempt,
                design_round=1,
                originating_thread_id="thread-1",
                mode="chair_feedback",
                deck_uri=DECK_URI,
                deck_content_hash=DECK_HASH,
            )


class TestEvidenceBinding:
    async def test_a_review_surface_records_the_exact_evidence_revision(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        await repo.attach_artifact(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="design",
            artifact_type="design_brief.v2",
            uri="/mnt/user-data/outputs/dbtl/cycle/design/design-review-rev1-cccccc.md",
            content_hash=EVIDENCE_HASH,
            created_by="user-1",
            expected_db_revision=int(cycle["db_revision"]),
            idempotency_key="artifact-1",
        )
        detail = await repo.get_cycle("cycle-1", project_id="project-1")
        assert detail is not None
        artifact = detail["artifacts"][-1]

        surface = await _register(
            repo,
            mode="stage_review",
            evidence_artifact_id=artifact["id"],
            evidence_artifact_revision=artifact["revision"],
            evidence_content_hash=EVIDENCE_HASH,
        )

        assert surface["evidence_artifact_id"] == artifact["id"]
        assert surface["evidence_artifact_revision"] == artifact["revision"]
        assert surface["evidence_content_hash"] == EVIDENCE_HASH

    async def test_evidence_from_another_cycle_is_refused(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)

        with pytest.raises(DbtlWorkflowRefused, match="evidence"):
            await _register(
                repo,
                mode="stage_review",
                evidence_artifact_id="artifact-that-does-not-exist",
                evidence_artifact_revision=1,
                evidence_content_hash=EVIDENCE_HASH,
            )


class TestCallerSuppliedIdentifiers:
    async def test_the_caller_may_name_the_surface_it_embedded_in_the_deck(self, tmp_path: Path) -> None:
        """The deck carries its own id, so the caller has to know it first."""
        repo = await _repo(tmp_path)

        surface = await _register(repo, surface_id="dfs-deterministic-1")

        assert surface["surface_id"] == "dfs-deterministic-1"

    async def test_reusing_an_id_with_new_bytes_supersedes_rather_than_collides(self, tmp_path: Path) -> None:
        """One execution can render twice — a round that paused, then completed."""
        repo = await _repo(tmp_path)
        first = await _register(repo, surface_id="dfs-deterministic-1")

        second = await _register(repo, surface_id="dfs-deterministic-1", deck_content_hash=OTHER_DECK_HASH)

        assert second["surface_id"] != first["surface_id"]
        stale = await repo.get_design_feedback_surface(first["surface_id"], project_id="project-1")
        assert stale is not None
        assert stale["superseded_by_surface_id"] == second["surface_id"]

    async def test_reusing_an_id_with_the_same_bytes_is_still_idempotent(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        first = await _register(repo, surface_id="dfs-deterministic-1")

        again = await _register(repo, surface_id="dfs-deterministic-1")

        assert again["surface_id"] == first["surface_id"]
