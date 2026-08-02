"""Settle activity rows whose owner can no longer close them.

The run worker's own `finally` covers the ordinary case, and it is not
sufficient on its own. When a lease expires the worker is **fenced**: it
performs no further journal, completion, status, or checkpoint writes, and the
peer recovery path owns the terminal receipt. That is exactly the multi-worker
case a permanent spinner comes from — the run is durably `error` with
`stop_reason=orphan_recovered` while its activity rows stay open forever.

So the recovery path settles them, reading what the store already holds rather
than remembering anything: whoever recovers the run may be a different process
from the one that opened the rows, on a different machine, with no memory of
either.

This lives in the harness and takes a store rather than reaching into the
Gateway, because `tests/test_harness_boundary.py` forbids `deerflow.*` importing
`app.*` — which is also why the hook it is called from is a generic
`RunManager.on_orphans_recovered` callback the Gateway registers.
"""

import logging
from typing import Any

from deerflow.constants import AGENT_ACTIVITY_EVENT_TYPE
from deerflow.runtime.activity.run_event import activity_run_event
from deerflow.runtime.activity.vocabulary import (
    TERMINAL_TRANSITIONS,
    ActivityState,
    ActivityTransition,
)

logger = logging.getLogger(__name__)

_TERMINAL_TRANSITION_VALUES = frozenset(member.value for member in TERMINAL_TRANSITIONS)

#: Enough to cover any realistic run. A run with more activity than this has
#: bigger problems than a stale row, and an unbounded read on a recovery path
#: would make one wedged run slow every later recovery.
_MAX_SCANNED_EVENTS = 2000


async def close_open_run_activity(
    event_store: Any,
    *,
    thread_id: str,
    run_id: str,
    state: ActivityState = ActivityState.INTERRUPTED,
) -> int:
    """Write a terminal transition for every row this run left open.

    Returns how many rows were settled. ``interrupted`` is the honest terminal:
    the work neither finished nor demonstrably failed, its owner stopped being
    able to say. Reports rather than raises — a run must not fail to be
    recovered because its instrumentation could not be tidied up.
    """
    events = await event_store.list_events(
        thread_id,
        run_id,
        event_types=[AGENT_ACTIVITY_EVENT_TYPE],
        limit=_MAX_SCANNED_EVENTS,
    )

    open_rows: dict[str, dict[str, Any]] = {}
    for event in events:
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        activity_id = content.get("activity_id")
        if not isinstance(activity_id, str) or not activity_id:
            continue
        if content.get("transition") in _TERMINAL_TRANSITION_VALUES:
            open_rows.pop(activity_id, None)
        else:
            open_rows[activity_id] = content

    if not open_rows:
        return 0

    batch: list[dict[str, Any]] = []
    for content in open_rows.values():
        record = activity_run_event(
            {
                **content,
                "transition": ActivityTransition.INTERRUPTED.value,
                "state": state.value,
            }
        )
        if record is None:  # pragma: no cover - the stored payload was already validated
            continue
        batch.append({"thread_id": thread_id, "run_id": run_id, **record})

    if batch:
        await event_store.put_batch(batch)
        logger.info("Run %s: settled %d orphaned agent activity row(s)", run_id, len(batch))
    return len(batch)
