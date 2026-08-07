"""The evidence-exception assessment schema upgrades and downgrades cleanly."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


@pytest.mark.asyncio
async def test_evidence_exception_assessment_round_trip(tmp_path: Path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'evidence-exception.db'}",
    )
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(
            alembic_command.upgrade,
            config,
            "0032_dbtl_slide_comments",
        )

        async with engine.connect() as connection:
            before = await connection.run_sync(
                lambda sync: {column["name"]: column for column in sa.inspect(sync).get_columns("dbtl_validity_assessments")},
            )
        assert before["build_lineage_id"]["nullable"] is False

        await asyncio.to_thread(alembic_command.upgrade, config, "head")
        async with engine.connect() as connection:
            upgraded = await connection.run_sync(
                lambda sync: {column["name"]: column for column in sa.inspect(sync).get_columns("dbtl_validity_assessments")},
            )
        assert upgraded["build_lineage_id"]["nullable"] is True
        assert "evidence_exception_artifact_id" in upgraded
        assert "evidence_exception_hash" in upgraded

        await asyncio.to_thread(
            alembic_command.downgrade,
            config,
            "0032_dbtl_slide_comments",
        )
        async with engine.connect() as connection:
            downgraded = await connection.run_sync(
                lambda sync: {column["name"]: column for column in sa.inspect(sync).get_columns("dbtl_validity_assessments")},
            )
        assert downgraded["build_lineage_id"]["nullable"] is False
        assert "evidence_exception_artifact_id" not in downgraded
        assert "evidence_exception_hash" not in downgraded
    finally:
        await engine.dispose()
