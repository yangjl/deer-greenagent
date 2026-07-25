"""Tests for the thread workspace file-listing router (GET /api/threads/{id}/files)."""

import asyncio
from types import SimpleNamespace

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

import app.gateway.routers.files as files_router
from deerflow.uploads import UPLOAD_STAGING_PREFIX, UPLOAD_STAGING_SUFFIX


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient over an authed app; virtual paths resolve into tmp_path."""

    def fake_resolve(_thread_id: str, virtual_path: str, user_id=None, project_root=None):
        stripped = virtual_path.lstrip("/")
        prefix = "mnt/user-data"
        if stripped != prefix and not stripped.startswith(prefix + "/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="Path must start with /mnt/user-data")
        relative = stripped[len(prefix) :].lstrip("/")
        resolved = (tmp_path / relative).resolve()
        if not str(resolved).startswith(str(tmp_path.resolve())):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Access denied: path traversal detected")
        return resolved

    monkeypatch.setattr(files_router, "resolve_thread_virtual_path", fake_resolve)
    # Hermetic default: no project link and no real config.yaml mounts; tests
    # opt in by re-patching read_project_link.
    monkeypatch.setattr(files_router, "read_project_link", lambda _thread_id, user_id=None: None)

    test_app = make_authed_test_app()
    test_app.include_router(files_router.router)
    with TestClient(test_app) as test_client:
        yield test_client


def _entry_names(payload: dict) -> list[str]:
    return [entry["name"] for entry in payload["entries"]]


def test_list_files_returns_entries_with_metadata(client, tmp_path) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "workspace" / "sub").mkdir()

    response = client.get("/api/threads/t1/files", params={"path": "/mnt/user-data/workspace"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "/mnt/user-data/workspace"
    assert payload["truncated"] is False

    by_name = {entry["name"]: entry for entry in payload["entries"]}
    assert set(by_name) == {"sub", "notes.txt"}

    file_entry = by_name["notes.txt"]
    assert file_entry["type"] == "file"
    assert file_entry["path"] == "/mnt/user-data/workspace/notes.txt"
    assert file_entry["size"] == 5
    assert isinstance(file_entry["modified_at"], str)

    dir_entry = by_name["sub"]
    assert dir_entry["type"] == "directory"
    assert dir_entry["path"] == "/mnt/user-data/workspace/sub"
    assert dir_entry["size"] is None


def test_list_files_defaults_to_user_data_root(client, tmp_path) -> None:
    for name in ("workspace", "uploads", "outputs"):
        (tmp_path / name).mkdir()

    response = client.get("/api/threads/t1/files")

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "/mnt/user-data"
    assert set(_entry_names(payload)) == {"workspace", "uploads", "outputs"}


def test_list_files_sorts_directories_first_then_names(client, tmp_path) -> None:
    (tmp_path / "b-file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "a-file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "z-dir").mkdir()
    (tmp_path / "a-dir").mkdir()

    response = client.get("/api/threads/t1/files", params={"path": "/mnt/user-data"})

    assert _entry_names(response.json()) == ["a-dir", "z-dir", "a-file.txt", "b-file.txt"]


def test_list_files_hides_upload_staging_files(client, tmp_path) -> None:
    (tmp_path / "real.txt").write_text("x", encoding="utf-8")
    (tmp_path / f"{UPLOAD_STAGING_PREFIX}abc{UPLOAD_STAGING_SUFFIX}").write_text("x", encoding="utf-8")

    response = client.get("/api/threads/t1/files", params={"path": "/mnt/user-data"})

    assert _entry_names(response.json()) == ["real.txt"]


def test_list_files_404_for_missing_directory(client) -> None:
    response = client.get("/api/threads/t1/files", params={"path": "/mnt/user-data/nope"})

    assert response.status_code == 404


def test_list_files_missing_root_returns_empty_listing_with_linked_project(tmp_path, monkeypatch) -> None:
    """A never-run thread has no user-data dir yet; the root must still render."""
    missing_root = tmp_path / "does-not-exist"

    def fake_resolve(_thread_id: str, _virtual_path: str, user_id=None, project_root=None):
        return missing_root

    monkeypatch.setattr(files_router, "resolve_thread_virtual_path", fake_resolve)
    project_dir = tmp_path / "external-project"
    project_dir.mkdir()
    monkeypatch.setattr(files_router, "read_project_link", lambda _thread_id, user_id=None: _fake_link(project_dir))

    test_app = make_authed_test_app()
    test_app.include_router(files_router.router)
    with TestClient(test_app) as test_client:
        response = test_client.get("/api/threads/t1/files")

    assert response.status_code == 200
    payload = response.json()
    assert _entry_names(payload) == ["external-project"]
    assert payload["truncated"] is False


def test_get_thread_project_returns_null_without_link(client) -> None:
    response = client.get("/api/threads/t1/project")

    assert response.status_code == 200
    assert response.json() == {"project": None}


def test_put_thread_project_links_and_returns_project(client, tmp_path, monkeypatch) -> None:
    project_dir = tmp_path / "uav"
    project_dir.mkdir()

    captured: dict = {}

    def fake_write(thread_id, container_path, user_id=None):
        captured["args"] = (thread_id, container_path)
        return _fake_link(project_dir, "/mnt/projects/uav")

    monkeypatch.setattr(files_router, "write_project_link", fake_write)

    response = client.put("/api/threads/t1/project", json={"container_path": "/mnt/projects/uav"})

    assert response.status_code == 200
    assert response.json()["project"] == {"container_path": "/mnt/projects/uav", "name": "uav"}
    assert captured["args"] == ("t1", "/mnt/projects/uav")


def test_put_thread_project_rejects_invalid_path(client, monkeypatch) -> None:
    def fake_write(thread_id, container_path, user_id=None):
        raise ValueError("Path is not an existing directory under a configured sandbox mount")

    monkeypatch.setattr(files_router, "write_project_link", fake_write)

    response = client.put("/api/threads/t1/project", json={"container_path": "/mnt/nope"})

    assert response.status_code == 400


def test_delete_thread_project_clears_link(client, monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(files_router, "clear_project_link", lambda thread_id, user_id=None: calls.append(thread_id))

    response = client.delete("/api/threads/t1/project")

    assert response.status_code == 200
    assert response.json() == {"project": None}
    assert calls == ["t1"]


def test_get_thread_project_candidates(client, tmp_path, monkeypatch) -> None:
    project_dir = tmp_path / "uav"
    project_dir.mkdir()
    monkeypatch.setattr(files_router, "list_project_candidates", lambda: [_fake_link(project_dir, "/mnt/projects/uav")])

    response = client.get("/api/threads/t1/project/candidates")

    assert response.status_code == 200
    assert response.json() == {"candidates": [{"container_path": "/mnt/projects/uav", "name": "uav"}]}


def test_list_files_400_when_path_is_a_file(client, tmp_path) -> None:
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")

    response = client.get("/api/threads/t1/files", params={"path": "/mnt/user-data/file.txt"})

    assert response.status_code == 400


def test_list_files_400_for_non_virtual_path(client) -> None:
    response = client.get("/api/threads/t1/files", params={"path": "/etc"})

    assert response.status_code == 400


def test_list_files_403_for_path_traversal(client) -> None:
    response = client.get("/api/threads/t1/files", params={"path": "/mnt/user-data/../../etc"})

    assert response.status_code in (400, 403)


def test_list_files_truncates_at_max_entries(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(files_router, "MAX_ENTRIES", 3)
    for i in range(5):
        (tmp_path / f"file-{i}.txt").write_text("x", encoding="utf-8")

    response = client.get("/api/threads/t1/files", params={"path": "/mnt/user-data"})

    payload = response.json()
    assert payload["truncated"] is True
    assert len(payload["entries"]) == 3


def test_list_files_marks_symlinks(client, tmp_path) -> None:
    (tmp_path / "target.txt").write_text("x", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(tmp_path / "target.txt")

    response = client.get("/api/threads/t1/files", params={"path": "/mnt/user-data"})

    by_name = {entry["name"]: entry for entry in response.json()["entries"]}
    assert by_name["link.txt"]["is_symlink"] is True
    assert by_name["target.txt"]["is_symlink"] is False


def _fake_link(project_dir, container_path="/mnt/projects/external-project"):
    return SimpleNamespace(container_path=container_path, host_path=project_dir, name=project_dir.name)


def test_list_files_root_includes_linked_project(client, tmp_path, monkeypatch) -> None:
    (tmp_path / "workspace").mkdir()
    project_dir = tmp_path / "external-project"
    project_dir.mkdir()
    monkeypatch.setattr(files_router, "read_project_link", lambda _thread_id, user_id=None: _fake_link(project_dir))

    response = client.get("/api/threads/t1/files")

    by_name = {entry["name"]: entry for entry in response.json()["entries"]}
    assert set(by_name) == {"workspace", "external-project"}
    project_entry = by_name["external-project"]
    assert project_entry["type"] == "directory"
    assert project_entry["path"] == "/mnt/projects/external-project"


def test_list_files_root_without_link_shows_only_user_data(client, tmp_path) -> None:
    (tmp_path / "workspace").mkdir()

    response = client.get("/api/threads/t1/files")

    assert _entry_names(response.json()) == ["workspace"]


def test_list_files_non_root_does_not_include_project(client, tmp_path, monkeypatch) -> None:
    (tmp_path / "workspace").mkdir()
    project_dir = tmp_path / "external-project"
    project_dir.mkdir()
    monkeypatch.setattr(files_router, "read_project_link", lambda _thread_id, user_id=None: _fake_link(project_dir))

    response = client.get("/api/threads/t1/files", params={"path": "/mnt/user-data/workspace"})

    assert _entry_names(response.json()) == []


def test_list_files_scandir_runs_off_event_loop(client, tmp_path, monkeypatch) -> None:
    """The directory scan must be offloaded (asyncio.to_thread), not run inline."""
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")

    calls: list[str] = []
    original_to_thread = asyncio.to_thread

    async def tracking_to_thread(func, *args, **kwargs):
        calls.append(getattr(func, "__name__", repr(func)))
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(files_router.asyncio, "to_thread", tracking_to_thread)

    response = client.get("/api/threads/t1/files", params={"path": "/mnt/user-data"})

    assert response.status_code == 200
    assert any("scan_directory" in name for name in calls)
