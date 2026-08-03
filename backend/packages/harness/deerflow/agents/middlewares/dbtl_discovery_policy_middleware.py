"""Fail-closed read-only tool policy for pre-cycle DBTL discovery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.agents.middlewares.tool_result_meta import normalize_tool_result

DBTL_DISCOVERY_CONTEXT_KEY = "dbtl_discovery_context"

DISCOVERY_READ_ONLY_TOOLS = frozenset(
    {
        "ls",
        "glob",
        "grep",
        "read_file",
        "list_uploaded_files",
        "view_image",
        "web_search",
        "web_fetch",
        "image_search",
        "memory_search",
    }
)


def _runtime_context(request: ModelRequest | ToolCallRequest) -> dict | None:
    context = getattr(getattr(request, "runtime", None), "context", None)
    return context if isinstance(context, dict) else None


def _discovery_active(request: ModelRequest | ToolCallRequest) -> bool:
    context = _runtime_context(request)
    discovery = context.get(DBTL_DISCOVERY_CONTEXT_KEY) if context is not None else None
    return isinstance(discovery, dict) and discovery.get("active") is True


class DbtlDiscoveryPolicyMiddleware(AgentMiddleware):
    """Expose inspection only and defend execution against forged tool calls."""

    @staticmethod
    def _filter(request: ModelRequest) -> ModelRequest:
        if not _discovery_active(request):
            return request
        return request.override(tools=[tool for tool in request.tools if str(getattr(tool, "name", "")) in DISCOVERY_READ_ONLY_TOOLS])

    @staticmethod
    def _blocked(request: ToolCallRequest) -> ToolMessage | None:
        if not _discovery_active(request):
            return None
        name = str(request.tool_call.get("name") or "")
        if name in DISCOVERY_READ_ONLY_TOOLS:
            return None
        return normalize_tool_result(
            ToolMessage(
                content=(f"Error: {name or 'tool'} blocked — DBTL discovery is read-only. Start the cycle or continue as ordinary work before using mutation, execution, connector-write, or delegation tools."),
                tool_call_id=str(request.tool_call.get("id") or "missing_tool_call_id"),
                name=name or None,
                status="error",
            )
        )

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        return handler(self._filter(request))

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelCallResult:
        return await handler(self._filter(request))

    @override
    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]) -> ToolMessage | Command:
        blocked = self._blocked(request)
        return blocked if blocked is not None else handler(request)

    @override
    async def awrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]) -> ToolMessage | Command:
        blocked = self._blocked(request)
        return blocked if blocked is not None else await handler(request)
