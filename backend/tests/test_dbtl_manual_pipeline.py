from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dbtl_manual.py"
SPEC = importlib.util.spec_from_file_location("dbtl_manual", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
dbtl_manual = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dbtl_manual)


def _source_config(path: Path) -> Path:
    config = {
        "config_version": 19,
        "models": [{"name": "manual-model", "use": "example:Model", "model": "example"}],
        "database": {"backend": "postgres", "postgres_url": "$DATABASE_URL"},
        "checkpointer": {"type": "postgres", "connection_string": "$DATABASE_URL"},
        "projects": {"root": "/real/projects"},
        "dbtl": {"mode": "manual", "design_deck_feedback": False},
        "memory": {"enabled": True, "injection_enabled": True},
        "scheduler": {"enabled": True},
        "run_ownership": {"heartbeat_enabled": True},
        "channel_connections": {"enabled": True},
        "channels": {
            "slack": {"enabled": True, "bot_token": "$SLACK_BOT_TOKEN"},
            "telegram": {"enabled": True, "bot_token": "$TELEGRAM_BOT_TOKEN"},
        },
    }
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def _create_live_database(path: Path, *, active_run: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE alembic_version (version_num TEXT NOT NULL);
            INSERT INTO alembic_version VALUES ('0023_dbtl_design_feedback_actions');

            CREATE TABLE runs (run_id TEXT PRIMARY KEY, status TEXT NOT NULL);
            CREATE TABLE scheduled_task_runs (id TEXT PRIMARY KEY, status TEXT NOT NULL);
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                root_path TEXT
            );
            CREATE TABLE dbtl_cycles (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                state TEXT NOT NULL,
                db_revision INTEGER NOT NULL
            );
            CREATE TABLE dbtl_stage_runs (
                id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE dbtl_design_feedback_surfaces (
                id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL,
                originating_thread_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                superseded_by_surface_id TEXT
            );
            """
        )
        conn.execute("INSERT INTO runs VALUES ('run-1', ?)", ("running" if active_run else "success",))
        conn.execute("INSERT INTO projects VALUES ('project-1', 'Maize', 'maize', ?)", (str(path.parents[1] / "projects" / "Maize"),))
        conn.execute("INSERT INTO dbtl_cycles VALUES ('cycle-1', 'project-1', 'Drought design', 'design', 7)")
        conn.execute("INSERT INTO dbtl_stage_runs VALUES ('attempt-1', 'cycle-1', 'design', 'awaiting_review')")
        conn.execute("INSERT INTO dbtl_design_feedback_surfaces VALUES ('surface-1', 'cycle-1', 'thread-1', 'stage_review', NULL)")


def test_profile_is_isolated_and_disables_background_writers(tmp_path: Path) -> None:
    source = _source_config(tmp_path / "config.yaml")
    manual_root = tmp_path / ".deer-flow" / "manual-dbtl"

    profile = dbtl_manual.initialize_profile(source_config=source, manual_root=manual_root)

    generated = yaml.safe_load(profile.read_text(encoding="utf-8"))
    assert generated["models"][0]["name"] == "manual-model"
    assert generated["database"] == {
        "backend": "sqlite",
        "sqlite_dir": str((manual_root / "live" / "db").resolve()),
    }
    assert "checkpointer" not in generated
    assert generated["projects"]["root"] == str((manual_root / "live" / "projects").resolve())
    assert generated["dbtl"]["mode"] == "graph_enabled"
    assert generated["dbtl"]["design_deck_feedback"] is True
    assert generated["memory"]["enabled"] is False
    assert generated["memory"]["injection_enabled"] is False
    assert generated["scheduler"]["enabled"] is False
    assert generated["run_ownership"]["heartbeat_enabled"] is False
    assert generated["channel_connections"]["enabled"] is False
    assert generated["channels"]["slack"]["enabled"] is False
    assert generated["channels"]["telegram"]["enabled"] is False

    original = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert original["database"]["backend"] == "postgres"
    assert original["channels"]["slack"]["enabled"] is True


@pytest.mark.parametrize(
    "name",
    ["../escape", "/absolute", "has space", "", "UPPER", "a" * 65],
)
def test_scenario_names_cannot_escape_the_manual_root(name: str) -> None:
    with pytest.raises(dbtl_manual.ManualPipelineError, match="scenario name"):
        dbtl_manual.validate_scenario_name(name)


def test_capture_refuses_a_database_with_active_runs(tmp_path: Path) -> None:
    manual_root = tmp_path / "manual"
    _create_live_database(manual_root / "live" / "db" / "deerflow.db", active_run=True)
    (manual_root / "live" / "projects").mkdir(parents=True)

    with pytest.raises(dbtl_manual.ManualPipelineError, match="active run"):
        dbtl_manual.capture_scenario(manual_root=manual_root, scenario="chair-choice")


def test_capture_keeps_database_and_project_files_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual_root = tmp_path / "manual"
    database = manual_root / "live" / "db" / "deerflow.db"
    projects = manual_root / "live" / "projects"
    _create_live_database(database)
    deck = projects / "Maize" / "outputs" / "dbtl" / "design-slides.html"
    deck.parent.mkdir(parents=True)
    deck.write_text("<html>trusted deck</html>", encoding="utf-8")
    monkeypatch.setattr(dbtl_manual, "_git_commit", lambda: "a" * 40)

    manifest_path = dbtl_manual.capture_scenario(
        manual_root=manual_root,
        scenario="awaiting-review",
        note="Ready to exercise deck verdicts.",
    )

    scenario_root = manual_root / "scenarios" / "awaiting-review"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    captured_db = scenario_root / "deerflow.db"
    assert captured_db.is_file()
    assert (scenario_root / "projects" / "Maize" / "outputs" / "dbtl" / "design-slides.html").read_text() == ("<html>trusted deck</html>")
    assert manifest["scenario"] == "awaiting-review"
    assert manifest["git_commit"] == "a" * 40
    assert manifest["database_sha256"] == hashlib.sha256(captured_db.read_bytes()).hexdigest()
    assert manifest["alembic_version"] == "0023_dbtl_design_feedback_actions"
    assert manifest["note"] == "Ready to exercise deck verdicts."
    assert manifest["cycles"] == [
        {
            "cycle_id": "cycle-1",
            "db_revision": 7,
            "project_id": "project-1",
            "project_slug": "maize",
            "state": "design",
            "surface_id": "surface-1",
            "surface_mode": "stage_review",
            "thread_id": "thread-1",
            "title": "Drought design",
            "url": "/workspace/maize/thread-1",
        }
    ]


def test_restore_replaces_only_the_manual_live_state_and_keeps_a_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual_root = tmp_path / "manual"
    database = manual_root / "live" / "db" / "deerflow.db"
    projects = manual_root / "live" / "projects"
    _create_live_database(database)
    project_file = projects / "Maize" / "result.txt"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("captured", encoding="utf-8")
    monkeypatch.setattr(dbtl_manual, "_git_commit", lambda: "b" * 40)
    dbtl_manual.capture_scenario(manual_root=manual_root, scenario="design-complete")

    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE dbtl_cycles SET state = 'reconciliation'")
        conn.commit()
    project_file.write_text("changed later", encoding="utf-8")
    monkeypatch.setattr(dbtl_manual, "gateway_is_listening", lambda: False)

    result = dbtl_manual.restore_scenario(manual_root=manual_root, scenario="design-complete")

    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT state FROM dbtl_cycles").fetchone()[0] == "design"
    assert project_file.read_text(encoding="utf-8") == "captured"
    backup_root = Path(result["backup_root"])
    assert backup_root.is_dir()
    with sqlite3.connect(backup_root / "deerflow.db") as conn:
        assert conn.execute("SELECT state FROM dbtl_cycles").fetchone()[0] == "reconciliation"
    assert (backup_root / "projects" / "Maize" / "result.txt").read_text(encoding="utf-8") == "changed later"


def test_restore_refuses_while_the_gateway_is_listening(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual_root = tmp_path / "manual"
    database = manual_root / "live" / "db" / "deerflow.db"
    _create_live_database(database)
    (manual_root / "live" / "projects").mkdir(parents=True)
    monkeypatch.setattr(dbtl_manual, "_git_commit", lambda: "c" * 40)
    dbtl_manual.capture_scenario(manual_root=manual_root, scenario="chair-choice")
    monkeypatch.setattr(dbtl_manual, "gateway_is_listening", lambda: True)

    with pytest.raises(dbtl_manual.ManualPipelineError, match="make stop"):
        dbtl_manual.restore_scenario(manual_root=manual_root, scenario="chair-choice")


def test_restore_refuses_while_the_manual_runtime_lock_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manual_root = tmp_path / "manual"
    database = manual_root / "live" / "db" / "deerflow.db"
    _create_live_database(database)
    (manual_root / "live" / "projects").mkdir(parents=True)
    monkeypatch.setattr(dbtl_manual, "_git_commit", lambda: "e" * 40)
    dbtl_manual.capture_scenario(manual_root=manual_root, scenario="chair-choice")
    monkeypatch.setattr(dbtl_manual, "gateway_is_listening", lambda: False)

    with dbtl_manual.manual_runtime_lock(manual_root=manual_root):
        with pytest.raises(dbtl_manual.ManualPipelineError, match="make stop"):
            dbtl_manual.restore_scenario(manual_root=manual_root, scenario="chair-choice")


def test_second_manual_launcher_reports_that_the_stack_is_already_running(tmp_path: Path) -> None:
    with dbtl_manual.manual_runtime_lock(manual_root=tmp_path):
        with pytest.raises(dbtl_manual.ManualPipelineError, match="already running"):
            with dbtl_manual.manual_runtime_lock(manual_root=tmp_path, starting_stack=True):
                pass


def test_tampered_scenario_database_is_not_restored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual_root = tmp_path / "manual"
    database = manual_root / "live" / "db" / "deerflow.db"
    _create_live_database(database)
    (manual_root / "live" / "projects").mkdir(parents=True)
    monkeypatch.setattr(dbtl_manual, "_git_commit", lambda: "d" * 40)
    dbtl_manual.capture_scenario(manual_root=manual_root, scenario="chair-choice")
    (manual_root / "scenarios" / "chair-choice" / "deerflow.db").write_bytes(b"tampered")
    monkeypatch.setattr(dbtl_manual, "gateway_is_listening", lambda: False)

    with pytest.raises(dbtl_manual.ManualPipelineError, match="integrity"):
        dbtl_manual.restore_scenario(manual_root=manual_root, scenario="chair-choice")
