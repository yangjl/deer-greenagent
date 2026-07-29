"""durable descriptors for rendered DBTL Design feedback decks.

A Design deck is HTML the workflow produced and a browser renders in an
opaque-origin iframe, which by itself makes it indistinguishable from any other
page an agent wrote. This table is how a server can answer "did we produce these
exact bytes, for which cycle, from which evidence, and which conversation may
they answer" before a parent application treats one as a Design surface.

The evidence columns are nullable because a paused meeting has no review package
yet; the write boundary refuses a ``stage_review`` surface that lacks them.

Revision ID: 0022_dbtl_design_feedback_surface
Revises: 0021_run_cancel_request
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_dbtl_design_feedback_surface"
down_revision: str | Sequence[str] | None = "0021_run_cancel_request"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_create_table

    safe_create_table("dbtl_design_feedback_surfaces", _create)


def _create() -> None:
    op.create_table(
        "dbtl_design_feedback_surfaces",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("stage_attempt_id", sa.String(length=96), nullable=False),
        sa.Column("design_round", sa.Integer(), nullable=False),
        sa.Column("originating_thread_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("chair_worker_run_id", sa.String(length=96), nullable=True),
        sa.Column("human_input_request_id", sa.String(length=128), nullable=True),
        sa.Column("deck_uri", sa.Text(), nullable=False),
        sa.Column("deck_content_hash", sa.String(length=64), nullable=False),
        sa.Column("deck_schema_version", sa.String(length=32), nullable=False),
        sa.Column("evidence_artifact_id", sa.String(length=96), nullable=True),
        sa.Column("evidence_artifact_revision", sa.Integer(), nullable=True),
        sa.Column("evidence_content_hash", sa.String(length=64), nullable=True),
        sa.Column("bound_db_revision", sa.Integer(), nullable=False),
        sa.Column("projection_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=96), nullable=False),
        sa.Column("superseded_by_surface_id", sa.String(length=96), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_artifact_id"], ["dbtl_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_attempt_id"], ["dbtl_stage_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stage_attempt_id",
            "mode",
            "deck_content_hash",
            name="uq_dbtl_design_feedback_deck",
        ),
    )
    with op.batch_alter_table("dbtl_design_feedback_surfaces", schema=None) as batch_op:
        batch_op.create_index("ix_dbtl_design_feedback_cycle_created", ["cycle_id", "created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_dbtl_design_feedback_surfaces_cycle_id"), ["cycle_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_dbtl_design_feedback_surfaces_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_dbtl_design_feedback_surfaces_stage_attempt_id"), ["stage_attempt_id"], unique=False)


def downgrade() -> None:
    # The indexes live on the table and go with it, so dropping the table is
    # enough; dropping them first would fail on a database where upgrade()
    # skipped creation and the table was never ours to begin with.
    from deerflow.persistence.migrations._helpers import safe_drop_table

    safe_drop_table("dbtl_design_feedback_surfaces")
