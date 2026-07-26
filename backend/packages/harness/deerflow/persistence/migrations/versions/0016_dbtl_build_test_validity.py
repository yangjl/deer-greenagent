"""Phase 7 Build lineage and Test validity.

Revision ID: 0016_dbtl_build_test_validity
Revises: 0015_repair_dbtl_schema_drift
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_dbtl_build_test_validity"
down_revision = "0015_repair_dbtl_schema_drift"
branch_labels = None
depends_on = None

_LINEAGE = "dbtl_build_lineage"
_ASSESSMENTS = "dbtl_validity_assessments"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "dbtl_cycles" not in tables:
        return

    if _LINEAGE not in tables:
        op.create_table(
            _LINEAGE,
            sa.Column("id", sa.String(length=96), primary_key=True),
            sa.Column("project_id", sa.String(length=64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("cycle_id", sa.String(length=64), sa.ForeignKey("dbtl_cycles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("stage_attempt_id", sa.String(length=96), sa.ForeignKey("dbtl_stage_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("lineage_revision", sa.Integer(), nullable=False),
            sa.Column("stage_spec_key", sa.String(length=96), nullable=False),
            sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("code_revision", sa.String(length=160), nullable=False),
            sa.Column("config_revision", sa.String(length=160), nullable=False),
            sa.Column("environment", sa.JSON(), nullable=False),
            sa.Column("input_artifacts", sa.JSON(), nullable=False),
            sa.Column("output_artifacts", sa.JSON(), nullable=False),
            sa.Column("deviations", sa.JSON(), nullable=False),
            sa.Column("logs_uri", sa.Text(), nullable=False),
            sa.Column("recorded_by", sa.String(length=64), nullable=False),
            sa.Column("db_revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("stage_attempt_id", "lineage_revision", name="uq_dbtl_build_lineage_revision"),
        )
        op.create_index("ix_dbtl_build_lineage_project_id", _LINEAGE, ["project_id"])
        op.create_index("ix_dbtl_build_lineage_cycle_id", _LINEAGE, ["cycle_id"])
        op.create_index("ix_dbtl_build_lineage_stage_attempt_id", _LINEAGE, ["stage_attempt_id"])
        tables.add(_LINEAGE)

    if _ASSESSMENTS not in tables:
        op.create_table(
            _ASSESSMENTS,
            sa.Column("id", sa.String(length=96), primary_key=True),
            sa.Column("project_id", sa.String(length=64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("cycle_id", sa.String(length=64), sa.ForeignKey("dbtl_cycles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("test_stage_attempt_id", sa.String(length=96), sa.ForeignKey("dbtl_stage_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("build_lineage_id", sa.String(length=96), sa.ForeignKey("dbtl_build_lineage.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("assessment_revision", sa.Integer(), nullable=False),
            sa.Column("validity_pack_key", sa.String(length=96), nullable=False),
            sa.Column("headline_metrics", sa.JSON(), nullable=False),
            sa.Column("checks", sa.JSON(), nullable=False),
            sa.Column("outcome", sa.String(length=32), nullable=False),
            sa.Column("recommendation", sa.String(length=48), nullable=False),
            sa.Column("reason_codes", sa.JSON(), nullable=False),
            sa.Column("limitations", sa.JSON(), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("reviewer_user_id", sa.String(length=64), nullable=False),
            sa.Column("reviewer_project_role", sa.String(length=24), nullable=False),
            sa.Column("db_revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("test_stage_attempt_id", "assessment_revision", name="uq_dbtl_validity_assessment_revision"),
        )
        op.create_index("ix_dbtl_validity_assessments_project_id", _ASSESSMENTS, ["project_id"])
        op.create_index("ix_dbtl_validity_assessments_cycle_id", _ASSESSMENTS, ["cycle_id"])
        op.create_index("ix_dbtl_validity_assessments_test_stage_attempt_id", _ASSESSMENTS, ["test_stage_attempt_id"])
        op.create_index("ix_dbtl_validity_assessments_build_lineage_id", _ASSESSMENTS, ["build_lineage_id"])
        op.create_index("ix_dbtl_validity_assessments_reviewer_user_id", _ASSESSMENTS, ["reviewer_user_id"])


def downgrade() -> None:
    tables = _tables()
    if _ASSESSMENTS in tables:
        op.drop_table(_ASSESSMENTS)
    if _LINEAGE in tables:
        op.drop_table(_LINEAGE)
