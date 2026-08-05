"""Private runtime context keys shared across DeerFlow runtime components."""

from collections.abc import Mapping
from typing import Final

CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY: Final[str] = "__deerflow_pre_run_message_ids"
RUN_EVENT_STORE_CONFIG_KEY: Final[str] = "__deerflow_run_event_store"
THREAD_STORE_CONFIG_KEY: Final[str] = "__deerflow_thread_store"


def is_project_scoped_context(context: object) -> bool:
    """Return whether runtime context identifies a durable project scope.

    ``project_id`` is the durable authority, while ``project_root`` is its
    resolved storage projection. Treat either non-empty value as scoped so a
    temporarily unavailable root cannot fall back to project-independent
    memory.
    """
    if not isinstance(context, Mapping):
        return False
    return any(isinstance(context.get(key), str) and bool(context.get(key)) for key in ("project_id", "project_root"))
