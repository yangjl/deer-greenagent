"""Add durable pre-cycle conversational discovery.

Revision ID: 0031_dbtl_conversational_discovery
Revises: 0030_dbtl_build_rerun_spec
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_create_table, safe_drop_table

revision = "0031_dbtl_conversational_discovery"
down_revision = "0030_dbtl_build_rerun_spec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    safe_create_table("dbtl_discoveries", _create_discoveries)
    safe_create_table("dbtl_discovery_outbox", _create_discovery_outbox)


def _create_discoveries() -> None:
    op.create_table(
        "dbtl_discoveries",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("project_id", sa.String(length=64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("trigger", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("policy_version", sa.String(length=96), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("draft_json", sa.JSON(), nullable=False),
        sa.Column("offered_revision", sa.Integer(), nullable=True),
        sa.Column("package_json", sa.JSON(), nullable=True),
        sa.Column("package_hash", sa.String(length=64), nullable=True),
        sa.Column("cycle_id", sa.String(length=64), sa.ForeignKey("dbtl_cycles.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("start_submission_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("start_submission_id", name="uq_dbtl_discovery_start_submission"),
    )
    op.create_index("ix_dbtl_discoveries_project_id", "dbtl_discoveries", ["project_id"])
    op.create_index("ix_dbtl_discoveries_thread_id", "dbtl_discoveries", ["thread_id"])
    op.create_index(
        "uq_dbtl_discovery_active_thread",
        "dbtl_discoveries",
        ["project_id", "thread_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('gathering', 'ready', 'offered')"),
        postgresql_where=sa.text("status IN ('gathering', 'ready', 'offered')"),
    )


def _create_discovery_outbox() -> None:
    op.create_table(
        "dbtl_discovery_outbox",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("discovery_id", sa.String(length=64), sa.ForeignKey("dbtl_discoveries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(length=64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dbtl_discovery_outbox_discovery_id", "dbtl_discovery_outbox", ["discovery_id"])
    op.create_index("ix_dbtl_discovery_outbox_project_id", "dbtl_discovery_outbox", ["project_id"])
    op.create_index("ix_dbtl_discovery_outbox_thread_id", "dbtl_discovery_outbox", ["thread_id"])
    op.create_index("ix_dbtl_discovery_outbox_pending", "dbtl_discovery_outbox", ["status", "created_at"])


def downgrade() -> None:
    safe_drop_table("dbtl_discovery_outbox")
    safe_drop_table("dbtl_discoveries")
