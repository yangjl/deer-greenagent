"""The cycle-origin column upgrades and downgrades without schema drift."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


@pytest.mark.asyncio
async def test_cycle_origin_column_round_trip(tmp_path: Path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'cycle-origin.db'}",
    )
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(
            alembic_command.upgrade,
            config,
            "0028_repair_dbtl_step_phase_slot",
        )

        async with engine.connect() as connection:
            before = await connection.run_sync(
                lambda sync: {column["name"] for column in sa.inspect(sync).get_columns("dbtl_cycles")},
            )
        assert "originating_thread_id" not in before

        await asyncio.to_thread(alembic_command.upgrade, config, "head")
        async with engine.connect() as connection:
            after = await connection.run_sync(
                lambda sync: {column["name"] for column in sa.inspect(sync).get_columns("dbtl_cycles")},
            )
        assert "originating_thread_id" in after

        await asyncio.to_thread(
            alembic_command.downgrade,
            config,
            "0028_repair_dbtl_step_phase_slot",
        )
        async with engine.connect() as connection:
            downgraded = await connection.run_sync(
                lambda sync: {column["name"] for column in sa.inspect(sync).get_columns("dbtl_cycles")},
            )
        assert "originating_thread_id" not in downgraded
    finally:
        await engine.dispose()
