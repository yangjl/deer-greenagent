"""Regression coverage for databases stamped ahead of their physical schema."""

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


@pytest.mark.asyncio
async def test_upgrade_repairs_missing_dbtl_columns_without_losing_rows(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'drifted.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(sa.text("DROP INDEX uq_dbtl_cycle_create_idempotency"))
            await connection.execute(sa.text("ALTER TABLE dbtl_cycles DROP COLUMN create_idempotency_key"))
            await connection.execute(sa.text("ALTER TABLE dbtl_classifier_evaluations DROP COLUMN request_fingerprint"))
            await connection.execute(
                sa.text(
                    """
                    INSERT INTO dbtl_classifier_evaluations (
                        id, project_id, thread_id, user_id, route_kind,
                        route_source, band, confidence, rule_hits_json,
                        missing_fields_json, proposed_objective, policy_version,
                        human_choice, decided_at, created_at
                    ) VALUES (
                        'eval-legacy', 'project-1', NULL, 'user-1', 'ordinary',
                        'classifier', 'low', 0.1, '[]', '[]', '', 'v1',
                        NULL, NULL, :created_at
                    )
                    """
                ).bindparams(
                    sa.bindparam(
                        "created_at",
                        type_=sa.DateTime(timezone=True),
                    )
                ),
                {"created_at": datetime.now(UTC)},
            )

        config = _get_alembic_config(engine)
        await asyncio.to_thread(
            alembic_command.stamp,
            config,
            "0014_dbtl_stage_execution",
        )
        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        async with engine.connect() as connection:
            cycle_columns, telemetry_columns, cycle_indexes = await connection.run_sync(
                lambda sync: (
                    sa.inspect(sync).get_columns("dbtl_cycles"),
                    sa.inspect(sync).get_columns("dbtl_classifier_evaluations"),
                    sa.inspect(sync).get_indexes("dbtl_cycles"),
                )
            )
            fingerprint = await connection.scalar(sa.text("SELECT request_fingerprint FROM dbtl_classifier_evaluations WHERE id = 'eval-legacy'"))
            version = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))

        assert "create_idempotency_key" in {item["name"] for item in cycle_columns}
        request_fingerprint = next(item for item in telemetry_columns if item["name"] == "request_fingerprint")
        assert request_fingerprint["nullable"] is False
        assert isinstance(fingerprint, str) and len(fingerprint) == 64
        assert "uq_dbtl_cycle_create_idempotency" in {item["name"] for item in cycle_indexes}
        assert version == "0031_dbtl_conversational_discovery"
    finally:
        await engine.dispose()
