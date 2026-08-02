from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config

PREVIOUS_HEAD = "0024_dbtl_stage_transitions"


@pytest.mark.asyncio
async def test_upgrade_backfills_legacy_design_surfaces_and_actions(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage-feedback.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, PREVIOUS_HEAD)
        now = datetime.now(UTC).isoformat()
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO dbtl_design_feedback_surfaces "
                    "(id, project_id, cycle_id, stage_attempt_id, design_round, originating_thread_id, mode, "
                    "deck_uri, deck_content_hash, deck_schema_version, bound_db_revision, projection_hash, "
                    "policy_version, created_at) VALUES "
                    "('surface-1', 'project-1', 'cycle-1', 'attempt-1', 2, 'thread-1', 'read_only', "
                    "'/deck.html', :hash, '1', 4, 'projection', 'policy', :now)"
                ),
                {"hash": "a" * 64, "now": now},
            )
        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        async with engine.connect() as connection:
            surface = (await connection.execute(sa.text("SELECT stage, surface_revision FROM dbtl_design_feedback_surfaces WHERE id='surface-1'"))).one()
            version = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        assert tuple(surface) == ("design", 1)
        assert version == "0029_dbtl_cycle_originating_thread"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_0025_round_trip_keeps_legacy_tables(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'round-trip.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, "head")
        await asyncio.to_thread(alembic_command.downgrade, config, PREVIOUS_HEAD)
        async with engine.connect() as connection:
            tables, columns = await connection.run_sync(
                lambda sync: (
                    set(sa.inspect(sync).get_table_names()),
                    {item["name"] for item in sa.inspect(sync).get_columns("dbtl_design_feedback_surfaces")},
                )
            )
        assert "dbtl_design_feedback_surfaces" in tables
        assert {"stage", "surface_revision"}.isdisjoint(columns)
    finally:
        await engine.dispose()
