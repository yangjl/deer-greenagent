"""Shared path resolution for thread virtual paths (e.g. mnt/user-data/outputs/...)."""

from pathlib import Path

from fastapi import HTTPException

from app.gateway.thread_project import read_project_link
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id


def resolve_linked_project_path(thread_id: str, virtual_path: str, user_id: str | None = None) -> Path | None:
    """Resolve *virtual_path* inside the thread's linked project folder.

    Returns ``None`` when the thread has no (valid) project link or the path
    is outside the linked project's container prefix. This deliberately does
    NOT expose other configured mounts: each thread sees only its own
    designated project.

    Raises:
        HTTPException: 403 if the resolved path escapes the project root.
    """
    link = read_project_link(thread_id, user_id=user_id)
    if link is None:
        return None

    normalized = "/" + virtual_path.strip("/")
    if normalized != link.container_path and not normalized.startswith(link.container_path + "/"):
        return None

    relative = normalized[len(link.container_path) :].lstrip("/")
    actual = (link.host_path / relative).resolve() if relative else link.host_path
    try:
        actual.relative_to(link.host_path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: path traversal detected")
    return actual


def resolve_thread_virtual_path(thread_id: str, virtual_path: str, user_id: str | None = None, project_root: str | None = None) -> Path:
    """Resolve a virtual path to the actual filesystem path.

    Paths under ``/mnt/user-data`` resolve into the thread's user-data tree.
    Paths under the thread's linked project (see
    :mod:`app.gateway.thread_project`) resolve into that project's host
    directory. Other mount prefixes are rejected: read-only Gateway views
    expose only the workspace plus the thread's one designated project.

    Args:
        thread_id: The thread ID.
        virtual_path: The virtual path as seen inside the sandbox
                      (e.g., /mnt/user-data/outputs/file.txt or the linked
                      project's /mnt/... path).
        user_id: The user whose storage to resolve under. Defaults to the
                 effective user when not given; callers acting on behalf of a
                 specific owner (e.g. trusted internal callers) pass it explicitly.

    Returns:
        The resolved filesystem path.

    Raises:
        HTTPException: If the path is invalid or outside allowed directories.
    """
    resolved_user = user_id or get_effective_user_id()
    if project_root:
        # A filed conversation's whole /mnt/user-data tree IS the project's
        # human-visible folder, matching the sandbox mount.
        from deerflow.projects.storage import resolve_project_virtual_path

        try:
            return resolve_project_virtual_path(Path(project_root), virtual_path)
        except ValueError as e:
            if "traversal" in str(e):
                raise HTTPException(status_code=403, detail=str(e))
            project_path = resolve_linked_project_path(thread_id, virtual_path, user_id=resolved_user)
            if project_path is not None:
                return project_path
            raise HTTPException(status_code=400, detail=str(e))
    try:
        return get_paths().resolve_virtual_path(thread_id, virtual_path, user_id=resolved_user)
    except ValueError as e:
        if "traversal" in str(e):
            raise HTTPException(status_code=403, detail=str(e))
        project_path = resolve_linked_project_path(thread_id, virtual_path, user_id=resolved_user)
        if project_path is not None:
            return project_path
        raise HTTPException(status_code=400, detail=str(e))
