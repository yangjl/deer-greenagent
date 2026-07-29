"""The authenticated read model a parent application consults before trusting a deck.

This endpoint is the only way a browser learns whether some HTML it is about to
render is a Design surface at all. It therefore has to be boring and strict:
membership first, project scope always, and no actionable state for a deck that
is stale, superseded, or from somewhere else.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import anyio
import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import dbtl_cycles, workspaces
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.dbtl_config import DbtlConfig
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

_USER_ID = UUID("11111111-2222-3333-4444-555555555555")
_OTHER_USER_ID = UUID("99999999-8888-7777-6666-555555555555")

DECK_HASH = "a" * 64
OTHER_DECK_HASH = "b" * 64
DECK_URI = "/mnt/user-data/outputs/dbtl/cycle/design/design-slides-rev1-aaaaaa.html"


@pytest.fixture(autouse=True)
def _close_test_engine():
    yield
    anyio.run(close_engine)


def _user() -> User:
    return User(id=_USER_ID, email="breeder@example.com", password_hash="x", system_role="user")


def _other_user() -> User:
    return User(id=_OTHER_USER_ID, email="stranger@example.com", password_hash="x", system_role="user")


def _internal_user():
    return SimpleNamespace(id=_USER_ID, system_role="internal")


async def _make_repos(tmp_path: Path):
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    return WorkspaceRepository(session_factory), DbtlCycleRepository(session_factory)


def _make_app(workspace_repo, cycle_repo, *, mode: str = "manual", user_factory=_user):
    app = make_authed_test_app(user_factory=user_factory)
    app.state.workspace_repo = workspace_repo
    app.state.dbtl_cycle_repo = cycle_repo
    app.state.dbtl_config_override = DbtlConfig(mode=mode)
    app.include_router(workspaces.router)
    app.include_router(dbtl_cycles.router)
    return app


def _seed_project(client, *, name: str = "Drought") -> str:
    workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
    return client.post(f"/api/workspaces/{workspace['id']}/projects", json={"name": name}).json()["id"]


def _create_cycle(client, project_id: str, *, key: str = "create-1") -> dict:
    return client.post(
        f"/api/projects/{project_id}/dbtl/cycles",
        json={
            "title": "Drought tolerance screen",
            "cycle_class": "computational",
            "research_question": "Which lines hold yield under late drought?",
            "objective": "Rank 200 lines",
            "success_criteria": "Top decile reproducible",
            "idempotency_key": key,
        },
    ).json()


async def _register(repo, cycle: dict, *, deck_content_hash: str = DECK_HASH, mode: str = "chair_feedback", design_round: int = 1) -> dict:
    attempt = next(item["id"] for item in cycle["stages"] if item["stage"] == "design")
    return await repo.register_design_feedback_surface(
        project_id=cycle["project_id"],
        cycle_id=cycle["id"],
        stage_attempt_id=attempt,
        design_round=design_round,
        originating_thread_id="thread-1",
        mode=mode,
        deck_uri=DECK_URI,
        deck_content_hash=deck_content_hash,
    )


def _url(project_id: str, cycle_id: str, surface_id: str) -> str:
    return f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}/design-feedback/{surface_id}"


def test_a_member_reads_the_surface_and_its_bindings(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        surface = anyio.run(_register, cycle_repo, cycle)

        response = client.get(_url(project_id, cycle["id"], surface["surface_id"]))

        assert response.status_code == 200
        body = response.json()
        assert body["surface_id"] == surface["surface_id"]
        assert body["deck_content_hash"] == DECK_HASH
        assert body["originating_thread_id"] == "thread-1"
        assert body["is_current"] is True


def test_the_read_model_reports_no_actions_yet(tmp_path: Path) -> None:
    """Phase 1 ships the descriptor, not the ability to answer through it."""
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        surface = anyio.run(_register, cycle_repo, cycle)

        body = client.get(_url(project_id, cycle["id"], surface["surface_id"])).json()

        assert body["allowed_actions"] == []
        assert body["interactive"] is False


def test_a_stale_surface_points_at_the_newest_one(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        first = anyio.run(_register, cycle_repo, cycle)
        second = anyio.run(partial(_register, cycle_repo, cycle, deck_content_hash=OTHER_DECK_HASH, design_round=2))

        body = client.get(_url(project_id, cycle["id"], first["surface_id"])).json()

        assert body["is_current"] is False
        assert body["superseded_by_surface_id"] == second["surface_id"]
        assert body["newest_surface_id"] == second["surface_id"]


def test_a_non_member_gets_a_not_found_rather_than_the_surface(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as owner_client:
        project_id = _seed_project(owner_client)
        cycle = _create_cycle(owner_client, project_id)
        surface = anyio.run(_register, cycle_repo, cycle)

    with TestClient(_make_app(workspace_repo, cycle_repo, user_factory=_other_user)) as stranger:
        response = stranger.get(_url(project_id, cycle["id"], surface["surface_id"]))

    assert response.status_code == 404


def test_a_surface_from_another_cycle_is_not_served(tmp_path: Path) -> None:
    """The cycle in the path is part of the addressing rule, not decoration."""
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        other = _create_cycle(client, project_id, key="create-2")
        surface = anyio.run(_register, cycle_repo, cycle)

        response = client.get(_url(project_id, other["id"], surface["surface_id"]))

    assert response.status_code == 404


def test_an_unknown_surface_is_a_not_found(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)

        response = client.get(_url(project_id, cycle["id"], "dfs-forged"))

    assert response.status_code == 404


def test_reads_work_while_mutations_are_disabled(tmp_path: Path) -> None:
    """A read model that vanishes in audit_only cannot report that nothing is actionable."""
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as seeded:
        project_id = _seed_project(seeded)
        cycle = _create_cycle(seeded, project_id)
        surface = anyio.run(_register, cycle_repo, cycle)

    with TestClient(_make_app(workspace_repo, cycle_repo, mode="audit_only")) as client:
        response = client.get(_url(project_id, cycle["id"], surface["surface_id"]))

    assert response.status_code == 200
    assert response.json()["surface_id"] == surface["surface_id"]


def test_an_internal_principal_may_read_but_gains_no_actions(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as seeded:
        project_id = _seed_project(seeded)
        cycle = _create_cycle(seeded, project_id)
        surface = anyio.run(_register, cycle_repo, cycle)

    with TestClient(_make_app(workspace_repo, cycle_repo, user_factory=_internal_user)) as client:
        body = client.get(_url(project_id, cycle["id"], surface["surface_id"])).json()

    assert body["allowed_actions"] == []
