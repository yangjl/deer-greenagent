"""The typed Build rerun record upgrades and downgrades without data loss."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


@pytest.mark.asyncio
async def test_build_rerun_spec_column_round_trip_and_backfill(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'build-rerun.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, "0029_dbtl_cycle_originating_thread")

        async with engine.connect() as connection:
            before = await connection.run_sync(lambda sync: {column["name"] for column in sa.inspect(sync).get_columns("dbtl_build_lineage")})
        assert "rerun_spec" not in before

        await asyncio.to_thread(alembic_command.upgrade, config, "head")
        async with engine.connect() as connection:
            columns = await connection.run_sync(lambda sync: {column["name"]: column for column in sa.inspect(sync).get_columns("dbtl_build_lineage")})
        assert "rerun_spec" in columns
        assert "{}" in str(columns["rerun_spec"]["default"])

        await asyncio.to_thread(alembic_command.downgrade, config, "0029_dbtl_cycle_originating_thread")
        async with engine.connect() as connection:
            after = await connection.run_sync(lambda sync: {column["name"] for column in sa.inspect(sync).get_columns("dbtl_build_lineage")})
        assert "rerun_spec" not in after
    finally:
        await engine.dispose()
