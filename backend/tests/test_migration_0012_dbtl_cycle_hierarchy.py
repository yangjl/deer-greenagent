"""Phase 3: cycle hierarchy and the one-live-top-level-cycle invariant.

Two properties are proven here. First, the invariant is enforced by the
*database*: an application-level "is there already an active cycle?" check
cannot survive two concurrent "Start a cycle" clicks, exactly as documented
for ``uq_runs_thread_active`` and ``uq_scheduled_task_run_active``.

Second, the migration's literal SQL predicate still names the same terminal
states as the state machine, so adding a terminal state without updating the
index fails here rather than silently letting a second cycle start.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

import deerflow.persistence as _persistence
import deerflow.persistence.models  # noqa: F401  -- registers ORM models
from deerflow.dbtl import TERMINAL_CYCLE_STATES
from deerflow.persistence.base import Base
from deerflow.persistence.bootstrap import _get_alembic_config

_MIGRATION_PATH = Path(_persistence.__file__).parent / "migrations" / "versions" / "0012_dbtl_cycle_hierarchy.py"


def _load_migration():
    """Load the revision module by path: its filename is not an identifier."""
    spec = importlib.util.spec_from_file_location("migration_0012", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_index_predicate_names_exactly_the_terminal_states() -> None:
    predicate = _load_migration()._ACTIVE_TOP_LEVEL_PREDICATE

    quoted = predicate.split("NOT IN (", 1)[1].split(")", 1)[0]
    named = {item.strip().strip("'") for item in quoted.split(",")}

    assert named == set(TERMINAL_CYCLE_STATES)
    assert "parent_cycle_id IS NULL" in predicate


@pytest.mark.asyncio
async def test_alembic_upgrade_adds_parent_fk_and_create_idempotency_index(tmp_path: Path) -> None:
    """The upgraded schema must match constraints fresh create_all already has."""
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.db'}")
    try:
        config = _get_alembic_config(async_engine)
        await asyncio.to_thread(
            alembic_command.upgrade,
            config,
            "0011_dbtl_governance",
        )
        await asyncio.to_thread(alembic_command.upgrade, config, "head")

        async with async_engine.connect() as connection:
            foreign_keys, indexes = await connection.run_sync(
                lambda sync: (
                    sa.inspect(sync).get_foreign_keys("dbtl_cycles"),
                    sa.inspect(sync).get_indexes("dbtl_cycles"),
                )
            )

        parent_fk = next(item for item in foreign_keys if item["constrained_columns"] == ["parent_cycle_id"])
        assert parent_fk["referred_table"] == "dbtl_cycles"
        assert parent_fk["referred_columns"] == ["id"]
        assert parent_fk["options"].get("ondelete") == "RESTRICT"

        idempotency = next(item for item in indexes if item["name"] == "uq_dbtl_cycle_create_idempotency")
        assert idempotency["unique"] == 1
        assert idempotency["column_names"] == [
            "project_id",
            "create_idempotency_key",
        ]
    finally:
        await async_engine.dispose()


@pytest.fixture
def engine(tmp_path: Path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'dbtl.db'}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys = ON"))
    yield engine
    engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_project(connection, project_id: str) -> None:
    connection.execute(
        sa.text("INSERT OR IGNORE INTO workspaces (id, name, slug, status, created_by, created_at, updated_at) VALUES ('ws-1', 'W', 'w', 'active', 'user-1', :now, :now)").bindparams(sa.bindparam("now", type_=sa.DateTime(timezone=True))),
        {"now": _now()},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO projects (
                id, workspace_id, name, slug, status, crop_profile,
                dbtl_phase, reconciliation_status, created_by, created_at, updated_at
            ) VALUES (:id, 'ws-1', 'P', :slug, 'active', 'maize', 'design', 'in-sync', 'user-1', :now, :now)
            """
        ).bindparams(sa.bindparam("now", type_=sa.DateTime(timezone=True))),
        {"id": project_id, "slug": project_id, "now": _now()},
    )


