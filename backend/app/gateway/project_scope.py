"""Server-side resolution of a conversation's project scope.

A project's workspace is a human-visible folder (``projects.root/<name>``,
e.g. ``~/Documents/projects/G2F``). These helpers own three things:

- computing/adopting that folder when a project is created,
- lazily backfilling ``root_path`` for projects created before it existed,
- resolving a conversation's ``(project_id, project_root)`` for run context,
  uploads, files, and artifacts — always server-side, never from the client.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Request

from deerflow.config import get_app_config
from deerflow.projects.storage import ensure_project_dirs, project_folder_name

logger = logging.getLogger(__name__)


def configured_projects_root(request: Request | None = None) -> Path:
    """The parent folder for project workspaces (test-overridable)."""
    if request is not None:
        override = getattr(request.app.state, "projects_root_override", None)
        if override:
            return Path(override).expanduser()
    return get_app_config().projects.resolved_root()


def compute_project_root(name: str, *, projects_root: Path, taken: set[str]) -> Path:
    """Folder for a new project: ``projects_root/<human name>``.

    Adopting an existing folder of the same name is intentional (a directory
    the human already owns becomes the project). Only a folder already claimed
    by *another project row* forces a numeric suffix.
    """
    base = project_folder_name(name)
    candidate = projects_root / base
    counter = 2
    while str(candidate) in taken:
        candidate = projects_root / f"{base}-{counter}"
        counter += 1
    return candidate


async def ensure_project_root(workspace_repo, project: dict, request: Request | None = None) -> str | None:
    """Return the project's folder path, backfilling rows created before it.

    Creates the folder on disk (or adopts it) and persists the path. Returns
    ``None`` only when persistence or the filesystem fails — callers then
    degrade to conversation-scoped storage rather than erroring the request.
    """
    root_path = project.get("root_path")
    if isinstance(root_path, str) and root_path:
        try:
            ensure_project_dirs(Path(root_path))
        except OSError:
            logger.warning("Project folder %s is not creatable", root_path, exc_info=True)
            return None
        return root_path
    try:
        root = compute_project_root(
            str(project.get("name") or project.get("slug") or project.get("id")),
            projects_root=configured_projects_root(request),
            taken=set(),
        )
        ensure_project_dirs(root)
        await workspace_repo.update_project_root(project["id"], str(root))
        return str(root)
    except Exception:
        logger.warning("Failed to backfill project root for %s", project.get("id"), exc_info=True)
        return None


async def resolve_thread_project_scope(request: Request, thread_id: str) -> tuple[str | None, str | None]:
    """(project_id, project_root) for a conversation, from its durable scope row.

    Fails soft: any missing piece yields ``(None, None)`` / ``(id, None)`` and
    the caller falls back to conversation-scoped storage.
    """
    from app.gateway.deps import get_thread_store

    try:
        record = await get_thread_store(request).get(thread_id)
    except Exception:
        return None, None
    project_id = (record or {}).get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return None, None

    workspace_repo = getattr(request.app.state, "workspace_repo", None)
    if workspace_repo is None:
        return project_id, None
    try:
        project = await workspace_repo.get_project_record(project_id)
    except Exception:
        return project_id, None
    if project is None:
        return project_id, None
    root = await ensure_project_root(workspace_repo, project, request)
    return project_id, root
