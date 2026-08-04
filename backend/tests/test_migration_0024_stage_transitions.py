"""The stage-transition table, its backfill, and the drifted-schema path.

Backfill runs only when this revision genuinely created the table: a database
whose ledger sat behind its physical schema already has the table (and maybe
rows), and inserting synthetic edges beside real ones would corrupt the path
history. Reconciliation reviews are never backfilled — reconciliation is a
Build-edge precondition, not a stage on the path.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

import deerflow.persistence.models  # noqa: F401 -- registers ORM models
from deerflow.persistence.base import Base
from deerflow.persistence.bootstrap import _get_alembic_config

TABLE = "dbtl_stage_transitions"
PREVIOUS_HEAD = "0023_dbtl_design_feedback_actions"


async def _tables(engine) -> set[str]:
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
    return set(names)


async def _seed_reviewed_cycle(engine) -> None:
    """A project with one cycle whose design and reconciliation were reviewed."""
    now = datetime.now(UTC).isoformat()
    async with engine.begin() as connection:
        await connection.execute(
            sa.text("INSERT INTO workspaces (id, name, slug, status, created_by, created_at, updated_at) VALUES ('ws-1', 'Maize', 'maize', 'active', 'user-1', :now, :now)"),
            {"now": now},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO projects (id, workspace_id, name, slug, crop_profile, status, dbtl_phase, reconciliation_status, created_by, created_at, updated_at) "
                "VALUES ('project-1', 'ws-1', 'Drought', 'drought', 'maize', 'active', 'design', 'in-sync', 'user-1', :now, :now)"
            ),
            {"now": now},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO dbtl_cycles (id, project_id, title, cycle_class, state, policy_version, db_revision, projection_hash, projection_json, created_by, created_at, updated_at) "
                "VALUES ('cycle-1', 'project-1', 'Drought model', 'computational', 'reconciliation', 'policy-v1', 3, 'hash', '{}', 'user-1', :now, :now)"
            ),
            {"now": now},
        )
        for stage, attempt_id in (("design", "attempt-design"), ("reconciliation", "attempt-recon")):
            await connection.execute(
                sa.text(f"INSERT INTO dbtl_stage_runs (id, project_id, cycle_id, stage, attempt_number, status, db_revision, created_at, updated_at) VALUES ('{attempt_id}', 'project-1', 'cycle-1', '{stage}', 1, 'approved', 3, :now, :now)"),
                {"now": now},
            )
        await connection.execute(
            sa.text(
                "INSERT INTO dbtl_artifacts (id, project_id, cycle_id, stage_attempt_id, artifact_type, revision, content_hash, uri, created_by, created_at) "
                "VALUES ('artifact-1', 'project-1', 'cycle-1', 'attempt-design', 'design_package', 1, 'hash-a', '/mnt/x', 'user-1', :now)"
            ),
            {"now": now},
        )
        for n, (attempt_id, created) in enumerate([("attempt-design", "2026-07-01T00:00:00+00:00"), ("attempt-recon", "2026-07-02T00:00:00+00:00")], start=1):
            await connection.execute(
                sa.text(
                    "INSERT INTO dbtl_reviews (id, project_id, cycle_id, stage_attempt_id, artifact_id, artifact_revision, bound_db_revision, bound_stage_revision, bound_projection_hash, "
                    "policy_version, idempotency_key, decision, rationale, reviewer_user_id, reviewer_project_role, authorization_reference, input_source, consumed_at, created_at) "
                    f"VALUES ('rev-{n}', 'project-1', 'cycle-1', '{attempt_id}', 'artifact-1', 1, 1, 1, 'hash', 'policy-v1', 'key-{n}', 'approve', 'ok', 'reviewer-1', 'owner', 'ref', 'design_sheet', :created, :created)"
                ),
                {"created": created},
            )


@pytest.mark.asyncio
async def test_backfill_is_idempotent_and_skips_reconciliation(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'backfill.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, PREVIOUS_HEAD)
        await _seed_reviewed_cycle(engine)

        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        async with engine.connect() as connection:
            rows = (await connection.execute(sa.text(f"SELECT seq, from_stage, chosen_route, to_stage, decided_by, backfilled FROM {TABLE} ORDER BY seq"))).fetchall()
        assert [tuple(row) for row in rows] == [(1, "design", "approve", "build", "reviewer-1", 1)]

        # Re-applying the target revision is a no-op: an existing path is
        # never backfilled beside itself.
        await asyncio.to_thread(alembic_command.upgrade, config, "head")
        async with engine.connect() as connection:
            repeated = (await connection.execute(sa.text(f"SELECT seq, from_stage, chosen_route, to_stage, decided_by, backfilled FROM {TABLE} ORDER BY seq"))).fetchall()
        assert [tuple(row) for row in repeated] == [(1, "design", "approve", "build", "reviewer-1", 1)]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_drifted_schema_is_not_backfilled(tmp_path: Path) -> None:
    """create_all made the table already; synthetic edges must not appear."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'drifted.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.stamp, config, PREVIOUS_HEAD)
        await _seed_reviewed_cycle(engine)

        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        async with engine.connect() as connection:
            version = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            count = await connection.scalar(sa.text(f"SELECT COUNT(*) FROM {TABLE}"))
        assert version == "0030_dbtl_build_rerun_spec"
        assert count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_downgrade_removes_the_table_and_can_be_repeated(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, "head")
        assert TABLE in await _tables(engine)

        await asyncio.to_thread(alembic_command.downgrade, config, PREVIOUS_HEAD)
        assert TABLE not in await _tables(engine)

        await asyncio.to_thread(alembic_command.upgrade, config, "head")
        assert TABLE in await _tables(engine)
    finally:
        await engine.dispose()
