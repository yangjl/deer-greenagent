"""Phase 4: the classifier telemetry table lands, and stays out of the domain.

The plan requires evaluation telemetry to be stored *separately from DBTL
domain records*. That is checked here at the schema level — an upgraded
database must carry the table with no foreign key into a research record —
because the separation is only meaningful if the shipped schema has it, not
just the ORM class.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

import deerflow.persistence as _persistence
import deerflow.persistence.models  # noqa: F401  -- registers ORM models
from deerflow.persistence.base import Base
from deerflow.persistence.bootstrap import _get_alembic_config

_TABLE = "dbtl_classifier_evaluations"
_MIGRATION_PATH = Path(_persistence.__file__).parent / "migrations" / "versions" / "0013_classifier_evaluations.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0013", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chains_after_the_cycle_hierarchy() -> None:
    module = _load_migration()
    assert module.revision == "0013_classifier_evaluations"
    assert module.down_revision == "0012_dbtl_cycle_hierarchy"


@pytest.mark.asyncio
async def test_upgrade_creates_the_telemetry_table(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, "0012_dbtl_cycle_hierarchy")
        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        async with engine.connect() as connection:
            tables, columns, foreign_keys = await connection.run_sync(
                lambda sync: (
                    sa.inspect(sync).get_table_names(),
                    sa.inspect(sync).get_columns(_TABLE),
                    sa.inspect(sync).get_foreign_keys(_TABLE),
                )
            )
        assert _TABLE in tables
        names = {column["name"] for column in columns}
        assert {
            "route_kind",
            "route_source",
            "request_fingerprint",
            "band",
            "confidence",
            "human_choice",
            "decided_at",
        } <= names
        # Telemetry stores evidence, not the conversation.
        assert "request_text" not in names
        referred = {fk["referred_table"] for fk in foreign_keys}
        assert referred == {"projects"}, f"telemetry must not join a research record: {referred}"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upgrade_is_idempotent_over_a_fresh_create_all(tmp_path: Path) -> None:
    """Fresh databases get the table from ``create_all``; the revision no-ops."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.stamp, config, "0012_dbtl_cycle_hierarchy")
        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
        assert _TABLE in tables
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_downgrade_removes_only_the_telemetry_table(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'down.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, "head")
        await asyncio.to_thread(alembic_command.downgrade, config, "0012_dbtl_cycle_hierarchy")

        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
        assert _TABLE not in tables
        assert "dbtl_cycles" in tables, "downgrading telemetry must not touch research records"
    finally:
        await engine.dispose()
