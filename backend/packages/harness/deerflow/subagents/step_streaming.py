"""Turn a running subagent's captured steps into live progress events.

`SubagentExecutor` appends to `SubagentResult.ai_messages` as the child graph
produces assistant turns and tool results (see `subagents/step_events.py`). Two
callers want to show that while it happens:

- the `task` tool, which has polled its background entry since #3779; and
- `LiveStageAdapter`, which awaited one terminal result and so rendered a DBTL
  stage worker as a single opaque stretch — no reads, no Bash, no output, and,
  when the worker's structured result was rejected, no way to see why.

The cursor, the ordering, and the usage snapshot are the parts a second
implementation gets subtly wrong, so they live here and are tested once.

**Two rules shape this module.** Steps must appear *while* the work runs —
collecting them and flushing at the end would satisfy every ordering property
and still leave a person watching a spinner — which is why `run_with_step_stream`
polls beside the worker thread rather than awaiting it. And reporting progress
must never be able to end the work it reports on: an emit that raises costs one
event, not a stage attempt. Terminal handling deliberately stays with each
caller, because the `task` tool answers to the subagent status protocol while
the adapter answers to a DBTL stage contract, and one shared notion of "done"
would have to lie to one of them.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

#: Matches the `task` tool's long-standing cadence. The executor offers no
#: completion signal to wait on, so this is a poll rather than a subscription;
#: a condition/queue-based executor would improve both callers at once.
POLL_INTERVAL_SECONDS = 5.0

_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")

#: Fields the streamer owns. A caller's base payload is merged *under* these, so
#: a stale or hand-built base cannot mislabel which step an event describes.
_OWNED_FIELDS = frozenset({"type", "task_id", "message", "message_index", "total_messages", "usage"})


def summarize_usage(
    records: Sequence[Mapping[str, Any]] | None,
    *,
    drop_zero: bool = False,
) -> dict[str, int] | None:
    """Collapse a subagent's per-call usage records into one cumulative meter.

    Non-numeric values are skipped rather than added, because a provider that
    reports a string must not take down the run that was merely trying to
    describe its own progress.

    `drop_zero` distinguishes the two callers' honest answers: a stream event
    reports a measured zero as zero, while a persisted review package records
    absent provider usage as absent rather than as a measurement of nothing.
    """
    if not records:
        return None
    usage = {key: sum(int(value) for record in records if isinstance(value := record.get(key, 0), (int, float))) for key in _USAGE_KEYS}
    if drop_zero and not any(usage.values()):
        return None
    return usage


class SubagentStepStreamer:
    """Emits one `task_running` event per newly captured step, exactly once.

    Holds a plain integer cursor because `ai_messages` is append-only: step
    capture appends to it, and the compaction contraction handled in
    `capture_new_step_messages` applies to the child's `messages` channel, not
    to this list. Reading it from the polling coroutine while the worker thread
    appends is safe for the same reason the `task` tool's loop has always been —
    a list append is atomic and the snapshot is taken by index.
    """

    __slots__ = ("_task_id", "_emit", "_base", "_sent")

    def __init__(
        self,
        *,
        task_id: str,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        base_event: Mapping[str, Any] | None = None,
    ) -> None:
        self._task_id = task_id
        self._emit = emit
        self._base = {key: value for key, value in (base_event or {}).items() if key not in _OWNED_FIELDS}
        self._sent = 0

    async def drain(self, result: Any | None) -> int:
        """Emit every step captured since the last drain; return how many."""
        if result is None:
            return 0
        messages = getattr(result, "ai_messages", None) or []
        total = len(messages)
        if total <= self._sent:
            return 0

        usage = summarize_usage(getattr(result, "token_usage_records", None))
        pending = list(messages[self._sent : total])
        # Advance before emitting: an emit that raises must not re-report the
        # same step on the next poll, which would duplicate the timeline for
        # the whole rest of the run.
        first_index = self._sent
        self._sent = total

        for offset, message in enumerate(pending):
            await self._safe_emit(
                {
                    **self._base,
                    "type": "task_running",
                    "task_id": self._task_id,
                    "message": message,
                    "message_index": first_index + offset + 1,  # 1-based for display
                    "total_messages": total,
                    "usage": usage,
                }
            )
        return len(pending)

    async def _safe_emit(self, payload: dict[str, Any]) -> None:
        try:
            await self._emit(payload)
        except Exception:  # pragma: no cover - defensive; exercised via the emit-failure test
            logger.warning("Failed to emit a subagent step event for task %s", self._task_id, exc_info=True)


async def run_with_step_stream[T](
    work: Callable[[], T],
    *,
    result: Any,
    streamer: SubagentStepStreamer,
    interval: float = POLL_INTERVAL_SECONDS,
) -> T:
    """Run blocking `work` on a worker thread, reporting steps as they appear.

    Returns whatever `work` returned and propagates whatever it raised, so a
    caller can keep owning its own terminal mapping. Cancellation propagates
    too: the underlying thread cannot be interrupted, so the caller remains
    responsible for signalling the subagent's cooperative cancel event.
    """
    task = asyncio.ensure_future(asyncio.to_thread(work))
    try:
        while True:
            await asyncio.wait({task}, timeout=interval)
            # Drain after every wait, including the one that observed
            # completion, so steps appended in the last instant are not lost.
            await streamer.drain(result)
            if task.done():
                return task.result()
    except asyncio.CancelledError:
        task.cancel()
        raise
