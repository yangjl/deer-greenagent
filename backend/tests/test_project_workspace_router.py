"""Project-scoped conversation and file endpoints.

The project — not the conversation — is the addressable unit: a project lists
its own conversations and browses its own files, and filing a conversation
into a project is an authorized, durable write.
"""

from __future__ import annotations

from uuid import UUID

import anyio
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore

from app.gateway.auth.models import User
from app.gateway.routers import workspaces
from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.engine import get_session_factory, init_engine_from_config
from deerflow.persistence.thread_meta import MemoryThreadMetaStore
from deerflow.persistence.workspaces import WorkspaceRepository

_USER_ID = UUID("11111111-2222-3333-4444-555555555555")
_OTHER_USER_ID = UUID("99999999-8888-7777-6666-555555555555")


def _user() -> User:
    return User(id=_USER_ID, email="breeder@example.com", password_hash="x", system_role="user")


def _other_user() -> User:
    return User(id=_OTHER_USER_ID, email="stranger@example.com", password_hash="x", system_role="user")


async def _make_repo(tmp_path):
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    return WorkspaceRepository(session_factory)


def _make_app(repo, thread_store, *, user_factory=_user, projects_root=None):
    app = make_authed_test_app(user_factory=user_factory)
    app.state.workspace_repo = repo
    app.state.thread_store = thread_store
    if projects_root is not None:
        app.state.projects_root_override = str(projects_root)
    app.include_router(workspaces.router)
    return app


def _seed_project(client) -> tuple[str, str]:
    workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
    project = client.post(
        f"/api/workspaces/{workspace['id']}/projects",
        json={"name": "Drought Resistance"},
    ).json()
    return workspace["id"], project["id"]


