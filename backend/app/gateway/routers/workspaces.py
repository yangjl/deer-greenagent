from __future__ import annotations

import asyncio
import re
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.deps import get_optional_user_from_request, get_thread_store, get_workspace_repo
from app.gateway.project_files import list_project_directory
from app.gateway.project_scope import compute_project_root, configured_projects_root, ensure_project_root
from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.persistence.workspaces import (
    ProjectSlugConflict,
    WorkspaceAccessDenied,
    WorkspaceSlugConflict,
)
from deerflow.projects.storage import ensure_project_dirs

router = APIRouter(prefix="/api", tags=["workspaces"])

_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:96] or f"workspace-{uuid.uuid4().hex[:8]}"


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str | None = Field(default=None, min_length=1, max_length=96, pattern=_SLUG_PATTERN)
    description: str | None = Field(default=None, max_length=4000)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    slug: str | None = Field(default=None, min_length=1, max_length=96, pattern=_SLUG_PATTERN)
    description: str | None = Field(default=None, max_length=4000)
    crop_profile: str = Field(default="generic-v1", min_length=1, max_length=64)


async def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        user = await get_optional_user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(user.id)


@router.get("/workspaces")
@require_permission("threads", "read")
async def list_workspaces(request: Request):
    return await get_workspace_repo(request).list_workspaces(await _user_id(request))


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
@require_permission("threads", "write")
async def create_workspace(request: Request, body: WorkspaceCreateRequest):
    user_id = await _user_id(request)
    try:
        return await get_workspace_repo(request).create_workspace(
            workspace_id=f"ws-{uuid.uuid4().hex}",
            name=body.name.strip(),
            slug=body.slug or _slugify(body.name),
            description=body.description,
            created_by=user_id,
        )
    except WorkspaceSlugConflict as exc:
        raise HTTPException(status_code=409, detail="Workspace slug already exists") from exc


@router.get("/workspaces/{workspace_id}/projects")
@require_permission("threads", "read")
async def list_projects(workspace_id: str, request: Request):
    try:
        return await get_workspace_repo(request).list_projects(
            workspace_id,
            user_id=await _user_id(request),
        )
    except WorkspaceAccessDenied as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc


@router.post(
    "/workspaces/{workspace_id}/projects",
    status_code=status.HTTP_201_CREATED,
)
@require_permission("threads", "write")
async def create_project(
    workspace_id: str,
    request: Request,
    body: ProjectCreateRequest,
):
    try:
        # The project's workspace is a real, human-visible folder under
        # projects.root — created now (or adopted if the human already made
        # it), so it exists before the first conversation runs.
        repo = get_workspace_repo(request)
        name = body.name.strip()
        taken = {p["root_path"] for p in await repo.list_projects(workspace_id, user_id=await _user_id(request)) if p.get("root_path")}
        root = compute_project_root(name, projects_root=configured_projects_root(request), taken=taken)
        ensure_project_dirs(root)
        return await repo.create_project(
            project_id=f"project-{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            name=name,
            slug=body.slug or _slugify(body.name),
            description=body.description,
            crop_profile=body.crop_profile,
            created_by=await _user_id(request),
            root_path=str(root),
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Could not create the project folder") from exc
    except WorkspaceAccessDenied as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ProjectSlugConflict as exc:
        raise HTTPException(status_code=409, detail="Project slug already exists in this workspace") from exc


@router.get("/projects/{project_id}")
@require_permission("threads", "read")
async def get_project(project_id: str, request: Request):
    repo = get_workspace_repo(request)
    project = await repo.get_project(
        project_id,
        user_id=await _user_id(request),
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    # Backfill for projects created before human-visible folders existed.
    if not project.get("root_path"):
        project["root_path"] = await ensure_project_root(repo, project, request)
    return project


# ── Project-owned conversations and files ────────────────────────────────
#
# A project is the addressable unit: it owns its conversations and its file
# tree, so neither depends on any single chat surviving.


async def _require_project(project_id: str, request: Request) -> dict:
    """Return the project or 404 — membership is enforced by the repository."""
    project = await get_workspace_repo(request).get_project(project_id, user_id=await _user_id(request))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/projects/{project_id}/threads")
@require_permission("threads", "read")
async def list_project_threads(project_id: str, request: Request):
    """Conversations filed into this project, newest first."""
    user_id = await _user_id(request)
    await _require_project(project_id, request)
    return await get_thread_store(request).list_by_project(project_id, user_id=user_id)


@router.put("/projects/{project_id}/threads/{thread_id}")
@require_permission("threads", "write", owner_check=True)
async def add_thread_to_project(project_id: str, thread_id: str, request: Request):
    """File a conversation into this project."""
    user_id = await _user_id(request)
    project = await _require_project(project_id, request)
    thread_store = get_thread_store(request)
    # The authenticated user is passed explicitly rather than relying on the
    # ambient contextvar: the write is owner-scoped in the store, so a caller
    # can only file a conversation they own.
    await thread_store.set_conversation_scope(
        thread_id,
        workspace_id=project["workspace_id"],
        project_id=project_id,
        user_id=user_id,
    )
    record = await thread_store.get(thread_id, user_id=user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return record


@router.delete("/projects/{project_id}/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_permission("threads", "write", owner_check=True)
async def remove_thread_from_project(project_id: str, thread_id: str, request: Request):
    """Return a conversation to the inbox."""
    user_id = await _user_id(request)
    await _require_project(project_id, request)
    await get_thread_store(request).set_conversation_scope(thread_id, workspace_id=None, project_id=None, user_id=user_id)


@router.get("/projects/{project_id}/files")
@require_permission("threads", "read")
async def list_project_files(
    project_id: str,
    request: Request,
    path: str = Query(VIRTUAL_PATH_PREFIX, description="Virtual directory path to list (defaults to /mnt/user-data)"),
):
    """List one directory of the project's own file tree.

    Read-only, and independent of any conversation: a project keeps its files
    whether or not a chat is open.
    """
    project = await _require_project(project_id, request)
    root = project.get("root_path") or await ensure_project_root(get_workspace_repo(request), project, request)
    if not root:
        raise HTTPException(status_code=503, detail="Project folder is unavailable")
    return await asyncio.to_thread(list_project_directory, root, path)
