"""Phase 6 stage execution: declared datasets, worker runs, and approval binding.

Revision ID: 0014_dbtl_stage_execution
Revises: 0013_classifier_evaluations
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

logger = logging.getLogger(__name__)

revision = "0014_dbtl_stage_execution"
down_revision = "0013_classifier_evaluations"
branch_labels = None
depends_on = None

_DATASETS = "dbtl_datasets"
_WORKER_RUNS = "dbtl_stage_worker_runs"
_STAGE_RUNS = "dbtl_stage_runs"


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _table_names()
    if "dbtl_cycles" not in tables:
        # A fresh database gets the whole shape from create_all before this
        # revision is reached; there is nothing to alter and nothing to add.
        return

    if _DATASETS not in tables:
        op.create_table(
            _DATASETS,
            sa.Column("id", sa.String(length=96), primary_key=True),
            sa.Column("project_id", sa.String(length=64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("cycle_id", sa.String(length=64), sa.ForeignKey("dbtl_cycles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_key", sa.String(length=120), nullable=False),
            sa.Column("uri", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("declared_immutable", sa.Boolean(), nullable=False),
            sa.Column("role", sa.String(length=24), nullable=False),
            sa.Column("recorded_by", sa.String(length=64), nullable=False),
            sa.Column("db_revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("cycle_id", "source_key", name="uq_dbtl_dataset_source"),
        )
        op.create_index("ix_dbtl_datasets_project_id", _DATASETS, ["project_id"])
        op.create_index("ix_dbtl_datasets_cycle_id", _DATASETS, ["cycle_id"])

    if _WORKER_RUNS not in tables:
        op.create_table(
            _WORKER_RUNS,
            sa.Column("id", sa.String(length=96), primary_key=True),
            sa.Column("project_id", sa.String(length=64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("cycle_id", sa.String(length=64), sa.ForeignKey("dbtl_cycles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("stage_attempt_id", sa.String(length=96), sa.ForeignKey("dbtl_stage_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("unit_id", sa.String(length=160), nullable=False),
            sa.Column("stage_spec_key", sa.String(length=96), nullable=False),
            sa.Column("capability", sa.String(length=64), nullable=False),
            sa.Column("agent_name", sa.String(length=96), nullable=False),
            sa.Column("via_generalist", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("stop_reason", sa.String(length=32), nullable=True),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("stage_attempt_id", "unit_id", name="uq_dbtl_stage_worker_unit"),
        )
        op.create_index("ix_dbtl_stage_worker_runs_project_id", _WORKER_RUNS, ["project_id"])
        op.create_index("ix_dbtl_stage_worker_runs_cycle_id", _WORKER_RUNS, ["cycle_id"])
        op.create_index("ix_dbtl_stage_worker_runs_stage_attempt_id", _WORKER_RUNS, ["stage_attempt_id"])

    if _STAGE_RUNS in tables:
        # All three are nullable: Phase 3 attempts predate the stage-spec
        # registry, and an attempt that cannot name its contract simply has no
        # approval binding to check. Backfilling a guess here would manufacture
        # exactly the false reassurance the binding exists to prevent.
        safe_add_column(_STAGE_RUNS, sa.Column("stage_spec_key", sa.String(length=96), nullable=True))
        safe_add_column(_STAGE_RUNS, sa.Column("approved_dataset_fingerprint", sa.String(length=64), nullable=True))
        safe_add_column(_STAGE_RUNS, sa.Column("approved_policy_version", sa.String(length=96), nullable=True))


def downgrade() -> None:
    tables = _table_names()
    if _WORKER_RUNS in tables:
        op.drop_table(_WORKER_RUNS)
    if _DATASETS in tables:
        op.drop_table(_DATASETS)
    if _STAGE_RUNS in tables:
        with op.batch_alter_table(_STAGE_RUNS) as batch:
            batch.drop_column("stage_spec_key")
            batch.drop_column("approved_dataset_fingerprint")
            batch.drop_column("approved_policy_version")
