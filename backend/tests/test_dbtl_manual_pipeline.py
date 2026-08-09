from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

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


def _create_live_database(path: Path, *, active_run: bool = False, feedback_action_status: str | None = None) -> None:
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
            CREATE TABLE dbtl_design_feedback_actions (
                id TEXT PRIMARY KEY,
                surface_id TEXT NOT NULL,
                action_kind TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute("INSERT INTO runs VALUES ('run-1', ?)", ("running" if active_run else "success",))
        if feedback_action_status is not None:
            conn.execute(
                "INSERT INTO dbtl_design_feedback_actions VALUES ('action-1', 'surface-1', 'chair_option', ?)",
                (feedback_action_status,),
            )
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
    assert generated["run_events"]["backend"] == "db"
    assert "checkpointer" not in generated
    assert generated["projects"]["root"] == str((manual_root / "live" / "projects").resolve())
    assert generated["dbtl"]["mode"] == "graph_enabled"
    assert generated["dbtl"]["design_deck_feedback"] is True
    assert generated["sandbox"]["allow_host_bash"] is True
    assert generated["dbtl"]["conversational_discovery"] is True
    assert generated["dbtl"]["discovery_project_history"] is True
    assert generated["dbtl"]["discovery_global_memory"] is False
    assert generated["dbtl"]["discovery_classifier_entry"] is False
    assert generated["dbtl"]["discovery_auto_offer"] is False
    assert generated["dbtl"]["degraded_evidence_continuation"] is True
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


def test_profile_enables_memory_for_opted_in_craft_specialists(tmp_path: Path) -> None:
    source = _source_config(tmp_path / "config.yaml")
    source_config = yaml.safe_load(source.read_text(encoding="utf-8"))
    source_config["subagents"] = {
        "custom_agents": {
            "build-engineer": {"description": "Build specialist", "craft_memory": True},
        }
    }
    source.write_text(yaml.safe_dump(source_config), encoding="utf-8")

    profile = dbtl_manual.initialize_profile(
        source_config=source,
        manual_root=tmp_path / ".deer-flow" / "manual-dbtl",
    )

    generated = yaml.safe_load(profile.read_text(encoding="utf-8"))
    assert generated["memory"]["enabled"] is True
    assert generated["memory"]["injection_enabled"] is True


def test_existing_manual_profile_is_upgraded_to_persistent_history_without_force(tmp_path: Path) -> None:
    source = _source_config(tmp_path / "config.yaml")
    manual_root = tmp_path / ".deer-flow" / "manual-dbtl"
    profile = dbtl_manual.initialize_profile(source_config=source, manual_root=manual_root)
    generated = yaml.safe_load(profile.read_text(encoding="utf-8"))
    generated["run_events"] = {"backend": "memory", "track_token_usage": True}
    generated["sandbox"]["allow_host_bash"] = False
    generated["dbtl"].pop("degraded_evidence_continuation")
    profile.write_text(yaml.safe_dump(generated), encoding="utf-8")

    same_profile = dbtl_manual.initialize_profile(source_config=source, manual_root=manual_root)

    upgraded = yaml.safe_load(same_profile.read_text(encoding="utf-8"))
    assert same_profile == profile
    assert upgraded["run_events"] == {"backend": "db", "track_token_usage": True}
    assert upgraded["sandbox"]["allow_host_bash"] is True
    assert upgraded["dbtl"]["degraded_evidence_continuation"] is True


def test_existing_profile_hot_adds_source_specialists_only_when_roster_is_empty(
    tmp_path: Path,
) -> None:
    source = _source_config(tmp_path / "config.yaml")
    source_config = yaml.safe_load(source.read_text(encoding="utf-8"))
    source_config["subagents"] = {
        "custom_agents": {
            "build-engineer": {"description": "Build specialist"},
            "statistician": {"description": "Statistics specialist"},
        }
    }
    source.write_text(yaml.safe_dump(source_config), encoding="utf-8")
    manual_root = tmp_path / ".deer-flow" / "manual-dbtl"
    profile = dbtl_manual.initialize_profile(
        source_config=source,
        manual_root=manual_root,
    )
    generated = yaml.safe_load(profile.read_text(encoding="utf-8"))
    generated["subagents"] = {"custom_agents": {}}
    profile.write_text(yaml.safe_dump(generated), encoding="utf-8")

    dbtl_manual.initialize_profile(source_config=source, manual_root=manual_root)

    upgraded = yaml.safe_load(profile.read_text(encoding="utf-8"))
    assert set(upgraded["subagents"]["custom_agents"]) == {
        "build-engineer",
        "statistician",
    }


def test_legacy_empty_event_feed_is_backfilled_once_from_latest_checkpoint(tmp_path: Path) -> None:
    database = tmp_path / "deerflow.db"
    payload_type, payload = JsonPlusSerializer().dumps_typed(
        {
            "channel_values": {
                "messages": [
                    HumanMessage(content="earlier question", id="human-1"),
                    AIMessage(content="earlier answer", id="ai-1"),
                ]
            }
        }
    )
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE threads_meta (
                thread_id TEXT PRIMARY KEY,
                user_id TEXT
            );
            CREATE TABLE checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                type TEXT,
                checkpoint BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            );
            CREATE TABLE run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                user_id TEXT,
                event_type TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                event_metadata TEXT NOT NULL,
                seq INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (thread_id, seq)
            );
            INSERT INTO threads_meta VALUES ('thread-1', 'owner-1');
            """
        )
        conn.execute(
            "INSERT INTO checkpoints VALUES ('thread-1', '', 'checkpoint-1', ?, ?)",
            (payload_type, payload),
        )

    assert dbtl_manual._backfill_checkpoint_history(database) == 2
    assert dbtl_manual._backfill_checkpoint_history(database) == 0

    with sqlite3.connect(database) as conn:
        rows = conn.execute("SELECT user_id, event_type, seq, content, event_metadata FROM run_events ORDER BY seq").fetchall()
    assert [(row[0], row[1], row[2]) for row in rows] == [
        ("owner-1", "llm.human.input", 1),
        ("owner-1", "llm.ai.response", 2),
    ]
    assert [json.loads(row[3])["id"] for row in rows] == ["human-1", "ai-1"]
    assert all(json.loads(row[4])["manual_restore_seed"] is True for row in rows)


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


def test_capture_refuses_a_feedback_action_that_never_reached_its_run(tmp_path: Path) -> None:
    """A `pending` deck action has an unknown outcome, so the checkpoint is not quiescent."""
    manual_root = tmp_path / "manual"
    _create_live_database(manual_root / "live" / "db" / "deerflow.db", feedback_action_status="pending")
    (manual_root / "live" / "projects").mkdir(parents=True)

    with pytest.raises(dbtl_manual.ManualPipelineError, match="dbtl_design_feedback_actions=1"):
        dbtl_manual.capture_scenario(manual_root=manual_root, scenario="chair-choice")


@pytest.mark.parametrize("status", ["resume_started", "accepted", "review_recorded"])
def test_a_settled_feedback_action_does_not_block_the_pipeline_forever(tmp_path: Path, status: str) -> None:
    """`resume_started` is terminal for the ledger; the run it launched is tracked in `runs`.

    Counting it as active work wedged the whole pipeline: the row is never advanced,
    so one answered chair question permanently refused every later capture and restore.
    """
    manual_root = tmp_path / "manual"
    _create_live_database(manual_root / "live" / "db" / "deerflow.db", feedback_action_status=status)
    (manual_root / "live" / "projects").mkdir(parents=True)

    manifest = dbtl_manual.capture_scenario(manual_root=manual_root, scenario="chair-choice")

    assert (manifest.parent / "deerflow.db").exists()


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
        expected_path_head="design",
        expected_assessment="standard",
        offered_routes=["approve", "request_changes", "approve"],
        next_action="Approve the registered Design deck.",
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
    assert manifest["expectations"] == {
        "path_head": "design",
        "assessment": "standard",
        "offered_routes": ["approve", "request_changes"],
        "next_action": "Approve the registered Design deck.",
    }
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


def _captured_manual_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, scenario: str) -> Path:
    manual_root = tmp_path / "manual"
    database = manual_root / "live" / "db" / "deerflow.db"
    projects = manual_root / "live" / "projects"
    _create_live_database(database)
    project_file = projects / "Maize" / "result.txt"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("captured", encoding="utf-8")
    monkeypatch.setattr(dbtl_manual, "_git_commit", lambda: "f" * 40)
    dbtl_manual.capture_scenario(manual_root=manual_root, scenario=scenario)
    return manual_root


def test_hot_restore_refuses_when_the_manual_stack_is_not_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual_root = _captured_manual_root(tmp_path, monkeypatch, scenario="chair-choice")
    monkeypatch.setattr(dbtl_manual, "gateway_is_listening", lambda: False)
    monkeypatch.setattr(dbtl_manual, "_touch_reload_trigger", lambda *args, **kwargs: pytest.fail("must not reload"))

    with pytest.raises(dbtl_manual.ManualPipelineError, match="not running"):
        dbtl_manual.restore_scenario(manual_root=manual_root, scenario="chair-choice", hot=True)


def test_hot_restore_refuses_while_the_live_database_is_not_quiescent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual_root = _captured_manual_root(tmp_path, monkeypatch, scenario="chair-choice")
    database = manual_root / "live" / "db" / "deerflow.db"
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE runs SET status = 'running'")
        conn.commit()
    monkeypatch.setattr(dbtl_manual, "gateway_is_listening", lambda: True)
    monkeypatch.setattr(dbtl_manual, "_touch_reload_trigger", lambda *args, **kwargs: pytest.fail("must not reload"))

    with pytest.raises(dbtl_manual.ManualPipelineError, match="active run"):
        dbtl_manual.restore_scenario(manual_root=manual_root, scenario="chair-choice", hot=True)

    # The live state must be untouched by a refused hot swap.
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT status FROM runs").fetchone()[0] == "running"


def test_hot_restore_swaps_the_pair_and_reloads_only_the_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual_root = _captured_manual_root(tmp_path, monkeypatch, scenario="design-complete")
    database = manual_root / "live" / "db" / "deerflow.db"
    project_file = manual_root / "live" / "projects" / "Maize" / "result.txt"
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE dbtl_cycles SET state = 'reconciliation'")
        conn.commit()
    project_file.write_text("changed later", encoding="utf-8")

    trigger = tmp_path / "reload_trigger.py"
    trigger.write_text("", encoding="utf-8")
    reloaded: list[Path] = []
    waited: list[bool] = []
    monkeypatch.setattr(dbtl_manual, "gateway_is_listening", lambda: True)
    monkeypatch.setattr(dbtl_manual, "_touch_reload_trigger", lambda path=trigger: (reloaded.append(path), path)[1])
    monkeypatch.setattr(dbtl_manual, "_wait_for_gateway_ready", lambda **kwargs: waited.append(True))

    result = dbtl_manual.restore_scenario(manual_root=manual_root, scenario="design-complete", hot=True)

    assert result["hot"] is True
    assert reloaded == [trigger]
    assert waited == [True]
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT state FROM dbtl_cycles").fetchone()[0] == "design"
    assert project_file.read_text(encoding="utf-8") == "captured"
    backup_root = Path(result["backup_root"])
    with sqlite3.connect(backup_root / "deerflow.db") as conn:
        assert conn.execute("SELECT state FROM dbtl_cycles").fetchone()[0] == "reconciliation"
    assert (backup_root / "projects" / "Maize" / "result.txt").read_text(encoding="utf-8") == "changed later"


def test_cold_restore_still_refuses_while_running_and_never_reloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual_root = _captured_manual_root(tmp_path, monkeypatch, scenario="chair-choice")
    monkeypatch.setattr(dbtl_manual, "gateway_is_listening", lambda: True)
    monkeypatch.setattr(dbtl_manual, "_touch_reload_trigger", lambda *args, **kwargs: pytest.fail("must not reload"))

    with pytest.raises(dbtl_manual.ManualPipelineError, match="make stop"):
        dbtl_manual.restore_scenario(manual_root=manual_root, scenario="chair-choice")


def test_gateway_readiness_probe_treats_an_unauthenticated_401_as_up(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def _unauthorized(*args: object, **kwargs: object) -> None:
        raise urllib.error.HTTPError("http://127.0.0.1:8001/api/features", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(dbtl_manual.urllib.request, "urlopen", _unauthorized)
    assert dbtl_manual._gateway_is_ready() is True

    def _refused(*args: object, **kwargs: object) -> None:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(dbtl_manual.urllib.request, "urlopen", _refused)
    assert dbtl_manual._gateway_is_ready() is False


def test_the_reload_trigger_is_a_watched_backend_source_file() -> None:
    trigger = dbtl_manual.RELOAD_TRIGGER_PATH
    assert trigger.is_file(), trigger
    backend = Path(dbtl_manual.REPO_ROOT) / "backend"
    relative = trigger.relative_to(backend)
    # The dev launcher excludes tests/**, .deer-flow, and sandbox from the watcher.
    assert relative.parts[0] not in {"tests", ".deer-flow", "sandbox"}
    assert trigger.read_bytes() == b"", "the trigger must stay empty; only its mtime is ever changed"


def test_manual_dev_pins_test_auth_and_config_in_the_child_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "manual-config.yaml"
    profile.write_text("config_version: 19\n", encoding="utf-8")
    captured: dict[str, str] = {}
    monkeypatch.setattr(dbtl_manual, "initialize_profile", lambda **_kwargs: profile)
    monkeypatch.setattr(dbtl_manual, "manual_runtime_lock", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(dbtl_manual, "_backfill_checkpoint_history", lambda _path: 0)
    monkeypatch.setattr(dbtl_manual, "_manual_user", lambda *_args, **_kwargs: ("manual-user", "dbtl-linear-10m@example.com"))
    monkeypatch.setattr(
        dbtl_manual.subprocess,
        "run",
        lambda *_args, **kwargs: (captured.update(kwargs["env"]), SimpleNamespace(returncode=0))[1],
    )
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "0")

    assert dbtl_manual.main(["--manual-root", str(tmp_path), "dev"]) == 0
    assert captured["DEER_FLOW_CONFIG_PATH"] == str(profile)
    assert captured["DEER_FLOW_AUTH_DISABLED"] == "1"
    assert captured["DEER_FLOW_MANUAL_PROFILE"] == "1"
    assert captured["DEER_FLOW_AUTH_DISABLED_USER_ID"] == "manual-user"
    assert captured["DEER_FLOW_AUTH_DISABLED_USER_EMAIL"] == "dbtl-linear-10m@example.com"


def test_manual_replay_user_is_resolved_from_the_isolated_checkpoint(tmp_path: Path) -> None:
    database = tmp_path / "deerflow.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO users (id, email) VALUES (?, ?)",
            ("user-10m", "dbtl-linear-10m@example.com"),
        )

    assert dbtl_manual._manual_user(database, email="DBTL-LINEAR-10M@example.com") == (
        "user-10m",
        "dbtl-linear-10m@example.com",
    )

    with pytest.raises(dbtl_manual.ManualPipelineError, match="has no user"):
        dbtl_manual._manual_user(database, email="missing@example.com")


def test_manual_dev_services_are_loopback_only_and_restore_pinned_env_after_dotenv() -> None:
    repo = SCRIPT_PATH.parents[1]
    serve = (repo / "scripts" / "serve.sh").read_text(encoding="utf-8")
    nginx = (repo / "docker" / "nginx" / "nginx.local.conf").read_text(encoding="utf-8")

    assert 'DEER_FLOW_MANUAL_PROFILE_INHERITED="${DEER_FLOW_MANUAL_PROFILE:-}"' in serve
    assert 'export DEER_FLOW_AUTH_DISABLED="1"' in serve
    assert 'export DEER_FLOW_AUTH_DISABLED_USER_ID="$DEER_FLOW_MANUAL_USER_ID_INHERITED"' in serve
    assert "--host 127.0.0.1 --port 8001" in serve
    assert "run dev --hostname 127.0.0.1" in serve
    assert "run dev -- --hostname" not in serve
    assert "listen 127.0.0.1:2026;" in nginx
    assert "listen [::1]:2026;" in nginx


def test_normal_signal_shutdown_is_not_reported_as_a_failed_manual_run() -> None:
    serve = (SCRIPT_PATH.parents[1] / "scripts" / "serve.sh").read_text(encoding="utf-8")

    assert "trap 'cleanup 0' INT" in serve
    assert "trap 'cleanup 0' TERM" in serve


def test_manual_dev_treats_keyboard_interrupt_as_a_clean_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "manual-config.yaml"
    profile.write_text("config_version: 45\n", encoding="utf-8")
    monkeypatch.setattr(dbtl_manual, "initialize_profile", lambda **_kwargs: profile)
    monkeypatch.setattr(dbtl_manual, "manual_runtime_lock", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(dbtl_manual, "_backfill_checkpoint_history", lambda _path: 0)
    monkeypatch.setattr(dbtl_manual, "_manual_user", lambda *_args, **_kwargs: ("manual-user", "dbtl-linear-10m@example.com"))

    def _interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(dbtl_manual.subprocess, "run", _interrupt)

    assert dbtl_manual.main(["--manual-root", str(tmp_path), "dev"]) == 0


@pytest.mark.parametrize("name", ["DEER_FLOW_ENV", "ENVIRONMENT"])
def test_manual_dev_refuses_an_explicit_production_environment(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv(name, "production")
    monkeypatch.setattr(dbtl_manual.subprocess, "run", _run)

    assert dbtl_manual.main(["--manual-root", str(tmp_path), "dev"]) == 2
    assert called is False


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
