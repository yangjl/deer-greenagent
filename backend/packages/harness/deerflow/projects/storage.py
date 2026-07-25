"""Storage layout for human-visible project folders.

A project's root (e.g. ``~/Documents/projects/G2F``) IS the workspace: the
human browses and edits it directly, and every conversation filed into the
project reads and writes it through the sandbox. ``uploads/`` and
``outputs/`` are plain, visible subfolders — no hidden application tree.

Virtual-path contract (matches the sandbox mounts):
- ``/mnt/user-data``            → the project root
- ``/mnt/user-data/workspace``  → the project root (alias, so the agent's
  conventional workspace path lands in the human folder, not a nested dir)
- ``/mnt/user-data/uploads``    → ``<root>/uploads``
- ``/mnt/user-data/outputs``    → ``<root>/outputs``
"""

from __future__ import annotations

import re
from pathlib import Path

_VIRTUAL_PREFIX = "mnt/user-data"
_UNSAFE_FOLDER_CHAR_RE = re.compile(r"[^A-Za-z0-9 ._\-]+")


def project_folder_name(name: str) -> str:
    """A filesystem-safe, human-readable folder name for a project.

    Keeps letters, digits, spaces, dots, dashes, and underscores; anything
    else (path separators, traversal dots at the edges) is stripped. Falls
    back to ``"project"`` rather than producing an empty or hidden name.
    """
    cleaned = _UNSAFE_FOLDER_CHAR_RE.sub("-", name).strip()
    cleaned = cleaned.strip(".").strip()
    return cleaned or "project"


def project_workspace_dir(root: Path) -> Path:
    return root


def project_uploads_dir(root: Path) -> Path:
    return root / "uploads"


def project_outputs_dir(root: Path) -> Path:
    return root / "outputs"


def ensure_project_dirs(root: Path) -> None:
    """Create (or adopt) the project folder and its visible subfolders.

    Never touches existing content — adopting a pre-existing folder is a
    supported way to bring a directory the human already owns into a project.
    Uses default permissions: this is a human directory (often under
    ``~/Documents``), not a container volume.
    """
    for directory in (root, project_uploads_dir(root), project_outputs_dir(root)):
        directory.mkdir(parents=True, exist_ok=True)


def resolve_project_virtual_path(root: Path, virtual_path: str) -> Path:
    """Resolve a sandbox virtual path inside the project's human folder.

    Raises:
        ValueError: If the path is outside ``/mnt/user-data`` or a traversal
                    attempt escapes the project root.
    """
    stripped = virtual_path.lstrip("/")
    if stripped != _VIRTUAL_PREFIX and not stripped.startswith(_VIRTUAL_PREFIX + "/"):
        raise ValueError(f"Path must start with /{_VIRTUAL_PREFIX}")

    relative = stripped[len(_VIRTUAL_PREFIX) :].lstrip("/")
    # The agent's conventional workspace path is an alias for the root itself.
    if relative == "workspace":
        relative = ""
    elif relative.startswith("workspace/"):
        relative = relative[len("workspace/") :]

    base = root.resolve()
    actual = (base / relative).resolve() if relative else base
    try:
        actual.relative_to(base)
    except ValueError:
        raise ValueError("Access denied: path traversal detected")
    return actual
