"""Add the typed Build rerun record to durable lineage.

Revision ID: 0030_dbtl_build_rerun_spec
Revises: 0029_dbtl_cycle_originating_thread
"""

from __future__ import annotations

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision = "0030_dbtl_build_rerun_spec"
down_revision = "0029_dbtl_cycle_originating_thread"
branch_labels = None
depends_on = None


def upgrade() -> None:
    safe_add_column(
        "dbtl_build_lineage",
        sa.Column(
            "rerun_spec",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    safe_drop_column("dbtl_build_lineage", "rerun_spec")
