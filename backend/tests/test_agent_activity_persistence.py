"""Durable projection for runtime agent activity.

Live custom events alone cannot carry this feature: a refresh, a reconnect, a
hidden deck-triggered run, or a replay gap would leave the rail spinning on work
that finished. So the persistence path is the thing under test here, and its two
sharp edges are batching (``put`` is a documented low-frequency path) and the
namespace branch (persistence must not depend on what a client asked to stream).
"""

import json

import pytest

from deerflow.runtime.activity.emitter import activity_span, make_activity_handle
from deerflow.runtime.activity.lineage import (
    deterministic_activity_id,
    stage_activity_id,
    supervisor_activity_id,
)
from deerflow.runtime.activity.spans import run_id_from_config
from deerflow.runtime.activity.vocabulary import ActivityState, ActorKind
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.activity_buffer import ActivityEventBuffer


async def _frames(**span_kwargs) -> list[dict]:
    captured: list[dict] = []
    async with activity_span(
        writer=captured.append,
        run_id="run-1",
        actor_kind=ActorKind.LEAD_AGENT,
        actor_id="lead-agent",
        operation="lead.respond",
        state=ActivityState.THINKING,
        **span_kwargs,
    ) as handle:
        await handle.update(state=ActivityState.COMPUTING)
    return captured


@pytest.mark.asyncio
class TestTheBufferPersistsWhatTheStreamCarried:
    async def test_a_closed_span_is_durable_without_waiting_for_run_end(self):
        store = MemoryRunEventStore()
        buffer = ActivityEventBuffer(store, "thread-1", "run-1")
        for frame in await _frames():
            await buffer.add(frame)

        # No explicit flush: the terminal transition is its own flush boundary,
        # so a finished actor is durable promptly rather than only at run end.
        events = await store.list_events("thread-1", "run-1")
        assert [event["content"]["transition"] for event in events] == ["started", "updated", "completed"]
        assert {event["event_type"] for event in events} == {"runtime.agent.activity"}
        assert {event["category"] for event in events} == {"activity"}

    async def test_frames_from_other_streams_are_not_persisted(self):
        store = MemoryRunEventStore()
        buffer = ActivityEventBuffer(store, "thread-1", "run-1")
        await buffer.add({"type": "task_started", "task_id": "t1"})
        await buffer.add({"type": "stream_replay_gap"})
        await buffer.flush()
        assert await store.list_events("thread-1", "run-1") == []

    async def test_no_store_is_a_silent_no_op(self):
        buffer = ActivityEventBuffer(None, "thread-1", "run-1")
        for frame in await _frames():
            await buffer.add(frame)
        await buffer.flush()

    async def test_a_store_outage_re_buffers_rather_than_dropping_the_closing_row(self):
        class FlakyStore:
            def __init__(self):
                self.fail = True
                self.batches: list[list[dict]] = []

            async def put_batch(self, events):
                if self.fail:
                    raise RuntimeError("database is unreachable")
                self.batches.append(list(events))
                return events

        store = FlakyStore()
        buffer = ActivityEventBuffer(store, "thread-1", "run-1")
        for frame in await _frames():
            await buffer.add(frame)
        assert store.batches == []

        store.fail = False
        await buffer.flush()
        assert [event["content"]["transition"] for event in store.batches[0]] == ["started", "updated", "completed"]

    async def test_the_task_id_filter_still_finds_one_worker_s_activity(self):
        from deerflow.runtime.activity.envelope import ActivityScope

        store = MemoryRunEventStore()
        buffer = ActivityEventBuffer(store, "thread-1", "run-1")
        for frame in await _frames(scope=ActivityScope(task_id="unit-3", stage="build")):
            await buffer.add(frame)

        scoped = await store.list_events("thread-1", "run-1", task_id="unit-3")
        assert len(scoped) == 3
        assert await store.list_events("thread-1", "run-1", task_id="unit-9") == []

    async def test_activity_stays_out_of_the_conversation_feed(self):
        # `list_messages` filters by category. Activity sharing "message" or
        # "trace" would put every routing transition inside a conversation.
        store = MemoryRunEventStore()
        buffer = ActivityEventBuffer(store, "thread-1", "run-1")
        for frame in await _frames():
            await buffer.add(frame)
        assert await store.list_messages("thread-1") == []


