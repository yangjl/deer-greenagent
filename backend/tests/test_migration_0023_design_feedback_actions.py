from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


@pytest.mark.asyncio
async def test_upgrade_adds_action_ledger_and_review_provenance(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'actions.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names()))
            action_columns = await connection.run_sync(lambda sync: {item["name"] for item in sa.inspect(sync).get_columns("dbtl_design_feedback_actions")})
            review_columns = await connection.run_sync(lambda sync: {item["name"] for item in sa.inspect(sync).get_columns("dbtl_reviews")})
            version = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))

        assert version == "0027_dbtl_build_collaborations"
        assert "dbtl_design_feedback_actions" in tables
        assert {"surface_id", "payload_hash", "status", "receipt"} <= action_columns
        assert {
            "input_source",
            "feedback_surface_id",
            "deck_content_hash",
            "selected_card_ids",
            "human_comment",
        } <= review_columns
    finally:
        await engine.dispose()
