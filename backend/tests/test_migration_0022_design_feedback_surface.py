"""The Design feedback surface table, on both shapes of database it can meet.

A revision that introduces a table cannot assume the table is absent. A
database whose alembic ledger sits behind its physical schema — stamped after a
full ``create_all``, the drift `0015` exists to repair — already has every ORM
table, and a bare ``op.create_table`` there aborts the upgrade and strands that
database one revision short of head.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

import deerflow.persistence.models  # noqa: F401 -- registers ORM models
from deerflow.persistence.base import Base
from deerflow.persistence.bootstrap import _get_alembic_config

TABLE = "dbtl_design_feedback_surfaces"
PREVIOUS_HEAD = "0021_run_cancel_request"


async def _columns(engine, table: str) -> set[str]:
    async with engine.connect() as connection:
        columns = await connection.run_sync(lambda sync: sa.inspect(sync).get_columns(table))
    return {column["name"] for column in columns}


async def _tables(engine) -> set[str]:
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
    return set(names)


@pytest.mark.asyncio
async def test_upgrade_creates_the_table_on_a_database_at_the_previous_head(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'clean.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, PREVIOUS_HEAD)
        assert TABLE not in await _tables(engine)

        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        columns = await _columns(engine, TABLE)
        assert {
            "id",
            "project_id",
            "cycle_id",
            "stage_attempt_id",
            "design_round",
            "originating_thread_id",
            "mode",
            "deck_uri",
            "deck_content_hash",
            "deck_schema_version",
            "evidence_artifact_id",
            "evidence_content_hash",
            "bound_db_revision",
            "superseded_by_surface_id",
        } <= columns
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upgrade_survives_a_database_whose_schema_ran_ahead_of_its_ledger(tmp_path: Path) -> None:
    """create_all made the table; the ledger has never heard of it."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'drifted.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.stamp, config, PREVIOUS_HEAD)

        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        async with engine.connect() as connection:
            version = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        assert version == "0033_dbtl_evidence_exception_assessment"
        assert TABLE in await _tables(engine)
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

        # Re-applying the pair must not depend on the table's presence.
        await asyncio.to_thread(alembic_command.upgrade, config, "head")
        assert TABLE in await _tables(engine)
    finally:
        await engine.dispose()
