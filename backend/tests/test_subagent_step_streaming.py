"""Making a running subagent visible while it is still running.

A subagent appends to its ``ai_messages`` list as it works, and two callers want
to turn that into progress events: the ``task`` tool, which has polled its
background task since #3779, and the DBTL stage adapter, which until now awaited
one terminal result and showed nothing in between. The cursor, the ordering, and
the usage snapshot are the parts that are easy to get subtly wrong twice, so they
live in one place and are tested once.

The load-bearing property is the one a second implementation would lose:
**steps are emitted while the work is still running**, not collected and flushed
at the end. A helper that only drained on completion would pass every ordering
assertion here and still leave the user staring at a spinner.
"""

import asyncio
import threading
from dataclasses import dataclass, field

import pytest

from deerflow.subagents.step_streaming import (
    SubagentStepStreamer,
    run_with_step_stream,
    summarize_usage,
)


@dataclass
class _Result:
    """Only what the streamer reads.

    Deliberately not the real `SubagentResult`: the streamer must stay
    duck-typed over the two shapes that feed it (a live holder the adapter owns
    and a background entry the task tool looks up), and importing the executor
    here would couple this module's tests to the executor's own construction
    rules for no gain.
    """

    ai_messages: list[dict] = field(default_factory=list)
    token_usage_records: list[dict] = field(default_factory=list)


def _result(*, messages: list[dict] | None = None, records: list[dict] | None = None) -> _Result:
    return _Result(ai_messages=list(messages or []), token_usage_records=list(records or []))


def _streamer(events: list[dict], **kwargs) -> SubagentStepStreamer:
    async def emit(payload: dict) -> None:
        events.append(payload)

    return SubagentStepStreamer(task_id="unit-1", emit=emit, **kwargs)


class TestEachCapturedStepIsReportedOnce:
    pytestmark = pytest.mark.asyncio

    async def test_a_new_message_becomes_one_running_event(self):
        events: list[dict] = []
        streamer = _streamer(events)

        await streamer.drain(_result(messages=[{"id": "a"}]))

        assert [event["type"] for event in events] == ["task_running"]
        assert events[0]["message"] == {"id": "a"}

    async def test_indexes_are_one_based_and_carry_the_running_total(self):
        events: list[dict] = []
        streamer = _streamer(events)

        await streamer.drain(_result(messages=[{"id": "a"}, {"id": "b"}]))

        assert [(event["message_index"], event["total_messages"]) for event in events] == [(1, 2), (2, 2)]

    async def test_draining_again_reports_nothing(self):
        # A reconnect or a fast poll must not replay the timeline.
        events: list[dict] = []
        streamer = _streamer(events)
        result = _result(messages=[{"id": "a"}])

        await streamer.drain(result)
        await streamer.drain(result)

        assert len(events) == 1

    async def test_only_the_appended_tail_is_reported(self):
        events: list[dict] = []
        streamer = _streamer(events)
        result = _result(messages=[{"id": "a"}])
        await streamer.drain(result)

        result.ai_messages.append({"id": "b"})
        await streamer.drain(result)

        assert [event["message"]["id"] for event in events] == ["a", "b"]

    async def test_a_result_with_no_steps_emits_nothing(self):
        events: list[dict] = []
        await _streamer(events).drain(_result())
        assert events == []

    async def test_a_missing_result_emits_nothing(self):
        # The task tool's background entry can disappear; that is its own
        # terminal path, not a reason for the streamer to raise.
        events: list[dict] = []
        await _streamer(events).drain(None)
        assert events == []


