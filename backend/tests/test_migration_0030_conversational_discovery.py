"""The discovery table and active-thread index migrate without drift."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


@pytest.mark.asyncio
async def test_discovery_schema_round_trip(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'discovery.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, "0029_dbtl_cycle_originating_thread")
        async with engine.connect() as connection:
            assert "dbtl_discoveries" not in await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())

        await asyncio.to_thread(alembic_command.upgrade, config, "head")
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names()))
            columns = await connection.run_sync(lambda sync: {item["name"] for item in sa.inspect(sync).get_columns("dbtl_discoveries")})
            indexes = await connection.run_sync(lambda sync: {item["name"] for item in sa.inspect(sync).get_indexes("dbtl_discoveries")})
            outbox_columns = await connection.run_sync(lambda sync: {item["name"] for item in sa.inspect(sync).get_columns("dbtl_discovery_outbox")})
        assert {"project_id", "thread_id", "user_id", "status", "revision", "draft_json"} <= columns
        assert "uq_dbtl_discovery_active_thread" in indexes
        assert "dbtl_discovery_outbox" in tables
        assert {"event_type", "payload_json", "status", "delivered_at"} <= outbox_columns

        await asyncio.to_thread(alembic_command.downgrade, config, "0029_dbtl_cycle_originating_thread")
        async with engine.connect() as connection:
            assert "dbtl_discoveries" not in await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
            assert "dbtl_discovery_outbox" not in await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
    finally:
        await engine.dispose()
