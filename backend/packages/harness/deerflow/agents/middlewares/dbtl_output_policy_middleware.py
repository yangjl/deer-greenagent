"""Fail-closed tool boundary for DBTL-owned output trees.

Repository code, not a model tool, publishes validated DBTL packages. Ordinary
lead and subagent calls may read those files but cannot make filesystem content
look like a governed stage result.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.agents.middlewares.tool_result_meta import normalize_tool_result

_PATH_WRITE_TOOLS = frozenset({"write_file", "str_replace"})
_DBTL_SEGMENTS = ("outputs", "dbtl")
_SHELL_DBTL_PATH = re.compile(
    r"(?:^|[\s'\";|&])(?:(?:\./)|(?:/mnt/user-data/(?:workspace/)?)|/)?outputs\s*/\s*dbtl(?:/|[\s'\";|&]|$)",
    re.IGNORECASE,
)
_BLOCKED = "Error: {tool} blocked — outputs/dbtl is governed by the DBTL stage adapter and is read-only to agent tools."


def is_dbtl_owned_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = posixpath.normpath(value.strip().replace("\\", "/"))
    parts = tuple(part.lower() for part in normalized.split("/") if part not in {"", "."})
    return any(parts[index : index + 2] == _DBTL_SEGMENTS for index in range(max(0, len(parts) - 1)))


class DbtlOutputPolicyMiddleware(AgentMiddleware):
    """Deny every model-facing write route into the governed output tree."""

    @staticmethod
    def _blocked(request: ToolCallRequest) -> ToolMessage | None:
        name = str(request.tool_call.get("name") or "")
        args = request.tool_call.get("args")
        arguments = args if isinstance(args, Mapping) else {}
        if name in _PATH_WRITE_TOOLS and is_dbtl_owned_path(arguments.get("path")):
            return normalize_tool_result(
                ToolMessage(
                    content=_BLOCKED.format(tool=name),
                    tool_call_id=str(request.tool_call.get("id") or ""),
                )
            )
        if name == "bash":
            command = arguments.get("command")
            if isinstance(command, str) and _SHELL_DBTL_PATH.search(command):
                return normalize_tool_result(
                    ToolMessage(
                        content=_BLOCKED.format(tool=name),
                        tool_call_id=str(request.tool_call.get("id") or ""),
                    )
                )
        return None

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        return self._blocked(request) or handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        return self._blocked(request) or await handler(request)
