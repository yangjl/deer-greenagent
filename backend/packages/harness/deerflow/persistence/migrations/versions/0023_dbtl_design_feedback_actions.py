"""Persist payload-bound Design deck actions and review provenance.

Revision ID: 0023_dbtl_design_feedback_actions
Revises: 0022_dbtl_design_feedback_surface
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_dbtl_design_feedback_actions"
down_revision: str | Sequence[str] | None = "0022_dbtl_design_feedback_surface"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_add_column, safe_create_table

    safe_add_column("dbtl_design_feedback_surfaces", sa.Column("decision_request", sa.JSON(), nullable=True))
    safe_add_column(
        "dbtl_reviews",
        sa.Column("input_source", sa.String(length=32), nullable=False, server_default="design_sheet"),
    )
    for column in (
        sa.Column("feedback_surface_id", sa.String(length=96), nullable=True),
        sa.Column("deck_content_hash", sa.String(length=64), nullable=True),
        sa.Column("deck_schema_version", sa.String(length=32), nullable=True),
        sa.Column("selected_action", sa.String(length=32), nullable=True),
        sa.Column("selected_card_ids", sa.JSON(), nullable=True),
        sa.Column("human_comment", sa.Text(), nullable=True),
        sa.Column("rationale_projection", sa.Text(), nullable=True),
        sa.Column("rationale_source", sa.String(length=32), nullable=True),
    ):
        safe_add_column("dbtl_reviews", column)
    safe_create_table("dbtl_design_feedback_actions", _create_actions)


def _create_actions() -> None:
    op.create_table(
        "dbtl_design_feedback_actions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("surface_id", sa.String(length=96), nullable=False),
        sa.Column("action_group", sa.String(length=32), nullable=False),
        sa.Column("action_kind", sa.String(length=32), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("selected_card_ids", sa.JSON(), nullable=False),
        sa.Column("human_comment", sa.Text(), nullable=True),
        sa.Column("expected_db_revision", sa.Integer(), nullable=False),
        sa.Column("expected_evidence", sa.JSON(), nullable=True),
        sa.Column("expected_deck_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=96), nullable=True),
        sa.Column("receipt", sa.JSON(), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["surface_id"], ["dbtl_design_feedback_surfaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "id", name="uq_dbtl_design_feedback_action_id"),
        sa.UniqueConstraint("surface_id", "action_group", name="uq_dbtl_design_feedback_action_group"),
    )
    with op.batch_alter_table("dbtl_design_feedback_actions", schema=None) as batch_op:
        batch_op.create_index(
            "ix_dbtl_design_feedback_action_cycle_created",
            ["cycle_id", "created_at"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_dbtl_design_feedback_actions_cycle_id"), ["cycle_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_dbtl_design_feedback_actions_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_dbtl_design_feedback_actions_surface_id"), ["surface_id"], unique=False)


def downgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_drop_column, safe_drop_table

    safe_drop_table("dbtl_design_feedback_actions")
    for column_name in (
        "rationale_source",
        "rationale_projection",
        "human_comment",
        "selected_card_ids",
        "selected_action",
        "deck_schema_version",
        "deck_content_hash",
        "feedback_surface_id",
        "input_source",
    ):
        safe_drop_column("dbtl_reviews", column_name)
    safe_drop_column("dbtl_design_feedback_surfaces", "decision_request")
