"""Fail-closed tool boundary for DBTL-owned output trees.

Repository code, not a model tool, publishes validated DBTL packages. Ordinary
lead and subagent calls may read those files but cannot make filesystem content
look like a governed stage result.
"""

from __future__ import annotations

import posixpath
import re
import secrets
import shlex
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.agents.middlewares.tool_result_meta import normalize_tool_result
from deerflow.runtime.secret_context import write_pre_isolation_command
from deerflow.sandbox.heredoc import HEREDOC_START, heredoc_delimiter, heredoc_is_literal_data

_PATH_WRITE_TOOLS = frozenset({"write_file", "str_replace"})
_DBTL_SEGMENTS = ("outputs", "dbtl")
_STAGE_WORK_SEGMENTS = ("outputs", ".dbtl-stage-work")
_SHELL_PROTECTED_PATH = re.compile(
    r"(?:^|[\s'\";|&])(?P<path>(?:(?:\./)|(?:/mnt/user-data/(?:workspace/)?)|/)?outputs\s*/\s*(?:dbtl|\.dbtl-stage-work)(?:\s*/\s*[^\s'\";|&<>]+)*)",
    re.IGNORECASE,
)
_BLOCKED = "Error: {tool} blocked — DBTL outputs are governed by the stage adapter and are read-only without an exact stage-work grant."
_UNISOLATED_SHELL = "Error: bash blocked — this sandbox cannot enforce the DBTL worker's exact writable directory."
_VIRTUAL_DATA_PATH = re.compile(r"/mnt/user-data(?=/|$|[^\w./-])(?:/[^\s\"']*)?")
_VIRTUAL_COMMAND_ROOT = re.compile(r"/mnt/user-data(?:/(?:workspace|uploads|outputs))?(?=/|$|[^\w./-])")


def _quote_unquoted_virtual_command_roots(command: str) -> str:
    """Keep a mount root one shell word if local resolution later adds spaces."""
    parts: list[str] = []
    cursor = 0
    quote: str | None = None
    escaped = False
    for match in _VIRTUAL_COMMAND_ROOT.finditer(command):
        for char in command[cursor : match.start()]:
            if escaped:
                escaped = False
            elif char == "\\" and quote != "'":
                escaped = True
            elif char in {"'", '"'}:
                if quote == char:
                    quote = None
                elif quote is None:
                    quote = char
        parts.append(command[cursor : match.start()])
        root = match.group(0)
        parts.append(root if quote is not None else f"'{root}'")
        cursor = match.end()
    parts.append(command[cursor:])
    return "".join(parts)


def sandbox_exec_command(
    command: str,
    *,
    writable_paths: Sequence[str],
    readable_paths: Sequence[str] | None = None,
    restricted_read_roots: Sequence[str] = (),
) -> str:
    """Wrap a server-owned command in the same macOS write grant as Bash.

    Build verification does not travel through a model tool call, so there is
    no ``ToolCallRequest`` for this middleware to rewrite.  It still must run
    under the identical process-tree boundary; keeping the profile builder here
    prevents the model and server execution paths from drifting.
    """
    canonical = tuple(_canonical_agent_path(path).rstrip("/") for path in writable_paths if path.strip())
    rules = ["(deny file-write*)"]
    rules.extend(f"(allow file-write* (subpath {_profile_path(path)}))" for path in canonical)
    rules.extend(
        [
            '(allow file-write* (subpath "/private/tmp"))',
            '(allow file-write* (subpath "/tmp"))',
            f"(allow file-write* (subpath {_profile_path(tempfile.gettempdir())}))",
            '(allow file-write* (literal "/dev/null"))',
        ]
    )
    if readable_paths is not None:
        readable = tuple(_canonical_agent_path(path).rstrip("/") for path in readable_paths if path.strip())
        restricted = tuple(_canonical_agent_path(path).rstrip("/") for path in restricted_read_roots if path.strip())
        # Interpreters need their operating-system and package files. Restrict
        # the mounted project tree instead of denying every process read, then
        # open only the issued files and writable phase workspace inside it.
        rules.extend(f"(deny file-read-data (subpath {_profile_path(path)}))" for path in restricted)
        rules.extend(f"(allow file-read-data (subpath {_profile_path(path)}))" for path in canonical if path)
        rules.extend(f"(allow file-read-data (literal {_profile_path(path)}))" for path in readable if path)
    profile = " ".join(["(version 1)", "(allow default)", *rules])
    protected_command = _quote_unquoted_virtual_command_roots(command)
    return f"sandbox-exec -p {shlex.quote(profile)} /bin/bash --noprofile --norc -c {shlex.quote(protected_command)}"


