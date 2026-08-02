"""Repair Build-step tables stamped before ``phase_slot`` was physical.

Revision ID: 0028_repair_dbtl_step_phase_slot
Revises: 0027_dbtl_build_collaborations

Some manual/development databases applied an early physical shape of revision
0026: uniqueness used nullable ``phase_key`` and the table had no non-null
``phase_slot``.  Their Alembic ledger later reached 0027, so a normal upgrade
could not revisit 0026 after the ORM began selecting ``phase_slot``.  Both the
Build recorder and the project-rail workflow read then failed before any work
could start.

This forward repair makes the schema promised by the current 0026 true without
deleting audit rows.  Legacy NULL uniqueness could admit duplicate non-phase
attempt numbers and multiple running rows; duplicates are renumbered in stable
start order and all but the newest running row are cancelled before the real
constraints are installed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

revision = "0028_repair_dbtl_step_phase_slot"
down_revision = "0027_dbtl_build_collaborations"
branch_labels = None
depends_on = None

_TABLE = "dbtl_stage_step_runs"
_ATTEMPT_CONSTRAINT = "uq_dbtl_stage_step_attempt"
_RUNNING_INDEX = "uq_dbtl_stage_step_running"
_ATTEMPT_COLUMNS = ["stage_attempt_id", "step_key", "phase_slot", "attempt"]
_RUNNING_COLUMNS = ["stage_attempt_id", "step_key", "phase_slot"]


def _grouped(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["stage_attempt_id"]), str(row["step_key"]), str(row["phase_slot"] or ""))].append(row)
    return grouped


def _repair_duplicate_attempt_numbers(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text(
            f"""
            SELECT id, stage_attempt_id, step_key, phase_slot, attempt, started_at
            FROM {_TABLE}
            ORDER BY stage_attempt_id, step_key, phase_slot, attempt, started_at, id
            """
        )
    ).mappings()
    for group in _grouped(rows).values():
        used: set[int] = set()
        next_attempt = max((int(row["attempt"]) for row in group), default=0)
        for row in group:
            attempt = int(row["attempt"])
            if attempt not in used:
                used.add(attempt)
                continue
            next_attempt += 1
            used.add(next_attempt)
            bind.execute(
                sa.text(f"UPDATE {_TABLE} SET attempt = :attempt WHERE id = :row_id"),
                {"attempt": next_attempt, "row_id": str(row["id"])},
            )


def _repair_duplicate_running_rows(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text(
            f"""
            SELECT id, stage_attempt_id, step_key, phase_slot, started_at
            FROM {_TABLE}
            WHERE status = 'running'
            ORDER BY stage_attempt_id, step_key, phase_slot, started_at DESC, id DESC
            """
        )
    ).mappings()
    for group in _grouped(rows).values():
        # The newest attempt retains the lease. Older rows were concurrently
        # admitted only because NULL bypassed the old partial unique index.
        for row in group[1:]:
            bind.execute(
                sa.text(
                    f"""
                    UPDATE {_TABLE}
                    SET status = 'cancelled',
                        error_code = COALESCE(error_code, 'cancelled'),
                        error_summary = CASE
                            WHEN error_summary = '' THEN :summary
                            ELSE error_summary
                        END,
                        completed_at = COALESCE(completed_at, started_at)
                    WHERE id = :row_id
                    """
                ),
                {
                    "row_id": str(row["id"]),
                    "summary": "Cancelled while repairing duplicate legacy Build-step leases.",
                },
            )


def _constraint_columns(bind: sa.Connection) -> list[str] | None:
    for item in sa.inspect(bind).get_unique_constraints(_TABLE):
        if item.get("name") == _ATTEMPT_CONSTRAINT:
            return list(item.get("column_names") or ())
    return None


def _index_columns(bind: sa.Connection) -> list[str] | None:
    for item in sa.inspect(bind).get_indexes(_TABLE):
        if item.get("name") == _RUNNING_INDEX:
            return list(item.get("column_names") or ())
    return None


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        return

    safe_add_column(
        _TABLE,
        sa.Column("phase_slot", sa.String(length=96), nullable=False, server_default=""),
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE {_TABLE}
            SET phase_slot = COALESCE(phase_key, '')
            WHERE phase_slot <> COALESCE(phase_key, '')
            """
        )
    )

    _repair_duplicate_attempt_numbers(bind)
    _repair_duplicate_running_rows(bind)

    if _index_columns(bind) != _RUNNING_COLUMNS:
        if _index_columns(bind) is not None:
            op.drop_index(_RUNNING_INDEX, table_name=_TABLE)

    if _constraint_columns(bind) != _ATTEMPT_COLUMNS:
        with op.batch_alter_table(_TABLE) as batch:
            if _constraint_columns(bind) is not None:
                batch.drop_constraint(_ATTEMPT_CONSTRAINT, type_="unique")
            batch.create_unique_constraint(_ATTEMPT_CONSTRAINT, _ATTEMPT_COLUMNS)

    if _index_columns(bind) != _RUNNING_COLUMNS:
        op.create_index(
            _RUNNING_INDEX,
            _TABLE,
            _RUNNING_COLUMNS,
            unique=True,
            sqlite_where=sa.text("status = 'running'"),
            postgresql_where=sa.text("status = 'running'"),
        )


def downgrade() -> None:
    # This revision repairs the physical schema already promised by 0026.
    # Downgrading to 0027 must retain that promised, ORM-compatible shape.
    pass
