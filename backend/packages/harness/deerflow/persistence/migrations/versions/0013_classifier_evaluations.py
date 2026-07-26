"""Classifier shadow-evaluation telemetry.

Revision ID: 0013_classifier_evaluations
Revises: 0012_dbtl_cycle_hierarchy

A plain additive table. It is deliberately *not* joined to any DBTL domain
table: telemetry about what the classifier believed is an observation, not a
research record, and a foreign key into ``dbtl_cycles`` would make it look
like one.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "0013_classifier_evaluations"
down_revision = "0012_dbtl_cycle_hierarchy"
branch_labels = None
depends_on = None

_TABLE = "dbtl_classifier_evaluations"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE in inspector.get_table_names():
        # Fresh databases receive the table from create_all before this
        # revision runs; re-creating it would abort the upgrade.
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=96), primary_key=True),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("route_kind", sa.String(length=32), nullable=False),
        sa.Column("route_source", sa.String(length=32), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("band", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rule_hits_json", sa.JSON(), nullable=False),
        sa.Column("missing_fields_json", sa.JSON(), nullable=False),
        sa.Column("proposed_objective", sa.String(length=240), nullable=False),
        sa.Column("policy_version", sa.String(length=96), nullable=False),
        sa.Column("human_choice", sa.String(length=32), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_dbtl_classifier_evaluations_project_id", _TABLE, ["project_id"])
    op.create_index("ix_dbtl_classifier_evaluations_thread_id", _TABLE, ["thread_id"])
    op.create_index("ix_dbtl_classifier_evaluations_user_id", _TABLE, ["user_id"])
    op.create_index("ix_dbtl_classifier_evaluations_human_choice", _TABLE, ["human_choice"])
    op.create_index("ix_dbtl_classifier_eval_project_created", _TABLE, ["project_id", "created_at"])
    op.create_index("ix_dbtl_classifier_eval_project_choice", _TABLE, ["project_id", "human_choice"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return
    op.drop_table(_TABLE)