class TestEveryEventCarriesItsCallersIdentity:
    pytestmark = pytest.mark.asyncio

    async def test_the_task_id_is_always_present(self):
        events: list[dict] = []
        await _streamer(events).drain(_result(messages=[{"id": "a"}]))
        assert events[0]["task_id"] == "unit-1"

    async def test_base_fields_ride_on_every_event(self):
        events: list[dict] = []
        streamer = _streamer(events, base_event={"dbtl_stage": "build", "parent_activity_id": "act_1"})

        await streamer.drain(_result(messages=[{"id": "a"}]))

        assert events[0]["dbtl_stage"] == "build"
        assert events[0]["parent_activity_id"] == "act_1"

    async def test_base_fields_cannot_overwrite_the_step_identity(self):
        # A caller passing `task_id` or `message_index` in its base payload must
        # not be able to mislabel which step this is.
        events: list[dict] = []
        streamer = _streamer(events, base_event={"task_id": "forged", "message_index": 99, "type": "task_completed"})

        await streamer.drain(_result(messages=[{"id": "a"}]))

        assert events[0]["task_id"] == "unit-1"
        assert events[0]["message_index"] == 1
        assert events[0]["type"] == "task_running"

    async def test_the_usage_snapshot_rides_along(self):
        events: list[dict] = []
        streamer = _streamer(events)
        result = _result(messages=[{"id": "a"}], records=[{"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}])

        await streamer.drain(result)

        assert events[0]["usage"] == {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}


class TestStepsAppearWhileTheWorkIsStillRunning:
    """The reason this helper exists at all."""

    pytestmark = pytest.mark.asyncio

    async def test_a_step_is_emitted_before_the_work_finishes(self):
        events: list[dict] = []
        streamer = _streamer(events)
        result = _result()
        released = threading.Event()
        observed = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def emit(payload: dict) -> None:
            events.append(payload)
            loop.call_soon(observed.set)

        streamer = SubagentStepStreamer(task_id="unit-1", emit=emit)

        def work() -> str:
            result.ai_messages.append({"id": "mid-flight"})
            released.wait(timeout=5)
            return "done"

        task = asyncio.create_task(run_with_step_stream(work, result=result, streamer=streamer, interval=0.01))
        await asyncio.wait_for(observed.wait(), timeout=5)
        assert [event["message"]["id"] for event in events] == ["mid-flight"]
        assert not task.done()

        released.set()
        assert await asyncio.wait_for(task, timeout=5) == "done"

    async def test_the_final_steps_are_emitted_after_the_work_returns(self):
        # Steps appended in the last instant must not be lost to a race with
        # the completion check.
        events: list[dict] = []
        streamer = _streamer(events)
        result = _result()

        def work() -> str:
            result.ai_messages.append({"id": "last"})
            return "done"

        assert await run_with_step_stream(work, result=result, streamer=streamer, interval=0.01) == "done"
        assert [event["message"]["id"] for event in events] == ["last"]

    async def test_the_work_result_is_returned_unchanged(self):
        sentinel = object()
        events: list[dict] = []
        assert await run_with_step_stream(lambda: sentinel, result=_result(), streamer=_streamer(events), interval=0.01) is sentinel

    async def test_a_raising_worker_propagates(self):
        events: list[dict] = []

        def work() -> str:
            raise RuntimeError("worker exploded")

        with pytest.raises(RuntimeError, match="exploded"):
            await run_with_step_stream(work, result=_result(), streamer=_streamer(events), interval=0.01)

    async def test_cancelling_the_caller_propagates(self):
        events: list[dict] = []
        result = _result()
        released = threading.Event()

        def work() -> str:
            released.wait(timeout=5)
            return "done"

        task = asyncio.create_task(run_with_step_stream(work, result=result, streamer=_streamer(events), interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        released.set()

    async def test_an_emit_failure_does_not_kill_the_work(self):
        # Progress reporting is not worth failing a stage worker for.
        async def emit(payload: dict) -> None:
            raise RuntimeError("stream bridge is down")

        streamer = SubagentStepStreamer(task_id="unit-1", emit=emit)
        result = _result()

        def work() -> str:
            result.ai_messages.append({"id": "a"})
            return "done"

        assert await run_with_step_stream(work, result=result, streamer=streamer, interval=0.01) == "done"


class TestUsageSummary:
    def test_no_records_is_no_usage(self):
        assert summarize_usage(None) is None
        assert summarize_usage([]) is None

    def test_records_are_summed_per_key(self):
        usage = summarize_usage([{"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}, {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9}])
        assert usage == {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}

    def test_a_non_numeric_value_is_skipped_rather_than_raising(self):
        # A provider that reports a string must not take down the run that was
        # merely trying to report its own progress.
        assert summarize_usage([{"input_tokens": "lots", "output_tokens": 2, "total_tokens": 2}]) == {"input_tokens": 0, "output_tokens": 2, "total_tokens": 2}

    def test_zeroes_are_reported_by_default(self):
        # The task tool's events distinguish "reported zero" from "not reported".
        assert summarize_usage([{"input_tokens": 0}]) == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def test_drop_zero_reports_nothing_when_a_provider_reported_nothing(self):
        # A stage review package records absent usage as absent rather than as
        # a measured zero.
        assert summarize_usage([{"input_tokens": 0}], drop_zero=True) is None
