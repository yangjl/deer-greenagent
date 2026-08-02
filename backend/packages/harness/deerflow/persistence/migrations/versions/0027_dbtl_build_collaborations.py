"""The durable record of a paused Build and the human answer that released it.

`dbtl_stage_step_runs` already records that a step is waiting and the sentence
it is waiting on. It cannot record the exchange — which formats were offered,
which option was chosen, by whom, against which cycle revision, and which
attempt resumed as a result — and without that a resumed Build cannot be
reconstructed. "Who decided to replan" is exactly the question asked when the
phases beneath a plan are gone.

Two constraints carry semantics rather than leaving them to calling code.
`uq_dbtl_build_collab_open` is a partial unique index enforcing "one open
control per stage attempt": a Build pauses in exactly one place, so a second
open request means a stale card competing with a live one for the same answer,
and a check-then-write in the repository would admit two dispatches that both
passed the check. `uq_dbtl_build_collab_submission` makes a response idempotent
by (request, client submission id), so a double-clicked button replays while a
genuinely different answer under the same id conflicts instead of silently
overwriting the decision already recorded.

Uses `safe_create_table` for the same reason `0022` and `0026` do: a database
whose alembic ledger sits behind its physical schema (stamped after a full
`create_all`) already has every ORM table, and a bare `op.create_table` there
aborts the upgrade and strands that database one revision short of head.

Revision ID: 0027_dbtl_build_collaborations
Revises: 0026_dbtl_stage_step_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_create_table, safe_drop_table

revision: str = "0027_dbtl_build_collaborations"
down_revision: str | Sequence[str] | None = "0026_dbtl_stage_step_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "dbtl_build_collaborations"


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
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("step_key", sa.String(48), nullable=False),
        sa.Column("step_run_id", sa.String(96), nullable=True),
        sa.Column("plan_digest", sa.String(64), nullable=True),
        sa.Column("input_digest", sa.String(64), nullable=True),
        sa.Column("bound_cycle_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("request_id", sa.String(96), nullable=False),
        sa.Column("response_format", sa.String(24), nullable=False, server_default="single_choice"),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("recommended_option_id", sa.String(48), nullable=True),
        sa.Column("lifecycle", sa.String(24), nullable=False, server_default="open"),
        sa.Column("action", sa.String(32), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("responder_user_id", sa.String(128), nullable=True),
        sa.Column("client_submission_id", sa.String(96), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("originating_thread_id", sa.String(64), nullable=True),
        sa.Column("parent_run_id", sa.String(64), nullable=True),
        sa.Column("resumed_step_run_id", sa.String(96), nullable=True),
        sa.Column("meeting_id", sa.String(96), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "client_submission_id", name="uq_dbtl_build_collab_submission"),
    )
    op.create_index("ix_dbtl_build_collaborations_project_id", _TABLE, ["project_id"])
    op.create_index("ix_dbtl_build_collaborations_cycle_id", _TABLE, ["cycle_id"])
    op.create_index("ix_dbtl_build_collaborations_stage_attempt_id", _TABLE, ["stage_attempt_id"])
    op.create_index("ix_dbtl_build_collaborations_request_id", _TABLE, ["request_id"])
    op.create_index("ix_dbtl_build_collaborations_lifecycle", _TABLE, ["lifecycle"])
    op.create_index("ix_dbtl_build_collab_attempt", _TABLE, ["stage_attempt_id", "lifecycle"])
    op.create_index(
        "uq_dbtl_build_collab_open",
        _TABLE,
        ["stage_attempt_id"],
        unique=True,
        sqlite_where=sa.text("lifecycle = 'open'"),
        postgresql_where=sa.text("lifecycle = 'open'"),
    )


def downgrade() -> None:
    safe_drop_table(_TABLE)
