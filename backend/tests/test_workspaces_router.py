from __future__ import annotations

from uuid import UUID

import anyio
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import workspaces
from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository


def _user() -> User:
    return User(
        id=UUID("11111111-2222-3333-4444-555555555555"),
        email="breeder@example.com",
        password_hash="x",
        system_role="user",
    )


async def _make_repo(tmp_path):
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    return WorkspaceRepository(session_factory)


def _make_app(repo):
    app = make_authed_test_app(user_factory=_user)
    app.state.workspace_repo = repo
    app.include_router(workspaces.router)
    return app


def test_workspace_and_project_api_round_trip(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    app = _make_app(repo)

    with TestClient(app) as client:
        created_workspace = client.post(
            "/api/workspaces",
            json={
                "name": "Maize Breeding",
                "slug": "maize-breeding",
                "description": "Long-term germplasm improvement",
            },
        )
        assert created_workspace.status_code == 201
        workspace = created_workspace.json()

        created_project = client.post(
            f"/api/workspaces/{workspace['id']}/projects",
            json={
                "name": "Drought Resilience 2032",
                "slug": "drought-resilience-2032",
                "description": "Yield stability under managed stress",
                "crop_profile": "maize-v1",
            },
        )
        assert created_project.status_code == 201

        listed = client.get("/api/workspaces")
        assert listed.status_code == 200
        assert listed.json()[0]["project_count"] == 1

        projects = client.get(f"/api/workspaces/{workspace['id']}/projects")
        assert projects.status_code == 200
        assert projects.json()[0]["name"] == "Drought Resilience 2032"

    anyio.run(close_engine)


def test_duplicate_workspace_slug_returns_conflict(tmp_path):
    repo = anyio.run(_make_repo, tmp_path)
    app = _make_app(repo)
    payload = {"name": "Maize Breeding", "slug": "maize-breeding"}

    with TestClient(app) as client:
        assert client.post("/api/workspaces", json=payload).status_code == 201
        duplicate = client.post("/api/workspaces", json=payload)

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Workspace slug already exists"
    anyio.run(close_engine)
