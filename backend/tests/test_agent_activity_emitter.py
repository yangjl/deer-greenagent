"""Emission contract for runtime agent activity.

Two properties dominate here and they pull in opposite directions. The span must
**always** close — a row left open is a spinner that never stops, which is the
exact failure this feature exists to remove. And the span must **never** raise —
instrumentation that can end a run is worse than no instrumentation at all.
"""

import asyncio

import pytest

from deerflow.runtime.activity.emitter import (
    ActivityHandle,
    activity_parent_context,
    activity_span,
    current_activity_id,
    make_activity_handle,
    new_activity_id,
)
from deerflow.runtime.activity.envelope import ActivityScope
from deerflow.runtime.activity.vocabulary import ActivityState, ActorKind

pytestmark = pytest.mark.asyncio


class RecordingWriter:
    """Stand-in for LangGraph's StreamWriter."""

    def __init__(self):
        self.payloads: list[dict] = []

    def __call__(self, payload):
        self.payloads.append(payload)

    def states(self) -> list[tuple[str, str]]:
        return [(p["transition"], p["state"]) for p in self.payloads]


def _span(writer, **overrides):
    kwargs = {
        "writer": writer,
        "run_id": "run_1",
        "actor_kind": ActorKind.LEAD_AGENT,
        "actor_id": "lead-agent",
        "operation": "lead.respond",
        "state": ActivityState.THINKING,
    }
    kwargs.update(overrides)
    return activity_span(**kwargs)


