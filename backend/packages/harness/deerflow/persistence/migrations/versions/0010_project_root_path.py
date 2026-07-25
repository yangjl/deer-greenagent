"""Add projects.root_path — the human-visible project folder.

Revision ID: 0010_project_root_path
Revises: 0009_thread_conversation_scope
"""

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision = "0010_project_root_path"
down_revision = "0009_thread_conversation_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    safe_add_column("projects", sa.Column("root_path", sa.Text(), nullable=True))


def downgrade() -> None:
    safe_drop_column("projects", "root_path")
