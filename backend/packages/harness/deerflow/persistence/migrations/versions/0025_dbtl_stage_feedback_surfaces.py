"""Generalize registered DBTL feedback surfaces across stages.

The physical table names remain unchanged for the one-release compatibility
window. Additive discriminators preserve every legacy Design surface/action
while allowing Build, Test, and Learn to use the same scope, hash,
supersession, and single-use ledger.

Revision ID: 0025_dbtl_stage_feedback_surfaces
Revises: 0024_dbtl_stage_transitions
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_dbtl_stage_feedback_surfaces"
down_revision: str | Sequence[str] | None = "0024_dbtl_stage_transitions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_add_column

    safe_add_column(
        "dbtl_design_feedback_surfaces",
        sa.Column("stage", sa.String(length=24), nullable=False, server_default="design"),
    )
    safe_add_column(
        "dbtl_design_feedback_surfaces",
        sa.Column("surface_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    safe_add_column(
        "dbtl_design_feedback_actions",
        sa.Column("stage", sa.String(length=24), nullable=False, server_default="design"),
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    surface_indexes = {item["name"] for item in inspector.get_indexes("dbtl_design_feedback_surfaces")}
    action_indexes = {item["name"] for item in inspector.get_indexes("dbtl_design_feedback_actions")}
    with op.batch_alter_table("dbtl_design_feedback_surfaces", schema=None) as batch_op:
        if "ix_dbtl_stage_feedback_cycle_stage_revision" not in surface_indexes:
            batch_op.create_index(
                "ix_dbtl_stage_feedback_cycle_stage_revision",
                ["cycle_id", "stage", "surface_revision"],
                unique=False,
            )
    with op.batch_alter_table("dbtl_design_feedback_actions", schema=None) as batch_op:
        if "ix_dbtl_stage_feedback_action_stage_created" not in action_indexes:
            batch_op.create_index(
                "ix_dbtl_stage_feedback_action_stage_created",
                ["stage", "created_at"],
                unique=False,
            )


def downgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_drop_column

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    surface_indexes = {item["name"] for item in inspector.get_indexes("dbtl_design_feedback_surfaces")}
    action_indexes = {item["name"] for item in inspector.get_indexes("dbtl_design_feedback_actions")}
    with op.batch_alter_table("dbtl_design_feedback_actions", schema=None) as batch_op:
        if "ix_dbtl_stage_feedback_action_stage_created" in action_indexes:
            batch_op.drop_index("ix_dbtl_stage_feedback_action_stage_created")
    with op.batch_alter_table("dbtl_design_feedback_surfaces", schema=None) as batch_op:
        if "ix_dbtl_stage_feedback_cycle_stage_revision" in surface_indexes:
            batch_op.drop_index("ix_dbtl_stage_feedback_cycle_stage_revision")
    safe_drop_column("dbtl_design_feedback_actions", "stage")
    safe_drop_column("dbtl_design_feedback_surfaces", "surface_revision")
    safe_drop_column("dbtl_design_feedback_surfaces", "stage")
