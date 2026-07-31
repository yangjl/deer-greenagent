"""A deck-started round that dies mid-flight must say so in chat.

A revision round or a review meeting runs in the originating conversation, and
its explanation rides on its own reply — so a run that ends in error says
nothing, and the person who asked sits in a conversation that stays silent.
The watcher polls the run's durable status and, on a terminal failure, appends
one server-owned visible message to the thread feed.

It is deliberately fail-soft: a successful run with its expected successor
writes nothing (its own reply is the explanation), transient store errors are
retried, and the message identity embeds the surface and run so a replayed
watcher cannot post twice.
"""

from __future__ import annotations

import pytest

from app.gateway.dbtl_round_watch import announce_failed_background_round

pytestmark = pytest.mark.asyncio


class _RunStore:
    def __init__(self, statuses: list[object]) -> None:
        self._statuses = statuses
        self.calls = 0

    async def get(self, run_id: str, *, user_id=None):
        self.calls += 1
        status = self._statuses[min(self.calls - 1, len(self._statuses) - 1)]
        if isinstance(status, Exception):
            raise status
        if status is None:
            return None
        return {"run_id": run_id, "status": status}


class _EventStore:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def put_if_absent(self, **kwargs) -> None:
        self.events.append(kwargs)


async def _announce(run_store: _RunStore, event_store: _EventStore, **overrides) -> bool:
    kwargs = {
        "user_id": "user-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "surface_id": "surface-1",
        "explanation": "The Design revision round stopped before it could reply.",
        "poll_interval_seconds": 0.0,
        "deadline_seconds": 1.0,
    }
    kwargs.update(overrides)
    return await announce_failed_background_round(run_store, event_store, **kwargs)


async def test_a_failed_run_writes_one_visible_message() -> None:
    run_store = _RunStore(["pending", "running", "error"])
    event_store = _EventStore()

    announced = await _announce(run_store, event_store)

    assert announced is True
    assert len(event_store.events) == 1
    event = event_store.events[0]
    assert event["thread_id"] == "thread-1"
    assert event["run_id"] == "run-1"
    assert event["event_type"] == "llm.ai.response"
    assert event["category"] == "message"
    content = event["content"]
    assert "stopped before it could reply" in str(content.get("content"))
    assert "error" in str(content.get("content"))


async def test_the_message_identity_binds_surface_and_run_so_replays_dedupe() -> None:
    """`put_if_absent` is the dedupe; the id is what makes two watchers for the
    same round write the same row instead of two."""
    run_store = _RunStore(["error"])
    event_store = _EventStore()

    await _announce(run_store, event_store)

    assert event_store.events[0]["content"]["id"] == "dbtl-round-failed__surface-1__run-1"


async def test_a_successful_run_stays_silent() -> None:
    """The round's own reply is the explanation; a second message would be
    noise stacked on a conversation that already answered."""
    run_store = _RunStore(["running", "success"])
    event_store = _EventStore()

    announced = await _announce(run_store, event_store)

    assert announced is False
    assert event_store.events == []


async def test_parent_success_without_a_successor_is_announced() -> None:
    """A rejected/failed stage worker can be audited inside a parent run that
    itself finishes successfully. The absent successor is the failure signal."""

    async def no_follow_up() -> bool:
        return False

    run_store = _RunStore(["success"])
    event_store = _EventStore()

    announced = await _announce(
        run_store,
        event_store,
        success_has_follow_up=no_follow_up,
        success_follow_up_grace_seconds=0.0,
    )

    assert announced is True
    assert "success_without_follow_up" in str(
        event_store.events[0]["content"]["content"]
    )


async def test_parent_success_with_a_successor_stays_silent() -> None:
    async def has_follow_up() -> bool:
        return True

    run_store = _RunStore(["success"])
    event_store = _EventStore()

    announced = await _announce(
        run_store,
        event_store,
        success_has_follow_up=has_follow_up,
        success_follow_up_grace_seconds=0.0,
    )

    assert announced is False
    assert event_store.events == []


async def test_transient_store_errors_are_retried_until_the_run_settles() -> None:
    run_store = _RunStore([RuntimeError("db blinked"), None, "timeout"])
    event_store = _EventStore()

    announced = await _announce(run_store, event_store)

    assert announced is True
    assert len(event_store.events) == 1


async def test_an_exhausted_deadline_gives_up_without_posting() -> None:
    """A run still draining is not a failed run; claiming failure over a live
    run would be worse than the silence this exists to fix."""
    run_store = _RunStore(["running"])
    event_store = _EventStore()

    announced = await _announce(run_store, event_store, deadline_seconds=0.0)

    assert announced is False
    assert event_store.events == []


async def test_enum_like_statuses_are_read_by_value() -> None:
    class _Status:
        value = "interrupted"

    run_store = _RunStore([_Status()])
    event_store = _EventStore()

    announced = await _announce(run_store, event_store)

    assert announced is True
    assert "interrupted" in str(event_store.events[0]["content"]["content"])


async def test_a_failing_event_write_is_swallowed_not_raised() -> None:
    """The watcher is a visibility aid on a fire-and-forget task; an exception
    here would be unobserved noise, never a fix."""

    class _BrokenEventStore:
        async def put_if_absent(self, **kwargs) -> None:
            raise RuntimeError("event store down")

    run_store = _RunStore(["error"])

    announced = await _announce(run_store, _BrokenEventStore())

    assert announced is False
