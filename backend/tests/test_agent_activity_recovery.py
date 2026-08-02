"""Reload, backfill, and the rows nobody is left to close.

Two gaps the plan named explicitly and this closes. **The cross-run read is a
new store method, not a filter**: `list_events` is run-scoped, and a
conversation's activity spans every run in it — so the base plus all three
implementations must grow one, or a `db`-only signature raises `TypeError` on
the other two at runtime rather than at import. And **a fenced worker cannot
write its own terminal transition**: when a lease expires the worker performs no
further writes and the peer recovery path owns the receipt, which is precisely
the multi-worker case a permanent spinner comes from.
"""

import pytest

from deerflow.constants import AGENT_ACTIVITY_EVENT_TYPE
from deerflow.runtime.activity.emitter import make_activity_handle
from deerflow.runtime.activity.recovery import close_open_run_activity
from deerflow.runtime.activity.vocabulary import ActivityState, ActorKind
from deerflow.runtime.events.store.jsonl import JsonlRunEventStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.activity_buffer import ActivityEventBuffer

pytestmark = pytest.mark.asyncio


async def _frames(*, run_id: str = "run-1", close: bool = False) -> list[dict]:
    captured: list[dict] = []
    handle = make_activity_handle(
        run_id=run_id,
        actor_kind=ActorKind.LEAD_AGENT,
        actor_id="lead-agent",
        operation="lead.respond",
        state=ActivityState.THINKING,
        writer=captured.append,
    )
    await handle.open()
    if close:
        await handle.settle(ActivityState.COMPLETED)
    return captured


async def _seed(store, thread_id: str, run_id: str, *, close: bool = False) -> None:
    buffer = ActivityEventBuffer(store, thread_id, run_id)
    for frame in await _frames(run_id=run_id, close=close):
        await buffer.add(frame)
    await buffer.flush()


class TestEveryStoreCanReadAConversationsActivity:
    """A `db`-only signature fails at runtime on the other two, not at import."""

    @pytest.fixture(params=["memory", "jsonl"])
    def store(self, request, tmp_path):
        if request.param == "memory":
            return MemoryRunEventStore()
        return JsonlRunEventStore(base_dir=str(tmp_path))

    async def test_it_spans_every_run_in_the_conversation(self, store):
        await _seed(store, "thread-1", "run-a", close=True)
        await _seed(store, "thread-1", "run-b")

        events = await store.list_thread_events("thread-1", event_types=[AGENT_ACTIVITY_EVENT_TYPE])

        assert {event["run_id"] for event in events} == {"run-a", "run-b"}

    async def test_it_returns_events_in_stream_order(self, store):
        await _seed(store, "thread-1", "run-a", close=True)
        seqs = [event["seq"] for event in await store.list_thread_events("thread-1")]
        assert seqs == sorted(seqs)

    async def test_another_conversation_is_not_visible(self, store):
        await _seed(store, "thread-1", "run-a")
        await _seed(store, "thread-2", "run-b")
        events = await store.list_thread_events("thread-1")
        assert {event["thread_id"] for event in events} == {"thread-1"}

    async def test_the_event_type_filter_excludes_everything_else(self, store):
        await _seed(store, "thread-1", "run-a")
        await store.put(thread_id="thread-1", run_id="run-a", event_type="llm.ai.response", category="message", content={"text": "hello"})

        events = await store.list_thread_events("thread-1", event_types=[AGENT_ACTIVITY_EVENT_TYPE])

        assert {event["event_type"] for event in events} == {AGENT_ACTIVITY_EVENT_TYPE}

    async def test_paging_backwards_walks_into_the_past(self, store):
        for index in range(4):
            await _seed(store, "thread-1", f"run-{index}", close=True)

        newest = await store.list_thread_events("thread-1", limit=2)
        older = await store.list_thread_events("thread-1", limit=2, before_seq=newest[0]["seq"])

        assert len(newest) == 2
        assert all(event["seq"] < newest[0]["seq"] for event in older)
        # Each page is ascending even though pages walk backwards, so a caller
        # folds a page in stream order without re-sorting it.
        assert [event["seq"] for event in older] == sorted(event["seq"] for event in older)

    async def test_an_empty_conversation_reads_as_empty(self, store):
        assert await store.list_thread_events("thread-none") == []


class TestAFencedWorkerLeavesNoOpenRow:
    """The worker's own `finally` is not sufficient on its own."""

    async def test_recovery_settles_a_row_its_owner_could_not_close(self):
        store = MemoryRunEventStore()
        await _seed(store, "thread-1", "run-1")

        settled = await close_open_run_activity(store, thread_id="thread-1", run_id="run-1")

        assert settled == 1
        events = await store.list_events("thread-1", "run-1")
        assert [event["content"]["transition"] for event in events] == ["started", "interrupted"]
        # Not `completed`, which would claim an outcome nobody observed, and not
        # `failed`, which would blame the work for its owner losing its lease.
        assert events[-1]["content"]["state"] == "interrupted"

    async def test_it_keeps_the_row_identity_the_owner_wrote(self):
        store = MemoryRunEventStore()
        await _seed(store, "thread-1", "run-1")
        opened = (await store.list_events("thread-1", "run-1"))[0]["content"]

        await close_open_run_activity(store, thread_id="thread-1", run_id="run-1")

        closing = (await store.list_events("thread-1", "run-1"))[-1]["content"]
        assert closing["activity_id"] == opened["activity_id"]
        assert closing["display_name"] == opened["display_name"]

    async def test_a_row_that_closed_itself_is_left_alone(self):
        store = MemoryRunEventStore()
        await _seed(store, "thread-1", "run-1", close=True)

        assert await close_open_run_activity(store, thread_id="thread-1", run_id="run-1") == 0

    async def test_a_run_with_no_activity_writes_nothing(self):
        store = MemoryRunEventStore()
        assert await close_open_run_activity(store, thread_id="thread-1", run_id="run-1") == 0

    async def test_running_twice_settles_nothing_the_second_time(self):
        # Startup recovery and the periodic scan can both reach the same run.
        store = MemoryRunEventStore()
        await _seed(store, "thread-1", "run-1")

        assert await close_open_run_activity(store, thread_id="thread-1", run_id="run-1") == 1
        assert await close_open_run_activity(store, thread_id="thread-1", run_id="run-1") == 0

    async def test_a_store_that_cannot_be_read_propagates_to_its_caller(self):
        # The Gateway wrapper swallows and logs; this layer reports, so a
        # silent failure cannot be mistaken for "there was nothing to close".
        class BrokenStore:
            async def list_events(self, *args, **kwargs):
                raise RuntimeError("database is unreachable")

        with pytest.raises(RuntimeError, match="unreachable"):
            await close_open_run_activity(BrokenStore(), thread_id="thread-1", run_id="run-1")
