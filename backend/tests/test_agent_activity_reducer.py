"""The consumer contract: what a sequence of transitions means to a reader.

Every rule here exists because a live stream misleads whoever is folding it — a
reconnect re-sends what was already seen, a backfill overlaps the live tail, a
late subscriber joins mid-flight, and a run can end without its actors saying
so. The producer cannot fix any of those; the fold has to.
"""

from dataclasses import fields

import pytest

from deerflow.runtime.activity.envelope import ActivityScope, build_activity_event
from deerflow.runtime.activity.reducer import (
    ActivityRow,
    active_leaves,
    active_rows,
    apply_activity_event,
    close_open_rows,
    reduce_activity_events,
)
from deerflow.runtime.activity.vocabulary import ActivityState, ActivityTransition, ActorKind


def _event(
    transition: ActivityTransition | str,
    state: ActivityState | str,
    *,
    activity_id: str = "act_one",
    operation: str = "lead.respond",
    actor_kind: ActorKind = ActorKind.LEAD_AGENT,
    display_name: str = "Lead agent",
    parent: str | None = None,
    scope: ActivityScope | None = None,
) -> dict:
    return build_activity_event(
        transition=transition,
        activity_id=activity_id,
        run_id="run-1",
        actor_kind=actor_kind,
        actor_id="lead-agent",
        display_name=display_name,
        state=state,
        operation=operation,
        parent_activity_id=parent,
        scope=scope,
    )


def _opened(**kwargs) -> dict:
    return _event(ActivityTransition.STARTED, ActivityState.PREPARING, **kwargs)


class TestOneRowFollowsItsOwnTransitions:
    def test_a_started_event_opens_a_row(self):
        rows = reduce_activity_events([_opened()])
        assert list(rows) == ["act_one"]
        assert rows["act_one"].state is ActivityState.PREPARING
        assert rows["act_one"].active

    def test_an_update_moves_the_state_and_the_operation(self):
        rows = reduce_activity_events(
            [
                _opened(),
                _event(ActivityTransition.UPDATED, ActivityState.THINKING, operation="lead.delegate"),
            ]
        )
        assert rows["act_one"].state is ActivityState.THINKING
        assert rows["act_one"].operation == "Delegating a subtask"

    def test_a_terminal_event_settles_the_row(self):
        rows = reduce_activity_events([_opened(), _event(ActivityTransition.COMPLETED, ActivityState.COMPLETED)])
        assert rows["act_one"].settled
        assert active_rows(rows) == []

    def test_identity_fields_come_from_the_opening_event(self):
        rows = reduce_activity_events(
            [
                _opened(parent="act_parent", scope=ActivityScope(cycle_id="cyc-1", stage="build", task_id="unit-2")),
                _event(ActivityTransition.UPDATED, ActivityState.COMPUTING),
            ]
        )
        row = rows["act_one"]
        assert (row.parent_activity_id, row.cycle_id, row.stage, row.task_id) == ("act_parent", "cyc-1", "build", "unit-2")


class TestReplayIsNotNewInformation:
    """A reconnect re-delivers events, and a backfill overlaps the live tail."""

    def test_folding_the_same_sequence_twice_is_the_same_as_once(self):
        events = [
            _opened(),
            _event(ActivityTransition.UPDATED, ActivityState.THINKING),
            _event(ActivityTransition.COMPLETED, ActivityState.COMPLETED),
        ]
        assert reduce_activity_events(events) == reduce_activity_events(events + events)

    def test_a_prefix_then_the_whole_stream_is_the_same_as_the_whole_stream(self):
        events = [
            _opened(),
            _event(ActivityTransition.UPDATED, ActivityState.COMPUTING),
            _event(ActivityTransition.COMPLETED, ActivityState.COMPLETED),
        ]
        incremental = reduce_activity_events(events, rows=reduce_activity_events(events[:2]))
        assert incremental == reduce_activity_events(events)

    def test_a_repeated_opening_does_not_reset_a_live_row(self):
        rows = reduce_activity_events(
            [
                _opened(),
                _event(ActivityTransition.UPDATED, ActivityState.THINKING),
                _opened(),
            ]
        )
        assert rows["act_one"].state is ActivityState.THINKING

    def test_the_input_snapshot_is_never_mutated(self):
        before = reduce_activity_events([_opened()])
        after = apply_activity_event(before, _event(ActivityTransition.COMPLETED, ActivityState.COMPLETED))
        assert before["act_one"].active
        assert after["act_one"].settled

    def test_durable_sequence_refuses_an_older_record_that_arrives_late(self):
        rows = reduce_activity_events(
            [
                {"seq": 10, "content": _opened()},
                {
                    "seq": 12,
                    "content": _event(ActivityTransition.UPDATED, ActivityState.COMPUTING),
                },
                {
                    "seq": 11,
                    "content": _event(ActivityTransition.UPDATED, ActivityState.THINKING),
                },
            ]
        )
        assert rows["act_one"].state is ActivityState.COMPUTING
        assert rows["act_one"].last_seq == 12


