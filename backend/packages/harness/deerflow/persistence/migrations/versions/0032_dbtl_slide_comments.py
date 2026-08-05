"""Add slide-attributed comments to stage feedback actions.

Revision ID: 0032_dbtl_slide_comments
Revises: 0031_dbtl_conversational_discovery
"""

from __future__ import annotations

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision = "0032_dbtl_slide_comments"
down_revision = "0031_dbtl_conversational_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    safe_add_column(
        "dbtl_design_feedback_actions",
        sa.Column("slide_comments", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    safe_add_column(
        "dbtl_design_feedback_actions",
        sa.Column("active_slide_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    safe_drop_column("dbtl_design_feedback_actions", "active_slide_id")
    safe_drop_column("dbtl_design_feedback_actions", "slide_comments")