@pytest.mark.asyncio
class TestATerminalRowIsNeverReopened:
    """The reducer contract's sharpest rule, enforced at the producer too.

    A span mints its own id per invocation, so repeated work cannot emit
    ``started → completed → started`` against one row. The stage adapter arrived
    at the same rule from the other direction: a Design meeting dispatches
    several rounds — independent positions, red team, chair — and a
    deterministic per-stage id shared across them would have reopened a settled
    row, so the adapter keeps one row for the whole ``execute`` and reports each
    round as a state change instead. The consumer refuses a reopen regardless
    (``test_agent_activity_reducer.py``); this pins that no producer asks.
    """

    async def test_repeated_rounds_never_reopen_a_settled_row(self):
        captured: list[dict] = []

        async def one_round():
            async with activity_span(
                writer=captured.append,
                run_id="run-1",
                actor_kind=ActorKind.STAGE_ADAPTER,
                actor_id="design-stage",
                operation="stage.coordinate",
                state=ActivityState.COORDINATING,
                stage="design",
            ):
                pass

        for _ in range(3):
            await one_round()

        settled: set[str] = set()
        for frame in captured:
            if frame["transition"] == "started":
                assert frame["activity_id"] not in settled, "a settled activity was reopened"
            else:
                settled.add(frame["activity_id"])

        assert len(settled) == 3, "each round is its own row"
        assert {frame["display_name"] for frame in captured} == {"Design coordinator"}


class TestLineageSurvivesAGraphNodeBoundary:
    def test_the_same_run_derives_the_same_supervisor_row(self):
        assert supervisor_activity_id("run-1") == supervisor_activity_id("run-1")

    def test_different_runs_derive_different_rows(self):
        assert supervisor_activity_id("run-1") != supervisor_activity_id("run-2")

    def test_a_stage_row_is_keyed_by_cycle_and_stage_as_well_as_run(self):
        # One request may touch a review meeting and then the stage it reviewed.
        assert stage_activity_id("run-1", "cyc-1", "build") != stage_activity_id("run-1", "cyc-1", "test")
        assert stage_activity_id("run-1", "cyc-1", "build") != stage_activity_id("run-1", "cyc-2", "build")

    def test_derived_ids_are_valid_envelope_identifiers(self):
        from deerflow.runtime.activity.envelope import build_activity_event

        event = build_activity_event(
            transition="started",
            activity_id=supervisor_activity_id("run-1"),
            run_id="run-1",
            actor_kind=ActorKind.DBTL_SUPERVISOR,
            actor_id="dbtl-supervisor",
            display_name="Cycle supervisor",
            state=ActivityState.ROUTING,
            operation="supervisor.route",
            parent_activity_id=stage_activity_id("run-1", "cyc-1", "build"),
        )
        assert event["activity_id"] == supervisor_activity_id("run-1")
        assert event["parent_activity_id"] is not None

    def test_the_actor_key_is_part_of_the_derivation(self):
        assert deterministic_activity_id("run-1", "a") != deterministic_activity_id("run-1", "b")


class TestTheRunIdAccessor:
    def test_it_reads_either_half_of_the_config(self):
        assert run_id_from_config({"context": {"run_id": "run-1"}}) == "run-1"
        assert run_id_from_config({"configurable": {"run_id": "run-2"}}) == "run-2"

    def test_context_wins_when_both_are_present(self):
        assert run_id_from_config({"context": {"run_id": "run-1"}, "configurable": {"run_id": "run-2"}}) == "run-1"

    def test_it_reads_the_context_shape_a_real_langgraph_node_receives(self):
        assert run_id_from_config({"configurable": {"context": {"run_id": "run-node"}}}) == "run-node"

    def test_it_reads_the_server_owned_pregel_runtime(self):
        from types import SimpleNamespace

        config = {
            "configurable": {
                "__pregel_runtime": SimpleNamespace(context={"run_id": "run-runtime"}),
                "context": {"run_id": "run-node"},
            }
        }
        assert run_id_from_config(config) == "run-runtime"

    def test_a_missing_or_malformed_run_id_is_none(self):
        assert run_id_from_config({}) is None
        assert run_id_from_config(None) is None
        assert run_id_from_config({"context": {"run_id": ""}}) is None
        assert run_id_from_config({"context": {"run_id": 7}}) is None


