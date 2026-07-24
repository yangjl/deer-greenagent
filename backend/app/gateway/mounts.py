"""Read-only view over operator-configured ``sandbox.mounts`` for Gateway routes."""

import logging
from pathlib import Path

from deerflow.config import get_app_config

logger = logging.getLogger(__name__)


def list_existing_custom_mounts():
    """Operator-configured ``sandbox.mounts`` whose host paths exist.

    Mirrors the existence filter used by the sandbox providers so the Gateway
    file views expose exactly the mounts the agent can actually reach.
    Config load failures degrade to "no mounts" instead of failing the request.
    """
    try:
        config = get_app_config()
    except Exception:
        logger.warning("Failed to load app config while resolving custom mounts", exc_info=True)
        return []
    if not config.sandbox or not config.sandbox.mounts:
        return []
    return [mount for mount in config.sandbox.mounts if Path(mount.host_path).exists()]


def normalize_container_path(container_path: str) -> str:
    """Canonical ``/mnt/...`` form of a mount container path."""
    return "/" + container_path.strip("/")


def host_root_for_container_path(container_path: str) -> Path | None:
    """Map a container path to its host directory via configured mounts.

    The longest matching mount ``container_path`` prefix wins, mirroring the
    sandbox tools' own mount resolution. Returns ``None`` when no mount
    matches or the relative part escapes the mount root.
    """
    normalized = normalize_container_path(container_path)
    best: tuple[str, object] | None = None
    for mount in list_existing_custom_mounts():
        prefix = normalize_container_path(mount.container_path)
        if normalized == prefix or normalized.startswith(prefix + "/"):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, mount)
    if best is None:
        return None

    prefix, mount = best
    root = Path(mount.host_path).resolve()
    relative = normalized[len(prefix) :].lstrip("/")
    actual = (root / relative).resolve() if relative else root
    try:
        actual.relative_to(root)
    except ValueError:
        return None
    return actual
