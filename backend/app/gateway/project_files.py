"""Read-only access to a project's human-visible folder.

A project owns its folder (e.g. ``~/Documents/projects/G2F``), so this
access needs no conversation: the same tree is what every conversation in the
project reads and writes through the sandbox, and what the human sees in
Finder.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from app.gateway.routers.files import FilesListResponse, scan_directory
from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.projects.storage import resolve_project_virtual_path


def _normalize_virtual_path(path: str) -> str:
    normalized = "/" + path.strip("/")
    return normalized.rstrip("/") or VIRTUAL_PATH_PREFIX


def list_project_directory(root: str, path: str) -> FilesListResponse:
    """Worker-thread body: resolve then scan one directory of the project folder.

    A folder that has not been materialized yet lists as empty rather than 404.
    """
    virtual_path = _normalize_virtual_path(path)
    try:
        actual_path = resolve_project_virtual_path(Path(root), virtual_path)
    except ValueError as exc:
        if "traversal" in str(exc):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return scan_directory(actual_path, virtual_path)
    except HTTPException as exc:
        if virtual_path == VIRTUAL_PATH_PREFIX and exc.status_code == 404:
            return FilesListResponse(path=virtual_path, entries=[], truncated=False)
        raise


def resolve_project_file(root: str, path: str) -> tuple[Path, str]:
    """Resolve one virtual project file while preserving traversal errors."""
    virtual_path = _normalize_virtual_path(path)
    try:
        return resolve_project_virtual_path(Path(root), virtual_path), virtual_path
    except ValueError as exc:
        if "traversal" in str(exc):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