def bubblewrap_exec_command(
    command: str,
    *,
    executable: str,
    writable_path: str,
    readable_paths: Sequence[str],
    restricted_root: str = "/mnt/user-data",
) -> str:
    """Confine a Linux sandbox process to issued project inputs.

    The sandbox container remains the outer security boundary. Bubblewrap adds
    the narrower Build contract inside it: the project mount is replaced with
    an empty tmpfs, then only the phase workspace and issued input files are
    mounted back. A provider without bubblewrap is refused by the caller's
    preflight instead of silently running with a broader read view.
    """
    workspace = _canonical_agent_path(writable_path).rstrip("/")
    readable = tuple(_canonical_agent_path(path).rstrip("/") for path in readable_paths if path.strip())
    restricted = _canonical_agent_path(restricted_root).rstrip("/")
    if not workspace.startswith(f"{restricted}/"):
        raise ValueError("The Build workspace is outside the restricted project root.")
    if any(not path.startswith(f"{restricted}/") for path in readable):
        raise ValueError("A Build input is outside the restricted project root.")

    parents: set[str] = {restricted}
    for path in (workspace, *readable):
        parent = posixpath.dirname(path)
        while parent.startswith(f"{restricted}/"):
            parents.add(parent)
            parent = posixpath.dirname(parent)

    args = [
        executable,
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        restricted,
    ]
    for parent in sorted(parents, key=lambda value: (value.count("/"), value)):
        if parent != restricted:
            args.extend(("--dir", parent))
    args.extend(("--bind", workspace, workspace))
    for path in readable:
        args.extend(("--ro-bind", path, path))
    args.extend(("--", "/bin/bash", "--noprofile", "--norc", "-c", command))
    return shlex.join(args)


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


def _protect_heredoc_virtual_paths(command: str) -> tuple[str, dict[str, str]]:
    """Replace virtual paths in heredoc bodies with server-bound placeholders.

    Local bash translates virtual path operands to host paths before execution.
    A heredoc body is data, not shell syntax, so applying that lexical rewrite
    there leaks the host workspace into JSON provenance and changes the stage
    result's bytes. The bash tool restores these placeholders only after path
    translation and only for the byte-identical middleware-authored wrapper.
    """
    lines = command.splitlines(keepends=True)
    protected: list[str] = []
    literals: dict[str, str] = {}
    delimiter: str | None = None
    strip_tabs = False
    protect_body = False

    for line in lines:
        candidate = line.rstrip("\r\n")
        comparable = candidate.lstrip("\t") if strip_tabs else candidate
        if delimiter is not None:
            if comparable == delimiter:
                delimiter = None
                strip_tabs = False
                protect_body = False
                protected.append(line)
                continue

            if not protect_body:
                protected.append(line)
                continue

            def replace_literal(match: re.Match[str]) -> str:
                token = f"__DEERFLOW_VIRTUAL_LITERAL_{secrets.token_hex(12)}__"
                literals[token] = match.group(0)
                return token

            protected.append(_VIRTUAL_DATA_PATH.sub(replace_literal, line))
            continue

        protected.append(line)
        marker = HEREDOC_START.search(candidate)
        if marker is not None:
            protect_body = heredoc_is_literal_data(candidate, marker)
            delimiter = heredoc_delimiter(marker)
            strip_tabs = bool(marker.group("strip"))

    return "".join(protected), literals


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

        protected_command, preserved_literals = _protect_heredoc_virtual_paths(command)
        if self._writable_paths:
            isolated = sandbox_exec_command(protected_command, writable_paths=self._writable_paths)
        else:
            rules = [
                '(deny file-write* (subpath "/mnt/user-data/outputs/dbtl"))',
                '(deny file-write* (subpath "/mnt/user-data/outputs/.dbtl-stage-work"))',
            ]
            profile = " ".join(["(version 1)", "(allow default)", *rules])
            isolated = f"sandbox-exec -p {shlex.quote(profile)} /bin/bash --noprofile --norc -c {shlex.quote(protected_command)}"
        # The wrapper is server-authored and names host scratch directories the
        # local-bash path guard excludes on purpose. Pair it with the model's
        # own command so that guard audits what the model asked for instead of
        # rejecting this middleware's text (which blocked every stage-worker
        # shell call, and only once a grant existed to put those paths in the
        # profile at all).
        write_pre_isolation_command(
            getattr(request.runtime, "context", None),
            authored=isolated,
            original=command,
            preserved_literals=preserved_literals,
        )
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
        if name in _PATH_WRITE_TOOLS and (is_dbtl_owned_path(path) or (is_dbtl_stage_work_path(path) and not self._stage_path_allowed(path))):
            return normalize_tool_result(
                ToolMessage(
                    content=_BLOCKED.format(tool=name),
                    tool_call_id=str(request.tool_call.get("id") or ""),
                )
            )
        if name == "bash":
            command = arguments.get("command")
            protected = list(_SHELL_PROTECTED_PATH.finditer(command)) if isinstance(command, str) else []
            # On macOS the process-level sandbox is the authority for shell
            # writes.  Do not treat every protected path *mentioned* in a
            # command as a write target: stage workers legitimately embed the
            # approved ``outputs/dbtl`` artifact path in JSON provenance, often
            # inside a heredoc.  Literal scanning cannot distinguish that data
            # from shell syntax, while ``sandbox-exec`` can enforce the actual
            # process-tree writes exactly.
            if self._shell_isolation != "sandbox-exec" and protected and any(is_dbtl_owned_path(match.group("path")) or not self._stage_path_allowed(match.group("path")) for match in protected):
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
