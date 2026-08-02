"""Thread workspace file listing.

Read-only directory listing over a thread's sandbox user-data tree
(``/mnt/user-data/{workspace,uploads,outputs}``) so the Web UI can render a
file browser. Reuses the same virtual-path resolution and thread-ownership
checks as the artifacts router; individual file content is served by the
existing artifacts endpoint.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.internal_auth import get_trusted_internal_owner_user_id
from app.gateway.path_utils import resolve_thread_virtual_path
from app.gateway.project_scope import resolve_thread_project_scope
from app.gateway.thread_project import (
    clear_project_link,
    list_project_candidates,
    read_project_link,
    write_project_link,
)
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, make_safe_user_id
from deerflow.uploads import is_upload_staging_file
from deerflow.utils.thread_id import ThreadId

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["files"])

MAX_ENTRIES = 500


class FileEntry(BaseModel):
    """One directory entry in a thread's user-data tree."""

    name: str = Field(..., description="Entry basename")
    path: str = Field(..., description="Virtual path of the entry (e.g. /mnt/user-data/workspace/notes.txt)")
    type: Literal["file", "directory"] = Field(..., description="Entry kind; symlinks report their target's kind")
    size: int | None = Field(None, description="File size in bytes; null for directories")
    modified_at: str | None = Field(None, description="Last modification time as an ISO 8601 UTC timestamp")
    is_symlink: bool = Field(False, description="Whether the entry itself is a symlink")


class FilesListResponse(BaseModel):
    """Directory listing for one virtual path."""

    path: str = Field(..., description="The listed virtual directory path")
    entries: list[FileEntry] = Field(..., description="Directory entries, directories first, then files, each name-sorted")
    truncated: bool = Field(False, description="True when the listing was cut off at the entry cap")


class ProjectInfo(BaseModel):
    """A linkable (or linked) project folder."""

    container_path: str = Field(..., description="Virtual path of the project folder (e.g. /mnt/projects/my-project)")
    name: str = Field(..., description="Display name (folder basename)")


class ThreadProjectResponse(BaseModel):
    """The thread's current project link."""

    project: ProjectInfo | None = Field(None, description="The linked project, or null when none is linked")


class ThreadProjectRequest(BaseModel):
    """Set the thread's project link."""

    container_path: str = Field(..., min_length=1, description="Virtual path of the project folder to link")


class ProjectCandidatesResponse(BaseModel):
    """Folders that may be linked as a thread's project."""

    candidates: list[ProjectInfo] = Field(..., description="Mount roots and their first-level subdirectories")


def _normalize_virtual_path(virtual_path: str) -> str:
    """Return the canonical `/mnt/user-data[/...]` form of *virtual_path*."""
    normalized = "/" + virtual_path.strip("/")
    return normalized if normalized != "/" else VIRTUAL_PATH_PREFIX


def _entry_sort_key(entry: FileEntry) -> tuple[int, str]:
    return (0 if entry.type == "directory" else 1, entry.name.lower())


def scan_directory(actual_path: Path, virtual_path: str) -> FilesListResponse:
    """Worker-thread body: stat + scandir are blocking filesystem IO.

    Shared with the project-scoped listing in
    :mod:`app.gateway.project_files`, so both trees render identically.
    """
    if not actual_path.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {virtual_path}")
    if not actual_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {virtual_path}")

    entries: list[FileEntry] = []
    with os.scandir(actual_path) as scan:
        for dir_entry in scan:
            if is_upload_staging_file(dir_entry.name):
                continue
            try:
                is_dir = dir_entry.is_dir()
                stat_result = dir_entry.stat()
            except OSError:
                # Broken symlink or a file deleted mid-scan: report what the
                # lstat view still knows instead of failing the whole listing.
                is_dir = False
                stat_result = None
            entries.append(
                FileEntry(
                    name=dir_entry.name,
                    path=f"{virtual_path}/{dir_entry.name}",
                    type="directory" if is_dir else "file",
                    size=None if is_dir or stat_result is None else stat_result.st_size,
                    modified_at=(datetime.fromtimestamp(stat_result.st_mtime, tz=UTC).isoformat() if stat_result is not None else None),
                    is_symlink=dir_entry.is_symlink(),
                )
            )

    entries.sort(key=_entry_sort_key)
    truncated = len(entries) > MAX_ENTRIES
    return FilesListResponse(path=virtual_path, entries=entries[:MAX_ENTRIES], truncated=truncated)


def _append_linked_project_root(listing: FilesListResponse, thread_id: str, user_id: str | None) -> FilesListResponse:
    """Worker-thread body: add the thread's linked project to the root listing.

    The linked project lives at its own virtual prefix (e.g.
    ``/mnt/projects/UAV-for-GS``), not under ``/mnt/user-data``, so the
    physical root scan cannot discover it. Only the thread's one designated
    project is appended — other configured mounts stay invisible.
    """
    link = read_project_link(thread_id, user_id=user_id)
    if link is None:
        return listing

    if any(entry.path == link.container_path for entry in listing.entries):
        return listing

    try:
        stat_result = link.host_path.stat()
        modified_at = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC).isoformat()
    except OSError:
        modified_at = None

    entries = [
        *listing.entries,
        FileEntry(
            name=link.name,
            path=link.container_path,
            type="directory",
            size=None,
            modified_at=modified_at,
            is_symlink=False,
        ),
    ]
    entries.sort(key=_entry_sort_key)
    return FilesListResponse(path=listing.path, entries=entries, truncated=listing.truncated)


