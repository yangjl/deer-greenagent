#!/usr/bin/env python3
"""Capture and restore isolated DBTL manual-test checkpoints.

The manual profile keeps its SQLite database and human-visible project folder
under ``.deer-flow/manual-dbtl/live``. A scenario captures both together so the
DBTL revision, evidence, deck, thread, and feedback-surface bindings cannot
drift apart.
"""

from __future__ import annotations

import argparse
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
ACTIVE_TABLE_STATUSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("runs", ("pending", "running")),
    ("scheduled_task_runs", ("queued", "running")),
    ("dbtl_design_feedback_actions", ("pending", "resume_started")),
)


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

    memory = config.setdefault("memory", {})
    if not isinstance(memory, dict):
        memory = {}
        config["memory"] = memory
    memory["enabled"] = False
    memory["injection_enabled"] = False

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


def restore_scenario(
    *,
    manual_root: Path = DEFAULT_MANUAL_ROOT,
    scenario: str,
) -> dict[str, Any]:
    """Replace only the isolated manual live state with a captured scenario."""
    scenario = validate_scenario_name(scenario)
    root = _resolved(manual_root)
    with manual_runtime_lock(manual_root=root):
        scenario_root = root / "scenarios" / scenario
        manifest = _load_manifest(scenario_root)
        _validate_scenario_integrity(scenario_root, manifest)
        if gateway_is_listening():
            raise ManualPipelineError("Gateway port 8001 is listening. Run `make stop` before restoring a manual checkpoint.")

        current_database = _database_path(root)
        _assert_no_active_work(current_database)
        backup_root = _backup_live_state(root)

        live = _live_root(root)
        staging = root / f".restore-{scenario}-{uuid.uuid4().hex}"
        old_live = root / f".live-old-{uuid.uuid4().hex}"
        try:
            (staging / "db").mkdir(parents=True)
            shutil.copy2(scenario_root / DATABASE_FILENAME, staging / "db" / DATABASE_FILENAME)
            _copy_projects(scenario_root / "projects", staging / "projects")

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

    cycles = manifest.get("cycles") if isinstance(manifest.get("cycles"), list) else []
    return {
        "scenario": scenario,
        "backup_root": str(backup_root) if backup_root else None,
        "cycles": cycles,
        "profile": str(_profile_path(root)),
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
    capture.add_argument("--replace", action="store_true")

    restore = commands.add_parser("restore", help="Restore a captured scenario into the isolated live profile.")
    restore.add_argument("scenario")

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
                replace=args.replace,
            )
            print(f"Captured DBTL scenario: {manifest.parent.name}")
            print(f"Manifest: {manifest}")
        elif args.command == "restore":
            result = restore_scenario(manual_root=args.manual_root, scenario=args.scenario)
            print(f"Restored DBTL scenario: {result['scenario']}")
            if result["backup_root"]:
                print(f"Previous isolated live state backed up to: {result['backup_root']}")
            for cycle in result["cycles"]:
                if cycle.get("url"):
                    print(f"Open after starting DeerFlow: http://localhost:2026{cycle['url']}")
        elif args.command == "dev":
            profile = initialize_profile(manual_root=args.manual_root)
            environment = os.environ.copy()
            environment["DEER_FLOW_CONFIG_PATH"] = str(profile)
            with manual_runtime_lock(manual_root=args.manual_root, starting_stack=True):
                return subprocess.run(["make", "dev"], cwd=REPO_ROOT, env=environment, check=False).returncode
        elif args.command == "list":
            _print_scenarios(list_scenarios(manual_root=args.manual_root))
        return 0
    except ManualPipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
