"""Emit runtime agent-activity transitions.

Two rules govern everything here, and they pull against each other.

**A span always closes.** An activity row left open is a spinner that never
stops, which is the precise failure this feature exists to remove — so the
context manager emits a terminal transition on normal return, on exception, and
on cancellation.

**A span never raises.** Instrumentation that can end a run is worse than no
instrumentation, so every emission is wrapped: a missing stream writer, a
rejected envelope, or a bridge outage costs the row and nothing else.

The current activity id travels as a :class:`~contextvars.ContextVar`, not as a
callback handler. ``_copy_isolated_subagent_context`` copies ambient ContextVars
into the persistent subagent loop while stripping ``deerflow_loop_bound``
handlers, so a ContextVar survives the crossing and a handler touching the store
would not. It is deliberately never written to ``configurable``: that section is
checkpointed, and a lineage accepted from there would keep asserting itself on
later turns after the work it describes had ended.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any, Final

from deerflow.runtime.activity.envelope import ActivityScope, build_activity_event
from deerflow.runtime.activity.vocabulary import (
    OPERATION_LABELS,
    TERMINAL_STATES,
    ActivityState,
    ActivityTransition,
    ActorKind,
    resolve_display_name,
)
from deerflow.utils.custom_events import aemit_custom_event

logger = logging.getLogger(__name__)

_CURRENT_ACTIVITY_ID: ContextVar[str | None] = ContextVar("deerflow_current_activity_id", default=None)
_CURRENT_ACTIVITY: ContextVar["ActivityHandle | None"] = ContextVar("deerflow_current_activity", default=None)

#: Runtime-context channel for the current activity id, for the rare boundary
#: that resets ContextVars. ``__``-prefixed so ``build_run_config`` strips a
#: caller-supplied value: lineage is server-authored or it is nothing.
ACTIVITY_CONTEXT_KEY: Final[str] = "__deerflow_activity_id"

_UNSET: Final[object] = object()


def new_activity_id() -> str:
    """Return an id unique to one actor *invocation*, not to one actor type."""
    return f"act_{uuid.uuid4().hex[:24]}"


def current_activity_id() -> str | None:
    """Return the innermost enclosing activity id, if any."""
    return _CURRENT_ACTIVITY_ID.get()


def current_activity() -> "ActivityHandle | None":
    """Return the innermost enclosing open handle, if any.

    Lets a long method report its own progress without threading a handle
    through every call it makes. Deliberately returns the handle rather than
    just its id: the id names a row, while reporting what that row is *doing*
    needs the object that can emit.
    """
    handle = _CURRENT_ACTIVITY.get()
    if handle is None or handle.settled:
        return None
    return handle


@contextmanager
def activity_parent_context(activity_id: str | None) -> Iterator[None]:
    """Make ``activity_id`` the parent of activity created in this block.

    This is intentionally not a span: it emits no row of its own. It is for a
    boundary where the parent already emitted and settled its routing work but
    the child graph creates its own authoritative actor row. Clearing the
    current handle prevents the child from accidentally reporting progress on
    that already-settled parent while preserving its lineage id.
    """
    id_token = _CURRENT_ACTIVITY_ID.set(activity_id)
    handle_token = _CURRENT_ACTIVITY.set(None)
    try:
        yield
    finally:
        _CURRENT_ACTIVITY.reset(handle_token)
        _CURRENT_ACTIVITY_ID.reset(id_token)


def resolve_writer() -> Any | None:
    """Return LangGraph's stream writer for the current node, or ``None``.

    Outside a graph invocation there is no writer and no stream to write to;
    that is a normal state (a unit test, a background task), not an error.
    """
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except (RuntimeError, ImportError):
        return None


class ActivityHandle:
    """Live handle on one open activity.

    Coalescing lives here as well as in the frontend reducer. A token-rate
    caller that re-reports the same state should not put one event per token on
    the wire before the browser gets a chance to drop it.
    """

    __slots__ = ("_base", "_dynamic_writer", "_opened", "_operation", "_settled", "_state", "_writer", "activity_id")

    def __init__(self, *, activity_id: str, base: dict[str, Any], state: ActivityState, operation: str, writer: Any | None, dynamic_writer: bool = False):
        self.activity_id = activity_id
        self._base = base
        self._state = state
        self._operation = operation
        self._writer = writer
        # A writer belongs to the graph node that asked for it. A span lives
        # inside one node so capturing is fine, but a handle held across hooks
        # opens in one node and closes in another — a captured writer there is
        # bound to a task that has already finished. Resolving per emit is what
        # makes the middleware's closing transition actually reach the stream.
        self._dynamic_writer = dynamic_writer
        self._opened = False
        self._settled = False

    @property
    def settled(self) -> bool:
        return self._settled

    @property
    def parent_activity_id(self) -> str | None:
        return self._base.get("parent_activity_id")

    @property
    def dispatcher_activity_id(self) -> str | None:
        return self._base.get("dispatcher_activity_id")

    def lineage_fields(self) -> dict[str, str]:
        """Return this row's ids, for enriching an event stream that predates it.

        The `task_*` events are the detailed worker-progress contract and stay
        that; these keys let a reader join one of those workers to the activity
        tree without the frontend inferring lineage from display names or
        `dbtl_stage`, which prove nothing about who dispatched whom. Absent
        values are omitted rather than sent as null, so an older consumer sees
        exactly the payload it saw before.
        """
        fields = {
            "activity_id": self.activity_id,
            "parent_activity_id": self.parent_activity_id,
            "dispatcher_activity_id": self.dispatcher_activity_id,
        }
        return {key: value for key, value in fields.items() if isinstance(value, str) and value}

    async def open(self) -> None:
        """Emit this activity's opening transition. Later calls are no-ops."""
        if self._opened or self._settled:
            return
        self._opened = True
        await self._emit(ActivityTransition.STARTED, self._state, self._operation)

    async def _emit(self, transition: ActivityTransition, state: ActivityState, operation: str) -> None:
        writer = resolve_writer() if self._dynamic_writer else self._writer
        if writer is None:
            return
        try:
            payload = build_activity_event(transition=transition, state=state, operation=operation, **self._base)
            await aemit_custom_event(payload, writer=writer)
        except Exception:  # noqa: BLE001 - instrumentation must never end a run
            logger.debug("Dropping agent activity event for %s", self.activity_id, exc_info=True)

    async def update(self, *, state: ActivityState | str | None = None, operation: str | None = None) -> None:
        """Report a state or operation change.

        A terminal state is refused here: closing an activity is the span's job,
        so that a body which merely *mentions* failure cannot leave the exit
        path with nothing to emit.

        Every argument is validated **before** the handle's last-known-good
        state is touched. Storing first and validating inside ``_emit`` cost two
        events rather than one: the rejected value stuck to the handle, so the
        span's own closing emit was built from it and was rejected too — one bad
        update left a row open forever, which is the failure this whole module
        exists to prevent.
        """
        if self._settled:
            return

        next_state = self._state if state is None else state
        try:
            resolved_state = ActivityState(next_state)
        except ValueError:
            logger.debug("Ignoring unknown agent activity state %r", next_state)
            return
        if resolved_state in TERMINAL_STATES:
            logger.debug("Ignoring terminal state %r on an update; a span closes itself", resolved_state)
            return

        next_operation = self._operation if operation is None else operation
        if next_operation not in OPERATION_LABELS:
            logger.debug("Ignoring unregistered agent activity operation %r", next_operation)
            return

        if resolved_state is self._state and next_operation == self._operation:
            return

        self._state = resolved_state
        self._operation = next_operation
        await self._emit(ActivityTransition.UPDATED, resolved_state, next_operation)

    async def settle(self, state: ActivityState | str, *, operation: str | None = None) -> None:
        """Close this activity explicitly. Later calls, and the span's own exit, are no-ops."""
        if self._settled:
            return
        try:
            resolved_state = ActivityState(state)
        except ValueError:
            resolved_state = ActivityState.COMPLETED
        if resolved_state not in TERMINAL_STATES:
            resolved_state = ActivityState.COMPLETED

        self._settled = True
        self._state = resolved_state
        # Same rule as ``update``: an unregistered operation is ignored rather
        # than stored, so a caller's bad label cannot make the closing emit
        # unbuildable and strand the row it was closing.
        if operation is not None and operation in OPERATION_LABELS:
            self._operation = operation
        await self._emit(ActivityTransition(resolved_state.value), resolved_state, self._operation)


