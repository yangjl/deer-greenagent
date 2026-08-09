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
from sqlalchemy.dialects import postgresql

from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.dbtl import (
    DbtlCycleRepository,
    DbtlWorkflowRefused,
    DesignFeedbackConflict,
)
from deerflow.persistence.dbtl.design_feedback_ops import _locked_cycle_for_feedback_surface
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

pytestmark = pytest.mark.asyncio

DECK_HASH = "a" * 64
OTHER_DECK_HASH = "b" * 64
EVIDENCE_HASH = "c" * 64
DECK_URI = "/mnt/user-data/outputs/dbtl/cycle/design/design-slides-rev1-aaaaaa.html"


async def test_surface_registration_locks_the_cycle_before_allocating_a_revision() -> None:
    statement = _locked_cycle_for_feedback_surface("cycle-1", "project-1")
    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE" in compiled


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
    return await repo.register_stage_feedback_surface(
        stage="design",
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

        found = await repo.get_stage_feedback_surface(surface["surface_id"], project_id="project-1")

        assert found is not None
        assert found["surface_id"] == surface["surface_id"]

    async def test_another_project_cannot_read_it(self, tmp_path: Path) -> None:
        """The descriptor is the addressing rule; a wrong project is not a hint."""
        repo = await _repo(tmp_path, projects=("project-1", "project-2"))
        surface = await _register(repo)

        found = await repo.get_stage_feedback_surface(surface["surface_id"], project_id="project-2")

        assert found is None

    async def test_an_unknown_surface_id_is_simply_absent(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)

        assert await repo.get_stage_feedback_surface("forged-surface-id", project_id="project-1") is None

    async def test_the_latest_surface_for_an_attempt_is_addressable(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        await _register(repo)
        second = await _register(repo, deck_content_hash=OTHER_DECK_HASH, design_round=2)

        latest = await repo.latest_stage_feedback_surface(project_id="project-1", cycle_id="cycle-1", stage="design")

        assert latest is not None
        assert latest["surface_id"] == second["surface_id"]


class TestRegenerationSupersedesRatherThanMutates:
    async def test_a_new_deck_supersedes_the_previous_surface(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        first = await _register(repo)

        second = await _register(repo, deck_content_hash=OTHER_DECK_HASH, design_round=2)

        stale = await repo.get_stage_feedback_surface(first["surface_id"], project_id="project-1")
        assert stale is not None
        assert stale["superseded_by_surface_id"] == second["surface_id"]
        assert stale["is_current"] is False
        assert second["is_current"] is True

    async def test_a_superseded_surface_stays_readable(self, tmp_path: Path) -> None:
        """It is the audit record of what somebody was shown."""
        repo = await _repo(tmp_path)
        first = await _register(repo)
        await _register(repo, deck_content_hash=OTHER_DECK_HASH, design_round=2)

        stale = await repo.get_stage_feedback_surface(first["surface_id"], project_id="project-1")

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

    async def test_a_later_deck_that_grants_nothing_does_not_hide_the_reviewable_one(self, tmp_path: Path) -> None:
        """Supersession records what was *shown*; it must not revoke authority.

        A round that produces no package renders a ``read_only`` deck. If that
        deck were read as replacing the ``stage_review`` deck bound to the
        package still awaiting a verdict, the Design would become undecidable —
        no surface anywhere could record the decision. Callers asking "which
        deck may still act?" therefore ask for the newest *stage_review*
        surface, not the newest surface.
        """
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
        reviewable = await _register(
            repo,
            mode="stage_review",
            evidence_artifact_id=artifact["id"],
            evidence_artifact_revision=artifact["revision"],
            evidence_content_hash=EVIDENCE_HASH,
        )

        await _register(repo, mode="read_only", deck_content_hash=OTHER_DECK_HASH, design_round=2)

        newest_review = await repo.latest_stage_feedback_surface(
            project_id="project-1",
            cycle_id="cycle-1",
            stage="design",
            stage_attempt_id=reviewable["stage_attempt_id"],
            mode="stage_review",
        )
        assert newest_review is not None
        assert newest_review["surface_id"] == reviewable["surface_id"]

        # The record itself is untouched: it still says a later deck came after.
        stale = await repo.get_stage_feedback_surface(reviewable["surface_id"], project_id="project-1")
        assert stale is not None
        assert stale["is_current"] is False

    async def test_a_newer_review_deck_does_replace_the_older_one(self, tmp_path: Path) -> None:
        """The relaxation is scoped: two review decks still order normally."""
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
        evidence = {
            "evidence_artifact_id": artifact["id"],
            "evidence_artifact_revision": artifact["revision"],
            "evidence_content_hash": EVIDENCE_HASH,
        }
        first = await _register(repo, mode="stage_review", **evidence)

        second = await _register(repo, mode="stage_review", deck_content_hash=OTHER_DECK_HASH, design_round=2, **evidence)

        newest_review = await repo.latest_stage_feedback_surface(
            project_id="project-1",
            cycle_id="cycle-1",
            stage="design",
            stage_attempt_id=first["stage_attempt_id"],
            mode="stage_review",
        )
        assert newest_review is not None
        assert newest_review["surface_id"] == second["surface_id"]


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
            await repo.register_stage_feedback_surface(
                stage="design",
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
            await repo.register_stage_feedback_surface(
                stage="design",
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

    async def test_a_claimed_evidence_hash_must_match_the_artifact_row(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        await repo.attach_artifact(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="design",
            artifact_type="design_brief.v2",
            uri="/mnt/user-data/outputs/design.md",
            content_hash=EVIDENCE_HASH,
            created_by="user-1",
            expected_db_revision=int(cycle["db_revision"]),
            idempotency_key="artifact-exact",
        )
        detail = await repo.get_cycle("cycle-1", project_id="project-1")
        assert detail is not None
        artifact = detail["artifacts"][-1]

        with pytest.raises(DbtlWorkflowRefused, match="does not match"):
            await _register(
                repo,
                mode="stage_review",
                evidence_artifact_id=artifact["id"],
                evidence_artifact_revision=artifact["revision"],
                evidence_content_hash="d" * 64,
            )

    async def test_a_newer_design_artifact_makes_the_old_deck_stale(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        await repo.attach_artifact(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="design",
            artifact_type="design_brief.v2",
            uri="/mnt/user-data/outputs/design-v1.md",
            content_hash=EVIDENCE_HASH,
            created_by="user-1",
            expected_db_revision=int(cycle["db_revision"]),
            idempotency_key="artifact-stale-1",
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
        await repo.attach_artifact(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="design",
            artifact_type="design_brief.v2",
            uri="/mnt/user-data/outputs/design-v2.md",
            content_hash="e" * 64,
            created_by="user-1",
            expected_db_revision=int(detail["db_revision"]),
            idempotency_key="artifact-stale-2",
        )
        newest = await repo.get_cycle("cycle-1", project_id="project-1")
        assert newest is not None

        with pytest.raises(DesignFeedbackConflict, match="evidence changed"):
            await repo.reserve_stage_feedback_action(
                project_id="project-1",
                cycle_id="cycle-1",
                surface_id=surface["surface_id"],
                originating_thread_id="thread-1",
                action_kind="submit_for_review",
                selected_card_ids=[],
                human_comment="",
                client_submission_id="submission-stale-evidence",
                expected_db_revision=int(newest["db_revision"]),
                expected_evidence={
                    "artifact_id": artifact["id"],
                    "revision": artifact["revision"],
                    "content_hash": EVIDENCE_HASH,
                },
                expected_deck_hash=DECK_HASH,
            )


class TestPayloadBoundActions:
    async def test_slide_comments_are_bound_to_registered_surface_slides(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        surface = await _register(
            repo,
            human_input_request_id="dbtl-design__slide-comments",
            decision_request={
                "question": "Which split?",
                "options": [{"id": "family", "label": "Family", "value": "Use families."}],
                "commentable_slides": [
                    {"id": "objectives", "title": "Objectives"},
                    {"id": "limitations", "title": "Limitations"},
                ],
            },
        )

        _surface, action, replayed = await repo.reserve_stage_feedback_action(
            project_id="project-1",
            cycle_id="cycle-1",
            surface_id=surface["surface_id"],
            originating_thread_id="thread-1",
            action_kind="chair_option",
            selected_card_ids=["family"],
            human_comment="",
            slide_comments={"objectives": "Tighten the threshold.", "limitations": "Name the small holdout."},
            active_slide_id="limitations",
            client_submission_id="submission-slide-comments",
            expected_db_revision=int(cycle["db_revision"]),
            expected_evidence=None,
            expected_deck_hash=DECK_HASH,
        )

        assert replayed is False
        assert action["slide_comments"] == {
            "objectives": "Tighten the threshold.",
            "limitations": "Name the small holdout.",
        }
        assert action["active_slide_id"] == "limitations"


class TestRecentProjectStageFeedback:
    async def test_feedback_is_project_scoped_newest_first_bounded_and_uses_registered_titles(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path, projects=("project-1", "project-2"))

        async def record(
            *,
            project_id: str,
            cycle_id: str,
            submission_id: str,
            deck_hash: str,
            slide_id: str,
            title: str,
            comment: str,
        ) -> None:
            surface = await _register(
                repo,
                project_id=project_id,
                cycle_id=cycle_id,
                deck_content_hash=deck_hash,
                human_input_request_id=f"request-{submission_id}",
                decision_request={
                    "question": "What should change?",
                    "options": [],
                    "commentable_slides": [{"id": slide_id, "title": title}],
                },
            )
            cycle = await repo.get_cycle(cycle_id, project_id=project_id)
            assert cycle is not None
            await repo.reserve_stage_feedback_action(
                project_id=project_id,
                cycle_id=cycle_id,
                surface_id=surface["surface_id"],
                originating_thread_id="thread-1",
                action_kind="chair_text",
                selected_card_ids=[],
                human_comment=f"General {comment}",
                slide_comments={slide_id: comment},
                active_slide_id=slide_id,
                client_submission_id=submission_id,
                expected_db_revision=int(cycle["db_revision"]),
                expected_evidence=None,
                expected_deck_hash=deck_hash,
            )

        await record(
            project_id="project-1",
            cycle_id="cycle-1",
            submission_id="feedback-1",
            deck_hash="1" * 64,
            slide_id="objectives",
            title="Objectives",
            comment="Make the threshold explicit.",
        )
        await record(
            project_id="project-1",
            cycle_id="cycle-1",
            submission_id="feedback-2",
            deck_hash="2" * 64,
            slide_id="limitations",
            title="Limitations",
            comment="Lead with the holdout caveat.",
        )
        decision_surface = await _register(
            repo,
            project_id="project-1",
            cycle_id="cycle-1",
            deck_content_hash="4" * 64,
            human_input_request_id="request-commentless-decision",
            decision_request={
                "question": "Continue?",
                "options": [{"id": "continue", "label": "Continue", "value": "Continue."}],
            },
        )
        decision_cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert decision_cycle is not None
        await repo.reserve_stage_feedback_action(
            project_id="project-1",
            cycle_id="cycle-1",
            surface_id=decision_surface["surface_id"],
            originating_thread_id="thread-1",
            action_kind="chair_option",
            selected_card_ids=["continue"],
            human_comment="",
            client_submission_id="feedback-commentless-decision",
            expected_db_revision=int(decision_cycle["db_revision"]),
            expected_evidence=None,
            expected_deck_hash="4" * 64,
        )
        await record(
            project_id="project-2",
            cycle_id="cycle-2",
            submission_id="feedback-other-project",
            deck_hash="3" * 64,
            slide_id="private",
            title="Private",
            comment="Must never leak.",
        )

        feedback = await repo.recent_project_stage_feedback(project_id="project-1", limit=1)

        assert [item["client_submission_id"] for item in feedback] == ["feedback-2"]
        assert feedback[0]["slide_comments"] == [
            {
                "slide_id": "limitations",
                "slide_title": "Limitations",
                "comment": "Lead with the holdout caveat.",
            }
        ]
        assert "Private" not in str(feedback)

    async def test_an_unknown_slide_comment_is_refused_without_consuming_the_surface(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        surface = await _register(
            repo,
            human_input_request_id="dbtl-design__unknown-slide",
            decision_request={
                "question": "Which split?",
                "options": [{"id": "family", "label": "Family", "value": "Use families."}],
                "commentable_slides": [{"id": "objectives", "title": "Objectives"}],
            },
        )

        with pytest.raises(DbtlWorkflowRefused, match="registered slide"):
            await repo.reserve_stage_feedback_action(
                project_id="project-1",
                cycle_id="cycle-1",
                surface_id=surface["surface_id"],
                originating_thread_id="thread-1",
                action_kind="chair_option",
                selected_card_ids=["family"],
                human_comment="",
                slide_comments={"invented": "Attach this nowhere."},
                active_slide_id="invented",
                client_submission_id="submission-unknown-slide",
                expected_db_revision=int(cycle["db_revision"]),
                expected_evidence=None,
                expected_deck_hash=DECK_HASH,
            )

        assert await repo.stage_feedback_actions(surface["surface_id"], project_id="project-1") == []

    async def test_a_legacy_surface_can_submit_after_its_empty_slide_draft_is_cleared(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        surface = await _register(
            repo,
            human_input_request_id="dbtl-build__legacy-slide-registry",
            decision_request={"question": "Review this Build?", "options": []},
        )

        _surface, action, replayed = await repo.reserve_stage_feedback_action(
            project_id="project-1",
            cycle_id="cycle-1",
            surface_id=surface["surface_id"],
            originating_thread_id="thread-1",
            action_kind="chair_text",
            selected_card_ids=[],
            human_comment="Keep the cleared note as a general review comment.",
            slide_comments={"build-limitations": ""},
            active_slide_id="build-limitations",
            client_submission_id="submission-legacy-cleared-slide",
            expected_db_revision=int(cycle["db_revision"]),
            expected_evidence=None,
            expected_deck_hash=DECK_HASH,
        )

        assert replayed is False
        assert action["slide_comments"] == {}
        assert action["active_slide_id"] is None

    async def test_a_rollback_card_answer_consumes_the_same_surface(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        surface = await _register(
            repo,
            human_input_request_id="dbtl-design__request-rollback",
            decision_request={
                "question": "Which split?",
                "options": [
                    {
                        "id": "family",
                        "label": "Family",
                        "value": "Use families.",
                    }
                ],
            },
        )

        await repo.mark_bound_design_feedback_answer(
            project_id="project-1",
            human_input_request_id="dbtl-design__request-rollback",
            answer="Use family holdout.",
        )

        actions = await repo.stage_feedback_actions(surface["surface_id"], project_id="project-1")
        assert len(actions) == 1
        assert actions[0]["action_group"] == "chair_response"
        assert actions[0]["status"] == "resume_started"

    async def test_a_chair_option_is_single_use_and_payload_bound(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        surface = await _register(
            repo,
            human_input_request_id="dbtl-design__request-1",
            decision_request={
                "question": "Which split?",
                "options": [{"id": "family", "label": "Family", "value": "Use families."}],
            },
        )
        kwargs = {
            "project_id": "project-1",
            "cycle_id": "cycle-1",
            "surface_id": surface["surface_id"],
            "originating_thread_id": "thread-1",
            "action_kind": "chair_option",
            "selected_card_ids": ["family"],
            "human_comment": "Keep one site external.",
            "client_submission_id": "submission-1",
            "expected_db_revision": int(cycle["db_revision"]),
            "expected_evidence": None,
            "expected_deck_hash": DECK_HASH,
        }

        _surface, first, replayed = await repo.reserve_stage_feedback_action(**kwargs)
        _surface, replay, was_replayed = await repo.reserve_stage_feedback_action(**kwargs)

        assert replayed is False
        assert was_replayed is True
        assert replay["client_submission_id"] == first["client_submission_id"]

        with pytest.raises(DesignFeedbackConflict, match="different payload"):
            await repo.reserve_stage_feedback_action(
                **{
                    **kwargs,
                    "human_comment": "A different answer.",
                    "client_submission_id": "submission-2",
                }
            )

    async def test_a_failed_chair_answer_can_be_edited_under_the_same_action_id(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        surface = await _register(
            repo,
            human_input_request_id="dbtl-design__request-editable-retry",
            decision_request={
                "question": "Which split?",
                "options": [
                    {"id": "family", "label": "Family", "value": "Use families."},
                    {"id": "site", "label": "Site", "value": "Hold out a site."},
                ],
            },
        )
        first_kwargs = {
            "project_id": "project-1",
            "cycle_id": "cycle-1",
            "surface_id": surface["surface_id"],
            "originating_thread_id": "thread-1",
            "action_kind": "chair_option",
            "selected_card_ids": ["family"],
            "human_comment": "Use the first draft.",
            "client_submission_id": "submission-editable-retry",
            "expected_db_revision": int(cycle["db_revision"]),
            "expected_evidence": None,
            "expected_deck_hash": DECK_HASH,
        }
        _surface, first, replayed = await repo.reserve_stage_feedback_action(**first_kwargs)
        assert replayed is False
        await repo.update_stage_feedback_action(
            first["client_submission_id"],
            project_id="project-1",
            status="failed",
            run_id="run-rejected-1",
            receipt={
                "message": "The chair response was rejected.",
                "failure_detail": "Malformed artifact reference.",
            },
            failure_code="resume_no_feedback_surface",
        )

        _surface, retried, reused = await repo.reserve_stage_feedback_action(
            **{
                **first_kwargs,
                "selected_card_ids": ["site"],
                "human_comment": "Use the edited site-aware answer.",
            }
        )

        assert reused is True
        assert retried["client_submission_id"] == first["client_submission_id"]
        assert retried["status"] == "pending"
        assert retried["run_id"] is None
        assert retried["selected_card_ids"] == ["site"]
        assert retried["human_comment"] == "Use the edited site-aware answer."
        assert retried["failure_code"] is None
        previous = retried["receipt"]["failed_attempts"][-1]
        assert previous["selected_card_ids"] == ["family"]
        assert previous["human_comment"] == "Use the first draft."
        assert previous["run_id"] == "run-rejected-1"
        assert previous["receipt"]["failure_detail"] == "Malformed artifact reference."

    async def test_a_failed_stage_review_decision_can_be_edited_under_the_same_action_id(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        await repo.attach_artifact(
            cycle_id="cycle-1",
            project_id="project-1",
            stage="design",
            artifact_type="design_brief.v2",
            uri="/mnt/user-data/outputs/design-review.md",
            content_hash=EVIDENCE_HASH,
            created_by="user-1",
            expected_db_revision=int(cycle["db_revision"]),
            idempotency_key="editable-stage-review-artifact",
        )
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        artifact = cycle["artifacts"][-1]
        surface = await _register(
            repo,
            mode="stage_review",
            evidence_artifact_id=artifact["id"],
            evidence_artifact_revision=artifact["revision"],
            evidence_content_hash=EVIDENCE_HASH,
            decision_request={
                "transition_gate": {
                    "stage": "design",
                    "assessment": {
                        "difficulty": "standard",
                        "rationale": "Review the design package.",
                    },
                    "routes": [],
                }
            },
        )
        common = {
            "project_id": "project-1",
            "cycle_id": "cycle-1",
            "surface_id": surface["surface_id"],
            "originating_thread_id": "thread-1",
            "client_submission_id": "submission-editable-stage-review",
            "expected_db_revision": int(cycle["db_revision"]),
            "expected_evidence": {
                "artifact_id": artifact["id"],
                "revision": artifact["revision"],
                "content_hash": EVIDENCE_HASH,
            },
            "expected_deck_hash": DECK_HASH,
        }
        _surface, first, replayed = await repo.reserve_stage_feedback_action(
            **common,
            action_kind="request_changes",
            selected_card_ids=[],
            human_comment="Revise the first draft.",
        )
        assert replayed is False
        await repo.update_stage_feedback_action(
            first["client_submission_id"],
            project_id="project-1",
            status="failed",
            failure_code="invalid_review_payload",
        )

        _surface, retried, reused = await repo.reserve_stage_feedback_action(
            **common,
            action_kind="approve",
            selected_card_ids=[],
            human_comment="The corrected package is acceptable.",
        )

        assert reused is True
        assert retried["status"] == "pending"
        assert retried["action_kind"] == "approve"
        assert retried["human_comment"] == "The corrected package is acceptable."
        assert retried["receipt"]["failed_attempts"][-1]["action_kind"] == "request_changes"

    async def test_a_chair_option_must_come_from_the_recorded_result(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        surface = await _register(
            repo,
            human_input_request_id="dbtl-design__request-1",
            decision_request={
                "question": "Which split?",
                "options": [{"id": "family", "label": "Family", "value": "Use families."}],
            },
        )

        with pytest.raises(DesignFeedbackConflict, match="not offered"):
            await repo.reserve_stage_feedback_action(
                project_id="project-1",
                cycle_id="cycle-1",
                surface_id=surface["surface_id"],
                originating_thread_id="thread-1",
                action_kind="chair_option",
                selected_card_ids=["forged"],
                human_comment="",
                client_submission_id="submission-forged",
                expected_db_revision=int(cycle["db_revision"]),
                expected_evidence=None,
                expected_deck_hash=DECK_HASH,
            )

    async def test_the_other_chair_option_requires_the_human_comment(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        cycle = await repo.get_cycle("cycle-1", project_id="project-1")
        assert cycle is not None
        surface = await _register(
            repo,
            human_input_request_id="dbtl-design__request-1",
            decision_request={
                "question": "Which split?",
                "options": [{"id": "other", "label": "Other", "value": "Use another split."}],
            },
        )

        with pytest.raises(DesignFeedbackConflict, match="requires a comment"):
            await repo.reserve_stage_feedback_action(
                project_id="project-1",
                cycle_id="cycle-1",
                surface_id=surface["surface_id"],
                originating_thread_id="thread-1",
                action_kind="chair_option",
                selected_card_ids=["other"],
                human_comment="",
                client_submission_id="submission-other",
                expected_db_revision=int(cycle["db_revision"]),
                expected_evidence=None,
                expected_deck_hash=DECK_HASH,
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
        stale = await repo.get_stage_feedback_surface(first["surface_id"], project_id="project-1")
        assert stale is not None
        assert stale["superseded_by_surface_id"] == second["surface_id"]

    async def test_reusing_an_id_with_the_same_bytes_is_still_idempotent(self, tmp_path: Path) -> None:
        repo = await _repo(tmp_path)
        first = await _register(repo, surface_id="dfs-deterministic-1")

        again = await _register(repo, surface_id="dfs-deterministic-1")

        assert again["surface_id"] == first["surface_id"]
