from __future__ import annotations

import anyio
import pytest

from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.dbtl import (
    DbtlGovernanceRepository,
    DbtlProjectionMismatch,
    DbtlReviewReplay,
    DbtlReviewStale,
)
from deerflow.persistence.engine import get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository


async def _repos(tmp_path):
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    return WorkspaceRepository(session_factory), DbtlGovernanceRepository(session_factory)


async def _seed(tmp_path):
    workspaces, dbtl = await _repos(tmp_path)
    await workspaces.create_workspace(
        workspace_id="ws-1",
        name="Maize",
        slug="maize",
        description=None,
        created_by="owner-1",
    )
    await workspaces.create_project(
        project_id="project-g2f",
        workspace_id="ws-1",
        name="G2F",
        slug="g2f",
        description=None,
        crop_profile="maize-v1",
        created_by="owner-1",
    )
    fixture = await dbtl.create_foundation_fixture(
        cycle_id="cycle-1",
        project_id="project-g2f",
        title="Genomic selection",
        created_by="owner-1",
        policy_version="policy-v1",
    )
    return workspaces, dbtl, fixture


def _review_kwargs(fixture: dict, **overrides):
    payload = {
        "review_id": "review-1",
        "project_id": "project-g2f",
        "cycle_id": "cycle-1",
        "stage_attempt_id": fixture["stage_attempt"]["id"],
        "artifact_id": fixture["artifact"]["id"],
        "artifact_revision": 1,
        "expected_db_revision": 1,
        "expected_stage_revision": 1,
        "expected_projection_hash": fixture["cycle"]["projection_hash"],
        "policy_version": "policy-v1",
        "idempotency_key": "review-key-1",
        "decision": "approve",
        "rationale": "Design evidence is sufficient.",
        "reviewer_user_id": "reviewer-1",
        "reviewer_project_role": "owner",
        "authorization_reference": "workspace-membership:ws-1:reviewer-1",
    }
    payload.update(overrides)
    return payload


def test_review_is_identity_revision_artifact_and_policy_bound(tmp_path) -> None:
    async def scenario():
        _, dbtl, fixture = await _seed(tmp_path)

        review = await dbtl.submit_review(**_review_kwargs(fixture))
        cycle = await dbtl.get_cycle("cycle-1", project_id="project-g2f")

        assert review["reviewer_user_id"] == "reviewer-1"
        assert review["reviewer_project_role"] == "owner"
        assert review["artifact_revision"] == 1
        assert review["policy_version"] == "policy-v1"
        assert review["consumed_at"] is not None
        assert cycle is not None
        assert cycle["db_revision"] == 2
        assert cycle["projection_hash"] != fixture["cycle"]["projection_hash"]

    anyio.run(scenario)


def test_review_replay_and_stale_revision_are_rejected(tmp_path) -> None:
    async def scenario():
        _, dbtl, fixture = await _seed(tmp_path)
        await dbtl.submit_review(**_review_kwargs(fixture))

        with pytest.raises(DbtlReviewReplay):
            await dbtl.submit_review(**_review_kwargs(fixture, review_id="review-replay"))
        with pytest.raises(DbtlReviewStale):
            await dbtl.submit_review(
                **_review_kwargs(
                    fixture,
                    review_id="review-stale",
                    idempotency_key="review-key-2",
                )
            )

    anyio.run(scenario)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("project_id", "project-other", LookupError),
        ("artifact_revision", 2, DbtlReviewStale),
        ("expected_stage_revision", 2, DbtlReviewStale),
        ("policy_version", "policy-v2", DbtlReviewStale),
        ("expected_projection_hash", "0" * 64, DbtlProjectionMismatch),
    ],
)
def test_review_rejects_wrong_scope_or_binding(tmp_path, field, value, error) -> None:
    async def scenario():
        _, dbtl, fixture = await _seed(tmp_path)
        with pytest.raises(error):
            await dbtl.submit_review(**_review_kwargs(fixture, **{field: value}))

    anyio.run(scenario)


def test_concurrent_reviews_have_exactly_one_winner(tmp_path) -> None:
    async def scenario():
        _, dbtl, fixture = await _seed(tmp_path)
        outcomes: list[str] = []

        async def submit(suffix: str):
            try:
                await dbtl.submit_review(
                    **_review_kwargs(
                        fixture,
                        review_id=f"review-{suffix}",
                        idempotency_key=f"key-{suffix}",
                    )
                )
                outcomes.append("accepted")
            except DbtlReviewStale:
                outcomes.append("stale")

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(submit, "a")
            task_group.start_soon(submit, "b")

        assert sorted(outcomes) == ["accepted", "stale"]

    anyio.run(scenario)


def test_projection_mismatch_is_detected_without_repair(tmp_path) -> None:
    async def scenario():
        _, dbtl, fixture = await _seed(tmp_path)
        await dbtl.force_projection_hash_for_test("cycle-1", "f" * 64)

        mismatches = await dbtl.list_projection_mismatches()

        assert mismatches == [
            {
                "cycle_id": "cycle-1",
                "project_id": "project-g2f",
                "stored_hash": "f" * 64,
                "computed_hash": fixture["cycle"]["projection_hash"],
            }
        ]

    anyio.run(scenario)


def test_governance_schema_includes_workflow_and_knowledge_foundation(tmp_path) -> None:
    async def scenario():
        _, dbtl = await _repos(tmp_path)

        snapshot = await dbtl.schema_snapshot()

        assert snapshot["tables_missing"] == []
        assert {
            "dbtl_cycles",
            "dbtl_stage_runs",
            "dbtl_transition_intents",
            "dbtl_transitions",
            "dbtl_gate_evaluations",
            "dbtl_reviews",
            "work_items",
            "dbtl_artifacts",
            "memory_candidates",
            "knowledge_claims",
            "knowledge_promotions",
            "knowledge_links",
            "activity_events",
        } <= set(snapshot["tables_present"])

    anyio.run(scenario)
