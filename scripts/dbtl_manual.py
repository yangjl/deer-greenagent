#!/usr/bin/env python3
"""Capture and restore isolated DBTL manual-test checkpoints.

The manual profile keeps its SQLite database and human-visible project folder
under ``.deer-flow/manual-dbtl/live``. A scenario captures both together so the
DBTL revision, evidence, deck, thread, and feedback-surface bindings cannot
drift apart.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANUAL_ROOT = REPO_ROOT / ".deer-flow" / "manual-dbtl"
DEFAULT_SOURCE_CONFIG = REPO_ROOT / "config.yaml"
PROFILE_FILENAME = "config.yaml"
DATABASE_FILENAME = "deerflow.db"
MANIFEST_FILENAME = "manifest.json"
RUNTIME_LOCK_FILENAME = "runtime.lock"
MANIFEST_VERSION = 1
SCENARIO_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# A deck action counts as in-flight only while `pending`, i.e. admitted to the
# single-use ledger but not yet resolved, so its outcome is unknown. Every other
# status is terminal for the ledger — including `resume_started`, which means the
# chair-resume run was launched and the action is finished; that run's own liveness
# is already covered by `runs` above. Treating `resume_started` as active wedged the
# pipeline permanently: nothing ever advances the row, so one answered chair question
# refused every later capture and restore for the lifetime of the database.
ACTIVE_TABLE_STATUSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("runs", ("pending", "running")),
    ("scheduled_task_runs", ("queued", "running")),
    ("dbtl_design_feedback_actions", ("pending",)),
)

GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 8001
GATEWAY_READY_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/features"
# Hot restore recycles only the Gateway, by touching one file the dev launcher's
# uvicorn reload watcher actually sees. The launcher excludes `tests/**`,
# `.deer-flow`, and `backend/sandbox`, so the trigger must be ordinary backend
# source. `backend/app/__init__.py` is chosen because it is a permanently empty
# package marker: nothing else has a reason to edit it, and only its mtime is
# ever changed here — its contents are never written.
RELOAD_TRIGGER_PATH = REPO_ROOT / "backend" / "app" / "__init__.py"
# The reloader keeps the listening socket in the parent process, so the port
# never stops accepting during a restart. A short settle window keeps the probe
# from reading the still-running old worker as the reloaded one.
RELOAD_SETTLE_SECONDS = 2.0
READY_TIMEOUT_SECONDS = 30.0
READY_POLL_INTERVAL_SECONDS = 0.5
READY_REQUEST_TIMEOUT_SECONDS = 5.0
DEFAULT_MANUAL_USER_EMAIL = "dbtl-linear-10m@example.com"
MANUAL_USER_EMAIL_ENV_VAR = "DEER_FLOW_MANUAL_USER_EMAIL"


class ManualPipelineError(RuntimeError):
    """A safe manual-pipeline operation cannot proceed."""


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def _live_root(manual_root: Path) -> Path:
    return _resolved(manual_root) / "live"


def _database_path(manual_root: Path) -> Path:
    return _live_root(manual_root) / "db" / DATABASE_FILENAME


def _projects_path(manual_root: Path) -> Path:
    return _live_root(manual_root) / "projects"


def _profile_path(manual_root: Path) -> Path:
    return _resolved(manual_root) / PROFILE_FILENAME


def _manual_user(database: Path, *, email: str) -> tuple[str, str]:
    """Resolve the replay owner from the isolated checkpoint, never the host app."""
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT id, email FROM users WHERE lower(email) = lower(?) LIMIT 1",
                (email,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise ManualPipelineError(f"Could not resolve the manual replay user from {database}: {exc}") from exc
    if row is None:
        raise ManualPipelineError(f"The manual checkpoint has no user {email!r}. Set {MANUAL_USER_EMAIL_ENV_VAR} to the checkpoint owner's email.")
    return str(row[0]), str(row[1])


@contextmanager
def manual_runtime_lock(
    *,
    manual_root: Path = DEFAULT_MANUAL_ROOT,
    starting_stack: bool = False,
) -> Iterator[None]:
    """Exclude checkpoint restores while the isolated stack is running."""
    root = _resolved(manual_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / RUNTIME_LOCK_FILENAME
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if starting_stack:
                message = "The manual DBTL stack is already running. Open http://localhost:2026 instead of starting a second copy. If the app is unavailable, stop the existing launcher with Ctrl+C and retry."
            else:
                message = "The manual DBTL stack is running. Run `make stop` before restoring a manual checkpoint."
            raise ManualPipelineError(message) from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def validate_scenario_name(name: str) -> str:
    if not SCENARIO_NAME.fullmatch(name):
        raise ManualPipelineError("Invalid scenario name. Use 1-64 lowercase letters, digits, hyphens, or underscores; the first character must be a letter or digit.")
    return name


def _disable_configured_channels(config: dict[str, Any]) -> None:
    channels = config.get("channels")
    if not isinstance(channels, dict):
        return
    for channel in channels.values():
        if isinstance(channel, dict) and "enabled" in channel:
            channel["enabled"] = False


def initialize_profile(
    *,
    source_config: Path = DEFAULT_SOURCE_CONFIG,
    manual_root: Path = DEFAULT_MANUAL_ROOT,
    force: bool = False,
) -> Path:
    """Create an isolated config derived from the developer's normal config."""
    source = _resolved(source_config)
    root = _resolved(manual_root)
    profile = _profile_path(root)
    if profile.exists() and not force:
        # The manual pipeline restores its SQLite database by replacing the
        # live directory and restarting the Gateway. A process-local event
        # store loses the transcript on both operations even though the
        # LangGraph checkpoints survive. Heal profiles generated before the
        # persistent-history invariant was introduced without requiring a
        # destructive full refresh from the developer's source config.
        loaded_profile = yaml.safe_load(profile.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded_profile, dict):
            raise ManualPipelineError(f"Manual profile must contain a YAML mapping: {profile}")
        run_events = loaded_profile.get("run_events")
        if not isinstance(run_events, dict):
            run_events = {}
            loaded_profile["run_events"] = run_events
        sandbox = loaded_profile.setdefault("sandbox", {})
        if not isinstance(sandbox, dict):
            sandbox = {}
            loaded_profile["sandbox"] = sandbox
        dbtl = loaded_profile.setdefault("dbtl", {})
        if not isinstance(dbtl, dict):
            dbtl = {}
            loaded_profile["dbtl"] = dbtl
        changed = run_events.get("backend") != "db"
        if changed:
            run_events["backend"] = "db"
        # Heal profiles created before executable Build work was part of the
        # manual scenario. This changes only the isolated developer profile;
        # the source config and production defaults remain untouched.
        if sandbox.get("allow_host_bash") is not True:
            sandbox["allow_host_bash"] = True
            changed = True
        if dbtl.get("degraded_evidence_continuation") is not True:
            dbtl["degraded_evidence_continuation"] = True
            changed = True
        # Older isolated profiles predate the ready-to-use DBTL specialists.
        # Copy them only when the manual profile has no custom roster, so a
        # tester's deliberate local agents are never overwritten.
        profile_subagents = loaded_profile.setdefault("subagents", {})
        if not isinstance(profile_subagents, dict):
            profile_subagents = {}
            loaded_profile["subagents"] = profile_subagents
        profile_agents = profile_subagents.get("custom_agents")
        if not isinstance(profile_agents, dict) or not profile_agents:
            source_loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {} if source.is_file() else {}
            source_agents = (source_loaded.get("subagents") or {}).get("custom_agents") if isinstance(source_loaded, dict) else None
            if isinstance(source_agents, dict) and source_agents:
                profile_subagents["custom_agents"] = copy.deepcopy(source_agents)
                changed = True
        # Make the Build contract under manual evaluation explicit while
        # preserving an operator's deliberate legacy rollback selection.
        if changed:
            rendered = yaml.safe_dump(loaded_profile, sort_keys=False, allow_unicode=True)
            profile.write_text(
                "# Generated by scripts/dbtl_manual.py. Edit the source config, then run init --force.\n" + rendered,
                encoding="utf-8",
            )
        return profile
    if not source.is_file():
        raise ManualPipelineError(f"Source config not found: {source}")

    loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ManualPipelineError(f"Source config must contain a YAML mapping: {source}")
    config: dict[str, Any] = loaded

    live = _live_root(root)
    database_dir = live / "db"
    projects_dir = live / "projects"
    database_dir.mkdir(parents=True, exist_ok=True)
    projects_dir.mkdir(parents=True, exist_ok=True)

    # A legacy standalone checkpointer would split thread state away from the
    # database this tool snapshots, so the manual profile always uses the
    # unified SQLite backend.
    config.pop("checkpointer", None)
    config["database"] = {
        "backend": "sqlite",
        "sqlite_dir": str(database_dir),
    }
    # Checkpoints alone are not the chat-history read model. The frontend's
    # paginated transcript reads run_events, so the isolated profile must keep
    # them in the same SQLite file captured and restored by this tool.
    run_events = config.setdefault("run_events", {})
    if not isinstance(run_events, dict):
        run_events = {}
        config["run_events"] = run_events
    run_events["backend"] = "db"
    projects = config.setdefault("projects", {})
    if not isinstance(projects, dict):
        projects = {}
        config["projects"] = projects
    projects["root"] = str(projects_dir)

    dbtl = config.setdefault("dbtl", {})
    if not isinstance(dbtl, dict):
        dbtl = {}
        config["dbtl"] = dbtl
    dbtl["mode"] = "graph_enabled"
    dbtl["design_deck_feedback"] = True
    # The current pre-cycle path under test is the durable conversational one.
    # Classifier entry and automatic cards stay off so a manual scenario begins
    # only when the tester explicitly asks to start a cycle.
    dbtl["conversational_discovery"] = True
    dbtl["discovery_project_history"] = True
    dbtl["discovery_global_memory"] = False
    dbtl["discovery_classifier_entry"] = False
    dbtl["discovery_auto_offer"] = False
    # The manual pipeline exists to exercise in-development DBTL surfaces, so
    # the progressive-gate read model (path strip) is on in the isolated
    # profile while the developer's normal config keeps the default.
    dbtl["progressive_gate"] = True
    # Build's phased workflow and its human controls, for the same reason: this
    # profile is where a new refusal — a Design that cannot be resolved through
    # the project root, a plan nobody confirmed, a phase that stopped — has to
    # be discovered, rather than mid-experiment in somebody's real project.
    dbtl["build_workflow_steps"] = True
    dbtl["build_plan_confirmation"] = True
    dbtl["build_work_meetings"] = True
    # Exercise the explicit human exception path only in this isolated profile.
    dbtl["degraded_evidence_continuation"] = True

    # The isolated manual profile is a developer-operated local environment.
    # Build workers are required to execute the implementation they produce,
    # and the local sandbox exposes that capability only when host Bash is
    # explicitly enabled.  Leaving the source profile's production-safe
    # default in place made every manual Build impossible after planning.
    sandbox = config.setdefault("sandbox", {})
    if not isinstance(sandbox, dict):
        sandbox = {}
        config["sandbox"] = sandbox
    sandbox["allow_host_bash"] = True

    memory = config.setdefault("memory", {})
    if not isinstance(memory, dict):
        memory = {}
        config["memory"] = memory
    custom_agents = (config.get("subagents") or {}).get("custom_agents") or {}
    craft_memory_enabled = any(isinstance(agent, dict) and agent.get("craft_memory") is True for agent in custom_agents.values())
    memory["enabled"] = craft_memory_enabled
    memory["injection_enabled"] = craft_memory_enabled

    scheduler = config.setdefault("scheduler", {})
    if not isinstance(scheduler, dict):
        scheduler = {}
        config["scheduler"] = scheduler
    scheduler["enabled"] = False

    run_ownership = config.setdefault("run_ownership", {})
    if not isinstance(run_ownership, dict):
        run_ownership = {}
        config["run_ownership"] = run_ownership
    run_ownership["heartbeat_enabled"] = False

    channel_connections = config.setdefault("channel_connections", {})
    if not isinstance(channel_connections, dict):
        channel_connections = {}
        config["channel_connections"] = channel_connections
    channel_connections["enabled"] = False
    _disable_configured_channels(config)

    root.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    profile.write_text(
        "# Generated by scripts/dbtl_manual.py. Edit the source config, then run init --force.\n" + rendered,
        encoding="utf-8",
    )
    return profile


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _active_work(conn: sqlite3.Connection) -> list[str]:
    active: list[str] = []
    for table, statuses in ACTIVE_TABLE_STATUSES:
        if not _table_exists(conn, table):
            continue
        placeholders = ", ".join("?" for _ in statuses)
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE status IN ({placeholders})",  # noqa: S608 - table names are constants
            statuses,
        ).fetchone()[0]
        if count:
            active.append(f"{table}={count}")
    return active


