"""Batched persistence for runtime agent-activity events.

A sibling of ``_SubagentEventBuffer`` and deliberately not a shared base with
it: the flush boundary differs (a terminal *transition* rather than a terminal
event type), the converter differs, and folding them together would make the
subagent step path depend on the activity vocabulary.

``RunEventStore.put`` is a documented low-frequency path — on PostgreSQL each
call opens its own transaction and takes a per-thread advisory lock. Activity is
the highest-frequency event type this system has proposed, so persisting each
transition with ``put()`` would serialize against the run's own message writer.

Capture happens **before** the namespace branch in ``_publish_stream_item``, and
that placement is load-bearing: that function returns early for any frame
carrying a subgraph namespace, so a run whose client requested subgraph
streaming would otherwise persist no activity at all. A durable projection that
exists to remove false spinners cannot depend on what a client asked to stream.
"""

import logging
from typing import Any

from deerflow.runtime.activity.run_event import activity_run_event
from deerflow.runtime.activity.vocabulary import TERMINAL_TRANSITIONS, ActivityState, ActivityTransition

logger = logging.getLogger(__name__)

_TERMINAL_TRANSITION_VALUES = frozenset(member.value for member in TERMINAL_TRANSITIONS)


class ActivityEventBuffer:
    """Buffer ``agent_activity`` frames and persist them in locked batches."""

    #: Flush once this many transitions are buffered. Lower than the subagent
    #: buffer's threshold because the rail's reload path reads these rows: a
    #: long-buffered transition is a stale row for anyone who refreshes.
    FLUSH_THRESHOLD = 15

    def __init__(self, event_store: Any | None, thread_id: str, run_id: str) -> None:
        self._event_store = event_store
        self._thread_id = thread_id
        self._run_id = run_id
        self._pending: list[dict[str, Any]] = []
        #: Rows opened on this run and not yet closed, newest payload per id.
        #: The run's own end is the last moment anything can honestly say what
        #: happened to them, so this is what ``close_open_activities`` settles.
        self._open: dict[str, dict[str, Any]] = {}

    async def add(self, chunk: Any) -> None:
        """Buffer one custom stream chunk; flush on a terminal transition or threshold.

        Frames from other custom-event streams and malformed frames are dropped
        by ``activity_run_event`` rather than reaching a store.
        """
        if self._event_store is None:
            return
        record = activity_run_event(chunk)
        if record is None:
            return
        self._pending.append({"thread_id": self._thread_id, "run_id": self._run_id, **record})

        content = record["content"]
        transition = content["transition"]
        terminal = transition in _TERMINAL_TRANSITION_VALUES
        if terminal:
            self._open.pop(content["activity_id"], None)
        else:
            self._open[content["activity_id"]] = content

        if terminal or len(self._pending) >= self.FLUSH_THRESHOLD:
            await self.flush()

    async def close_open_activities(self) -> None:
        """Settle every row this run opened and never closed.

        An actor normally closes its own row, and its terminal state is the
        honest one. But a cancelled run, a lost lease, or a middleware hook the
        graph raised past all end a run with rows still open, and nothing later
        will ever close them — a reload would show work that has been finished
        for weeks as still in progress. The run's end is the last point at which
        anything knows, so it settles them ``interrupted``: not completed, which
        would claim an outcome nobody observed, and not failed, which would
        blame the work for the run being taken away.

        Called before the worker's final flush, so the synthesized rows ride the
        same batch as whatever is still buffered.
        """
        if self._event_store is None or not self._open:
            return
        stranded = list(self._open.values())
        self._open = {}
        for content in stranded:
            closing = {
                **content,
                "transition": ActivityTransition.INTERRUPTED.value,
                "state": ActivityState.INTERRUPTED.value,
            }
            record = activity_run_event(closing)
            if record is None:  # pragma: no cover - the source payload was already validated
                continue
            self._pending.append({"thread_id": self._thread_id, "run_id": self._run_id, **record})
            logger.debug("Run %s: settled a stranded agent activity row %s", self._run_id, content["activity_id"])

    async def flush(self) -> None:
        """Persist buffered events in one ``put_batch`` call; swallow store errors."""
        if self._event_store is None or not self._pending:
            return
        batch = self._pending
        self._pending = []
        try:
            await self._event_store.put_batch(batch)
        except Exception:
            # Re-buffer ahead of anything queued since, so a transient store
            # error does not silently drop the transition that closes a row.
            self._pending = batch + self._pending
            logger.warning("Run %s: failed to persist %d agent activity event(s)", self._run_id, len(batch), exc_info=True)
