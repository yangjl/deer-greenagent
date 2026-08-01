"""Fail-closed tool boundary for DBTL-owned output trees.

Repository code, not a model tool, publishes validated DBTL packages. Ordinary
lead and subagent calls may read those files but cannot make filesystem content
look like a governed stage result.
"""

from __future__ import annotations

import posixpath
import re
import shlex
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.agents.middlewares.tool_result_meta import normalize_tool_result

_PATH_WRITE_TOOLS = frozenset({"write_file", "str_replace"})
_DBTL_SEGMENTS = ("outputs", "dbtl")
_STAGE_WORK_SEGMENTS = ("outputs", ".dbtl-stage-work")
_SHELL_PROTECTED_PATH = re.compile(
    r"(?:^|[\s'\";|&])(?P<path>(?:(?:\./)|(?:/mnt/user-data/(?:workspace/)?)|/)?outputs\s*/\s*(?:dbtl|\.dbtl-stage-work)(?:\s*/\s*[^\s'\";|&<>]+)*)",
    re.IGNORECASE,
)
_BLOCKED = "Error: {tool} blocked — DBTL outputs are governed by the stage adapter and are read-only without an exact stage-work grant."
_UNISOLATED_SHELL = "Error: bash blocked — this sandbox cannot enforce the DBTL worker's exact writable directory."


def _profile_path(path: str) -> str:
    """Quote one path for an Apple sandbox profile string."""
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _canonical_agent_path(value: str) -> str:
    """Normalize the aliases a project-scoped sandbox exposes for one path."""
    compact = re.sub(r"\s*/\s*", "/", value.strip().replace("\\", "/"))
    normalized = posixpath.normpath(compact)
    workspace_prefix = "/mnt/user-data/workspace/"
    if normalized.startswith(workspace_prefix):
        return "/mnt/user-data/" + normalized[len(workspace_prefix) :]
    if normalized == "/mnt/user-data/workspace":
        return "/mnt/user-data"
    if normalized == "outputs" or normalized.startswith("outputs/"):
        return f"/mnt/user-data/{normalized}"
    if normalized == "./outputs" or normalized.startswith("./outputs/"):
        return f"/mnt/user-data/{normalized[2:]}"
    return normalized


def _has_segments(value: Any, expected: tuple[str, str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = _canonical_agent_path(value)
    parts = tuple(part.lower() for part in normalized.split("/") if part not in {"", "."})
    return any(parts[index : index + 2] == expected for index in range(max(0, len(parts) - 1)))


def is_dbtl_owned_path(value: Any) -> bool:
    return _has_segments(value, _DBTL_SEGMENTS)


def is_dbtl_stage_work_path(value: Any) -> bool:
    return _has_segments(value, _STAGE_WORK_SEGMENTS)


class DbtlOutputPolicyMiddleware(AgentMiddleware):
    """Deny every model-facing write route into the governed output tree."""

    def __init__(
        self,
        *,
        writable_paths: Sequence[str] = (),
        shell_isolation: str = "literal",
    ) -> None:
        super().__init__()
        self._writable_paths = tuple(_canonical_agent_path(path).rstrip("/") for path in writable_paths if path.strip())
        self._shell_isolation = shell_isolation

    def _sandboxed_shell_request(self, request: ToolCallRequest, command: str) -> ToolCallRequest | ToolMessage:
        """Confine the shell's process tree instead of parsing shell syntax."""
        if self._shell_isolation == "deny":
            return normalize_tool_result(
                ToolMessage(
                    content=_UNISOLATED_SHELL,
                    tool_call_id=str(request.tool_call.get("id") or ""),
                )
            )
        if self._shell_isolation != "sandbox-exec":
            return request

        if self._writable_paths:
            rules = ["(deny file-write*)"]
            rules.extend(f"(allow file-write* (subpath {_profile_path(path)}))" for path in self._writable_paths)
            # Interpreters and compilers commonly need scratch files. These
            # locations are outside the project and are never published.
            rules.extend(
                [
                    '(allow file-write* (subpath "/private/tmp"))',
                    '(allow file-write* (subpath "/tmp"))',
                    f"(allow file-write* (subpath {_profile_path(tempfile.gettempdir())}))",
                    '(allow file-write* (literal "/dev/null"))',
                ]
            )
        else:
            rules = [
                '(deny file-write* (subpath "/mnt/user-data/outputs/dbtl"))',
                '(deny file-write* (subpath "/mnt/user-data/outputs/.dbtl-stage-work"))',
            ]
        profile = " ".join(["(version 1)", "(allow default)", *rules])
        isolated = f"sandbox-exec -p {shlex.quote(profile)} /bin/bash --noprofile --norc -c {shlex.quote(command)}"
        raw_args = request.tool_call.get("args")
        args = {**(raw_args if isinstance(raw_args, Mapping) else {}), "command": isolated}
        return request.override(tool_call={**request.tool_call, "args": args})

    def _stage_path_allowed(self, path: Any) -> bool:
        if not isinstance(path, str) or not is_dbtl_stage_work_path(path):
            return False
        candidate = _canonical_agent_path(path)
        return any(candidate == allowed or candidate.startswith(f"{allowed}/") for allowed in self._writable_paths)

    def _blocked(self, request: ToolCallRequest) -> ToolMessage | None:
        name = str(request.tool_call.get("name") or "")
        args = request.tool_call.get("args")
        arguments = args if isinstance(args, Mapping) else {}
        path = arguments.get("path")
        if name in _PATH_WRITE_TOOLS and (
            is_dbtl_owned_path(path)
            or (is_dbtl_stage_work_path(path) and not self._stage_path_allowed(path))
        ):
            return normalize_tool_result(
                ToolMessage(
                    content=_BLOCKED.format(tool=name),
                    tool_call_id=str(request.tool_call.get("id") or ""),
                )
            )
        if name == "bash":
            command = arguments.get("command")
            protected = list(_SHELL_PROTECTED_PATH.finditer(command)) if isinstance(command, str) else []
            if protected and any(
                is_dbtl_owned_path(match.group("path")) or not self._stage_path_allowed(match.group("path"))
                for match in protected
            ):
                return normalize_tool_result(
                    ToolMessage(
                        content=_BLOCKED.format(tool=name),
                        tool_call_id=str(request.tool_call.get("id") or ""),
                    )
                )
        return None

    def _prepare_request(self, request: ToolCallRequest) -> ToolCallRequest | ToolMessage:
        blocked = self._blocked(request)
        if blocked is not None:
            return blocked
        if str(request.tool_call.get("name") or "") != "bash":
            return request
        raw_args = request.tool_call.get("args")
        command = raw_args.get("command") if isinstance(raw_args, Mapping) else None
        if not isinstance(command, str):
            return request
        return self._sandboxed_shell_request(request, command)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        prepared = self._prepare_request(request)
        return prepared if isinstance(prepared, ToolMessage) else handler(prepared)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        prepared = self._prepare_request(request)
        return prepared if isinstance(prepared, ToolMessage) else await handler(prepared)