def _assert_no_active_work(database: Path) -> None:
    if not database.exists():
        return
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
            active = _active_work(conn)
    except sqlite3.Error as exc:
        raise ManualPipelineError(f"Could not inspect manual database {database}: {exc}") from exc
    if active:
        raise ManualPipelineError(f"Cannot capture or replace a checkpoint while active runs/actions exist ({', '.join(active)}). Wait for the run to finish or cancel it first.")


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (
            sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_conn,
            sqlite3.connect(destination) as destination_conn,
        ):
            source_conn.backup(destination_conn)
            result = destination_conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise ManualPipelineError(f"Could not snapshot SQLite database {source}: {exc}") from exc
    if result is None or result[0] != "ok":
        raise ManualPipelineError(f"SQLite integrity check failed for captured database: {result}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    if not root.exists():
        return digest.hexdigest(), file_count, byte_count
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        if path.is_symlink():
            digest.update(b"L")
            digest.update(os.readlink(path).encode("utf-8"))
            continue
        if path.is_dir():
            digest.update(b"D")
            continue
        if path.is_file():
            digest.update(b"F")
            size = path.stat().st_size
            byte_count += size
            file_count += 1
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest(), file_count, byte_count


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _alembic_version(conn: sqlite3.Connection) -> str | None:
    if not _table_exists(conn, "alembic_version"):
        return None
    row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    return str(row[0]) if row else None


def _cycle_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    required = {"projects", "dbtl_cycles"}
    if not all(_table_exists(conn, table) for table in required):
        return []
    has_surfaces = _table_exists(conn, "dbtl_design_feedback_surfaces")
    if has_surfaces:
        rows = conn.execute(
            """
            SELECT
                c.id,
                c.project_id,
                c.title,
                c.state,
                c.db_revision,
                p.slug,
                s.id,
                s.mode,
                s.originating_thread_id
            FROM dbtl_cycles AS c
            JOIN projects AS p ON p.id = c.project_id
            LEFT JOIN dbtl_design_feedback_surfaces AS s
              ON s.cycle_id = c.id
             AND s.superseded_by_surface_id IS NULL
            ORDER BY c.id, s.id
            """
        ).fetchall()
    else:
        rows = [
            (*row, None, None, None)
            for row in conn.execute(
                """
                SELECT c.id, c.project_id, c.title, c.state, c.db_revision, p.slug
                FROM dbtl_cycles AS c
                JOIN projects AS p ON p.id = c.project_id
                ORDER BY c.id
                """
            ).fetchall()
        ]

    summaries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        cycle_id = str(row[0])
        if cycle_id in seen:
            continue
        seen.add(cycle_id)
        slug = str(row[5])
        thread_id = str(row[8]) if row[8] else None
        summaries.append(
            {
                "cycle_id": cycle_id,
                "db_revision": int(row[4]),
                "project_id": str(row[1]),
                "project_slug": slug,
                "state": str(row[3]),
                "surface_id": str(row[6]) if row[6] else None,
                "surface_mode": str(row[7]) if row[7] else None,
                "thread_id": thread_id,
                "title": str(row[2]),
                "url": f"/workspace/{slug}/{thread_id}" if thread_id else f"/workspace/{slug}",
            }
        )
    return summaries


def _database_manifest(database: Path) -> tuple[str | None, list[dict[str, Any]]]:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        return _alembic_version(conn), _cycle_summary(conn)


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _copy_projects(source: Path, destination: Path) -> None:
    if source.exists():
        shutil.copytree(source, destination, symlinks=True)
    else:
        destination.mkdir(parents=True)


def capture_scenario(
    *,
    manual_root: Path = DEFAULT_MANUAL_ROOT,
    scenario: str,
    note: str | None = None,
    expected_path_head: str | None = None,
    expected_assessment: str | None = None,
    offered_routes: list[str] | None = None,
    next_action: str | None = None,
    replace: bool = False,
) -> Path:
    """Capture the isolated live database and project tree as one scenario."""
    scenario = validate_scenario_name(scenario)
    root = _resolved(manual_root)
    database = _database_path(root)
    projects = _projects_path(root)
    if not database.is_file():
        raise ManualPipelineError(f"Manual database not found: {database}. Initialize the profile and run a manual DBTL flow first.")
    _assert_no_active_work(database)

    scenarios_root = root / "scenarios"
    scenarios_root.mkdir(parents=True, exist_ok=True)
    target = scenarios_root / scenario
    if target.exists() and not replace:
        raise ManualPipelineError(f"Scenario {scenario!r} already exists. Use --replace to archive and replace it.")

    staging = scenarios_root / f".capture-{scenario}-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        captured_database = staging / DATABASE_FILENAME
        _backup_sqlite(database, captured_database)
        _copy_projects(projects, staging / "projects")
        projects_hash, project_files, project_bytes = _tree_digest(staging / "projects")
        alembic_version, cycles = _database_manifest(captured_database)
        manifest = {
            "version": MANIFEST_VERSION,
            "scenario": scenario,
            "captured_at": datetime.now(UTC).isoformat(),
            "git_commit": _git_commit(),
            "alembic_version": alembic_version,
            "database_sha256": _sha256(captured_database),
            "projects_sha256": projects_hash,
            "project_file_count": project_files,
            "project_bytes": project_bytes,
            "note": note.strip() if note and note.strip() else None,
            "expectations": {
                "path_head": (expected_path_head.strip() if expected_path_head and expected_path_head.strip() else None),
                "assessment": (expected_assessment.strip() if expected_assessment and expected_assessment.strip() else None),
                "offered_routes": list(dict.fromkeys(item.strip() for item in (offered_routes or []) if item.strip())),
                "next_action": (next_action.strip() if next_action and next_action.strip() else None),
            },
            "cycles": cycles,
        }
        (staging / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        if target.exists():
            replaced_root = root / "scenario-backups"
            replaced_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(replaced_root / f"{scenario}-{_timestamp()}"))
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target / MANIFEST_FILENAME


def _load_manifest(scenario_root: Path) -> dict[str, Any]:
    path = scenario_root / MANIFEST_FILENAME
    if not path.is_file():
        raise ManualPipelineError(f"Scenario manifest not found: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManualPipelineError(f"Scenario manifest is invalid: {path}") from exc
    if manifest.get("version") != MANIFEST_VERSION:
        raise ManualPipelineError(f"Unsupported scenario manifest version {manifest.get('version')!r}; expected {MANIFEST_VERSION}.")
    return manifest


def gateway_is_listening(host: str = "127.0.0.1", port: int = 8001) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _validate_scenario_integrity(scenario_root: Path, manifest: dict[str, Any]) -> None:
    database = scenario_root / DATABASE_FILENAME
    projects = scenario_root / "projects"
    if not database.is_file():
        raise ManualPipelineError(f"Scenario database not found: {database}")
    if _sha256(database) != manifest.get("database_sha256"):
        raise ManualPipelineError("Scenario database integrity check failed; its SHA-256 no longer matches the manifest.")
    projects_hash, _, _ = _tree_digest(projects)
    if projects_hash != manifest.get("projects_sha256"):
        raise ManualPipelineError("Scenario project-folder integrity check failed; its contents no longer match the manifest.")
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise ManualPipelineError(f"Scenario database integrity check failed: {exc}") from exc
    if result is None or result[0] != "ok":
        raise ManualPipelineError(f"Scenario database integrity check failed: {result}")


def _backup_live_state(manual_root: Path) -> Path | None:
    database = _database_path(manual_root)
    projects = _projects_path(manual_root)
    if not database.exists() and not projects.exists():
        return None
    backup_root = _resolved(manual_root) / "restore-backups" / f"{_timestamp()}-{uuid.uuid4().hex[:8]}"
    backup_root.mkdir(parents=True)
    if database.exists():
        _backup_sqlite(database, backup_root / DATABASE_FILENAME)
    _copy_projects(projects, backup_root / "projects")
    return backup_root


def _backfill_checkpoint_history(database: Path) -> int:
    """Seed a legacy manual database's empty thread feeds from checkpoints.

    Older manual profiles used ``run_events.backend=memory``. Their scenario
    databases therefore contain complete checkpoint state but no durable rows
    for ``GET /messages/page``. Backfill only threads with a completely empty
    feed; an existing feed remains authoritative and is never mixed with a
    synthetic copy.
    """
    if not database.is_file():
        return 0

    # These imports are intentionally local: the command runs through the
    # backend environment, while keeping simple list/integrity operations free
    # from checkpoint/runtime imports.
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from deerflow.runtime.journal import build_branch_history_seed_events

    inserted = 0
    serde = JsonPlusSerializer()
    try:
        with sqlite3.connect(database) as conn:
            if not _table_exists(conn, "run_events") or not _table_exists(conn, "checkpoints"):
                return 0
            rows = conn.execute(
                """
                SELECT c.thread_id, c.type, c.checkpoint, tm.user_id
                FROM checkpoints AS c
                JOIN (
                    SELECT thread_id, MAX(checkpoint_id) AS checkpoint_id
                    FROM checkpoints
                    WHERE checkpoint_ns = ''
                    GROUP BY thread_id
                ) AS latest
                  ON latest.thread_id = c.thread_id
                 AND latest.checkpoint_id = c.checkpoint_id
                LEFT JOIN threads_meta AS tm ON tm.thread_id = c.thread_id
                WHERE c.checkpoint_ns = ''
                  AND NOT EXISTS (
                    SELECT 1 FROM run_events AS re
                    WHERE re.thread_id = c.thread_id
                  )
                ORDER BY c.thread_id
                """
            ).fetchall()
            for thread_id, payload_type, payload, user_id in rows:
                checkpoint = serde.loads_typed((payload_type, bytes(payload)))
                messages = checkpoint.get("channel_values", {}).get("messages", [])
                events = build_branch_history_seed_events(
                    messages,
                    thread_id=thread_id,
                    run_id_prefix=f"manual-restore-{thread_id}",
                    parent_thread_id=thread_id,
                )
                for seq, event in enumerate(events, start=1):
                    metadata = dict(event.get("metadata") or {})
                    metadata.pop("branch_seed", None)
                    metadata.pop("branch_parent_thread_id", None)
                    metadata.update(
                        {
                            "manual_restore_seed": True,
                            "content_is_json": True,
                            "content_is_dict": True,
                        }
                    )
                    conn.execute(
                        """
                        INSERT INTO run_events (
                            thread_id, run_id, user_id, event_type, category,
                            content, event_metadata, seq, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            thread_id,
                            event["run_id"],
                            user_id,
                            event["event_type"],
                            event["category"],
                            json.dumps(event["content"], default=str, ensure_ascii=False),
                            json.dumps(metadata, ensure_ascii=False),
                            seq,
                            event["created_at"],
                        ),
                    )
                    inserted += 1
            conn.commit()
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise ManualPipelineError(f"Could not restore checkpoint-backed chat history in {database}: {exc}") from exc
    return inserted


def _swap_live_state(*, root: Path, scenario_root: Path, scenario: str) -> None:
    """Atomically replace the isolated live pair with the scenario's copy."""
    live = _live_root(root)
    staging = root / f".restore-{scenario}-{uuid.uuid4().hex}"
    old_live = root / f".live-old-{uuid.uuid4().hex}"
    try:
        (staging / "db").mkdir(parents=True)
        shutil.copy2(scenario_root / DATABASE_FILENAME, staging / "db" / DATABASE_FILENAME)
        _copy_projects(scenario_root / "projects", staging / "projects")
        _backfill_checkpoint_history(staging / "db" / DATABASE_FILENAME)

        if live.exists():
            os.replace(live, old_live)
        try:
            os.replace(staging, live)
        except Exception:
            if old_live.exists() and not live.exists():
                os.replace(old_live, live)
            raise
        shutil.rmtree(old_live, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _touch_reload_trigger(path: Path = RELOAD_TRIGGER_PATH) -> Path:
    """Bump one watched backend source file's mtime so uvicorn recycles the Gateway."""
    if not path.is_file():
        raise ManualPipelineError(f"Gateway reload trigger not found: {path}. Restore with the cold path instead: `make stop`, `make dbtl-manual-restore SCENARIO=<name>`, `make dbtl-manual-dev`.")
    os.utime(path, None)
    return path


def _gateway_is_ready(url: str = GATEWAY_READY_URL) -> bool:
    """Report whether the Gateway app answers an unauthenticated probe."""
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, method="GET"),
            timeout=READY_REQUEST_TIMEOUT_SECONDS,
        ):
            return True
    except urllib.error.HTTPError:
        # An unauthenticated probe is answered with 401; the app is serving.
        return True
    except (urllib.error.URLError, OSError):
        return False


def _wait_for_gateway_ready(
    *,
    timeout: float = READY_TIMEOUT_SECONDS,
    settle: float = RELOAD_SETTLE_SECONDS,
) -> None:
    time.sleep(settle)
    deadline = time.monotonic() + timeout
    while True:
        if _gateway_is_ready():
            return
        if time.monotonic() >= deadline:
            raise ManualPipelineError(f"The Gateway did not answer within {timeout:.0f}s after the hot swap. The scenario is already in place; fall back to the cold path: `make stop`, then `make dbtl-manual-dev`.")
        time.sleep(READY_POLL_INTERVAL_SECONDS)


def _hot_restore_scenario(*, manual_root: Path, scenario: str) -> dict[str, Any]:
    """Swap a scenario under the running stack and recycle only the Gateway.

    This inverts the cold path's rule on purpose: only the Gateway holds the
    isolated SQLite file, so it can be recycled in seconds while the frontend
    and nginx keep running. The quiescence guard below is what makes replacing
    state under a live process acceptable, so it is never skipped.
    """
    scenario = validate_scenario_name(scenario)
    root = _resolved(manual_root)
    if not gateway_is_listening():
        raise ManualPipelineError(
            "The manual DBTL stack is not running (nothing is listening on Gateway port 8001), so there is no Gateway to reload. Use the cold restore instead: `make dbtl-manual-restore SCENARIO=<name>`, then `make dbtl-manual-dev`.",
        )

    # The running stack owns the advisory runtime lock, so the hot path must not
    # try to take it; the quiescence guard is its safety property instead.
    scenario_root = root / "scenarios" / scenario
    manifest = _load_manifest(scenario_root)
    _validate_scenario_integrity(scenario_root, manifest)

    current_database = _database_path(root)
    _assert_no_active_work(current_database)
    backup_root = _backup_live_state(root)
    # Re-checked immediately before the swap: the backup above takes time, and a
    # run started in that window must not have its database replaced under it.
    _assert_no_active_work(current_database)
    _swap_live_state(root=root, scenario_root=scenario_root, scenario=scenario)

    trigger = _touch_reload_trigger()
    _wait_for_gateway_ready()

    cycles = manifest.get("cycles") if isinstance(manifest.get("cycles"), list) else []
    return {
        "scenario": scenario,
        "backup_root": str(backup_root) if backup_root else None,
        "cycles": cycles,
        "profile": str(_profile_path(root)),
        "hot": True,
        "reload_trigger": str(trigger),
    }


def restore_scenario(
    *,
    manual_root: Path = DEFAULT_MANUAL_ROOT,
    scenario: str,
    hot: bool = False,
) -> dict[str, Any]:
    """Replace only the isolated manual live state with a captured scenario."""
    if hot:
        return _hot_restore_scenario(manual_root=manual_root, scenario=scenario)

    scenario = validate_scenario_name(scenario)
    root = _resolved(manual_root)
    with manual_runtime_lock(manual_root=root):
        scenario_root = root / "scenarios" / scenario
        manifest = _load_manifest(scenario_root)
        _validate_scenario_integrity(scenario_root, manifest)
        if gateway_is_listening():
            raise ManualPipelineError("Gateway port 8001 is listening. Run `make stop` before restoring a manual checkpoint, or swap under the running stack with `make dbtl-manual-restore-hot SCENARIO=<name>`.")

        current_database = _database_path(root)
        _assert_no_active_work(current_database)
        backup_root = _backup_live_state(root)
        _swap_live_state(root=root, scenario_root=scenario_root, scenario=scenario)

    cycles = manifest.get("cycles") if isinstance(manifest.get("cycles"), list) else []
    return {
        "scenario": scenario,
        "backup_root": str(backup_root) if backup_root else None,
        "cycles": cycles,
        "profile": str(_profile_path(root)),
        "hot": False,
    }


def list_scenarios(*, manual_root: Path = DEFAULT_MANUAL_ROOT) -> list[dict[str, Any]]:
    root = _resolved(manual_root) / "scenarios"
    if not root.exists():
        return []
    scenarios: list[dict[str, Any]] = []
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        try:
            manifest = _load_manifest(candidate)
        except ManualPipelineError as exc:
            scenarios.append({"scenario": candidate.name, "error": str(exc)})
            continue
        scenarios.append(manifest)
    return scenarios


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manual-root",
        type=Path,
        default=DEFAULT_MANUAL_ROOT,
        help="Isolated manual-pipeline root (default: .deer-flow/manual-dbtl).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create or refresh the isolated manual-test config.")
    init.add_argument("--source-config", type=Path, default=DEFAULT_SOURCE_CONFIG)
    init.add_argument("--force", action="store_true", help="Regenerate an existing manual config.")

    capture = commands.add_parser("capture", help="Capture the live manual DB and project tree.")
    capture.add_argument("scenario")
    capture.add_argument("--note")
    capture.add_argument("--expected-path-head")
    capture.add_argument("--expected-assessment")
    capture.add_argument(
        "--offered-routes",
        help="Comma-separated stable route slugs expected at this checkpoint.",
    )
    capture.add_argument("--next-action")
    capture.add_argument("--replace", action="store_true")

    restore = commands.add_parser("restore", help="Restore a captured scenario into the isolated live profile.")
    restore.add_argument("scenario")
    restore.add_argument(
        "--hot",
        action="store_true",
        help="Swap under the running manual stack and recycle only the Gateway (requires it to be running).",
    )

    commands.add_parser("dev", help="Run the isolated stack while holding the restore safety lock.")
    commands.add_parser("list", help="List captured scenarios and their resumable URLs.")
    return parser


def _print_scenarios(scenarios: list[dict[str, Any]]) -> None:
    if not scenarios:
        print("No DBTL manual scenarios captured.")
        return
    for manifest in scenarios:
        scenario = manifest.get("scenario", "unknown")
        if manifest.get("error"):
            print(f"{scenario}: INVALID — {manifest['error']}")
            continue
        commit = str(manifest.get("git_commit") or "unknown")[:10]
        captured = manifest.get("captured_at") or "unknown"
        print(f"{scenario}  commit={commit}  captured={captured}")
        expectations = manifest.get("expectations") if isinstance(manifest.get("expectations"), dict) else {}
        expected = [
            f"head={expectations.get('path_head')}" if expectations.get("path_head") else None,
            f"assessment={expectations.get('assessment')}" if expectations.get("assessment") else None,
            ("routes=" + ",".join(expectations.get("offered_routes") or []) if expectations.get("offered_routes") else None),
            f"next={expectations.get('next_action')}" if expectations.get("next_action") else None,
        ]
        expected = [item for item in expected if item]
        if expected:
            print("  expected: " + "  ".join(expected))
        cycles = manifest.get("cycles") if isinstance(manifest.get("cycles"), list) else []
        for cycle in cycles:
            print(f"  {cycle.get('state', '?')}/{cycle.get('surface_mode') or 'no-surface'} rev={cycle.get('db_revision', '?')}  {cycle.get('url', '')}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            profile = initialize_profile(
                source_config=args.source_config,
                manual_root=args.manual_root,
                force=args.force,
            )
            print(f"Manual DBTL profile: {profile}")
            print("Start with: make dbtl-manual-dev")
        elif args.command == "capture":
            manifest = capture_scenario(
                manual_root=args.manual_root,
                scenario=args.scenario,
                note=args.note,
                expected_path_head=args.expected_path_head,
                expected_assessment=args.expected_assessment,
                offered_routes=(args.offered_routes.split(",") if args.offered_routes else None),
                next_action=args.next_action,
                replace=args.replace,
            )
            print(f"Captured DBTL scenario: {manifest.parent.name}")
            print(f"Manifest: {manifest}")
        elif args.command == "restore":
            result = restore_scenario(manual_root=args.manual_root, scenario=args.scenario, hot=args.hot)
            print(f"Restored DBTL scenario: {result['scenario']}")
            if result["backup_root"]:
                print(f"Previous isolated live state backed up to: {result['backup_root']}")
            if result["hot"]:
                print("Gateway reloaded in place; the frontend and proxy kept running.")
            for cycle in result["cycles"]:
                if cycle.get("url"):
                    prefix = "Open" if result["hot"] else "Open after starting DeerFlow"
                    print(f"{prefix}: http://localhost:2026{cycle['url']}")
            if result["hot"]:
                print("HARD-REFRESH the browser (Cmd+Shift+R / Ctrl+Shift+R) so it drops the previous scenario's cached state.")
        elif args.command == "dev":
            if any(os.environ.get(name, "").strip().lower() in {"prod", "production"} for name in ("DEER_FLOW_ENV", "ENVIRONMENT")):
                raise ManualPipelineError("The auth-disabled manual DBTL profile cannot run in an explicit production environment.")
            profile = initialize_profile(manual_root=args.manual_root)
            environment = os.environ.copy()
            environment["DEER_FLOW_CONFIG_PATH"] = str(profile)
            environment["DEER_FLOW_AUTH_DISABLED"] = "1"
            environment["DEER_FLOW_MANUAL_PROFILE"] = "1"
            with manual_runtime_lock(manual_root=args.manual_root, starting_stack=True):
                database = _database_path(args.manual_root)
                _backfill_checkpoint_history(database)
                user_id, user_email = _manual_user(
                    database,
                    email=os.environ.get(MANUAL_USER_EMAIL_ENV_VAR, "").strip() or DEFAULT_MANUAL_USER_EMAIL,
                )
                environment["DEER_FLOW_AUTH_DISABLED_USER_ID"] = user_id
                environment["DEER_FLOW_AUTH_DISABLED_USER_EMAIL"] = user_email
                try:
                    return subprocess.run(["make", "dev"], cwd=REPO_ROOT, env=environment, check=False).returncode
                except KeyboardInterrupt:
                    # The foreground serve script receives the same signal and
                    # performs its own orderly cleanup. Stopping a manual stack
                    # is a successful lifecycle action, not a failed replay.
                    return 0
        elif args.command == "list":
            _print_scenarios(list_scenarios(manual_root=args.manual_root))
        return 0
    except ManualPipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
