"""Failure callbacks for deck-started background runs remain fail-soft."""

from __future__ import annotations

import pytest

from app.gateway.dbtl_round_watch import announce_failed_background_round


class _RunStore:
    async def get(self, run_id: str, *, user_id: str):
        return {"run_id": run_id, "user_id": user_id, "status": "error"}


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
