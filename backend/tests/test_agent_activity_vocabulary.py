"""Contract tests for the runtime agent-activity vocabulary and envelope.

The activity stream is a presence-and-lineage projection, not a transcript, so
the tests here are mostly about what the envelope *refuses*: unknown actors and
states, unbounded labels, internal identifiers reaching a screen, and any field
that could carry a prompt, a tool argument, or a secret.
"""

import json

import pytest

from deerflow.runtime.activity.envelope import (
    ACTIVITY_ENVELOPE_VERSION,
    ACTIVITY_STREAM_NAME,
    MAX_DISPLAY_NAME_CHARS,
    MAX_OPERATION_CHARS,
    ActivityScope,
    build_activity_event,
)
from deerflow.runtime.activity.vocabulary import (
    OPERATION_LABELS,
    RUNTIME_ROLE_ACTORS,
    TERMINAL_STATES,
    TERMINAL_TRANSITIONS,
    ActivityState,
    ActivityTransition,
    ActorKind,
    resolve_display_name,
)


def _event(**overrides):
    payload = {
        "transition": ActivityTransition.STARTED,
        "activity_id": "act_1",
        "run_id": "run_1",
        "actor_kind": ActorKind.DBTL_SUPERVISOR,
        "actor_id": "dbtl-supervisor",
        "display_name": "Cycle supervisor",
        "state": ActivityState.ROUTING,
        "operation": "supervisor.route",
    }
    payload.update(overrides)
    return build_activity_event(**payload)


class TestTheVocabularyIsClosed:
    """Every enum here is a wire contract; an open one is a silent schema break."""

    def test_actor_kinds_are_exactly_the_version_one_set(self):
        assert {kind.value for kind in ActorKind} == {
            "lead_agent",
            "dbtl_supervisor",
            "stage_adapter",
            "subagent",
            "stage_worker",
        }

    def test_states_are_exactly_the_version_one_set(self):
        assert {state.value for state in ActivityState} == {
            "routing",
            "preparing",
            "thinking",
            "computing",
            "dispatching",
            "coordinating",
            "recording",
            "waiting",
            "completed",
            "failed",
            "cancelled",
            "interrupted",
        }

    def test_terminal_states_and_transitions_agree(self):
        assert {state.value for state in TERMINAL_STATES} == {transition.value for transition in TERMINAL_TRANSITIONS}

    def test_started_and_updated_are_not_terminal(self):
        assert ActivityTransition.STARTED not in TERMINAL_TRANSITIONS
        assert ActivityTransition.UPDATED not in TERMINAL_TRANSITIONS

    def test_supervisor_and_adapter_are_the_runtime_role_actors(self):
        assert RUNTIME_ROLE_ACTORS == frozenset({ActorKind.DBTL_SUPERVISOR, ActorKind.STAGE_ADAPTER})


class TestNoVisibleStringIsAnInternalIdentifier:
    """A breeder has no reason to learn a class name or an enum member."""

    @pytest.mark.parametrize(
        "name",
        [
            resolve_display_name(ActorKind.LEAD_AGENT),
            resolve_display_name(ActorKind.DBTL_SUPERVISOR),
            resolve_display_name(ActorKind.STAGE_ADAPTER, stage="build"),
            resolve_display_name(ActorKind.STAGE_ADAPTER, stage="reconciliation"),
            resolve_display_name(ActorKind.STAGE_WORKER, stage="build", index=2),
            resolve_display_name(ActorKind.SUBAGENT, label="Research subagent"),
        ],
    )
    def test_display_names_are_plain_words(self, name):
        lowered = name.lower()
        assert "_" not in name
        assert "adapter" not in lowered
        assert "livestageadapter" not in lowered
        assert "dbtl" not in lowered
        assert name not in {kind.value for kind in ActorKind}

    def test_the_supervisor_is_a_cycle_supervisor(self):
        assert resolve_display_name(ActorKind.DBTL_SUPERVISOR) == "Cycle supervisor"

    def test_the_adapter_is_named_for_its_stage(self):
        assert resolve_display_name(ActorKind.STAGE_ADAPTER, stage="build") == "Build coordinator"
        assert resolve_display_name(ActorKind.STAGE_ADAPTER, stage="reconciliation") == "Data reconciliation coordinator"

    def test_a_meeting_seat_keeps_its_validated_label(self):
        assert resolve_display_name(ActorKind.STAGE_WORKER, stage="design", index=1, label="Red team") == "Red team"

    def test_a_missing_stage_degrades_to_plain_words_rather_than_raising(self):
        # Instrumentation must never be the reason a run fails.
        assert resolve_display_name(ActorKind.STAGE_ADAPTER) == "Cycle coordinator"
        assert resolve_display_name(ActorKind.STAGE_WORKER) == "Cycle worker"


