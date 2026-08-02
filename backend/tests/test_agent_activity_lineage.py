"""Who dispatched whom, recorded by the backend rather than guessed in a browser.

A `task_started` event only proves that a worker started. Whether it came from
the lead agent or from a stage adapter is exactly what a reader needs and
exactly what the event never said — so the frontend would have had to infer it
from display names, card types, or `dbtl_stage`, none of which are authoritative.
These tests pin the linkage the backend now states outright.
"""

import pytest

from deerflow.runtime.activity.emitter import make_activity_handle
from deerflow.runtime.activity.envelope import ActivityScope
from deerflow.runtime.activity.lineage import (
    deterministic_activity_id,
    lead_activity_id,
    stage_activity_id,
    supervisor_activity_id,
)
from deerflow.runtime.activity.reducer import active_leaves, reduce_activity_events
from deerflow.runtime.activity.vocabulary import ActivityState, ActorKind, subagent_label


class TestTheLeadRowCanBeNamedFromAnywhereInItsRun:
    """A delegation runs in the tools node, several nodes from the opening hook."""

    def test_the_same_run_derives_the_same_lead_row(self):
        assert lead_activity_id("run-1") == lead_activity_id("run-1")

    def test_different_runs_derive_different_lead_rows(self):
        assert lead_activity_id("run-1") != lead_activity_id("run-2")

    def test_the_lead_and_supervisor_rows_are_distinct(self):
        assert lead_activity_id("run-1") != supervisor_activity_id("run-1")

    def test_a_delegation_id_is_stable_for_one_task(self):
        first = deterministic_activity_id("run-1", "subagent:call-1")
        assert first == deterministic_activity_id("run-1", "subagent:call-1")
        assert first != deterministic_activity_id("run-1", "subagent:call-2")


class TestADelegatedSubagentIsNamedByItsRegistryName:
    @pytest.mark.parametrize(
        ("registered", "expected"),
        [
            ("general-purpose", "General-purpose subagent"),
            ("bash", "Bash subagent"),
            ("research_agent", "Research agent"),
        ],
    )
    def test_a_registered_name_becomes_words(self, registered, expected):
        assert subagent_label(registered) == expected

    @pytest.mark.parametrize("hostile", [None, "", "   ", "x" * 60, "name\nBearer sk-live", "../../etc/passwd", 7])
    def test_anything_unusable_falls_back_rather_than_printing(self, hostile):
        assert subagent_label(hostile) is None

    def test_the_generic_label_is_used_when_the_name_is_unusable(self):
        handle = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.SUBAGENT,
            actor_id="general-purpose",
            operation="subagent.run",
            display_name=subagent_label("sk-live-0000\nexport"),
            writer=lambda payload: None,
        )
        assert handle._base["display_name"] == "Subagent"


class TestLineageFieldsRideOnTheExistingTaskEvents:
    def _handle(self, **kwargs):
        return make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.SUBAGENT,
            actor_id="general-purpose",
            operation="subagent.run",
            writer=lambda payload: None,
            **kwargs,
        )

    def test_a_parented_row_reports_all_three_ids(self):
        handle = self._handle(parent_activity_id=lead_activity_id("run-1"))
        fields = handle.lineage_fields()
        assert set(fields) == {"activity_id", "parent_activity_id", "dispatcher_activity_id"}
        assert fields["parent_activity_id"] == lead_activity_id("run-1")

    def test_an_unparented_row_omits_the_keys_rather_than_sending_null(self):
        # An older consumer must see exactly the payload it saw before, and a
        # null lineage key is not the same as no lineage key to a strict parser.
        fields = self._handle(parent_activity_id=None, dispatcher_activity_id=None).lineage_fields()
        assert set(fields) == {"activity_id"}

    def test_merging_into_a_task_event_leaves_the_original_keys_intact(self):
        handle = self._handle(parent_activity_id=lead_activity_id("run-1"))
        event = {"type": "task_started", "task_id": "call-1", "description": "Research", **handle.lineage_fields()}
        assert event["type"] == "task_started"
        assert event["task_id"] == "call-1"
        assert event["parent_activity_id"] == lead_activity_id("run-1")