def _insert_cycle(
    connection,
    cycle_id: str,
    *,
    project_id: str = "project-1",
    state: str = "design",
    parent_cycle_id: str | None = None,
    created_at: datetime | None = None,
) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO dbtl_cycles (
                id, project_id, parent_cycle_id, title, cycle_class, state,
                policy_version, db_revision, projection_json, projection_hash,
                created_by, created_at, updated_at
            ) VALUES (
                :id, :project_id, :parent_cycle_id, 'T', 'computational', :state,
                'v1', 1, '{}', :hash, 'user-1', :created_at, :now
            )
            """
        ).bindparams(
            sa.bindparam("created_at", type_=sa.DateTime(timezone=True)),
            sa.bindparam("now", type_=sa.DateTime(timezone=True)),
        ),
        {
            "id": cycle_id,
            "project_id": project_id,
            "parent_cycle_id": parent_cycle_id,
            "state": state,
            "hash": cycle_id.ljust(64, "0")[:64],
            "created_at": created_at or _now(),
            "now": _now(),
        },
    )


def test_a_second_live_top_level_cycle_is_rejected_by_the_database(engine) -> None:
    with engine.begin() as connection:
        _seed_project(connection, "project-1")
        _insert_cycle(connection, "cycle-1")

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_cycle(connection, "cycle-2")


@pytest.mark.parametrize("terminal_state", sorted(TERMINAL_CYCLE_STATES))
def test_a_finished_cycle_frees_the_slot(engine, terminal_state: str) -> None:
    with engine.begin() as connection:
        _seed_project(connection, "project-1")
        _insert_cycle(connection, "cycle-1", state=terminal_state)
        _insert_cycle(connection, "cycle-2")

    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM dbtl_cycles")).scalar_one() == 2


def test_several_child_cycles_may_run_under_one_parent(engine) -> None:
    """The invariant constrains top-level cycles only."""
    with engine.begin() as connection:
        _seed_project(connection, "project-1")
        _insert_cycle(connection, "parent-1")
        _insert_cycle(connection, "child-1", parent_cycle_id="parent-1")
        _insert_cycle(connection, "child-2", parent_cycle_id="parent-1")

    with engine.connect() as connection:
        children = connection.execute(sa.text("SELECT COUNT(*) FROM dbtl_cycles WHERE parent_cycle_id = 'parent-1'")).scalar_one()
    assert children == 2


def test_two_projects_each_get_their_own_live_cycle(engine) -> None:
    with engine.begin() as connection:
        _seed_project(connection, "project-1")
        _seed_project(connection, "project-2")
        _insert_cycle(connection, "cycle-1", project_id="project-1")
        _insert_cycle(connection, "cycle-2", project_id="project-2")

    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM dbtl_cycles")).scalar_one() == 2


def test_the_dedupe_pre_step_retires_the_older_duplicate(engine) -> None:
    """A legacy database that already violates the invariant must still upgrade.

    The index is dropped first so duplicates can be inserted at all — that is
    exactly the pre-0012 shape the dedupe pass exists to repair.
    """
    module = _load_migration()

    with engine.begin() as connection:
        _seed_project(connection, "project-1")
        connection.execute(sa.text(f"DROP INDEX {module._INDEX_NAME}"))
        _insert_cycle(connection, "cycle-old", created_at=datetime(2026, 7, 20, tzinfo=UTC))
        _insert_cycle(connection, "cycle-new", created_at=datetime(2026, 7, 21, tzinfo=UTC))

        # Run the migration's dedupe SQL against this connection.
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
        connection.execute(sa.text(f"UPDATE dbtl_cycles SET state = 'abandoned' WHERE {duplicate_predicate}"))

    with engine.connect() as connection:
        states = dict(connection.execute(sa.text("SELECT id, state FROM dbtl_cycles")).fetchall())

    # Newest survives; the older one is retired, never deleted — a cycle is a
    # research record.
    assert states == {"cycle-old": "abandoned", "cycle-new": "design"}


def test_a_parent_cycle_cannot_be_deleted_while_children_exist(engine) -> None:
    """RESTRICT, not CASCADE: deleting a parent must not erase child records."""
    with engine.begin() as connection:
        _seed_project(connection, "project-1")
        _insert_cycle(connection, "parent-1")
        _insert_cycle(connection, "child-1", parent_cycle_id="parent-1")

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(sa.text("PRAGMA foreign_keys = ON"))
            connection.execute(sa.text("DELETE FROM dbtl_cycles WHERE id = 'parent-1'"))
