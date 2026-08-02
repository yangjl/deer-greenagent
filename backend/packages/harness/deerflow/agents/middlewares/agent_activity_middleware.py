"""Report the lead agent's presence as runtime activity.

The DBTL supervisor and the stage adapter can open their own spans because each
does its work inside one block. An agent cannot: the run begins in one graph
node and ends in another, so this middleware holds an open handle across hooks
and is responsible for closing it — the one obligation ``activity_span`` exists
to remove.

Two consequences follow from that, and both are deliberate.

**The handle is keyed by run, not held on the instance.** The middleware is
built once per agent and the agent is cached, so an instance attribute would be
shared by every concurrent run through it and the second run would close the
first one's row.

**Closing here is best-effort by construction.** ``aafter_agent`` does not run
when a run is cancelled, when the process loses its lease, or when the graph
raises past it. That is why the durable backstop lives in the run worker's
buffer instead: it settles anything still open when the run ends, so a missing
``aafter_agent`` costs a precise terminal state and never a permanent spinner.

Only the async hooks are implemented. LangGraph's sync path would need the
synchronous emitter, and every surface this projection feeds — Gateway runs, IM
channels, scheduled runs — is async. An embedded synchronous caller therefore
emits nothing rather than emitting something half-wired.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from deerflow.runtime.activity.emitter import ActivityHandle, make_activity_handle
from deerflow.runtime.activity.lineage import lead_activity_id
from deerflow.runtime.activity.vocabulary import ActivityState, ActorKind

logger = logging.getLogger(__name__)

#: Tools whose call means the lead agent is waiting on somebody else rather than
#: computing. Keeping this to delegation is intentional: every other tool is the
#: agent doing its own work, and a per-tool vocabulary would leak tool names into
#: a projection whose whole safety argument is that it carries none.
_DELEGATION_TOOLS = frozenset({"task"})

#: Upper bound on tracked open runs. A hook that never fires would otherwise
#: leak one handle per run for the process's lifetime; the worker's own backstop
#: closes the rows, this only bounds the bookkeeping.
_MAX_TRACKED_RUNS = 64


def _run_id(runtime: Runtime | None) -> str | None:
    context = getattr(runtime, "context", None)
    value = context.get("run_id") if isinstance(context, dict) else getattr(context, "run_id", None)
    return value if isinstance(value, str) and value else None


class AgentActivityMiddleware(AgentMiddleware[AgentState]):
    """Open one activity row for an agent invocation and keep it honest."""

    def __init__(
        self,
        *,
        actor_kind: ActorKind = ActorKind.LEAD_AGENT,
        actor_id: str = "lead-agent",
        display_name: str | None = None,
    ) -> None:
        super().__init__()
        self._actor_kind = actor_kind
        self._actor_id = actor_id
        self._display_name = display_name
        self._handles: dict[str, ActivityHandle] = {}

    async def _settle(self, run_id: str, state: ActivityState) -> None:
        handle = self._handles.pop(run_id, None)
        if handle is not None:
            await handle.settle(state)

    def _current(self, runtime: Runtime | None) -> ActivityHandle | None:
        run_id = _run_id(runtime)
        if run_id is None:
            return None
        handle = self._handles.get(run_id)
        return None if handle is None or handle.settled else handle

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        run_id = _run_id(runtime)
        if run_id is None or run_id in self._handles:
            # No run identity means no row a reader could reconcile with
            # anything, and an already-open row means this hook ran twice.
            return None

        while len(self._handles) >= _MAX_TRACKED_RUNS:
            oldest, handle = next(iter(self._handles.items()))
            self._handles.pop(oldest, None)
            await handle.settle(ActivityState.INTERRUPTED)

        handle = make_activity_handle(
            run_id=run_id,
            actor_kind=self._actor_kind,
            actor_id=self._actor_id,
            operation="lead.respond",
            state=ActivityState.PREPARING,
            display_name=self._display_name,
            # Derived rather than random so a delegation several graph nodes
            # away can name this row as its dispatcher without anything having
            # been handed to it. Safe to derive because exactly one lead row
            # exists per run: the guard above refuses a second opening.
            activity_id=lead_activity_id(run_id),
        )
        self._handles[run_id] = handle
        await handle.open()
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        run_id = _run_id(runtime)
        if run_id is not None:
            await self._settle(run_id, ActivityState.COMPLETED)
        return None

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        handle = self._current(getattr(request, "runtime", None))
        if handle is not None:
            await handle.update(state=ActivityState.THINKING, operation="lead.respond")
        return await handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        handle = self._current(getattr(request, "runtime", None))
        if handle is not None:
            delegating = _tool_name(request) in _DELEGATION_TOOLS
            await handle.update(
                state=ActivityState.WAITING if delegating else ActivityState.COMPUTING,
                operation="lead.wait" if delegating else "lead.respond",
            )
        return await handler(request)


def _tool_name(request: Any) -> str:
    call = getattr(request, "tool_call", None)
    if isinstance(call, dict):
        name = call.get("name")
        return name if isinstance(name, str) else ""
    return ""
