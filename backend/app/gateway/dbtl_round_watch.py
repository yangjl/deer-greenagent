"""Fail-soft chat visibility for deck-started background rounds.

A deck action can start a run in the originating conversation — a Design
revision round after "Request changes", or a stage review meeting. That run's
explanation rides on its own reply, so a run that dies mid-flight (provider
outage, timeout, interruption) says nothing: the person who asked sits in a
conversation that stays silent, and the only durable symptom is a run row they
never look at.

The watcher polls the run's durable status and, when it ends in a terminal
failure, appends one server-owned visible message to the thread's event feed
naming what stopped and how to retry. Three deliberate limits keep it a
visibility aid rather than a second control plane:

- **It never touches cycle state.** The verdict that started the round is
  already recorded; this only explains why no reply followed it.
- **A successful run with its expected follow-up writes nothing.** The round's
  own reply is the explanation there. A parent run that says success but
  creates no successor surface is treated as the audited worker-failure case,
  after a short consistency grace period.
- **It is process-local and fail-soft.** A Gateway restart loses the watcher,
  a store outage is retried until the deadline, and the message identity binds
  the surface and run so a replayed watcher dedupes through ``put_if_absent``
  instead of posting twice.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0
#: A stage worker's own timeout cap is 1800s and the parent run drains after
#: it, so an hour bounds every legitimate round without cutting one off.
WATCH_DEADLINE_SECONDS = 3600.0
SUCCESS_FOLLOW_UP_GRACE_SECONDS = 15.0

_FAILED_STATUSES = frozenset({"error", "timeout", "interrupted"})

#: Strong references so fire-and-forget watcher tasks are not garbage-collected
#: mid-poll.
_WATCHERS: set[asyncio.Task[bool]] = set()


def _run_status(record: Any) -> str:
    raw = record.get("status") if isinstance(record, dict) else getattr(record, "status", None)
    return str(getattr(raw, "value", raw) or "")


async def announce_failed_background_round(
    run_store: Any,
    event_store: Any,
    *,
    user_id: str,
    thread_id: str,
    run_id: str,
    surface_id: str,
    explanation: str,
    success_has_follow_up: Callable[[], Awaitable[bool]] | None = None,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    deadline_seconds: float = WATCH_DEADLINE_SECONDS,
    success_follow_up_grace_seconds: float = SUCCESS_FOLLOW_UP_GRACE_SECONDS,
) -> bool:
    """Wait for the round's run to settle; say so in chat if it failed.

    Returns ``True`` when a failure message was written. Every store error is
    swallowed: this runs on a detached task, and an exception here is
    unobserved noise, never a fix.
    """
    deadline = monotonic() + deadline_seconds
    success_follow_up_deadline: float | None = None
    while monotonic() < deadline:
        await asyncio.sleep(poll_interval_seconds)
        try:
            record = await run_store.get(run_id, user_id=user_id)
        except Exception:  # noqa: BLE001 - an unreadable store proves nothing about the run
            continue
        if record is None:
            continue
        status = _run_status(record)
        if status == "success":
            if success_has_follow_up is None:
                return False
            try:
                if await success_has_follow_up():
                    return False
            except Exception:  # noqa: BLE001 - an unreadable successor proves nothing
                continue
            if success_follow_up_deadline is None:
                success_follow_up_deadline = monotonic() + success_follow_up_grace_seconds
            if monotonic() < success_follow_up_deadline:
                continue
            # A failed stage worker can be an audited outcome inside an
            # otherwise successful parent run.  No successor surface is the
            # observable failure in that case, and is the same condition the
            # authenticated deck read model uses to restore the action.
            status = "success_without_follow_up"
        if status not in _FAILED_STATUSES:
            if status != "success_without_follow_up":
                continue
        message = AIMessage(
            # The identity binds the surface and run: a second watcher for the
            # same round writes the same row, and `put_if_absent` drops it.
            id=f"dbtl-round-failed__{surface_id}__{run_id}",
            content=f"{explanation}\n\nThe background run ended with status “{status}”.",
            additional_kwargs={
                "dbtl_round_failure": True,
                "design_feedback_surface_id": surface_id,
                "run_id": run_id,
            },
        )
        try:
            await event_store.put_if_absent(
                thread_id=thread_id,
                run_id=run_id,
                event_type="llm.ai.response",
                category="message",
                content=message.model_dump(),
                metadata={
                    "caller": "lead_agent",
                    "dbtl_round_failure": True,
                },
            )
        except Exception:  # noqa: BLE001 - visibility aid; the failed run is already durable
            logger.exception(
                "Failed to announce failed background round %s in thread %s",
                run_id,
                thread_id,
            )
            return False
        return True
    logger.debug("Background round %s did not settle within the watch deadline", run_id)
    return False


def watch_background_round(
    run_store: Any,
    event_store: Any,
    *,
    user_id: str,
    thread_id: str,
    run_id: str,
    surface_id: str,
    explanation: str,
    success_has_follow_up: Callable[[], Awaitable[bool]] | None = None,
) -> None:
    """Spawn the fail-soft watcher as a detached task."""
    task = asyncio.create_task(
        announce_failed_background_round(
            run_store,
            event_store,
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            surface_id=surface_id,
            explanation=explanation,
            success_has_follow_up=success_has_follow_up,
        )
    )
    _WATCHERS.add(task)
    task.add_done_callback(_WATCHERS.discard)