class TestThePersistedRowIsRebuiltNotCopied:
    """A durable row outlives the process that wrote it and is served to clients.

    The envelope's safety argument — that no prompt, tool argument, or secret can
    reach a screen through it — is a property of the constructor. Copying an
    inbound frame into a store would move that property to whoever emitted the
    frame, so anything an emitter attached, now or after some future edit, would
    be stored and served. The parse boundary keeps it where it started.
    """

    def _frame(self, **extra) -> dict:
        from deerflow.runtime.activity.envelope import build_activity_event

        return {
            **build_activity_event(
                transition="started",
                activity_id="act_one",
                run_id="run-1",
                actor_kind=ActorKind.LEAD_AGENT,
                actor_id="lead-agent",
                display_name="Lead agent",
                state=ActivityState.THINKING,
                operation="lead.respond",
            ),
            **extra,
        }

    def test_a_field_the_projection_does_not_describe_is_not_stored(self):
        from deerflow.runtime.activity.envelope import ACTIVITY_EVENT_FIELDS
        from deerflow.runtime.activity.run_event import activity_run_event

        record = activity_run_event(
            self._frame(
                prompt="the user's private question",
                api_key="sk-live-0000",
                tool_args={"command": "cat ~/.ssh/id_rsa"},
            )
        )

        assert record is not None
        assert set(record["content"]) == set(ACTIVITY_EVENT_FIELDS)
        assert "sk-live" not in json.dumps(record)

    def test_an_operation_outside_the_server_owned_table_is_refused(self):
        from deerflow.runtime.activity.run_event import activity_run_event

        assert activity_run_event(self._frame(operation="cat /etc/passwd")) is None

    def test_a_hostile_identifier_is_refused_rather_than_stored(self):
        from deerflow.runtime.activity.run_event import activity_run_event

        assert activity_run_event(self._frame(actor_id="lead\nBearer sk-live-0000")) is None

    def test_an_unknown_scope_key_is_dropped_and_the_row_survives(self):
        from deerflow.runtime.activity.run_event import activity_run_event

        record = activity_run_event(self._frame(scope={"cycle_id": "cyc-1", "stage": "build", "task_id": "unit-2", "workspace_path": "/Users/someone/secrets"}))

        assert record is not None
        assert record["content"]["scope"] == {"cycle_id": "cyc-1", "stage": "build", "task_id": "unit-2"}

    def test_a_transition_and_state_that_disagree_are_refused(self):
        from deerflow.runtime.activity.run_event import activity_run_event

        assert activity_run_event(self._frame(transition="completed")) is None


@pytest.mark.asyncio
class TestARunEndsWhetherOrNotItsActorsSaySo:
    """A cancelled run, a lost lease, or a hook the graph raised past.

    Nothing after the run will ever close those rows, so a reload would show
    work that finished weeks ago as still in progress. The run's own end is the
    last moment anything knows.
    """

    async def _opened_only(self) -> list[dict]:
        captured: list[dict] = []
        handle = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.LEAD_AGENT,
            actor_id="lead-agent",
            operation="lead.respond",
            state=ActivityState.THINKING,
            writer=captured.append,
        )
        await handle.open()
        return captured

    async def test_a_row_left_open_is_settled_as_interrupted(self):
        store = MemoryRunEventStore()
        buffer = ActivityEventBuffer(store, "thread-1", "run-1")
        for frame in await self._opened_only():
            await buffer.add(frame)

        await buffer.close_open_activities()
        await buffer.flush()

        events = await store.list_events("thread-1", "run-1")
        assert [event["content"]["transition"] for event in events] == ["started", "interrupted"]
        # Not "completed", which would claim an outcome nobody observed, and not
        # "failed", which would blame the work for the run being taken away.
        assert events[-1]["content"]["state"] == "interrupted"

    async def test_the_synthesized_close_keeps_the_row_s_identity(self):
        store = MemoryRunEventStore()
        buffer = ActivityEventBuffer(store, "thread-1", "run-1")
        opened = await self._opened_only()
        for frame in opened:
            await buffer.add(frame)
        await buffer.close_open_activities()
        await buffer.flush()

        events = await store.list_events("thread-1", "run-1")
        assert events[-1]["content"]["activity_id"] == opened[0]["activity_id"]
        assert events[-1]["content"]["display_name"] == "Lead agent"

    async def test_a_row_that_closed_itself_is_not_closed_again(self):
        store = MemoryRunEventStore()
        buffer = ActivityEventBuffer(store, "thread-1", "run-1")
        for frame in await _frames():
            await buffer.add(frame)

        await buffer.close_open_activities()
        await buffer.flush()

        events = await store.list_events("thread-1", "run-1")
        assert [event["content"]["transition"] for event in events] == ["started", "updated", "completed"]

    async def test_closing_nothing_is_a_no_op(self):
        store = MemoryRunEventStore()
        buffer = ActivityEventBuffer(store, "thread-1", "run-1")
        await buffer.close_open_activities()
        await buffer.flush()
        assert await store.list_events("thread-1", "run-1") == []
