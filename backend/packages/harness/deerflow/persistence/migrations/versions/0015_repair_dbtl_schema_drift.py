"""Repair DBTL columns missing from databases already stamped past their revisions.

Revision ID: 0015_repair_dbtl_schema_drift
Revises: 0014_dbtl_stage_execution

Some development databases were stamped through revisions 0012/0013 while
carrying an earlier physical shape: ``dbtl_cycles.create_idempotency_key`` and
``dbtl_classifier_evaluations.request_fingerprint`` were absent even though
Alembic considered their owning revisions complete. A normal ``upgrade head``
cannot revisit an applied revision, so this forward repair makes the intended
0014 schema true again without deleting cycle or telemetry records.
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

revision = "0015_repair_dbtl_schema_drift"
down_revision = "0014_dbtl_stage_execution"
branch_labels = None
depends_on = None

_CYCLES = "dbtl_cycles"
_CLASSIFIER_EVALUATIONS = "dbtl_classifier_evaluations"
_IDEMPOTENCY_INDEX = "uq_dbtl_cycle_create_idempotency"


def _legacy_fingerprint(evaluation_id: str) -> str:
    """Create a stable placeholder when the original request is unrecoverable."""
    payload = f"legacy-dbtl-classifier-evaluation:{evaluation_id}".encode()
    return hashlib.sha256(payload).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if _CYCLES in tables:
        safe_add_column(
            _CYCLES,
            sa.Column(
                "create_idempotency_key",
                sa.String(length=128),
                nullable=True,
            ),
        )
        indexes = {item["name"] for item in sa.inspect(bind).get_indexes(_CYCLES)}
        if _IDEMPOTENCY_INDEX not in indexes:
            op.create_index(
                _IDEMPOTENCY_INDEX,
                _CYCLES,
                ["project_id", "create_idempotency_key"],
                unique=True,
                sqlite_where=sa.text("create_idempotency_key IS NOT NULL"),
                postgresql_where=sa.text("create_idempotency_key IS NOT NULL"),
            )

    if _CLASSIFIER_EVALUATIONS not in tables:
        return

    columns = {item["name"]: item for item in sa.inspect(bind).get_columns(_CLASSIFIER_EVALUATIONS)}
    if "request_fingerprint" not in columns:
        # Add nullable first so databases with existing telemetry rows can be
        # repaired without manufacturing one shared server default.
        safe_add_column(
            _CLASSIFIER_EVALUATIONS,
            sa.Column(
                "request_fingerprint",
                sa.String(length=64),
                nullable=True,
            ),
        )

    missing = bind.execute(
        sa.text(
            f"""
            SELECT id
            FROM {_CLASSIFIER_EVALUATIONS}
            WHERE request_fingerprint IS NULL
            """
        )
    )
    for row in missing:
        evaluation_id = str(row[0])
        bind.execute(
            sa.text(
                f"""
                UPDATE {_CLASSIFIER_EVALUATIONS}
                SET request_fingerprint = :fingerprint
                WHERE id = :evaluation_id
                """
            ),
            {
                "evaluation_id": evaluation_id,
                "fingerprint": _legacy_fingerprint(evaluation_id),
            },
        )

    reflected = {item["name"]: item for item in sa.inspect(bind).get_columns(_CLASSIFIER_EVALUATIONS)}
    if reflected["request_fingerprint"]["nullable"]:
        with op.batch_alter_table(_CLASSIFIER_EVALUATIONS) as batch:
            batch.alter_column(
                "request_fingerprint",
                existing_type=sa.String(length=64),
                nullable=False,
            )


def downgrade() -> None:
    # This revision repairs the physical schema promised by 0012/0013; moving
    # back to 0014 must retain that promised shape.
    pass
