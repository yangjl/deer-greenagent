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
from html import escape
from pathlib import Path, PurePath
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage, SystemMessage

from deerflow.agents.memory.scope import memory_scope_label
from deerflow.agents.middlewares.dynamic_context_middleware import (
    dynamic_memory_scope,
    is_dynamic_memory_reminder,
)
from deerflow.runtime.context_keys import is_project_scoped_context

logger = logging.getLogger(__name__)
_CURRENT_PROJECT_DATA_KEY = "current_project_data"


def build_project_reminder(project_root: str | None) -> str | None:
    """The project-context block for a scoped conversation, or ``None``."""
    if not isinstance(project_root, str) or not project_root:
        return None
    name = escape(PurePath(project_root).name or project_root, quote=False)
    safe_root = escape(project_root, quote=False)
    return (
        "<project_context>\n"
        f"This conversation belongs to the project '{name}'.\n"
        f"Your workspace /mnt/user-data/workspace IS the human's local folder {safe_root} — "
        "they are the same directory. Files you create or edit in your workspace appear "
        "immediately on the human's machine, and files the human puts in that folder are "
        "already in your workspace.\n"
        f"When the human mentions the path {safe_root} (or files inside it), operate on "
        "/mnt/user-data/workspace directly with your file and bash tools — do NOT tell them "
        "you cannot access their filesystem, and do NOT hand back copy-paste terminal "
        "commands for work you can do yourself.\n"
        "This is the canonical and only default location for requests about 'this project' "
        "or relative project files. For example, create README.md at "
        "/mnt/user-data/workspace/README.md. Do not use /mnt/projects or another shared "
        "folder unless the human explicitly names a different location.\n"
        f"The selected project '{name}' is authoritative for this request. Ignore any "
        "earlier memory or assistant response that claims a different project is current; "
        "do not switch projects unless the human explicitly asks to switch.\n"
        "</project_context>"
    )


def build_current_project_data(project_root: str | None) -> str | None:
    """Return a compact, current-turn project identity data block."""
    if not isinstance(project_root, str) or not project_root:
        return None
    name = escape(PurePath(project_root).name or project_root, quote=False)
    safe_root = escape(project_root, quote=False)
    return f"<current_project_data>\nname: {name}\nhuman_path: {safe_root}\nagent_workspace: /mnt/user-data/workspace\n</current_project_data>"


def build_parked_design_reminder(value: object) -> str | None:
    """Framework-owned context for ordinary work while a DBTL cycle is parked."""
    if not isinstance(value, dict) or value.get("approval_status") != "unapproved":
        return None
    evidence = value.get("evidence")
    if not isinstance(evidence, dict):
        return None
    uri = str(evidence.get("uri") or "").strip()
    content_hash = str(evidence.get("content_hash") or "").strip()
    if not uri or not content_hash:
        return None
    title = escape(str(value.get("cycle_title") or value.get("cycle_id") or "DBTL cycle"), quote=False)
    return (
        "<parked_dbtl_context>\n"
        f"The DBTL cycle '{title}' is parked for ordinary work with the lead agent.\n"
        "The following Design package is context only and is explicitly UNAPPROVED. "
        "You may discuss, inspect, or help revise it, but must not describe it as gate passage "
        "or use it to start Build/Test/Learn.\n"
        f"uri: {escape(uri, quote=False)}\n"
        f"sha256: {escape(content_hash, quote=False)}\n"
        "The project owner must return to the authenticated feedback deck to move the cycle.\n"
        "</parked_dbtl_context>"
    )


_MAX_STATUS_CYCLE_LINES = 5


def _status_cycle_line(cycle: object) -> str | None:
    if not isinstance(cycle, dict):
        return None
    cycle_id = str(cycle.get("cycle_id") or "").strip()
    if not cycle_id:
        return None
    title = escape(str(cycle.get("title") or cycle_id), quote=False)
    state = escape(str(cycle.get("state") or "unknown").replace("_", " "), quote=False)
    stages = cycle.get("stages")
    detail = ""
    if isinstance(stages, dict) and stages:
        detail = "; stages: " + ", ".join(f"{escape(str(stage), quote=False)}={escape(str(status).replace('_', ' '), quote=False)}" for stage, status in stages.items())
    parked = " (parked for ordinary work)" if cycle.get("parked") else ""
    return f"- {title} [{escape(cycle_id, quote=False)}] is at {state}{parked}{detail}."


