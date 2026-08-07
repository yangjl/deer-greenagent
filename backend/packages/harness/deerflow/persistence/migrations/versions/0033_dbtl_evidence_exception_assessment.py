"""Bind invalidated Test assessments to evidence-exception dossiers.

Revision ID: 0033_dbtl_evidence_exception_assessment
Revises: 0032_dbtl_slide_comments
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision = "0033_dbtl_evidence_exception_assessment"
down_revision = "0032_dbtl_slide_comments"
branch_labels = None
depends_on = None

_TABLE = "dbtl_validity_assessments"


def upgrade() -> None:
    safe_add_column(
        _TABLE,
        sa.Column(
            "evidence_exception_artifact_id",
            sa.String(length=96),
            sa.ForeignKey(
                "dbtl_artifacts.id",
                name="fk_dbtl_validity_assessments_evidence_exception_artifact_id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
    )
    safe_add_column(
        _TABLE,
        sa.Column("evidence_exception_hash", sa.String(length=64), nullable=True),
    )
    inspector = sa.inspect(op.get_bind())
    if _TABLE in inspector.get_table_names():
        columns = {item["name"]: item for item in inspector.get_columns(_TABLE)}
        if columns.get("build_lineage_id", {}).get("nullable") is False:
            with op.batch_alter_table(_TABLE) as batch:
                batch.alter_column(
                    "build_lineage_id",
                    existing_type=sa.String(length=96),
                    nullable=True,
                )
        indexes = {item["name"] for item in inspector.get_indexes(_TABLE)}
        if "ix_dbtl_validity_assessments_evidence_exception_artifact_id" not in indexes:
            op.create_index(
                "ix_dbtl_validity_assessments_evidence_exception_artifact_id",
                _TABLE,
                ["evidence_exception_artifact_id"],
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE in inspector.get_table_names():
        indexes = {item["name"] for item in inspector.get_indexes(_TABLE)}
        if "ix_dbtl_validity_assessments_evidence_exception_artifact_id" in indexes:
            op.drop_index(
                "ix_dbtl_validity_assessments_evidence_exception_artifact_id",
                table_name=_TABLE,
            )
        # The previous schema cannot represent an assessment without Build
        # lineage. Remove only the new exception-only rows, then restore its
        # original NOT NULL contract instead of leaving a silently widened
        # schema behind after downgrade.
        op.execute(sa.text("DELETE FROM dbtl_validity_assessments WHERE build_lineage_id IS NULL"))
        columns = {item["name"]: item for item in inspector.get_columns(_TABLE)}
        if columns.get("build_lineage_id", {}).get("nullable") is True:
            with op.batch_alter_table(_TABLE) as batch:
                batch.alter_column(
                    "build_lineage_id",
                    existing_type=sa.String(length=96),
                    nullable=False,
                )
    safe_drop_column(_TABLE, "evidence_exception_hash")
    safe_drop_column(_TABLE, "evidence_exception_artifact_id")
