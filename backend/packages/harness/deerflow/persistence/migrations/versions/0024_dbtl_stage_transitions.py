"""durable stage-graph transition records for the progressive DBTL gate.

A cycle becomes a recorded walk over the Design/Build/Test/Learn stage graph:
one append-only row per human-decided boundary, ordered by ``seq``. Existing
cycles are backfilled with one synthetic row per already-passed gate — read
from ``dbtl_reviews`` (design/build/learn verdicts) and
``dbtl_validity_assessments`` (test routes) — marked ``backfilled`` so a
reviewer can tell a reconstructed edge from one somebody clicked.
Reconciliation reviews are deliberately not backfilled: reconciliation is a
Build-edge precondition, not a stage on the path.

Revision ID: 0024_dbtl_stage_transitions
Revises: 0023_dbtl_design_feedback_actions
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0024_dbtl_stage_transitions"
down_revision: str | Sequence[str] | None = "0023_dbtl_design_feedback_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PATH_STAGES = ("design", "build", "learn")

_REVIEW_TARGET = {"approve": {"design": "build", "build": "test", "learn": "completed"}, "request_changes": None, "reject": None}

_TEST_TARGET = {
    "advance_to_learn": "learn",
    "repeat_test": "test",
    "return_to_build": "build",
    "return_to_reconciliation": "build",
    "return_to_design": "design",
    "close_cycle": "abandoned",
}


def upgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_create_table

    # Backfill only when this revision genuinely created the table: a database
    # whose ledger sits behind its physical schema already has the table (and
    # possibly rows), and inserting synthetic edges beside real ones would
    # corrupt the very path history this table exists to keep honest.
    already_present = "dbtl_stage_transitions" in sa.inspect(op.get_bind()).get_table_names()
    safe_create_table("dbtl_stage_transitions", _create)
    if not already_present:
        _backfill()


def _create() -> None:
    op.create_table(
        "dbtl_stage_transitions",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("from_stage", sa.String(length=24), nullable=False),
        sa.Column("from_attempt", sa.Integer(), nullable=True),
        sa.Column("stage_attempt_id", sa.String(length=96), nullable=True),
        sa.Column("chosen_route", sa.String(length=64), nullable=False),
        sa.Column("to_stage", sa.String(length=24), nullable=False),
        sa.Column("assessed_difficulty", sa.String(length=16), nullable=True),
        sa.Column("assessment_rationale", sa.Text(), nullable=True),
        sa.Column("human_override", sa.String(length=16), nullable=True),
        sa.Column("offered_routes", sa.JSON(), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=False),
        sa.Column("decision_surface_id", sa.String(length=96), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("stage_spec_version", sa.String(length=96), nullable=True),
        sa.Column("policy_version", sa.String(length=96), nullable=True),
        sa.Column("backfilled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "seq", name="uq_dbtl_stage_transition_seq"),
    )
    with op.batch_alter_table("dbtl_stage_transitions", schema=None) as batch_op:
        batch_op.create_index("ix_dbtl_stage_transitions_cycle_seq", ["cycle_id", "seq"], unique=False)
        batch_op.create_index(batch_op.f("ix_dbtl_stage_transitions_cycle_id"), ["cycle_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_dbtl_stage_transitions_project_id"), ["project_id"], unique=False)


def _backfill() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if not {"dbtl_reviews", "dbtl_stage_runs"} <= existing:
        return

    edges: dict[str, list[tuple[datetime, str, str, str, str, str, int | None, str | None]]] = {}

    rows = bind.execute(
        sa.text("SELECT r.cycle_id, r.project_id, s.stage, r.decision, r.reviewer_user_id, r.created_at, s.attempt_number, r.stage_attempt_id FROM dbtl_reviews r JOIN dbtl_stage_runs s ON s.id = r.stage_attempt_id")
    ).fetchall()
    for cycle_id, project_id, stage, decision, reviewer, created_at, attempt_number, attempt_id in rows:
        if stage not in _PATH_STAGES or decision not in _REVIEW_TARGET:
            continue
        target = _REVIEW_TARGET[decision]
        to_stage = target[stage] if target else stage
        edges.setdefault(cycle_id, []).append((_as_dt(created_at), project_id, stage, decision, to_stage, reviewer, attempt_number, attempt_id))

    if "dbtl_validity_assessments" in existing:
        rows = bind.execute(
            sa.text("SELECT a.cycle_id, a.project_id, a.recommendation, a.reviewer_user_id, a.created_at, a.test_stage_attempt_id, s.attempt_number FROM dbtl_validity_assessments a JOIN dbtl_stage_runs s ON s.id = a.test_stage_attempt_id")
        ).fetchall()
        for cycle_id, project_id, recommendation, reviewer, created_at, attempt_id, attempt_number in rows:
            to_stage = _TEST_TARGET.get(recommendation)
            if to_stage is None:
                continue
            edges.setdefault(cycle_id, []).append((_as_dt(created_at), project_id, "test", recommendation, to_stage, reviewer, attempt_number, attempt_id))

    if not edges:
        return

    table = sa.table(
        "dbtl_stage_transitions",
        sa.column("id", sa.String),
        sa.column("project_id", sa.String),
        sa.column("cycle_id", sa.String),
        sa.column("seq", sa.Integer),
        sa.column("from_stage", sa.String),
        sa.column("from_attempt", sa.Integer),
        sa.column("stage_attempt_id", sa.String),
        sa.column("chosen_route", sa.String),
        sa.column("to_stage", sa.String),
        sa.column("decided_by", sa.String),
        sa.column("backfilled", sa.Boolean),
        sa.column("decided_at", sa.DateTime(timezone=True)),
    )
    inserts = []
    for cycle_id, items in edges.items():
        items.sort(key=lambda item: item[0])
        for seq, (decided_at, project_id, stage, route, to_stage, reviewer, attempt_number, attempt_id) in enumerate(items, start=1):
            inserts.append(
                {
                    "id": f"dst-{cycle_id}-{seq}",
                    "project_id": project_id,
                    "cycle_id": cycle_id,
                    "seq": seq,
                    "from_stage": stage,
                    "from_attempt": attempt_number,
                    "stage_attempt_id": attempt_id,
                    "chosen_route": route,
                    "to_stage": to_stage,
                    "decided_by": reviewer,
                    "backfilled": True,
                    "decided_at": decided_at,
                }
            )
    op.bulk_insert(table, inserts)


def _as_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(UTC)


def downgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_drop_table

    safe_drop_table("dbtl_stage_transitions")