class TestASettledRowStaysSettled:
    def test_a_later_started_cannot_reopen_it(self):
        rows = reduce_activity_events(
            [
                _opened(),
                _event(ActivityTransition.COMPLETED, ActivityState.COMPLETED),
                _opened(),
            ]
        )
        assert rows["act_one"].state is ActivityState.COMPLETED

    def test_a_stale_update_arriving_after_the_close_is_refused(self):
        rows = reduce_activity_events(
            [
                _opened(),
                _event(ActivityTransition.FAILED, ActivityState.FAILED),
                _event(ActivityTransition.UPDATED, ActivityState.THINKING),
            ]
        )
        assert rows["act_one"].state is ActivityState.FAILED

    def test_a_second_terminal_does_not_change_the_recorded_outcome(self):
        rows = reduce_activity_events(
            [
                _opened(),
                _event(ActivityTransition.CANCELLED, ActivityState.CANCELLED),
                _event(ActivityTransition.COMPLETED, ActivityState.COMPLETED),
            ]
        )
        assert rows["act_one"].state is ActivityState.CANCELLED


class TestARowMustBeOpenedBeforeItCanBeUpdated:
    """A late subscriber sees updates whose opening it missed."""

    def test_an_update_for_an_unknown_row_is_dropped(self):
        assert reduce_activity_events([_event(ActivityTransition.UPDATED, ActivityState.THINKING)]) == {}

    def test_a_terminal_for_an_unknown_row_is_dropped(self):
        assert reduce_activity_events([_event(ActivityTransition.COMPLETED, ActivityState.COMPLETED)]) == {}

    def test_backfill_before_the_live_tail_recovers_the_row(self):
        live = [_event(ActivityTransition.UPDATED, ActivityState.THINKING)]
        rows = reduce_activity_events(live, rows=reduce_activity_events([_opened()]))
        assert rows["act_one"].state is ActivityState.THINKING


class TestMalformedFramesCostTheFrameAndNothingElse:
    @pytest.mark.parametrize(
        "frame",
        [
            None,
            "started",
            {"type": "task_started", "task_id": "t1"},
            {"type": "agent_activity", "transition": "invented"},
            {**_opened(), "operation": "rm -rf /"},
            {**_opened(), "actor_id": "sk-live-secret\nexport"},
            {**_opened(), "transition": "completed"},
        ],
    )
    def test_a_bad_frame_leaves_the_rows_untouched(self, frame):
        before = reduce_activity_events([_opened(activity_id="act_kept")])
        assert apply_activity_event(before, frame) == before

    def test_an_unknown_field_is_not_carried_into_a_row(self):
        rows = reduce_activity_events([{**_opened(), "prompt": "the user's private message"}])
        row = rows["act_one"]
        assert isinstance(row, ActivityRow)
        assert not hasattr(row, "prompt")
        assert not any("private" in str(getattr(row, field.name)) for field in fields(ActivityRow))


class TestWhoIsWorkingNow:
    def _tree(self) -> dict[str, ActivityRow]:
        return reduce_activity_events(
            [
                _opened(activity_id="act_super", actor_kind=ActorKind.DBTL_SUPERVISOR, display_name="Cycle supervisor"),
                _opened(activity_id="act_stage", actor_kind=ActorKind.STAGE_ADAPTER, display_name="Build stage", parent="act_super"),
            ]
        )

    def test_the_innermost_active_row_is_the_answer(self):
        assert [row.activity_id for row in active_leaves(self._tree())] == ["act_stage"]

    def test_a_parent_becomes_the_answer_once_its_child_closes(self):
        rows = reduce_activity_events(
            [_event(ActivityTransition.COMPLETED, ActivityState.COMPLETED, activity_id="act_stage")],
            rows=self._tree(),
        )
        assert [row.activity_id for row in active_leaves(rows)] == ["act_super"]

    def test_rows_keep_the_order_they_opened_in(self):
        rows = reduce_activity_events(
            [_opened(activity_id="act_c"), _opened(activity_id="act_a"), _opened(activity_id="act_b")],
        )
        assert [row.activity_id for row in active_rows(rows)] == ["act_c", "act_a", "act_b"]


class TestARunEndsWhetherOrNotItsActorsSayS0:
    def test_open_rows_settle_as_interrupted(self):
        rows = close_open_rows(reduce_activity_events([_opened()]))
        assert rows["act_one"].state is ActivityState.INTERRUPTED

    def test_an_actor_that_did_report_its_outcome_keeps_it(self):
        rows = close_open_rows(reduce_activity_events([_opened(), _event(ActivityTransition.FAILED, ActivityState.FAILED)]))
        assert rows["act_one"].state is ActivityState.FAILED
