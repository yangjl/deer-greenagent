from __future__ import annotations

import asyncio
import re
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.deps import get_optional_user_from_request, get_thread_store, get_workspace_repo
from app.gateway.project_files import list_project_directory, resolve_project_file
from app.gateway.project_scope import (
    allowed_project_roots,
    compute_project_root,
    configured_projects_root,
    ensure_project_root,
    resolve_allowed_project_path,
)
from app.gateway.routers.artifacts import (
    _build_attachment_headers,
    _build_content_disposition,
    _read_artifact_payload,
)
from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.persistence.workspaces import (
    ProjectSlugConflict,
    WorkspaceAccessDenied,
    WorkspaceSlugConflict,
)
from deerflow.projects.storage import ensure_project_dirs, project_folder_name

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
    location_mode: Literal["default", "existing", "full_path", "new_under_parent"] = "default"
    root_path: str | None = Field(default=None, max_length=4096)
    parent_path: str | None = Field(default=None, max_length=4096)
    folder_name: str | None = Field(default=None, max_length=255)


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


def _required_path(value: str | None, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=422, detail=f"{field_name} is required for the selected folder option")
    return value.strip()


async def _resolve_creation_root(
    *,
    repo,
    workspace_id: str,
    user_id: str,
    name: str,
    body: ProjectCreateRequest,
    request: Request,
):
    await repo.list_projects(workspace_id, user_id=user_id)
    taken = await repo.list_claimed_project_roots()

    try:
        if body.location_mode == "default":
            root = compute_project_root(
                name,
                projects_root=configured_projects_root(request).expanduser().resolve(),
                taken=taken,
            ).resolve()
        elif body.location_mode == "existing":
            root = resolve_allowed_project_path(
                _required_path(body.root_path, field_name="root_path"),
                request=request,
                must_exist=True,
            )
        elif body.location_mode == "full_path":
            root = resolve_allowed_project_path(
                _required_path(body.root_path, field_name="root_path"),
                request=request,
            )
        else:
            parent = resolve_allowed_project_path(
                _required_path(body.parent_path, field_name="parent_path"),
                request=request,
                must_exist=True,
            )
            folder = project_folder_name(body.folder_name or name)
            root = resolve_allowed_project_path(str(parent / folder), request=request)
            if root.exists():
                raise HTTPException(
                    status_code=409,
                    detail="That folder already exists; choose Use existing folder instead",
                )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.location_mode != "default" and str(root) in taken:
        raise HTTPException(status_code=409, detail="That folder is already assigned to another project")
    return root


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
        user_id = await _user_id(request)
        root = await _resolve_creation_root(
            repo=repo,
            workspace_id=workspace_id,
            user_id=user_id,
            name=name,
            body=body,
            request=request,
        )
        ensure_project_dirs(root)
        return await repo.create_project(
            project_id=f"project-{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            name=name,
            slug=body.slug or _slugify(body.name),
            description=body.description,
            crop_profile=body.crop_profile,
            created_by=user_id,
            root_path=str(root),
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Could not create the project folder") from exc
    except WorkspaceAccessDenied as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ProjectSlugConflict as exc:
        raise HTTPException(status_code=409, detail="Project slug already exists in this workspace") from exc


@router.get("/project-folders")
@require_permission("threads", "read")
async def browse_project_folders(
    request: Request,
    path: str | None = Query(default=None, max_length=4096),
):
    """Browse operator-approved writable host directories for project setup."""
    await _user_id(request)
    roots = allowed_project_roots(request)
    for root in roots:
        if root == configured_projects_root(request).expanduser().resolve():
            await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)

    try:
        current = resolve_allowed_project_path(
            path or str(roots[0]),
            request=request,
            must_exist=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    def _list_directories():
        directories = []
        for child in current.iterdir():
            try:
                if child.is_dir() and not child.name.startswith("."):
                    directories.append({"name": child.name, "path": str(child.resolve())})
            except OSError:
                continue
        directories.sort(key=lambda item: item["name"].casefold())
        parent = current.parent.resolve()
        parent_path = str(parent) if parent != current and any(parent == root or parent.is_relative_to(root) for root in roots) else None
        return directories, parent_path

    directories, parent_path = await asyncio.to_thread(_list_directories)
    return {
        "current_path": str(current),
        "parent_path": parent_path,
        "allowed_roots": [str(root) for root in roots],
        "directories": directories,
    }


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


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_permission("threads", "write")
async def archive_project(project_id: str, request: Request):
    """Remove a project from navigation while preserving its folder and audit data."""
    user_id = await _user_id(request)
    repo = get_workspace_repo(request)
    project = await repo.get_project(project_id, user_id=user_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.get("current_user_role") not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Only a workspace owner or admin can remove a project")

    # Conversations are independent records. Return every member's threads to
    # the inbox before hiding the shared project so no chat becomes stranded.
    await get_thread_store(request).clear_project_scope(project_id)
    try:
        archived = await repo.archive_project(project_id, user_id=user_id)
    except WorkspaceAccessDenied as exc:
        raise HTTPException(status_code=403, detail="Only a workspace owner or admin can remove a project") from exc
    if archived is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.get("/projects/{project_id}/file")
@require_permission("threads", "read")
async def get_project_file(
    project_id: str,
    request: Request,
    path: str = Query(..., description="Virtual project file path"),
    download: bool = False,
) -> Response:
    """Read or download one project-owned file without requiring a thread."""
    project = await _require_project(project_id, request)
    root = project.get("root_path") or await ensure_project_root(get_workspace_repo(request), project, request)
    if not root:
        raise HTTPException(status_code=503, detail="Project folder is unavailable")

    actual_path, virtual_path = await asyncio.to_thread(resolve_project_file, root, path)
    kind, mime_type, payload = await asyncio.to_thread(
        _read_artifact_payload,
        actual_path,
        virtual_path,
        download,
    )
    if kind == "file":
        return FileResponse(
            path=actual_path,
            filename=actual_path.name,
            media_type=mime_type,
            headers=_build_attachment_headers(actual_path.name),
        )
    if kind == "inline_file":
        return FileResponse(
            path=actual_path,
            media_type=mime_type,
            headers={"Content-Disposition": _build_content_disposition("inline", actual_path.name)},
        )
    if kind == "text":
        return PlainTextResponse(content=payload, media_type=mime_type)
    raise AssertionError(f"Unhandled project file response kind: {kind!r}")