class TestASpanAlwaysCloses:
    async def test_a_normal_body_completes(self):
        writer = RecordingWriter()
        async with _span(writer) as handle:
            assert isinstance(handle, ActivityHandle)
        assert writer.states() == [("started", "thinking"), ("completed", "completed")]

    async def test_an_exception_fails_the_span_and_propagates(self):
        writer = RecordingWriter()
        with pytest.raises(RuntimeError, match="provider outage"):
            async with _span(writer):
                raise RuntimeError("provider outage")
        assert writer.states() == [("started", "thinking"), ("failed", "failed")]

    async def test_cancellation_cancels_the_span_and_propagates(self):
        writer = RecordingWriter()
        with pytest.raises(asyncio.CancelledError):
            async with _span(writer):
                raise asyncio.CancelledError()
        assert writer.states() == [("started", "thinking"), ("cancelled", "cancelled")]

    async def test_an_explicit_terminal_is_not_followed_by_a_second_one(self):
        writer = RecordingWriter()
        async with _span(writer) as handle:
            await handle.settle(ActivityState.INTERRUPTED)
        assert writer.states() == [("started", "thinking"), ("interrupted", "interrupted")]

    async def test_a_settled_span_ignores_later_updates(self):
        writer = RecordingWriter()
        async with _span(writer) as handle:
            await handle.settle(ActivityState.COMPLETED)
            await handle.update(state=ActivityState.COMPUTING)
        assert writer.states() == [("started", "thinking"), ("completed", "completed")]

    async def test_cancellation_while_opening_still_closes_the_row(self, monkeypatch):
        """The opening emit must be inside the guard, not before it.

        ``aemit_custom_event`` hands the payload to the writer synchronously and
        then awaits a best-effort callback dispatch. A lease loss or a shutdown
        landing on that await is realistic, and if the opening emit sits outside
        the try/finally the row reaches the stream and nothing can ever close
        it — a spinner that never stops, which is the failure this whole
        feature exists to remove.
        """
        import deerflow.utils.custom_events as custom_events

        calls = {"n": 0}

        async def cancel_on_first_dispatch(_name, _payload, config=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise asyncio.CancelledError()

        monkeypatch.setattr(custom_events, "adispatch_custom_event", cancel_on_first_dispatch)

        writer = RecordingWriter()
        with pytest.raises(asyncio.CancelledError):
            async with _span(writer):
                pytest.fail("the body must not run when opening was cancelled")

        assert writer.states() == [("started", "thinking"), ("cancelled", "cancelled")]
        assert current_activity_id() is None


class TestASpanNeverRaises:
    async def test_a_writer_that_throws_does_not_break_the_body(self):
        def exploding_writer(_payload):
            raise RuntimeError("stream bridge is down")

        ran = False
        async with activity_span(
            writer=exploding_writer,
            run_id="run_1",
            actor_kind=ActorKind.LEAD_AGENT,
            actor_id="lead-agent",
            operation="lead.respond",
            state=ActivityState.THINKING,
        ):
            ran = True
        assert ran

    async def test_no_writer_is_a_silent_no_op(self):
        async with activity_span(
            writer=None,
            run_id="run_1",
            actor_kind=ActorKind.LEAD_AGENT,
            actor_id="lead-agent",
            operation="lead.respond",
            state=ActivityState.THINKING,
        ) as handle:
            await handle.update(state=ActivityState.COMPUTING)
            assert handle.activity_id

    async def test_an_invalid_operation_costs_the_row_not_the_run(self):
        writer = RecordingWriter()
        ran = False
        async with _span(writer, operation="not.registered"):
            ran = True
        assert ran
        assert writer.payloads == []


class TestUpdatesAreCoalesced:
    async def test_an_update_that_changes_nothing_emits_nothing(self):
        writer = RecordingWriter()
        async with _span(writer) as handle:
            await handle.update(state=ActivityState.THINKING, operation="lead.respond")
        assert writer.states() == [("started", "thinking"), ("completed", "completed")]

    async def test_a_real_state_change_emits_an_update(self):
        writer = RecordingWriter()
        async with _span(writer) as handle:
            await handle.update(state=ActivityState.COMPUTING)
            await handle.update(state=ActivityState.COMPUTING)
            await handle.update(state=ActivityState.THINKING)
        assert writer.states() == [
            ("started", "thinking"),
            ("updated", "computing"),
            ("updated", "thinking"),
            ("completed", "completed"),
        ]

    async def test_an_update_may_not_smuggle_in_a_terminal_state(self):
        writer = RecordingWriter()
        async with _span(writer) as handle:
            await handle.update(state=ActivityState.FAILED)
        # The update is refused; the span still closes honestly on its own terms.
        assert writer.states() == [("started", "thinking"), ("completed", "completed")]


class TestLineage:
    async def test_a_context_only_parent_adds_lineage_without_an_extra_row(self):
        writer = RecordingWriter()
        with activity_parent_context("act_supervisor"):
            async with _span(writer) as child:
                assert child.parent_activity_id == "act_supervisor"
        assert len(writer.payloads) == 2
        assert {payload["activity_id"] for payload in writer.payloads} == {child.activity_id}
        assert all(payload["parent_activity_id"] == "act_supervisor" for payload in writer.payloads)

    async def test_a_nested_span_inherits_its_parent(self):
        writer = RecordingWriter()
        async with _span(writer) as parent:
            async with _span(writer, actor_kind=ActorKind.SUBAGENT, actor_id="research", operation="subagent.run") as child:
                assert child.activity_id != parent.activity_id
        child_started = writer.payloads[1]
        assert child_started["parent_activity_id"] == parent.activity_id
        assert child_started["dispatcher_activity_id"] == parent.activity_id

    async def test_a_dispatcher_may_differ_from_the_visual_parent(self):
        writer = RecordingWriter()
        async with _span(writer, dispatcher_activity_id="act_authorizer") as handle:
            assert handle.activity_id
        started = writer.payloads[0]
        assert started["dispatcher_activity_id"] == "act_authorizer"
        assert started["parent_activity_id"] is None

    async def test_the_current_activity_is_restored_after_the_span(self):
        writer = RecordingWriter()
        assert current_activity_id() is None
        async with _span(writer) as handle:
            assert current_activity_id() == handle.activity_id
        assert current_activity_id() is None

    async def test_the_current_activity_is_restored_even_when_the_body_fails(self):
        writer = RecordingWriter()
        with pytest.raises(RuntimeError):
            async with _span(writer):
                raise RuntimeError("boom")
        assert current_activity_id() is None

    async def test_scope_rides_on_every_event_of_the_span(self):
        writer = RecordingWriter()
        scope = ActivityScope(cycle_id="cyc_1", stage="build")
        async with _span(writer, scope=scope) as handle:
            await handle.update(state=ActivityState.COMPUTING)
        assert all(p["scope"]["cycle_id"] == "cyc_1" for p in writer.payloads)

    async def test_activity_ids_are_unique_per_invocation(self):
        assert new_activity_id() != new_activity_id()


class TestConcurrentSiblings:
    async def test_parallel_workers_are_siblings_of_one_parent(self):
        writer = RecordingWriter()

        async def worker(index: int):
            async with _span(
                writer,
                actor_kind=ActorKind.STAGE_WORKER,
                actor_id=f"unit-{index}",
                operation="worker.run",
                display_name=f"Build worker {index}",
            ):
                await asyncio.sleep(0)

        async with _span(writer, actor_kind=ActorKind.STAGE_ADAPTER, actor_id="build-stage", operation="stage.coordinate") as adapter:
            await asyncio.gather(worker(1), worker(2))

        worker_starts = [p for p in writer.payloads if p["actor_kind"] == "stage_worker" and p["transition"] == "started"]
        assert len(worker_starts) == 2
        assert {p["parent_activity_id"] for p in worker_starts} == {adapter.activity_id}
        assert len({p["activity_id"] for p in worker_starts}) == 2


@pytest.mark.asyncio
class TestABadUpdateCostsTheUpdateAndNotTheRow:
    """The handle's last-known-good state must survive a rejected argument.

    Storing the value first and letting ``build_activity_event`` reject it later
    cost two events rather than one: the bad value stuck to the handle, so the
    span's own closing emit was built from it and was rejected too. One bad
    update left a row open forever — the exact failure this module exists to
    prevent, reached through the method meant to report progress.
    """

    async def test_an_unregistered_operation_still_leaves_a_closable_row(self):
        captured: list[dict] = []
        async with activity_span(
            writer=captured.append,
            run_id="run-1",
            actor_kind=ActorKind.LEAD_AGENT,
            actor_id="lead-agent",
            operation="lead.respond",
            state=ActivityState.THINKING,
        ) as handle:
            await handle.update(state=ActivityState.COMPUTING, operation="cat /etc/passwd")

        assert [frame["transition"] for frame in captured] == ["started", "completed"]
        assert {frame["operation"] for frame in captured} == {"Working on your request"}

    async def test_a_rejected_update_does_not_move_the_state_either(self):
        captured: list[dict] = []
        async with activity_span(
            writer=captured.append,
            run_id="run-1",
            actor_kind=ActorKind.LEAD_AGENT,
            actor_id="lead-agent",
            operation="lead.respond",
            state=ActivityState.THINKING,
        ) as handle:
            await handle.update(state=ActivityState.COMPUTING, operation="not-a-registered-key")
            # The refused call must not have consumed the state change either,
            # or a later legitimate update would be coalesced away as a no-op.
            await handle.update(state=ActivityState.COMPUTING)

        assert [frame["state"] for frame in captured] == ["thinking", "computing", "completed"]

    async def test_an_unregistered_operation_on_settle_is_ignored_not_stored(self):
        captured: list[dict] = []
        async with activity_span(
            writer=captured.append,
            run_id="run-1",
            actor_kind=ActorKind.LEAD_AGENT,
            actor_id="lead-agent",
            operation="lead.respond",
            state=ActivityState.THINKING,
        ) as handle:
            await handle.settle(ActivityState.COMPLETED, operation="/bin/sh -c whoami")

        assert [frame["transition"] for frame in captured] == ["started", "completed"]
        assert captured[-1]["operation"] == "Working on your request"


@pytest.mark.asyncio
class TestAHandleCanOutliveOneBlock:
    """An agent middleware opens in one hook and closes in another."""

    async def test_open_then_settle_emits_the_same_pair_a_span_would(self):
        captured: list[dict] = []
        handle = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.LEAD_AGENT,
            actor_id="lead-agent",
            operation="lead.respond",
            state=ActivityState.PREPARING,
            writer=captured.append,
        )
        assert captured == [], "building a handle must not announce work that has not started"

        await handle.open()
        await handle.update(state=ActivityState.THINKING)
        await handle.settle(ActivityState.COMPLETED)

        assert [frame["transition"] for frame in captured] == ["started", "updated", "completed"]

    async def test_opening_twice_does_not_announce_the_row_twice(self):
        captured: list[dict] = []
        handle = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.LEAD_AGENT,
            actor_id="lead-agent",
            operation="lead.respond",
            writer=captured.append,
        )
        await handle.open()
        await handle.open()
        assert [frame["transition"] for frame in captured] == ["started"]

    async def test_a_settled_handle_refuses_a_late_open(self):
        captured: list[dict] = []
        handle = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.LEAD_AGENT,
            actor_id="lead-agent",
            operation="lead.respond",
            writer=captured.append,
        )
        await handle.open()
        await handle.settle(ActivityState.COMPLETED)
        await handle.open()
        assert [frame["transition"] for frame in captured] == ["started", "completed"]
