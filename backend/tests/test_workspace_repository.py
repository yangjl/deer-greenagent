from __future__ import annotations

import pytest
import pytest_asyncio

from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import (
    ProjectSlugConflict,
    WorkspaceAccessDenied,
    WorkspaceRepository,
    WorkspaceSlugConflict,
)


@pytest_asyncio.fixture
async def workspace_repo(tmp_path):
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    yield WorkspaceRepository(session_factory)
    await close_engine()


@pytest.mark.asyncio
async def test_create_workspace_adds_owner_membership_and_lists_by_member(workspace_repo):
    workspace = await workspace_repo.create_workspace(
        workspace_id="ws-maize",
        name="Maize Breeding",
        slug="maize-breeding",
        description="Long-term maize improvement program",
        created_by="user-1",
    )

    assert workspace["slug"] == "maize-breeding"
    assert workspace["current_user_role"] == "owner"
    assert [item["id"] for item in await workspace_repo.list_workspaces("user-1")] == ["ws-maize"]
    assert await workspace_repo.list_workspaces("user-2") == []


@pytest.mark.asyncio
async def test_workspace_slug_is_unique(workspace_repo):
    await workspace_repo.create_workspace(
        workspace_id="ws-1",
        name="Program One",
        slug="maize",
        description=None,
        created_by="user-1",
    )

    with pytest.raises(WorkspaceSlugConflict):
        await workspace_repo.create_workspace(
            workspace_id="ws-2",
            name="Program Two",
            slug="maize",
            description=None,
            created_by="user-2",
        )


@pytest.mark.asyncio
async def test_projects_are_scoped_to_workspace_members(workspace_repo):
    await workspace_repo.create_workspace(
        workspace_id="ws-maize",
        name="Maize Breeding",
        slug="maize-breeding",
        description=None,
        created_by="user-1",
    )
    project = await workspace_repo.create_project(
        project_id="project-drought",
        workspace_id="ws-maize",
        name="Drought Resilience 2032",
        slug="drought-resilience-2032",
        description="Improve yield stability under water limitation",
        crop_profile="maize-v1",
        created_by="user-1",
    )

    assert project["workspace_id"] == "ws-maize"
    assert project["crop_profile"] == "maize-v1"
    assert project["dbtl_phase"] == "design"
    assert [item["id"] for item in await workspace_repo.list_projects("ws-maize", user_id="user-1")] == ["project-drought"]

    with pytest.raises(WorkspaceAccessDenied):
        await workspace_repo.list_projects("ws-maize", user_id="user-2")


@pytest.mark.asyncio
async def test_project_slug_is_unique_within_workspace(workspace_repo):
    await workspace_repo.create_workspace(
        workspace_id="ws-maize",
        name="Maize Breeding",
        slug="maize-breeding",
        description=None,
        created_by="user-1",
    )
    create = dict(
        workspace_id="ws-maize",
        name="Drought Resilience",
        slug="drought-resilience",
        description=None,
        crop_profile="maize-v1",
        created_by="user-1",
    )
    await workspace_repo.create_project(project_id="project-1", **create)

    with pytest.raises(ProjectSlugConflict):
        await workspace_repo.create_project(project_id="project-2", **create)