class TestTheTreeAReaderEndsUpWith:
    """The point of the lineage: an answer to "who is working, under whom"."""

    def _events(self, *handles) -> list[dict]:
        frames: list[dict] = []
        for handle in handles:
            handle._writer = frames.append
        return frames

    @pytest.mark.asyncio
    async def test_a_lead_delegation_nests_under_the_lead(self):
        frames: list[dict] = []
        lead = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.LEAD_AGENT,
            actor_id="lead-agent",
            operation="lead.respond",
            activity_id=lead_activity_id("run-1"),
            writer=frames.append,
        )
        await lead.open()
        await lead.update(state=ActivityState.WAITING, operation="lead.wait")

        delegated = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.SUBAGENT,
            actor_id="general-purpose",
            operation="subagent.run",
            state=ActivityState.COMPUTING,
            display_name=subagent_label("general-purpose"),
            parent_activity_id=lead_activity_id("run-1"),
            scope=ActivityScope(task_id="call-1"),
            writer=frames.append,
        )
        await delegated.open()

        rows = reduce_activity_events(frames)
        # The innermost active row is the answer to "who is working now" — the
        # lead is waiting on this delegation, not doing the work itself.
        leaves = active_leaves(rows)
        assert [row.display_name for row in leaves] == ["General-purpose subagent"]
        assert leaves[0].parent_activity_id == lead_activity_id("run-1")
        assert leaves[0].task_id == "call-1"

    @pytest.mark.asyncio
    async def test_parallel_stage_workers_are_siblings_under_one_stage(self):
        frames: list[dict] = []
        adapter_id = stage_activity_id("run-1", "cyc-1", "build")
        adapter = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.STAGE_ADAPTER,
            actor_id="build-stage",
            operation="stage.coordinate",
            state=ActivityState.COORDINATING,
            stage="build",
            activity_id=adapter_id,
            writer=frames.append,
        )
        await adapter.open()

        for index in (1, 2):
            worker = make_activity_handle(
                run_id="run-1",
                actor_kind=ActorKind.STAGE_WORKER,
                actor_id=f"unit-{index}",
                operation="worker.run",
                state=ActivityState.COMPUTING,
                stage="build",
                index=index,
                parent_activity_id=adapter_id,
                scope=ActivityScope(cycle_id="cyc-1", stage="build", task_id=f"unit-{index}"),
                writer=frames.append,
            )
            await worker.open()

        rows = reduce_activity_events(frames)
        leaves = active_leaves(rows)
        assert [row.display_name for row in leaves] == ["Build worker 1", "Build worker 2"]
        assert {row.parent_activity_id for row in leaves} == {adapter_id}

    @pytest.mark.asyncio
    async def test_a_meeting_seat_keeps_its_validated_role_label(self):
        frames: list[dict] = []
        seat = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.STAGE_WORKER,
            actor_id="unit-red",
            operation="meeting.participate",
            state=ActivityState.COMPUTING,
            stage="design",
            index=2,
            display_name="Red team",
            scope=ActivityScope(cycle_id="cyc-1", stage="design", task_id="unit-red"),
            writer=frames.append,
        )
        await seat.open()

        rows = reduce_activity_events(frames)
        # The seat's own label wins over the generated "Design worker 2":
        # replacing it would hide which seat is speaking.
        assert [row.display_name for row in rows.values()] == ["Red team"]
        assert [row.operation for row in rows.values()] == ["Taking part in the meeting"]

    @pytest.mark.asyncio
    async def test_a_finished_worker_hands_the_answer_back_to_its_coordinator(self):
        frames: list[dict] = []
        adapter_id = stage_activity_id("run-1", "cyc-1", "build")
        adapter = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.STAGE_ADAPTER,
            actor_id="build-stage",
            operation="stage.coordinate",
            state=ActivityState.COORDINATING,
            stage="build",
            activity_id=adapter_id,
            writer=frames.append,
        )
        await adapter.open()
        worker = make_activity_handle(
            run_id="run-1",
            actor_kind=ActorKind.STAGE_WORKER,
            actor_id="unit-1",
            operation="worker.run",
            state=ActivityState.COMPUTING,
            stage="build",
            index=1,
            parent_activity_id=adapter_id,
            writer=frames.append,
        )
        await worker.open()
        await worker.settle(ActivityState.COMPLETED)

        rows = reduce_activity_events(frames)
        assert [row.display_name for row in active_leaves(rows)] == ["Build stage"]