def test_conversation_is_filed_into_a_project_and_listed_by_it(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    anyio.run(lambda: thread_store.create("thread-1", user_id=str(_USER_ID)))

    with TestClient(_make_app(repo, thread_store, projects_root=tmp_path / "roots")) as client:
        _, project_id = _seed_project(client)

        assert client.get(f"/api/projects/{project_id}/threads").json() == []

        filed = client.put(f"/api/projects/{project_id}/threads/thread-1")
        assert filed.status_code == 200
        assert filed.json()["project_id"] == project_id
        assert filed.json()["scope_type"] == "project"

        listed = client.get(f"/api/projects/{project_id}/threads").json()
        assert [row["thread_id"] for row in listed] == ["thread-1"]


def test_unfiling_returns_the_conversation_to_the_inbox(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    anyio.run(lambda: thread_store.create("thread-1", user_id=str(_USER_ID)))

    with TestClient(_make_app(repo, thread_store, projects_root=tmp_path / "roots")) as client:
        _, project_id = _seed_project(client)
        client.put(f"/api/projects/{project_id}/threads/thread-1")

        removed = client.delete(f"/api/projects/{project_id}/threads/thread-1")
        assert removed.status_code == 204
        assert client.get(f"/api/projects/{project_id}/threads").json() == []


def test_filing_into_an_unreachable_project_is_rejected(tmp_path):
    """A non-member must not be able to file a conversation into a project."""
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    anyio.run(lambda: thread_store.create("thread-1", user_id=str(_OTHER_USER_ID)))

    with TestClient(_make_app(repo, thread_store, projects_root=tmp_path / "roots")) as client:
        _, project_id = _seed_project(client)

    with TestClient(_make_app(repo, thread_store, user_factory=_other_user, projects_root=tmp_path / "roots")) as stranger:
        assert stranger.put(f"/api/projects/{project_id}/threads/thread-1").status_code == 404
        assert stranger.get(f"/api/projects/{project_id}/threads").status_code == 404


def test_project_creation_makes_a_human_visible_folder(tmp_path):
    """Creating "G2F" creates <projects_root>/G2F — a folder the human owns."""
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    projects_root = tmp_path / "Documents" / "projects"

    with TestClient(_make_app(repo, thread_store, projects_root=projects_root)) as client:
        workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
        project = client.post(
            f"/api/workspaces/{workspace['id']}/projects",
            json={"name": "G2F"},
        ).json()
        assert project["root_path"] == str(projects_root / "G2F")
        assert (projects_root / "G2F").is_dir()
        assert (projects_root / "G2F" / "uploads").is_dir()


def test_project_creation_can_adopt_an_existing_local_folder(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    projects_root = tmp_path / "Documents" / "projects"
    existing = projects_root / "existing-g2f"
    existing.mkdir(parents=True)
    (existing / "fieldbook.csv").write_text("plot,yield\n1,10\n")

    with TestClient(_make_app(repo, thread_store, projects_root=projects_root)) as client:
        workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
        response = client.post(
            f"/api/workspaces/{workspace['id']}/projects",
            json={
                "name": "G2F",
                "location_mode": "existing",
                "root_path": str(existing),
            },
        )

    assert response.status_code == 201
    assert response.json()["root_path"] == str(existing.resolve())
    assert (existing / "fieldbook.csv").read_text() == "plot,yield\n1,10\n"
    assert (existing / "uploads").is_dir()
    assert (existing / "outputs").is_dir()


def test_project_creation_accepts_a_full_new_path_inside_an_allowed_root(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    projects_root = tmp_path / "Documents" / "projects"
    requested = projects_root / "programs" / "g2f-2035"

    with TestClient(_make_app(repo, thread_store, projects_root=projects_root)) as client:
        workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
        response = client.post(
            f"/api/workspaces/{workspace['id']}/projects",
            json={
                "name": "G2F 2035",
                "location_mode": "full_path",
                "root_path": str(requested),
            },
        )

    assert response.status_code == 201
    assert response.json()["root_path"] == str(requested.resolve())
    assert requested.is_dir()


def test_project_creation_can_create_a_named_folder_under_a_selected_parent(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    projects_root = tmp_path / "Documents" / "projects"
    parent = projects_root / "breeding-programs"
    parent.mkdir(parents=True)

    with TestClient(_make_app(repo, thread_store, projects_root=projects_root)) as client:
        workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
        response = client.post(
            f"/api/workspaces/{workspace['id']}/projects",
            json={
                "name": "Drought 2040",
                "location_mode": "new_under_parent",
                "parent_path": str(parent),
                "folder_name": "managed-stress",
            },
        )

    expected = parent / "managed-stress"
    assert response.status_code == 201
    assert response.json()["root_path"] == str(expected.resolve())
    assert expected.is_dir()


def test_project_creation_rejects_paths_outside_human_ai_accessible_roots(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    projects_root = tmp_path / "Documents" / "projects"
    outside = tmp_path / "private" / "G2F"

    with TestClient(_make_app(repo, thread_store, projects_root=projects_root)) as client:
        workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
        response = client.post(
            f"/api/workspaces/{workspace['id']}/projects",
            json={
                "name": "G2F",
                "location_mode": "full_path",
                "root_path": str(outside),
            },
        )

    assert response.status_code == 422
    assert "accessible" in response.json()["detail"].lower()
    assert not outside.exists()


def test_project_folder_cannot_be_claimed_by_two_workspaces(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    projects_root = tmp_path / "Documents" / "projects"

    with TestClient(_make_app(repo, thread_store, projects_root=projects_root)) as client:
        first_workspace = client.post("/api/workspaces", json={"name": "Program One"}).json()
        first_project = client.post(
            f"/api/workspaces/{first_workspace['id']}/projects",
            json={"name": "G2F"},
        ).json()
        second_workspace = client.post("/api/workspaces", json={"name": "Program Two"}).json()
        response = client.post(
            f"/api/workspaces/{second_workspace['id']}/projects",
            json={
                "name": "G2F mirror",
                "location_mode": "existing",
                "root_path": first_project["root_path"],
            },
        )

    assert response.status_code == 409
    assert "already assigned" in response.json()["detail"]


def test_project_folder_browser_lists_only_allowed_directories(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    projects_root = tmp_path / "Documents" / "projects"
    (projects_root / "G2F").mkdir(parents=True)
    (projects_root / "notes.txt").write_text("not a folder")

    with TestClient(_make_app(repo, thread_store, projects_root=projects_root)) as client:
        response = client.get("/api/project-folders", params={"path": str(projects_root)})
        escaped = client.get("/api/project-folders", params={"path": str(tmp_path)})

    assert response.status_code == 200
    assert response.json()["current_path"] == str(projects_root.resolve())
    assert response.json()["directories"] == [{"name": "G2F", "path": str((projects_root / "G2F").resolve())}]
    assert escaped.status_code == 403


def test_project_files_list_the_human_folder_without_any_conversation(tmp_path):
    """The whole point: the human folder IS the project's file tree."""
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    projects_root = tmp_path / "Documents" / "projects"

    with TestClient(_make_app(repo, thread_store, projects_root=projects_root)) as client:
        workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
        project = client.post(
            f"/api/workspaces/{workspace['id']}/projects",
            json={"name": "G2F"},
        ).json()
        project_id = project["id"]

        root_dir = projects_root / "G2F"
        (root_dir / "data" / "raw").mkdir(parents=True)
        (root_dir / "trial-plan.md").write_text("rows")

        root = client.get(f"/api/projects/{project_id}/files").json()
        names = {entry["name"] for entry in root["entries"]}
        assert {"data", "trial-plan.md", "uploads", "outputs"} <= names

        # The agent's conventional workspace path aliases the folder itself.
        workspace_view = client.get(
            f"/api/projects/{project_id}/files",
            params={"path": "/mnt/user-data/workspace"},
        ).json()
        assert {entry["name"] for entry in workspace_view["entries"]} == names


def test_project_files_reject_path_traversal(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())

    with TestClient(_make_app(repo, thread_store, projects_root=tmp_path / "roots")) as client:
        _, project_id = _seed_project(client)
        escape = client.get(
            f"/api/projects/{project_id}/files",
            params={"path": "/mnt/user-data/../../../etc"},
        )
        assert escape.status_code == 403


def test_project_file_content_is_available_without_a_conversation(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    projects_root = tmp_path / "Documents" / "projects"

    with TestClient(_make_app(repo, thread_store, projects_root=projects_root)) as client:
        _, project_id = _seed_project(client)
        readme = projects_root / "Drought Resistance" / "README.md"
        readme.write_text("# Drought Resistance\n", encoding="utf-8")

        response = client.get(
            f"/api/projects/{project_id}/file",
            params={"path": "/mnt/user-data/workspace/README.md"},
        )

    assert response.status_code == 200
    assert response.text == "# Drought Resistance\n"
    assert response.headers["content-type"].startswith("text/markdown")


def test_project_file_content_rejects_path_traversal(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())

    with TestClient(_make_app(repo, thread_store, projects_root=tmp_path / "roots")) as client:
        _, project_id = _seed_project(client)
        response = client.get(
            f"/api/projects/{project_id}/file",
            params={"path": "/mnt/user-data/workspace/../../../private.txt"},
        )

    assert response.status_code == 403


def test_project_file_content_requires_membership(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    projects_root = tmp_path / "roots"

    with TestClient(_make_app(repo, thread_store, projects_root=projects_root)) as client:
        _, project_id = _seed_project(client)
        (projects_root / "Drought Resistance" / "README.md").write_text("private")

    with TestClient(_make_app(repo, thread_store, user_factory=_other_user, projects_root=projects_root)) as stranger:
        response = stranger.get(
            f"/api/projects/{project_id}/file",
            params={"path": "/mnt/user-data/workspace/README.md"},
        )

    assert response.status_code == 404


def test_project_files_require_membership(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    thread_store = MemoryThreadMetaStore(InMemoryStore())

    with TestClient(_make_app(repo, thread_store, projects_root=tmp_path / "roots")) as client:
        _, project_id = _seed_project(client)

    with TestClient(_make_app(repo, thread_store, user_factory=_other_user, projects_root=tmp_path / "roots")) as stranger:
        assert stranger.get(f"/api/projects/{project_id}/files").status_code == 404
