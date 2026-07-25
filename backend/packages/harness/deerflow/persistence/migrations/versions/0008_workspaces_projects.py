"""workspace and breeding project foundation.

Revision ID: 0008_workspaces_projects
Revises: 0007_scheduled_run_active_index
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_workspaces_projects"
down_revision: str | Sequence[str] | None = "0007_scheduled_run_active_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A legacy database may have run Base.metadata.create_all() from a newer
    # application build before it received an Alembic stamp. Mirror the
    # existing post-baseline migration policy and tolerate those already
    # materialized tables instead of blocking startup.
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "workspaces" not in existing_tables:
        op.create_table(
            "workspaces",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("slug", sa.String(length=96), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("created_by", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("slug"),
        )
        op.create_index("ix_workspaces_status", "workspaces", ["status"], unique=False)
        op.create_index("ix_workspaces_created_by", "workspaces", ["created_by"], unique=False)

    if "workspace_members" not in existing_tables:
        op.create_table(
            "workspace_members",
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("role", sa.String(length=24), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("workspace_id", "user_id"),
        )
        op.create_index(
            "ix_workspace_members_user_status",
            "workspace_members",
            ["user_id", "status"],
            unique=False,
        )

    if "projects" not in existing_tables:
        op.create_table(
            "projects",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("slug", sa.String(length=96), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("crop_profile", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("dbtl_phase", sa.String(length=24), nullable=False),
            sa.Column("reconciliation_status", sa.String(length=32), nullable=False),
            sa.Column("created_by", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),
        )
        op.create_index("ix_projects_workspace_id", "projects", ["workspace_id"], unique=False)
        op.create_index("ix_projects_status", "projects", ["status"], unique=False)
        op.create_index(
            "ix_projects_workspace_status",
            "projects",
            ["workspace_id", "status"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_projects_workspace_status", table_name="projects")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_index("ix_projects_workspace_id", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_workspace_members_user_status", table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_index("ix_workspaces_created_by", table_name="workspaces")
    op.drop_index("ix_workspaces_status", table_name="workspaces")
    op.drop_table("workspaces")