def make_activity_handle(
    *,
    run_id: str,
    actor_kind: ActorKind | str,
    actor_id: str,
    operation: str,
    state: ActivityState | str = ActivityState.PREPARING,
    display_name: str | None = None,
    stage: str | None = None,
    index: int | None = None,
    parent_activity_id: str | None | object = _UNSET,
    dispatcher_activity_id: str | None | object = _UNSET,
    scope: ActivityScope | None = None,
    activity_id: str | None = None,
    writer: Any | None | object = _UNSET,
) -> ActivityHandle:
    """Build an unopened handle.

    Exists because a span is not always the right shape: an agent middleware
    opens in one hook and closes in another, so it cannot hold a context
    manager across the gap. Callers that take this route own the closing
    themselves, and must arrange one on every exit — which is exactly what
    ``activity_span`` gives for free, so prefer the span wherever the work fits
    inside one block.
    """
    dynamic_writer = writer is _UNSET
    resolved_writer = None if dynamic_writer else writer
    resolved_id = activity_id or new_activity_id()
    parent = current_activity_id() if parent_activity_id is _UNSET else parent_activity_id
    dispatcher = parent if dispatcher_activity_id is _UNSET else dispatcher_activity_id

    base = {
        "activity_id": resolved_id,
        "run_id": run_id,
        "actor_kind": actor_kind,
        "actor_id": actor_id,
        "display_name": display_name or resolve_display_name(actor_kind, stage=stage, index=index),
        "parent_activity_id": parent if isinstance(parent, str) else None,
        "dispatcher_activity_id": dispatcher if isinstance(dispatcher, str) else None,
        "scope": scope,
    }

    try:
        initial_state = ActivityState(state)
    except ValueError:
        initial_state = ActivityState.PREPARING
    if initial_state in TERMINAL_STATES:
        initial_state = ActivityState.PREPARING

    return ActivityHandle(
        activity_id=resolved_id,
        base=base,
        state=initial_state,
        operation=operation,
        writer=resolved_writer,
        dynamic_writer=dynamic_writer,
    )


