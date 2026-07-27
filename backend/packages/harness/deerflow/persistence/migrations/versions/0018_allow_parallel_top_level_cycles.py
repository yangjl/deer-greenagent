"""Allow a project to run several top-level cycles at once.

0012 enforced "at most one live top-level cycle per project" with a partial
unique index. That was doing two jobs, and only one of them is still wanted:

1. **A product rule** — a project works on one question at a time. That rule is
   being dropped deliberately. A breeding project legitimately runs cycles in
   parallel (different traits, populations, or seasons), and refusing the second
   one blocked real work.
2. **A concurrency guard** — two simultaneous "Start a cycle" clicks must not
   create two records. That job is still needed, and it is already covered by
   ``uq_dbtl_cycle_create_idempotency``: the dialog mints one create key per
   opening, so a double-submit collapses onto the same row regardless of this
   index. Dropping this one therefore does not reopen the double-click race.

Nothing is deleted. Cycles previously moved to ``abandoned`` by 0012's dedupe
pre-step stay abandoned — a cycle is a research record, and silently reviving
one would rewrite history. Reopening such a cycle is a human decision.

Revision ID: 0018_allow_parallel_top_level_cycles
Revises: 0017_dbtl_learn_knowledge
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text

revision = "0018_allow_parallel_top_level_cycles"
down_revision = "0017_dbtl_learn_knowledge"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_dbtl_active_top_level_cycle"
_TABLE = "dbtl_cycles"
_PREDICATE = "parent_cycle_id IS NULL AND state NOT IN ('completed', 'abandoned')"


def _has_index(name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return False
    return any(index["name"] == name for index in inspector.get_indexes(_TABLE))


def upgrade() -> None:
    # Idempotent: a database provisioned by ``create_all`` after this revision
    # never had the index, and a re-run must not fail.
    if _has_index(_INDEX_NAME):
        op.drop_index(_INDEX_NAME, table_name=_TABLE)


def downgrade() -> None:
    """Restore the single-active-cycle rule.

    Re-creating a unique index over data that has since gained parallel cycles
    would fail, so the extra live cycles are retired first — newest kept, older
    ones marked ``abandoned`` rather than deleted. This mirrors 0012's own
    dedupe pre-step and keeps the records inspectable.
    """
    if _has_index(_INDEX_NAME):
        return
    bind = op.get_bind()
    bind.execute(
        text(
            f"""
            UPDATE {_TABLE}
               SET state = 'abandoned'
             WHERE parent_cycle_id IS NULL
               AND state NOT IN ('completed', 'abandoned')
               AND id NOT IN (
                   SELECT id FROM (
                       SELECT id,
                              ROW_NUMBER() OVER (
                                  PARTITION BY project_id ORDER BY created_at DESC, id DESC
                              ) AS rank_in_project
                         FROM {_TABLE}
                        WHERE parent_cycle_id IS NULL
                          AND state NOT IN ('completed', 'abandoned')
                   ) ranked
                    WHERE ranked.rank_in_project = 1
               )
            """
        )
    )
    op.create_index(
        _INDEX_NAME,
        _TABLE,
        ["project_id"],
        unique=True,
        sqlite_where=text(_PREDICATE),
        postgresql_where=text(_PREDICATE),
    )