class TestTheEnvelopeRefusesWhatItCannotDescribe:
    def test_a_well_formed_event_carries_its_type_and_version(self):
        event = _event()
        assert event["type"] == ACTIVITY_STREAM_NAME
        assert event["version"] == ACTIVITY_ENVELOPE_VERSION

    def test_an_unknown_actor_kind_is_refused(self):
        with pytest.raises(ValueError, match="actor_kind"):
            _event(actor_kind="warp_core")

    def test_an_unknown_state_is_refused(self):
        with pytest.raises(ValueError, match="state"):
            _event(state="pondering")

    def test_an_unknown_transition_is_refused(self):
        with pytest.raises(ValueError, match="transition"):
            _event(transition="resumed")

    def test_an_unregistered_operation_is_refused(self):
        # ``operation`` is a bounded server-owned template key, not model prose.
        with pytest.raises(ValueError, match="operation"):
            _event(operation="Summarize the user's private notes")

    def test_every_registered_operation_renders_a_bounded_label(self):
        assert OPERATION_LABELS
        for key, label in OPERATION_LABELS.items():
            assert key.replace(".", "").replace("_", "").isalnum()
            assert 0 < len(label) <= MAX_OPERATION_CHARS

    def test_a_terminal_transition_requires_a_terminal_state(self):
        with pytest.raises(ValueError, match="terminal"):
            _event(transition=ActivityTransition.COMPLETED, state=ActivityState.THINKING)

    def test_a_non_terminal_transition_refuses_a_terminal_state(self):
        with pytest.raises(ValueError, match="terminal"):
            _event(transition=ActivityTransition.UPDATED, state=ActivityState.COMPLETED)

    def test_a_blank_activity_id_is_refused(self):
        with pytest.raises(ValueError, match="activity_id"):
            _event(activity_id="  ")

    @pytest.mark.parametrize(
        "hostile",
        [
            "/Users/someone/.ssh/id_rsa",
            "C:\\Users\\someone\\secrets.env",
            "sk-live-0000 leaked from a tool argument",
            "line one\nline two",
            "tab\tseparated",
        ],
    )
    def test_identifier_fields_refuse_anything_that_is_not_an_identifier(self, hostile):
        # Length bounds alone would let a truncated secret or a host path
        # through. These fields name things; they are never free text.
        with pytest.raises(ValueError, match="actor_id"):
            _event(actor_id=hostile)

    def test_ordinary_runtime_identifiers_are_accepted(self):
        for identifier in ["act_9f3c", "run-1", "general-purpose", "dbtl-supervisor", "context:memory", "unit.3"]:
            assert _event(actor_id=identifier)["actor_id"] == identifier

    def test_a_hostile_optional_identifier_is_dropped_rather_than_failing_the_row(self):
        # Optional lineage is worth losing; the row is not.
        event = _event(parent_activity_id="../../etc/passwd")
        assert event["parent_activity_id"] is None

    def test_display_name_may_hold_real_words_because_that_is_its_job(self):
        # A validated meeting seat label or a subagent name is human text. It is
        # length-bounded rather than charset-bounded, unlike the identifiers.
        assert _event(display_name="Red team (population structure)")["display_name"] == "Red team (population structure)"

    def test_an_overlong_display_name_is_truncated_rather_than_refused(self):
        # A long subagent name is a cosmetic problem; losing the row is not.
        event = _event(display_name="x" * (MAX_DISPLAY_NAME_CHARS + 50))
        assert len(event["display_name"]) == MAX_DISPLAY_NAME_CHARS

    def test_a_blank_display_name_falls_back_to_the_actor_kind_default(self):
        event = _event(actor_kind=ActorKind.LEAD_AGENT, actor_id="lead-agent", display_name="   ")
        assert event["display_name"] == "Lead agent"


class TestTheEnvelopeCarriesLineageAndNothingElse:
    def test_the_field_set_is_closed(self):
        assert set(_event().keys()) == {
            "type",
            "version",
            "transition",
            "activity_id",
            "parent_activity_id",
            "dispatcher_activity_id",
            "run_id",
            "actor_kind",
            "actor_id",
            "display_name",
            "state",
            "operation",
            "scope",
        }

    def test_lineage_defaults_to_null_rather_than_being_omitted(self):
        event = _event()
        assert event["parent_activity_id"] is None
        assert event["dispatcher_activity_id"] is None

    def test_scope_carries_only_reconciliation_identifiers(self):
        event = _event(scope=ActivityScope(cycle_id="cyc_1", stage="build", task_id="unit_3"))
        assert event["scope"] == {"cycle_id": "cyc_1", "stage": "build", "task_id": "unit_3"}

    def test_the_payload_is_json_native_so_every_store_writes_it_identically(self):
        event = _event(scope=ActivityScope(cycle_id="cyc_1", stage="build"))
        assert json.loads(json.dumps(event)) == event

    def test_enum_members_are_serialized_as_their_string_values(self):
        event = _event()
        assert isinstance(event["actor_kind"], str)
        assert not isinstance(event["actor_kind"], ActorKind)
        assert event["state"] == "routing"