@router.get(
    "/threads/{thread_id}/files",
    response_model=FilesListResponse,
    summary="List Thread Files",
    description="List one directory of the thread's sandbox user-data tree (/mnt/user-data). Read-only; file content is served by the artifacts endpoint.",
)
@require_permission("threads", "read", owner_check=True)
async def list_files(
    thread_id: ThreadId,
    request: Request,
    path: str = Query(VIRTUAL_PATH_PREFIX, description="Virtual directory path to list (defaults to /mnt/user-data)"),
) -> FilesListResponse:
    """List a single directory of the thread's user-data tree.

    Args:
        thread_id: The thread ID.
        request: FastAPI request object (automatically injected).
        path: Virtual directory path (e.g. ``/mnt/user-data/workspace``).

    Returns:
        The directory listing with per-entry metadata, directories first.

    Raises:
        HTTPException:
            - 400 if the path is not under /mnt/user-data or is not a directory
            - 403 if access is denied (path traversal detected)
            - 404 if the directory does not exist
    """
    # Same trusted-internal-owner resolution as the artifacts router: honored
    # only after the internal token validates; browser callers get None and
    # fall back to the effective user.
    raw_owner_user_id = get_trusted_internal_owner_user_id(request)
    owner_user_id = make_safe_user_id(raw_owner_user_id) if raw_owner_user_id else None

    virtual_path = _normalize_virtual_path(path)
    _, project_root = await resolve_thread_project_scope(request, thread_id)
    actual_path = await asyncio.to_thread(resolve_thread_virtual_path, thread_id, virtual_path, user_id=owner_user_id, project_root=project_root)

    try:
        listing = await asyncio.to_thread(scan_directory, actual_path, virtual_path)
    except HTTPException as exc:
        # A thread's user-data tree is materialized lazily by the run
        # middleware, so a brand-new (or never-run) thread has no root dir
        # yet. Render that as an empty workspace instead of an error — the
        # linked project (if any) exists independently of the thread.
        if virtual_path != VIRTUAL_PATH_PREFIX or exc.status_code != 404:
            raise
        listing = FilesListResponse(path=virtual_path, entries=[], truncated=False)

    if virtual_path == VIRTUAL_PATH_PREFIX:
        listing = await asyncio.to_thread(_append_linked_project_root, listing, thread_id, owner_user_id)
    return listing


def _resolve_owner_user_id(request: Request) -> str | None:
    raw_owner_user_id = get_trusted_internal_owner_user_id(request)
    return make_safe_user_id(raw_owner_user_id) if raw_owner_user_id else None


def _project_info(link) -> ProjectInfo:
    return ProjectInfo(container_path=link.container_path, name=link.name)


@router.get(
    "/threads/{thread_id}/project",
    response_model=ThreadProjectResponse,
    summary="Get Thread Project Link",
    description="Return the single project folder linked to this thread, or null when none is linked.",
)
@require_permission("threads", "read", owner_check=True)
async def get_thread_project(thread_id: ThreadId, request: Request) -> ThreadProjectResponse:
    """Get the thread's linked project folder."""
    owner_user_id = _resolve_owner_user_id(request)
    link = await asyncio.to_thread(read_project_link, thread_id, owner_user_id)
    return ThreadProjectResponse(project=_project_info(link) if link else None)


@router.put(
    "/threads/{thread_id}/project",
    response_model=ThreadProjectResponse,
    summary="Set Thread Project Link",
    description="Link one project folder (an existing directory under a configured sandbox mount) to this thread.",
)
@require_permission("threads", "write", owner_check=True)
async def set_thread_project(thread_id: ThreadId, body: ThreadProjectRequest, request: Request) -> ThreadProjectResponse:
    """Set the thread's linked project folder."""
    owner_user_id = _resolve_owner_user_id(request)
    try:
        link = await asyncio.to_thread(write_project_link, thread_id, body.container_path, owner_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ThreadProjectResponse(project=_project_info(link))


@router.delete(
    "/threads/{thread_id}/project",
    response_model=ThreadProjectResponse,
    summary="Clear Thread Project Link",
    description="Unlink the thread's project folder. Idempotent.",
)
@require_permission("threads", "write", owner_check=True)
async def delete_thread_project(thread_id: ThreadId, request: Request) -> ThreadProjectResponse:
    """Clear the thread's linked project folder."""
    owner_user_id = _resolve_owner_user_id(request)
    await asyncio.to_thread(clear_project_link, thread_id, owner_user_id)
    return ThreadProjectResponse(project=None)


@router.get(
    "/threads/{thread_id}/project/candidates",
    response_model=ProjectCandidatesResponse,
    summary="List Thread Project Candidates",
    description="List folders that can be linked as this thread's project: configured sandbox mount roots and their first-level subdirectories.",
)
@require_permission("threads", "read", owner_check=True)
async def get_thread_project_candidates(thread_id: ThreadId, request: Request) -> ProjectCandidatesResponse:
    """List linkable project folders."""
    candidates = await asyncio.to_thread(list_project_candidates)
    return ProjectCandidatesResponse(candidates=[_project_info(link) for link in candidates])
