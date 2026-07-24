"""Tests for the per-thread project link store and project-scoped path resolution."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.gateway.path_utils as path_utils
import app.gateway.thread_project as thread_project


def _config_with_mounts(mounts):
    return SimpleNamespace(sandbox=SimpleNamespace(mounts=mounts))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake paths + one configured mount at /mnt/projects → tmp_path/projects."""
    projects_root = tmp_path / "projects"
    (projects_root / "uav").mkdir(parents=True)
    (projects_root / "uav" / "README.md").write_text("hello", encoding="utf-8")
    (projects_root / "other").mkdir()
    (projects_root / ".hidden").mkdir()

    threads_root = tmp_path / "threads"

    import app.gateway.mounts as mounts_module

    mount = SimpleNamespace(host_path=str(projects_root), container_path="/mnt/projects", read_only=False)
    monkeypatch.setattr(mounts_module, "get_app_config", lambda: _config_with_mounts([mount]))

    fake_paths = SimpleNamespace(thread_dir=lambda thread_id, *, user_id=None: threads_root / (user_id or "default") / thread_id)
    monkeypatch.setattr(thread_project, "get_paths", lambda: fake_paths)
    monkeypatch.setattr(thread_project, "get_effective_user_id", lambda: "default")

    return SimpleNamespace(projects_root=projects_root, threads_root=threads_root)


def test_write_and_read_project_link_round_trip(env) -> None:
    written = thread_project.write_project_link("t1", "/mnt/projects/uav", user_id="u1")

    assert written.container_path == "/mnt/projects/uav"
    assert written.host_path == (env.projects_root / "uav").resolve()

    loaded = thread_project.read_project_link("t1", user_id="u1")
    assert loaded == written


def test_read_project_link_returns_none_without_marker(env) -> None:
    assert thread_project.read_project_link("t-none", user_id="u1") is None


def test_write_project_link_rejects_paths_outside_mounts(env) -> None:
    with pytest.raises(ValueError):
        thread_project.write_project_link("t1", "/etc", user_id="u1")


def test_write_project_link_rejects_missing_directories(env) -> None:
    with pytest.raises(ValueError):
        thread_project.write_project_link("t1", "/mnt/projects/nope", user_id="u1")


def test_clear_project_link_is_idempotent(env) -> None:
    thread_project.write_project_link("t1", "/mnt/projects/uav", user_id="u1")
    thread_project.clear_project_link("t1", user_id="u1")
    thread_project.clear_project_link("t1", user_id="u1")

    assert thread_project.read_project_link("t1", user_id="u1") is None


def test_link_deactivates_when_mount_disappears(env, monkeypatch) -> None:
    thread_project.write_project_link("t1", "/mnt/projects/uav", user_id="u1")

    import app.gateway.mounts as mounts_module

    monkeypatch.setattr(mounts_module, "get_app_config", lambda: _config_with_mounts([]))

    assert thread_project.read_project_link("t1", user_id="u1") is None


def test_list_project_candidates_includes_root_and_subdirs(env) -> None:
    candidates = thread_project.list_project_candidates()

    paths = [candidate.container_path for candidate in candidates]
    assert paths == ["/mnt/projects", "/mnt/projects/other", "/mnt/projects/uav"]


def test_candidates_skip_hidden_directories(env) -> None:
    paths = [candidate.container_path for candidate in thread_project.list_project_candidates()]

    assert "/mnt/projects/.hidden" not in paths


def test_resolve_linked_project_path_scopes_to_link(env, monkeypatch) -> None:
    link = thread_project.write_project_link("t1", "/mnt/projects/uav", user_id="u1")
    monkeypatch.setattr(path_utils, "read_project_link", lambda thread_id, user_id=None: link)

    resolved = path_utils.resolve_linked_project_path("t1", "/mnt/projects/uav/README.md", user_id="u1")
    assert resolved == (env.projects_root / "uav" / "README.md").resolve()

    # A sibling project under the same mount is NOT reachable.
    assert path_utils.resolve_linked_project_path("t1", "/mnt/projects/other", user_id="u1") is None


def test_resolve_linked_project_path_none_without_link(monkeypatch) -> None:
    monkeypatch.setattr(path_utils, "read_project_link", lambda thread_id, user_id=None: None)

    assert path_utils.resolve_linked_project_path("t1", "/mnt/projects/uav", user_id="u1") is None


def test_resolve_linked_project_path_rejects_traversal(env, monkeypatch) -> None:
    link = thread_project.write_project_link("t1", "/mnt/projects/uav", user_id="u1")
    monkeypatch.setattr(path_utils, "read_project_link", lambda thread_id, user_id=None: link)

    with pytest.raises(HTTPException) as exc_info:
        path_utils.resolve_linked_project_path("t1", "/mnt/projects/uav/../../secrets", user_id="u1")

    assert exc_info.value.status_code == 403


def test_resolve_thread_virtual_path_uses_link_and_rejects_others(env, monkeypatch) -> None:
    link = thread_project.write_project_link("t1", "/mnt/projects/uav", user_id="u1")
    monkeypatch.setattr(path_utils, "read_project_link", lambda thread_id, user_id=None: link)

    resolved = path_utils.resolve_thread_virtual_path("t1", "/mnt/projects/uav/README.md", user_id="u1")
    assert resolved == (env.projects_root / "uav" / "README.md").resolve()

    with pytest.raises(HTTPException) as exc_info:
        path_utils.resolve_thread_virtual_path("t1", "/mnt/projects/other/file.txt", user_id="u1")
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info:
        path_utils.resolve_thread_virtual_path("t1", "/etc/passwd", user_id="u1")
    assert exc_info.value.status_code == 400
