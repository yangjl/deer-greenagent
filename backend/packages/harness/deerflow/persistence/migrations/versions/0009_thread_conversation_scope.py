"""thread conversation scope and private Inbox backfill.

Revision ID: 0009_thread_conversation_scope
Revises: 0008_workspaces_projects
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_thread_conversation_scope"
down_revision: str | Sequence[str] | None = "0008_workspaces_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_add_column

    safe_add_column(
        "threads_meta",
        sa.Column("workspace_id", sa.String(64), nullable=True),
    )
    safe_add_column(
        "threads_meta",
        sa.Column("project_id", sa.String(64), nullable=True),
    )
    safe_add_column(
        "threads_meta",
        sa.Column(
            "scope_type",
            sa.String(24),
            nullable=False,
            server_default="inbox",
        ),
    )
    safe_add_column(
        "threads_meta",
        sa.Column(
            "visibility",
            sa.String(32),
            nullable=False,
            server_default="private-owner",
        ),
    )

    bind = op.get_bind()
    existing_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("threads_meta")}
    for name, column in (
        ("ix_threads_meta_workspace_id", "workspace_id"),
        ("ix_threads_meta_project_id", "project_id"),
    ):
        if name not in existing_indexes:
            op.create_index(name, "threads_meta", [column], unique=False)


def downgrade() -> None:
    op.drop_index("ix_threads_meta_project_id", table_name="threads_meta")
    op.drop_index("ix_threads_meta_workspace_id", table_name="threads_meta")
    op.drop_column("threads_meta", "visibility")
    op.drop_column("threads_meta", "scope_type")
    op.drop_column("threads_meta", "project_id")
    op.drop_column("threads_meta", "workspace_id")
