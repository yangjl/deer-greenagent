"""Failure callbacks for deck-started background runs remain fail-soft."""

from __future__ import annotations

import pytest

from app.gateway.dbtl_round_watch import announce_failed_background_round


class _RunStore:
    def __init__(self, status: str = "error") -> None:
        self._status = status

    async def get(self, run_id: str, *, user_id: str):
        return {"run_id": run_id, "user_id": user_id, "status": self._status}


class _EventStore:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def put_if_absent(self, **event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_terminal_failure_persists_recovery_state_before_announcing() -> None:
    event_store = _EventStore()
    failures: list[str] = []

    async def record_failure(status: str) -> None:
        failures.append(status)

    announced = await announce_failed_background_round(
        _RunStore(),
        event_store,
        user_id="user-1",
        thread_id="thread-1",
        run_id="run-1",
        surface_id="surface-1",
        explanation="The approval remains recorded.",
        on_failure=record_failure,
        poll_interval_seconds=0,
        deadline_seconds=1,
    )

    assert announced is True
    assert failures == ["error"]
    assert len(event_store.events) == 1
    assert "approval remains recorded" in event_store.events[0]["content"]["content"].lower()


@pytest.mark.asyncio
async def test_a_successful_run_that_delivered_no_card_is_treated_as_a_failure() -> None:
    """Run status is not delivery.

    The 2026-08-01 handoff run reported ``success`` and left nothing in thread
    history, so the owner had no control to answer and their next message
    escaped to the ordinary lead agent. The watcher therefore asks the thread's
    own projection whether the card arrived, and a "yes it ran" with no card
    reopens the deck action exactly as a dead run does.
    """
    event_store = _EventStore()
    failures: list[str] = []

    async def record_failure(status: str) -> None:
        failures.append(status)

    async def card_never_appeared() -> bool:
        return False

    announced = await announce_failed_background_round(
        _RunStore(status="success"),
        event_store,
        user_id="user-1",
        thread_id="thread-1",
        run_id="run-1",
        surface_id="surface-1",
        explanation="Design approval is recorded, but the prompt for the next stage never appeared.",
        success_has_follow_up=card_never_appeared,
        on_failure=record_failure,
        poll_interval_seconds=0,
        deadline_seconds=1,
        success_follow_up_grace_seconds=0,
    )

    assert announced is True
    assert failures == ["success_without_follow_up"]
    assert "never appeared" in event_store.events[0]["content"]["content"].lower()


@pytest.mark.asyncio
async def test_a_delivered_card_keeps_the_conversation_quiet() -> None:
    """The round's own reply is the explanation when delivery worked."""
    event_store = _EventStore()

    async def card_arrived() -> bool:
        return True

    announced = await announce_failed_background_round(
        _RunStore(status="success"),
        event_store,
        user_id="user-1",
        thread_id="thread-1",
        run_id="run-1",
        surface_id="surface-1",
        explanation="unused",
        success_has_follow_up=card_arrived,
        poll_interval_seconds=0,
        deadline_seconds=1,
        success_follow_up_grace_seconds=0,
    )

    assert announced is False
    assert event_store.events == []
