"""Record the conversation that launched a DBTL cycle.

Revision ID: 0029_dbtl_cycle_originating_thread
Revises: 0028_repair_dbtl_step_phase_slot
"""

from __future__ import annotations

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision = "0029_dbtl_cycle_originating_thread"
down_revision = "0028_repair_dbtl_step_phase_slot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    safe_add_column(
        "dbtl_cycles",
        sa.Column("originating_thread_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    safe_drop_column("dbtl_cycles", "originating_thread_id")
