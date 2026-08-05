"""Phase 8 Learn and governed-knowledge lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl.knowledge import (
    ClaimGrade,
    KnowledgeLifecycleRefused,
    candidate_eligibility,
    render_claim_markdown,
)
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.dbtl.model import (
    DbtlBuildLineageRow,
    DbtlCycleRow,
    DbtlStageAttemptRow,
    DbtlValidityAssessmentRow,
)
from deerflow.persistence.engine import (
    close_engine,
    get_session_factory,
    init_engine_from_config,
)
from deerflow.persistence.workspaces import WorkspaceRepository


@pytest_asyncio.fixture(autouse=True)
async def _close_test_engine():
    yield
    await close_engine()


def test_candidate_eligibility_is_fail_closed() -> None:
    assert candidate_eligibility("supported").eligible
    assert candidate_eligibility("not_supported").eligible
    assert not candidate_eligibility("inconclusive").eligible
    assert not candidate_eligibility("invalidated").eligible
    assert not candidate_eligibility("unknown").eligible


def test_claim_markdown_names_authority_scope_and_limitations() -> None:
    rendered = render_claim_markdown(
        claim_id="claim-1",
        statement="Model X did not improve target-environment prediction.",
        grade=ClaimGrade.VALID_NEGATIVE,
        evidence=[{"kind": "artifact", "reference": "artifact://test"}],
        limitations=["2024 environments only"],
        reviewer_user_id="reviewer-1",
        status="active",
    )
    assert "Validated project knowledge" in rendered
    assert "claim-1" in rendered
    assert "valid_negative" in rendered
    assert "2024 environments only" in rendered
    assert "AI-generated candidate" not in rendered


async def _repo(tmp_path: Path) -> DbtlCycleRepository:
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    sf = get_session_factory()
    assert sf is not None
    workspaces = WorkspaceRepository(sf)
    workspace = await workspaces.create_workspace(
        workspace_id="ws-1",
        name="Research",
        slug="research",
        description=None,
        created_by="user-1",
    )
    for project_id, slug in (
        ("project-1", "source"),
        ("project-2", "target-a"),
        ("project-3", "target-b"),
    ):
        await workspaces.create_project(
            project_id=project_id,
            workspace_id=workspace["id"],
            name=slug,
            slug=slug,
            description=None,
            crop_profile="generic",
            created_by="user-1",
        )

    repo = DbtlCycleRepository(sf)
    await repo.create_cycle(
        cycle_id="cycle-1",
        project_id="project-1",
        title="Generalization",
        cycle_class="computational",
        research_question="Does the model generalize?",
        objective="Evaluate a held-out environment",
        success_criteria="All validity checks pass",
        created_by="user-1",
        policy_version="greenagent-dbtl-v2-draft",
        idempotency_key="create",
    )

    # Seed the exact durable Phase 7 authority Phase 8 is allowed to read. The
    # lifecycle under test must derive eligibility from this row, not from a
    # caller-provided outcome.
    async with sf() as session:
        cycle = await session.get(DbtlCycleRow, "cycle-1")
        assert cycle is not None
        stages = list((await session.execute(__import__("sqlalchemy").select(DbtlStageAttemptRow).where(DbtlStageAttemptRow.cycle_id == "cycle-1"))).scalars())
        by_stage = {row.stage: row for row in stages}
        cycle.state = "learn"
        by_stage["test"].status = "approved"
        by_stage["learn"].status = "in_progress"
        lineage = DbtlBuildLineageRow(
            id="lineage-1",
            project_id="project-1",
            cycle_id="cycle-1",
            stage_attempt_id=by_stage["build"].id,
            lineage_revision=1,
            stage_spec_key="generic:build:v12",
            dataset_fingerprint="a" * 64,
            code_revision="git:test",
            config_revision="config:test",
            environment={},
            input_artifacts=[],
            output_artifacts=[],
            deviations=[],
            logs_uri="artifact://build-log",
            recorded_by="user-1",
            db_revision=cycle.db_revision,
        )
        session.add(lineage)
        session.add(
            DbtlValidityAssessmentRow(
                id="validity-1",
                project_id="project-1",
                cycle_id="cycle-1",
                test_stage_attempt_id=by_stage["test"].id,
                build_lineage_id=lineage.id,
                assessment_revision=1,
                validity_pack_key="generic-predictive:v1",
                headline_metrics=[],
                checks=[],
                outcome="not_supported",
                recommendation="advance_to_learn",
                reason_codes=[],
                limitations=["One season only"],
                rationale="Valid negative result.",
                reviewer_user_id="reviewer-1",
                reviewer_project_role="owner",
                db_revision=cycle.db_revision,
            )
        )
        await session.commit()
    return repo


@pytest.mark.asyncio
async def test_candidate_promotion_publication_and_retraction_are_distinct(
    tmp_path: Path,
) -> None:
    repo = await _repo(tmp_path)
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None

    learn = await repo.record_learn_synthesis(
        cycle_id="cycle-1",
        project_id="project-1",
        summary="The well-powered negative result is informative.",
        candidates=[
            {
                "statement": "Model X did not improve held-out prediction.",
                "evidence": [{"kind": "artifact", "reference": "artifact://test"}],
                "limitations": ["One season only"],
                "grade": "valid_negative",
            }
        ],
        actor_user_id="agent:learn",
        expected_db_revision=cycle["db_revision"],
        idempotency_key="learn-1",
    )
    candidate = learn["candidates"][0]
    assert candidate["status"] == "proposed"
    assert learn["claims"] == []

    with pytest.raises(KnowledgeLifecycleRefused, match="owner or administrator"):
        await repo.promote_candidate(
            candidate_id=candidate["id"],
            project_id="project-1",
            statement=candidate["statement"],
            grade="valid_negative",
            limitations=candidate["limitations"],
            reviewer_user_id="member-1",
            reviewer_project_role="member",
            rationale="A member must not own the knowledge gate.",
            authorization_reference="manual-promotion:project-1:member-1",
            idempotency_key="member-promote",
            rendered_uri="/mnt/user-data/knowledge/member.md",
            claim_id="member-claim",
        )

    promoted = await repo.promote_candidate(
        candidate_id=candidate["id"],
        project_id="project-1",
        statement=candidate["statement"],
        grade="valid_negative",
        limitations=candidate["limitations"],
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        rationale="The evidence supports a bounded negative claim.",
        authorization_reference="manual-promotion:project-1:reviewer-1",
        idempotency_key="promote-1",
        rendered_uri="/mnt/user-data/knowledge/claim-1.md",
        claim_id="claim-1",
    )
    claim = promoted["claim"]
    assert claim["status"] == "active"
    assert promoted["publications"] == []
    replayed_promotion = await repo.promote_candidate(
        candidate_id=candidate["id"],
        project_id="project-1",
        statement=candidate["statement"],
        grade="valid_negative",
        limitations=candidate["limitations"],
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        rationale="The evidence supports a bounded negative claim.",
        authorization_reference="manual-promotion:project-1:reviewer-1",
        idempotency_key="promote-1",
        rendered_uri="/mnt/user-data/knowledge/different-retry-id.md",
        claim_id="different-retry-id",
    )
    assert replayed_promotion["claim"]["id"] == "claim-1"
    with pytest.raises(KnowledgeLifecycleRefused, match="idempotency key"):
        await repo.promote_candidate(
            candidate_id=candidate["id"],
            project_id="project-1",
            statement="A materially different statement.",
            grade="valid_negative",
            limitations=candidate["limitations"],
            reviewer_user_id="reviewer-1",
            reviewer_project_role="owner",
            rationale="The evidence supports a bounded negative claim.",
            authorization_reference="manual-promotion:project-1:reviewer-1",
            idempotency_key="promote-1",
            rendered_uri="/mnt/user-data/knowledge/another-id.md",
            claim_id="another-id",
        )

    published = await repo.publish_claim(
        claim_id=claim["id"],
        source_project_id="project-1",
        target_project_ids=["project-2", "project-3"],
        publisher_user_id="reviewer-1",
        publisher_project_role="owner",
        rationale="Both projects use the same protocol.",
        authorization_reference="manual-publication:project-1:reviewer-1",
        idempotency_key="publish-1",
    )
    assert {item["target_project_id"] for item in published["publications"]} == {
        "project-2",
        "project-3",
    }
    assert all(item["status"] == "active" for item in published["publications"])
    with pytest.raises(KnowledgeLifecycleRefused, match="idempotency key"):
        await repo.publish_claim(
            claim_id=claim["id"],
            source_project_id="project-1",
            target_project_ids=["project-2"],
            publisher_user_id="reviewer-1",
            publisher_project_role="owner",
            rationale="Both projects use the same protocol.",
            authorization_reference="manual-publication:project-1:reviewer-1",
            idempotency_key="publish-1",
        )

    retracted = await repo.retract_claim(
        claim_id=claim["id"],
        project_id="project-1",
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        rationale="A source-data coding error was discovered.",
        authorization_reference="manual-retraction:project-1:reviewer-1",
        idempotency_key="retract-1",
    )
    assert retracted["claim"]["status"] == "retracted"
    assert all(item["status"] == "retracted" for item in retracted["publications"])
    assert len(retracted["events"]) >= 4


@pytest.mark.asyncio
async def test_invalidated_assessment_cannot_create_candidate(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    sf = get_session_factory()
    assert sf is not None
    async with sf() as session:
        row = await session.get(DbtlValidityAssessmentRow, "validity-1")
        assert row is not None
        row.outcome = "invalidated"
        await session.commit()
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    with pytest.raises(KnowledgeLifecycleRefused, match="invalidated"):
        await repo.record_learn_synthesis(
            cycle_id="cycle-1",
            project_id="project-1",
            summary="This must not become knowledge.",
            candidates=[
                {
                    "statement": "Unsafe claim",
                    "evidence": [{"kind": "artifact", "reference": "artifact://bad"}],
                    "limitations": [],
                    "grade": "supported",
                }
            ],
            actor_user_id="agent:learn",
            expected_db_revision=cycle["db_revision"],
            idempotency_key="learn-invalid",
        )


@pytest.mark.asyncio
async def test_inconclusive_cycle_can_record_learn_closeout_without_candidate(
    tmp_path: Path,
) -> None:
    repo = await _repo(tmp_path)
    sf = get_session_factory()
    assert sf is not None
    async with sf() as session:
        row = await session.get(DbtlValidityAssessmentRow, "validity-1")
        assert row is not None
        row.outcome = "inconclusive"
        await session.commit()
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None

    view = await repo.record_learn_synthesis(
        cycle_id="cycle-1",
        project_id="project-1",
        summary="The cycle closed without a transferable finding.",
        candidates=[],
        actor_user_id="agent:learn",
        expected_db_revision=cycle["db_revision"],
        idempotency_key="learn-inconclusive",
    )

    assert view["candidates"] == []
    assert any(event["event_type"] == "learn.synthesized" for event in view["events"])


@pytest.mark.asyncio
async def test_supersession_preserves_history_and_closes_old_publications(
    tmp_path: Path,
) -> None:
    repo = await _repo(tmp_path)
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    first = await repo.record_learn_synthesis(
        cycle_id="cycle-1",
        project_id="project-1",
        summary="First bounded result.",
        candidates=[
            {
                "statement": "The original bounded claim.",
                "evidence": [{"kind": "artifact", "reference": "artifact://v1"}],
                "limitations": [],
                "grade": "valid_negative",
            }
        ],
        actor_user_id="agent:learn",
        expected_db_revision=cycle["db_revision"],
        idempotency_key="learn-first",
    )
    original = await repo.promote_candidate(
        candidate_id=first["candidates"][0]["id"],
        project_id="project-1",
        statement="The original bounded claim.",
        grade="valid_negative",
        limitations=[],
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        rationale="The original evidence is valid.",
        authorization_reference="manual",
        idempotency_key="promote-original",
        rendered_uri="/mnt/user-data/knowledge/original.md",
        claim_id="claim-original",
    )
    await repo.publish_claim(
        claim_id=original["claim"]["id"],
        source_project_id="project-1",
        target_project_ids=["project-2"],
        publisher_user_id="reviewer-1",
        publisher_project_role="owner",
        rationale="Shared protocol.",
        authorization_reference="manual",
        idempotency_key="publish-original",
    )

    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    second = await repo.record_learn_synthesis(
        cycle_id="cycle-1",
        project_id="project-1",
        summary="Updated bounded result.",
        candidates=[
            {
                "statement": "The corrected bounded claim.",
                "evidence": [{"kind": "artifact", "reference": "artifact://v2"}],
                "limitations": ["Corrected coding"],
                "grade": "valid_negative",
            }
        ],
        actor_user_id="agent:learn",
        expected_db_revision=cycle["db_revision"],
        idempotency_key="learn-second",
    )
    superseded = await repo.promote_candidate(
        candidate_id=second["candidates"][-1]["id"],
        project_id="project-1",
        statement="The corrected bounded claim.",
        grade="valid_negative",
        limitations=["Corrected coding"],
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        rationale="This replaces the earlier bounded statement.",
        authorization_reference="manual",
        idempotency_key="promote-corrected",
        rendered_uri="/mnt/user-data/knowledge/corrected.md",
        claim_id="claim-corrected",
        supersedes_claim_id="claim-original",
    )

    old_claim = next(item for item in superseded["claims"] if item["id"] == "claim-original")
    old_publication = next(item for item in superseded["publications"] if item["claim_id"] == "claim-original")
    assert old_claim["status"] == "superseded"
    assert old_publication["status"] == "superseded"
    assert superseded["claim"]["status"] == "active"
