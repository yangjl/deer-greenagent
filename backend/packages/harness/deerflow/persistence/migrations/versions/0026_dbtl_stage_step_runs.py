"""Append-only Build workflow step attempts.

Adds `dbtl_stage_step_runs`: one immutable row per attempt at one Build workflow
step, so a presentational failure can retry alone instead of discarding the
sandbox work that produced it.

Two constraints carry the semantics rather than leaving them to calling code.
`uq_dbtl_stage_step_attempt` makes an attempt number unique per
(stage attempt, step, phase), so a replayed dispatch collides instead of
recording the same work twice. Both constraints are written against the
non-null `phase_slot` rather than the nullable `phase_key`: SQL compares NULL
unequal to itself, so indexing the nullable column would constrain nothing for
the four steps that have no phase — which is every step but one. `uq_dbtl_stage_step_running` is a partial unique
index enforcing "only one attempt for a step may be `running`" at the database
boundary — a check-then-write in the repository would admit two dispatches that
both passed the check, which is precisely the double-run this workflow exists to
make impossible.

Uses `safe_create_table` for the same reason `0022` does: a database whose
alembic ledger sits behind its physical schema (stamped after a full
`create_all`) already has every ORM table, and a bare `op.create_table` there
aborts the upgrade and strands that database one revision short of head.

Revision ID: 0026_dbtl_stage_step_runs
Revises: 0025_dbtl_stage_feedback_surfaces
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_create_table, safe_drop_table

revision: str = "0026_dbtl_stage_step_runs"
down_revision: str | Sequence[str] | None = "0025_dbtl_stage_feedback_surfaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "dbtl_stage_step_runs"


def upgrade() -> None:
    safe_create_table(_TABLE, _create)


def _create() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cycle_id", sa.String(64), sa.ForeignKey("dbtl_cycles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_attempt_id", sa.String(96), sa.ForeignKey("dbtl_stage_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_spec_key", sa.String(96), nullable=False),
        sa.Column("step_key", sa.String(48), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("phase_index", sa.Integer(), nullable=True),
        sa.Column("phase_key", sa.String(96), nullable=True),
        sa.Column("phase_slot", sa.String(96), nullable=False, server_default=""),
        sa.Column("plan_digest", sa.String(64), nullable=True),
        sa.Column("capability", sa.String(64), nullable=True),
        sa.Column("agent_name", sa.String(96), nullable=True),
        sa.Column("via_generalist", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("output_digest", sa.String(64), nullable=True),
        sa.Column("predecessor_step_run_ids", sa.JSON(), nullable=False),
        sa.Column("parent_run_id", sa.String(64), nullable=True),
        sa.Column("task_id", sa.String(160), nullable=True),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("execution", sa.JSON(), nullable=False),
        sa.Column("human_input_request_id", sa.String(96), nullable=True),
        sa.Column("meeting_id", sa.String(96), nullable=True),
        sa.Column("supersedes_step_run_id", sa.String(96), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stage_attempt_id", "step_key", "phase_slot", "attempt", name="uq_dbtl_stage_step_attempt"),
    )
    op.create_index("ix_dbtl_stage_step_runs_project_id", _TABLE, ["project_id"])
    op.create_index("ix_dbtl_stage_step_runs_cycle_id", _TABLE, ["cycle_id"])
    op.create_index("ix_dbtl_stage_step_runs_stage_attempt_id", _TABLE, ["stage_attempt_id"])
    op.create_index("ix_dbtl_stage_step_runs_lookup", _TABLE, ["stage_attempt_id", "step_key"])
    # The one-running-attempt rule, enforced where two concurrent dispatches
    # cannot both win. A check-then-write in the repository would admit two
    # dispatches that both passed the check — the exact double-run this
    # workflow exists to make impossible. Both SQLite and PostgreSQL support
    # partial indexes.
    op.create_index(
        "uq_dbtl_stage_step_running",
        _TABLE,
        ["stage_attempt_id", "step_key", "phase_slot"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    safe_drop_table(_TABLE)
