"""Per-thread project link: binds a chat to one designated project folder.

A thread may link exactly one directory that lives under an
operator-configured ``sandbox.mounts`` entry (e.g. ``/mnt/projects/UAV-for-GS``
under a ``/mnt/projects`` mount). The Gateway file browser and artifact
preview expose only the thread's workspace plus that linked project — never
every configured mount.

The link is stored as a small JSON marker next to the thread's ``user-data``
directory (``{thread_dir}/project_link.json``), so it needs no schema
migration and is removed together with the thread. The marker stores only the
container path; the host directory is re-derived from the live mount config on
every read, so removing a mount from ``config.yaml`` instantly deactivates any
links that pointed into it.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from app.gateway.mounts import (
    host_root_for_container_path,
    list_existing_custom_mounts,
    normalize_container_path,
)
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

PROJECT_LINK_FILENAME = "project_link.json"
MAX_CANDIDATES_PER_MOUNT = 200


@dataclass(frozen=True)
class ProjectLink:
    """A resolved thread → project-folder binding."""

    container_path: str
    host_path: Path
    name: str


def _link_file(thread_id: str, user_id: str | None = None) -> Path:
    resolved_user = user_id or get_effective_user_id()
    return get_paths().thread_dir(thread_id, user_id=resolved_user) / PROJECT_LINK_FILENAME


def _build_link(container_path: str) -> ProjectLink | None:
    normalized = normalize_container_path(container_path)
    host_root = host_root_for_container_path(normalized)
    if host_root is None or not host_root.is_dir():
        return None
    return ProjectLink(
        container_path=normalized,
        host_path=host_root,
        name=host_root.name or normalized.strip("/"),
    )


def read_project_link(thread_id: str, user_id: str | None = None) -> ProjectLink | None:
    """Load the thread's linked project, or None when absent or no longer valid."""
    link_file = _link_file(thread_id, user_id=user_id)
    try:
        data = json.loads(link_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        logger.warning("Unreadable project link marker: %s", link_file, exc_info=True)
        return None
    container_path = data.get("container_path") if isinstance(data, dict) else None
    if not isinstance(container_path, str) or not container_path.strip("/"):
        return None
    return _build_link(container_path)


def write_project_link(thread_id: str, container_path: str, user_id: str | None = None) -> ProjectLink:
    """Persist the thread's project link after validating it against live mounts.

    Raises:
        ValueError: If the path does not resolve into an existing directory
                    under a configured mount.
    """
    link = _build_link(container_path)
    if link is None:
        raise ValueError(f"Path is not an existing directory under a configured sandbox mount: {container_path}")
    link_file = _link_file(thread_id, user_id=user_id)
    link_file.parent.mkdir(parents=True, exist_ok=True)
    link_file.write_text(json.dumps({"container_path": link.container_path}), encoding="utf-8")
    return link


def clear_project_link(thread_id: str, user_id: str | None = None) -> None:
    """Remove the thread's project link (idempotent)."""
    try:
        _link_file(thread_id, user_id=user_id).unlink()
    except FileNotFoundError:
        pass


def list_project_candidates() -> list[ProjectLink]:
    """Linkable project folders: each mount root plus its first-level subdirectories."""
    candidates: list[ProjectLink] = []
    for mount in list_existing_custom_mounts():
        prefix = normalize_container_path(mount.container_path)
        root = Path(mount.host_path)
        if not root.is_dir():
            continue
        candidates.append(ProjectLink(container_path=prefix, host_path=root.resolve(), name=root.name or prefix.strip("/")))
        try:
            with os.scandir(root) as scan:
                children = sorted(
                    (entry for entry in scan if entry.is_dir() and not entry.name.startswith(".")),
                    key=lambda entry: entry.name.lower(),
                )
        except OSError:
            continue
        for entry in children[:MAX_CANDIDATES_PER_MOUNT]:
            candidates.append(
                ProjectLink(
                    container_path=f"{prefix}/{entry.name}",
                    host_path=Path(entry.path).resolve(),
                    name=entry.name,
                )
            )
    return candidates
