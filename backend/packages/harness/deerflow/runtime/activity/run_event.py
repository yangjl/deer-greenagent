"""Convert a live activity stream payload into a persisted run-event record.

The sibling of ``subagents.step_events.subagent_run_event``, and deliberately
the same shape: a pure function that either returns a complete record or returns
``None``, so a malformed frame is dropped at the boundary instead of reaching a
store.

Live custom events alone are insufficient for this feature. Refresh, reconnect,
hidden deck-triggered runs, and stream replay gaps would otherwise leave a rail
spinning forever on work that finished — which is the exact failure the durable
projection exists to remove, so it cannot be best-effort.
"""

from typing import Any

from deerflow.constants import AGENT_ACTIVITY_EVENT_CATEGORY, AGENT_ACTIVITY_EVENT_TYPE
from deerflow.runtime.activity.envelope import parse_activity_event


def activity_run_event(chunk: Any) -> dict[str, Any] | None:
    """Return the persisted record for one ``agent_activity`` frame.

    The content is **rebuilt** by ``parse_activity_event`` rather than copied
    from the chunk. A durable row outlives the process that produced it and is
    read back by clients, so persisting the inbound mapping verbatim would make
    the closed field set a property of whoever emitted the frame — anything an
    emitter attached, now or after a future edit, would be stored and served.
    Rebuilding keeps that guarantee where the rest of the envelope's safety
    lives: in the constructor.

    Returns ``None`` for anything that is not a well-formed activity frame,
    including frames belonging to other custom-event streams.
    """
    content = parse_activity_event(chunk)
    if content is None:
        return None

    metadata: dict[str, Any] = {"activity_id": content["activity_id"]}
    # Reuse the existing task_id filter so one worker's activity and its
    # subtask steps page through list_events under the same key.
    task_id = content["scope"].get("task_id")
    if task_id:
        metadata["task_id"] = task_id

    return {
        "event_type": AGENT_ACTIVITY_EVENT_TYPE,
        "category": AGENT_ACTIVITY_EVENT_CATEGORY,
        "content": content,
        "metadata": metadata,
    }
