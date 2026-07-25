"""Tell the agent where it lives: project folder and shared local folders.

Two knowledge gaps produced the same failure ("I can't see your local
machine's filesystem" + copy-paste terminal commands, observed live):

1. A conversation filed into a project has ``/mnt/user-data/workspace``
   mounted on the project's human-visible folder (e.g.
   ``~/Documents/projects/G2F``) — but nothing said so.
2. Operator-configured ``sandbox.mounts`` expose other human folders (e.g.
   ``~/Documents/projects`` at ``/mnt/projects``) — but the model has no way
   to know a host path the human mentions maps to a container path it can
   write.

Both reminders are appended per model request via ``wrap_model_call``
(mirroring ``SystemMessageCoalescingMiddleware``): never checkpointed, always
current, merged into the single leading system message by the coalescing
middleware that runs after this one.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import PurePath
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)


def build_project_reminder(project_root: str | None) -> str | None:
    """The project-context block for a scoped conversation, or ``None``."""
    if not isinstance(project_root, str) or not project_root:
        return None
    name = PurePath(project_root).name or project_root
    return (
        "<project_context>\n"
        f"This conversation belongs to the project '{name}'.\n"
        f"Your workspace /mnt/user-data/workspace IS the human's local folder {project_root} — "
        "they are the same directory. Files you create or edit in your workspace appear "
        "immediately on the human's machine, and files the human puts in that folder are "
        "already in your workspace.\n"
        f"When the human mentions the path {project_root} (or files inside it), operate on "
        "/mnt/user-data/workspace directly with your file and bash tools — do NOT tell them "
        "you cannot access their filesystem, and do NOT hand back copy-paste terminal "
        "commands for work you can do yourself.\n"
        "</project_context>"
    )


def build_mounts_reminder(mounts) -> str | None:
    """The shared-folders block for operator-configured sandbox mounts.

    Maps each human (host) path to the container path the agent's tools
    accept, so a host path the human mentions is actionable instead of
    "inaccessible".
    """
    lines: list[str] = []
    for mount in mounts or []:
        host_path = getattr(mount, "host_path", None)
        container_path = getattr(mount, "container_path", None)
        if not host_path or not container_path:
            continue
        access = "read-only" if getattr(mount, "read_only", False) else "read-write"
        lines.append(f"- The human's folder {host_path} is available to you at {container_path} ({access}).")
    if not lines:
        return None
    return (
        "<local_folders>\n"
        "These folders on the human's machine are shared with you:\n" + "\n".join(lines) + "\nWhen the human mentions a path under one of these host folders, translate it to "
        "the matching container path and operate on it directly with your file and bash "
        "tools (respecting read-only mounts) — do NOT say you cannot access their "
        "filesystem.\n"
        "</local_folders>"
    )


def _configured_mounts():
    """Operator-configured sandbox mounts; empty when config is unavailable."""
    try:
        from deerflow.config import get_app_config

        return get_app_config().sandbox.mounts or []
    except Exception:
        return []


class ProjectContextMiddleware(AgentMiddleware[AgentState]):
    """Append project + shared-folder context to model requests."""

    def __init__(self, mounts_provider: Callable[[], list] | None = None) -> None:
        super().__init__()
        # Injectable for tests; the default reads live config every request so
        # config.yaml mount edits apply on the next message.
        self._mounts_provider = mounts_provider or _configured_mounts

    def _with_reminder(self, request: ModelRequest) -> ModelRequest:
        context = getattr(getattr(request, "runtime", None), "context", None) or {}
        blocks = [
            block
            for block in (
                build_project_reminder(context.get("project_root")),
                build_mounts_reminder(self._mounts_provider()),
            )
            if block is not None
        ]
        if not blocks:
            return request
        messages = [*request.messages, SystemMessage(content="\n\n".join(blocks))]
        override = getattr(request, "override", None)
        if callable(override):
            return override(messages=messages)
        request.messages = messages
        return request

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._with_reminder(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._with_reminder(request))