def build_dbtl_status_reminder(value: object) -> str | None:
    """State the governed work around this conversation, and its hard limit.

    The failure this exists for was not a missing rule but missing knowledge:
    the lead agent answered "start to build following the approved design" with
    a plausible, ungoverned Build because nothing had told it a governed cycle
    owned that work. Naming the cycles and any waiting control lets it say where
    the boundary is.

    The closing paragraph is deliberately blunt and non-negotiable. Awareness of
    a stage is not permission to enter it, and the model must not read a status
    line as an invitation. This is a prompt, so it is not the enforcement — the
    routing fence and the stage-owned output paths are — but a model that knows
    the boundary explains it instead of walking into a refusal.
    """
    if not isinstance(value, dict):
        return None
    cycles = value.get("cycles")
    lines = [line for cycle in (cycles if isinstance(cycles, list) else []) if (line := _status_cycle_line(cycle))][:_MAX_STATUS_CYCLE_LINES]
    control = value.get("pending_control")
    control_lines: list[str] = []
    if isinstance(control, dict) and str(control.get("next_stage") or ""):
        next_stage = escape(str(control["next_stage"]).replace("_", " "), quote=False)
        approved = escape(str(control.get("approved_stage") or "").replace("_", " "), quote=False)
        answered = str(control.get("answered_with") or "")
        if answered == "hold_here":
            control_lines.append(f"The project owner approved {approved} and explicitly chose to hold: {next_stage} is open but deliberately not started.")
        elif answered:
            control_lines.append(f"The project owner has already answered the {next_stage} start prompt.")
        else:
            control_lines.append(f"A start-or-hold decision for {next_stage} is still waiting on the project owner.")
    if not lines and not control_lines:
        return None
    body = ""
    if lines:
        body += "Governed DBTL cycles in this project:\n" + "\n".join(lines) + "\n"
    if control_lines:
        body += "\n".join(control_lines) + "\n"
    return (
        "<dbtl_status>\n"
        "This conversation belongs to a project with governed Design/Build/Test/Learn work. "
        "The following is a read-only status snapshot, current as of this request.\n"
        f"{body}"
        "You are NOT the workflow owner and this snapshot grants you no authority over it. "
        "You may discuss this work, read its files, explain what a stage contains, and help "
        "prepare for it. You must NOT start, run, advance, approve, reject, or record any "
        "stage, and you must NOT describe ordinary work you do as a stage result, a gate "
        "passage, or an approval — even if the person asks you to.\n"
        "If the request is to start or approve a stage, say plainly that the governed "
        "workflow owns that action and point at the waiting control or the project review "
        "sheet. Do not produce a substitute for it.\n"
        "</dbtl_status>"
    )


def _insert_before_latest_visible_user(messages: list, message: HumanMessage) -> list:
    for index in range(len(messages) - 1, -1, -1):
        candidate = messages[index]
        if not isinstance(candidate, HumanMessage):
            continue
        if candidate.additional_kwargs.get("hide_from_ui"):
            continue
        return [*messages[:index], message, *messages[index:]]
    return [*messages, message]


def build_mounts_reminder(mounts, project_root: str | None = None) -> str | None:
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
        if project_root:
            try:
                resolved_project = Path(project_root).resolve()
                resolved_mount = Path(host_path).expanduser().resolve()
                if resolved_project == resolved_mount or resolved_project.is_relative_to(resolved_mount):
                    continue
            except OSError:
                pass
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
        project_scoped = is_project_scoped_context(context)
        project_data = build_current_project_data(context.get("project_root"))
        blocks = [
            block
            for block in (
                build_project_reminder(context.get("project_root")),
                build_mounts_reminder(self._mounts_provider(), context.get("project_root")),
                build_parked_design_reminder(context.get("dbtl_parked_design_brief")),
                build_dbtl_status_reminder(context.get("dbtl_status_snapshot")),
            )
            if block is not None
        ]
        messages = list(request.messages)
        if project_scoped:
            # Frozen user-global memory is persisted in a thread checkpoint.
            # Strip it on every scoped request so existing conversations are
            # repaired immediately and cannot inherit facts about a sibling
            # project. The framework-owned date reminder remains intact.
            expected_memory_scope = memory_scope_label(context)
            messages = [message for message in messages if not is_dynamic_memory_reminder(message) or dynamic_memory_scope(message) == expected_memory_scope]
        if not blocks and len(messages) == len(request.messages):
            return request
        if blocks:
            messages.append(SystemMessage(content="\n\n".join(blocks)))
        if project_data:
            messages = _insert_before_latest_visible_user(
                messages,
                HumanMessage(
                    content=project_data,
                    additional_kwargs={
                        "hide_from_ui": True,
                        _CURRENT_PROJECT_DATA_KEY: True,
                    },
                ),
            )
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
