"""A stamped Build-step table must still match the ORM after upgrade.

The manual DBTL profile exposed a real forward-drift shape: its Alembic ledger
said ``0027`` while ``dbtl_stage_step_runs`` still used nullable ``phase_key``
for uniqueness and had no ``phase_slot`` column.  Because Alembic never revisits
an applied ``0026``, every Build and every rail workflow read then failed with
``no such column: dbtl_stage_step_runs.phase_slot``.

This test deliberately recreates that exact physical schema, including the two
NULL-sensitive uniqueness definitions, before asking Alembic to upgrade head.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config
from deerflow.persistence.dbtl.model import DbtlStageStepRunRow

_LEGACY_STEP_TABLE = """
CREATE TABLE dbtl_stage_step_runs (
    id VARCHAR(96) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    cycle_id VARCHAR(64) NOT NULL,
    stage_attempt_id VARCHAR(96) NOT NULL,
    workflow_spec_key VARCHAR(96) NOT NULL,
    step_key VARCHAR(48) NOT NULL,
    attempt INTEGER NOT NULL,
    status VARCHAR(24) NOT NULL,
    phase_index INTEGER,
    phase_key VARCHAR(96),
    plan_digest VARCHAR(64),
    capability VARCHAR(64),
    agent_name VARCHAR(96),
    via_generalist BOOLEAN DEFAULT 0 NOT NULL,
    input_digest VARCHAR(64) NOT NULL,
    output_digest VARCHAR(64),
    predecessor_step_run_ids JSON NOT NULL,
    parent_run_id VARCHAR(64),
    task_id VARCHAR(160),
    error_code VARCHAR(48),
    error_summary TEXT DEFAULT '' NOT NULL,
    execution JSON NOT NULL,
    human_input_request_id VARCHAR(96),
    meeting_id VARCHAR(96),
    supersedes_step_run_id VARCHAR(96),
    started_at DATETIME NOT NULL,
    completed_at DATETIME,
    PRIMARY KEY (id),
    CONSTRAINT uq_dbtl_stage_step_attempt
        UNIQUE (stage_attempt_id, step_key, phase_key, attempt)
)
"""


async def _seed_stamped_legacy_table(engine) -> None:
    async with engine.begin() as connection:
        await connection.exec_driver_sql(_LEGACY_STEP_TABLE)
        await connection.exec_driver_sql(
            """
            CREATE UNIQUE INDEX uq_dbtl_stage_step_running
            ON dbtl_stage_step_runs (stage_attempt_id, step_key, phase_key)
            WHERE status = 'running'
            """
        )
        # NULL phase keys bypass both legacy uniqueness definitions. Keep two
        # rows to prove the repair can enforce the intended invariant without
        # deleting either attempt.
        for row_id, started_at in (("step-old", "2026-08-01 10:00:00"), ("step-new", "2026-08-01 10:01:00")):
            await connection.execute(
                sa.text(
                    """
                    INSERT INTO dbtl_stage_step_runs (
                        id, project_id, cycle_id, stage_attempt_id,
                        workflow_spec_key, step_key, attempt, status,
                        phase_key, via_generalist, input_digest,
                        predecessor_step_run_ids, error_summary, execution,
                        started_at
                    ) VALUES (
                        :id, 'project-1', 'cycle-1', 'build-1',
                        'generic:build-workflow:v1', 'load_design', 1, 'running',
                        NULL, 0, :input_digest, '[]', '', '{}', :started_at
                    )
                    """
                ),
                {"id": row_id, "input_digest": row_id.ljust(64, "0"), "started_at": started_at},
            )


@pytest.mark.asyncio
async def test_upgrade_repairs_a_stamped_step_table_missing_phase_slot(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-step-table.db'}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(alembic_command.upgrade, config, "0025_dbtl_stage_feedback_surfaces")
        await _seed_stamped_legacy_table(engine)
        await asyncio.to_thread(alembic_command.stamp, config, "0026_dbtl_stage_step_runs")
        await asyncio.to_thread(alembic_command.upgrade, config, "0027_dbtl_build_collaborations")

        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        async with engine.connect() as connection:
            columns, indexes, constraints, version = await connection.run_sync(
                lambda sync: (
                    sa.inspect(sync).get_columns("dbtl_stage_step_runs"),
                    sa.inspect(sync).get_indexes("dbtl_stage_step_runs"),
                    sa.inspect(sync).get_unique_constraints("dbtl_stage_step_runs"),
                    sync.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one(),
                )
            )
        assert version == "0033_dbtl_evidence_exception_assessment"
        assert {column["name"] for column in columns} >= {"phase_key", "phase_slot"}
        assert next(item for item in constraints if item["name"] == "uq_dbtl_stage_step_attempt")["column_names"] == [
            "stage_attempt_id",
            "step_key",
            "phase_slot",
            "attempt",
        ]
        assert next(item for item in indexes if item["name"] == "uq_dbtl_stage_step_running")["column_names"] == [
            "stage_attempt_id",
            "step_key",
            "phase_slot",
        ]

        # The exact ORM read that failed in the live rail must now compile and
        # return both legacy attempts. The repair may renumber/cancel a
        # duplicate, but it may not erase audit history.
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            rows = list((await session.scalars(sa.select(DbtlStageStepRunRow).order_by(DbtlStageStepRunRow.started_at))).all())
        assert [row.id for row in rows] == ["step-old", "step-new"]
        assert {row.phase_slot for row in rows} == {""}
        assert len({row.attempt for row in rows}) == 2
        assert [row.status for row in rows].count("running") == 1
    finally:
        await engine.dispose()
