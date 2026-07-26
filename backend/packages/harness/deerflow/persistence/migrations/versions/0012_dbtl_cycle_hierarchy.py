"""Parent/child DBTL cycles and one live top-level cycle per project.

Revision ID: 0012_dbtl_cycle_hierarchy
Revises: 0011_dbtl_governance
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

logger = logging.getLogger(__name__)

revision = "0012_dbtl_cycle_hierarchy"
down_revision = "0011_dbtl_governance"
branch_labels = None
depends_on = None

# Kept literal because a partial index predicate must be SQL text.
# ``tests/test_migration_0012_dbtl_cycle_hierarchy.py`` pins it against
# ``TERMINAL_CYCLE_STATES`` so the two cannot drift apart.
_ACTIVE_TOP_LEVEL_PREDICATE = "parent_cycle_id IS NULL AND state NOT IN ('completed', 'abandoned')"
_INDEX_NAME = "uq_dbtl_active_top_level_cycle"
_IDEMPOTENCY_INDEX_NAME = "uq_dbtl_cycle_create_idempotency"
_PARENT_FK_NAME = "fk_dbtl_cycles_parent_cycle_id"


def _abandon_duplicate_active_top_level_cycles() -> None:
    """Abandon extra live top-level cycles so the unique index can be built.

    Mirrors the dedupe pre-steps in 0004 and 0007: a database that already
    violates the invariant would fail ``CREATE UNIQUE INDEX`` and abort the
    upgrade, which blocks Gateway startup entirely.

    The newest cycle per project wins (``created_at`` DESC, ``id`` DESC as a
    deterministic tiebreaker). Losers are moved to ``abandoned`` rather than
    deleted — a DBTL cycle is a research record, so it is retired, never
    destroyed.
    """
    bind = op.get_bind()
    duplicate_predicate = """
        state NOT IN ('completed', 'abandoned')
          AND parent_cycle_id IS NULL
          AND EXISTS (
            SELECT 1 FROM dbtl_cycles AS c2
            WHERE c2.project_id = dbtl_cycles.project_id
              AND c2.parent_cycle_id IS NULL
              AND c2.state NOT IN ('completed', 'abandoned')
              AND c2.id <> dbtl_cycles.id
              AND (
                c2.created_at > dbtl_cycles.created_at
                OR (c2.created_at = dbtl_cycles.created_at AND c2.id > dbtl_cycles.id)
              )
          )
    """
    rows = list(bind.execute(sa.text(f"SELECT id, project_id FROM dbtl_cycles WHERE {duplicate_predicate}")).fetchall())
    if not rows:
        return
    for cycle_id, project_id in rows:
        logger.warning(
            "migration 0012_dbtl_cycle_hierarchy: abandoning duplicate active top-level cycle %s on project %s",
            cycle_id,
            project_id,
        )
    bind.execute(sa.text(f"UPDATE dbtl_cycles SET state = 'abandoned' WHERE {duplicate_predicate}"))


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "dbtl_cycles" not in insp.get_table_names():
        # Fresh databases get the whole shape from create_all before this
        # revision is reached; nothing to alter.
        return

    safe_add_column(
        "dbtl_cycles",
        sa.Column("parent_cycle_id", sa.String(length=64), nullable=True),
    )
    safe_add_column(
        "dbtl_cycles",
        sa.Column("create_idempotency_key", sa.String(length=128), nullable=True),
    )

    inspector = sa.inspect(op.get_bind())
    foreign_keys = {item.get("name") for item in inspector.get_foreign_keys("dbtl_cycles")}
    if _PARENT_FK_NAME not in foreign_keys:
        with op.batch_alter_table("dbtl_cycles") as batch:
            batch.create_foreign_key(
                _PARENT_FK_NAME,
                "dbtl_cycles",
                ["parent_cycle_id"],
                ["id"],
                ondelete="RESTRICT",
            )

    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("dbtl_cycles")}
    if "ix_dbtl_cycles_parent_cycle_id" not in existing:
        op.create_index("ix_dbtl_cycles_parent_cycle_id", "dbtl_cycles", ["parent_cycle_id"])
    if _INDEX_NAME not in existing:
        _abandon_duplicate_active_top_level_cycles()
        op.create_index(
            _INDEX_NAME,
            "dbtl_cycles",
            ["project_id"],
            unique=True,
            sqlite_where=sa.text(_ACTIVE_TOP_LEVEL_PREDICATE),
            postgresql_where=sa.text(_ACTIVE_TOP_LEVEL_PREDICATE),
        )
    if _IDEMPOTENCY_INDEX_NAME not in existing:
        op.create_index(
            _IDEMPOTENCY_INDEX_NAME,
            "dbtl_cycles",
            ["project_id", "create_idempotency_key"],
            unique=True,
            sqlite_where=sa.text("create_idempotency_key IS NOT NULL"),
            postgresql_where=sa.text("create_idempotency_key IS NOT NULL"),
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "dbtl_cycles" not in insp.get_table_names():
        return
    existing = {index["name"] for index in insp.get_indexes("dbtl_cycles")}
    if _IDEMPOTENCY_INDEX_NAME in existing:
        op.drop_index(_IDEMPOTENCY_INDEX_NAME, table_name="dbtl_cycles")
    if _INDEX_NAME in existing:
        op.drop_index(_INDEX_NAME, table_name="dbtl_cycles")
    if "ix_dbtl_cycles_parent_cycle_id" in existing:
        op.drop_index("ix_dbtl_cycles_parent_cycle_id", table_name="dbtl_cycles")
    with op.batch_alter_table("dbtl_cycles") as batch:
        batch.drop_column("parent_cycle_id")
        batch.drop_column("create_idempotency_key")