@asynccontextmanager
async def activity_span(
    *,
    run_id: str,
    actor_kind: ActorKind | str,
    actor_id: str,
    operation: str,
    state: ActivityState | str = ActivityState.PREPARING,
    display_name: str | None = None,
    stage: str | None = None,
    index: int | None = None,
    parent_activity_id: str | None | object = _UNSET,
    dispatcher_activity_id: str | None | object = _UNSET,
    scope: ActivityScope | None = None,
    activity_id: str | None = None,
    writer: Any | None | object = _UNSET,
) -> AsyncIterator[ActivityHandle]:
    """Open one activity for the duration of the block.

    ``parent_activity_id`` defaults to the innermost enclosing span, which is
    what makes the dispatch tree fall out of ordinary nesting.
    ``dispatcher_activity_id`` defaults to the parent but stays a separate
    argument, because a runtime wrapper may group work visually without being
    the component that authorized it.
    """
    handle = make_activity_handle(
        run_id=run_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        operation=operation,
        state=state,
        display_name=display_name,
        stage=stage,
        index=index,
        parent_activity_id=parent_activity_id,
        dispatcher_activity_id=dispatcher_activity_id,
        scope=scope,
        activity_id=activity_id,
        writer=writer,
    )

    token = _CURRENT_ACTIVITY_ID.set(handle.activity_id)
    handle_token = _CURRENT_ACTIVITY.set(handle)
    try:
        # The opening emit belongs *inside* the guard. ``aemit_custom_event``
        # hands the payload to the writer synchronously and then awaits a
        # best-effort callback dispatch; a lease loss or a shutdown landing on
        # that await raises BaseException, which ``_emit``'s own ``except
        # Exception`` does not catch. Opening outside the guard therefore put a
        # row on the stream that nothing could ever close.
        await handle.open()
        yield handle
    except BaseException as exc:
        # Cancellation is a distinct outcome from failure: one is a person or a
        # lease taking the work away, the other is the work going wrong, and a
        # reader needs to tell them apart.
        terminal = ActivityState.CANCELLED if isinstance(exc, asyncio.CancelledError) else ActivityState.FAILED
        await handle.settle(terminal)
        raise
    else:
        await handle.settle(ActivityState.COMPLETED)
    finally:
        _CURRENT_ACTIVITY.reset(handle_token)
        _CURRENT_ACTIVITY_ID.reset(token)
