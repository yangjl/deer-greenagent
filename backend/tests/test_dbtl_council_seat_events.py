"""What a live debate view is told, while the debate is happening.

The council already emitted ``task_started`` / ``task_completed`` per unit, but
they carried a unit id and a capability — enough to draw three anonymous
progress bars and nothing more. A viewer could not tell the red team from a
position, could not see that all three seats were the same stand-in agent, and
could not tell which lane's result would actually count.

Every one of those is derivable from the unit id today, which is exactly why it
must not be: a view that parses identifiers to decide who is speaking is one
rename away from labelling every seat wrong, silently.
"""

from __future__ import annotations

import json

from deerflow.agents.dbtl.live_stage.adapter import (
    _seat_description,
    _seat_identity,
    _terminal_seat_event,
)
from deerflow.dbtl.stage_runner import DispatchOutcome, WorkUnit


def _unit(**overrides) -> WorkUnit:
    values = {
        "unit_id": "dbtl-abc-1-experimental_design",
        "capability": "experimental_design",
        "agent_name": "quant-geneticist",
        "prompt": "…",
        "role": "position",
        "focus": "quantitative genetics",
    }
    values.update(overrides)
    return WorkUnit(**values)


class TestSeatIdentity:
    def test_it_names_the_role_without_parsing_the_unit_id(self):
        identity = _seat_identity(_unit(role="red_team", focus="argues against the design"), model="gpt-5.6-sol")

        assert identity["stage"] == "design"
        assert identity["role"] == "red_team"
        assert identity["role_label"] == "Red team"
        assert identity["focus"] == "argues against the design"

    def test_it_says_which_lane_actually_counts(self):
        """Three lanes finish; only one of them is the stage's answer."""
        chair = _seat_identity(_unit(role="chair"), model="m")
        position = _seat_identity(_unit(role="position"), model="m")

        assert chair["counts_toward_stage_output"] is True
        assert position["counts_toward_stage_output"] is False

    def test_a_stand_in_generalist_is_visible_live(self):
        """The whole roster feature is undone if this is only in the package.

        A viewer watching three lanes labelled with three different expertises,
        all secretly one generalist, has been told something false in real time
        and corrected only afterwards in a file they may never open.
        """
        identity = _seat_identity(
            _unit(agent_name="general-purpose", via_generalist=True),
            model="m",
        )

        assert identity["agent_name"] == "general-purpose"
        assert identity["via_generalist"] is True

    def test_it_carries_the_model_that_actually_ran(self):
        identity = _seat_identity(_unit(model="claude-sonnet-5"), model="claude-sonnet-5")

        assert identity["model"] == "claude-sonnet-5"

    def test_the_round_travels_with_the_seat(self):
        identity = _seat_identity(_unit(round=2), model="m")

        assert identity["round"] == 2

    def test_an_unknown_role_degrades_to_a_neutral_label(self):
        """A future role must render as something, not as a crash or a blank."""
        identity = _seat_identity(_unit(role="observer"), model="m")

        assert identity["role"] == "observer"
        assert identity["role_label"] == "Council seat"


class TestSeatDescription:
    def test_it_leads_with_the_role_then_the_angle(self):
        assert _seat_description(_unit(role="position", focus="field logistics")) == "Independent position: field logistics"

    def test_a_capability_selected_seat_falls_back_to_its_capability(self):
        """A selected seat has no focus; its capability is its angle."""
        assert _seat_description(_unit(role="position", focus="")) == "Independent position: experimental design"


class TestTerminalSeatEvent:
    def test_contract_valid_output_completes_the_live_lane(self):
        event = _terminal_seat_event(
            _unit(role="chair"),
            DispatchOutcome(
                unit_id="dbtl-abc-1-experimental_design",
                text=(
                    '{"status":"completed","summary":"Use two seasons.",'
                    '"artifact_refs":[],"claims":[],"evidence_refs":[],'
                    '"limitations":[],"quality_checks":[],'
                    '"recommended_next_actions":[],"provenance":'
                    '{"inputs_examined":[],"tools_used":[]}}'
                ),
            ),
            model="gpt-5.6-sol",
        )

        assert event["type"] == "task_completed"
        assert event["council_seat"]["role"] == "chair"
        assert json.loads(event["result"])["summary"] == "Use two seasons."

    def test_a_valid_result_wrapped_in_prose_is_normalized_for_the_live_view(self):
        event = _terminal_seat_event(
            _unit(role="chair"),
            DispatchOutcome(
                unit_id="dbtl-abc-1-experimental_design",
                text=(
                    "Here is the result:\n```json\n"
                    '{"status":"completed","summary":"Use two seasons.",'
                    '"artifact_refs":[],"claims":[],"evidence_refs":[],'
                    '"limitations":[],"quality_checks":[],'
                    '"recommended_next_actions":[],"provenance":{}}\n```'
                ),
            ),
            model="gpt-5.6-sol",
        )

        assert json.loads(event["result"])["summary"] == "Use two seasons."

    def test_ordinary_stage_work_is_not_mislabeled_as_a_design_meeting(self):
        event = _terminal_seat_event(
            _unit(role="position"),
            DispatchOutcome(
                unit_id="dbtl-build-1",
                text=('{"status":"completed","summary":"Built it.","artifact_refs":[],"claims":[],"evidence_refs":[],"limitations":[],"quality_checks":[],"recommended_next_actions":[],"provenance":{}}'),
            ),
            model="gpt-5.6-sol",
            meeting_stage=None,
        )

        assert event["type"] == "task_completed"
        assert "council_seat" not in event

    def test_a_named_structured_claim_completes_instead_of_showing_no_result(self):
        event = _terminal_seat_event(
            _unit(),
            DispatchOutcome(
                unit_id="dbtl-abc-1-experimental_design",
                text=json.dumps(
                    {
                        "status": "completed",
                        "summary": "Preserve the breeding-population structure.",
                        "artifact_refs": [],
                        "claims": [
                            {
                                "claim": "Validation families must remain held out.",
                            }
                        ],
                        "evidence_refs": [
                            {
                                "kind": "workspace_file",
                                "reference": "/mnt/user-data/design.md",
                            }
                        ],
                        "limitations": [],
                        "quality_checks": [],
                        "recommended_next_actions": [],
                        "provenance": {},
                    }
                ),
            ),
            model="gpt-5.4",
        )

        assert event["type"] == "task_completed"
        assert json.loads(event["result"])["claims"] == ["Validation families must remain held out."]

    def test_prose_output_fails_the_live_lane_instead_of_claiming_completion(self):
        event = _terminal_seat_event(
            _unit(),
            DispatchOutcome(
                unit_id="dbtl-abc-1-experimental_design",
                text="I investigated the design but ran out of time.",
                stop_reason="turn_capped",
            ),
            model="gpt-5.6-sol",
        )

        assert event["type"] == "task_failed"
        assert event["stop_reason"] == "turn_capped"
        assert "returned prose" in event["error"]


class TestTerminalSeatEventCarriesItsActivityLineage:
    """A `task_started` proves a worker started, never who dispatched it.

    Without this, the rail would have to infer lineage from a display name, a
    card type, or `dbtl_stage` — none of which are authoritative, and all of
    which are one rename away from attributing a stage worker to the lead agent.
    """

    def _outcome(self) -> DispatchOutcome:
        return DispatchOutcome(unit_id="dbtl-abc-1-experimental_design", text=None, error="The worker returned no output.")

    def test_lineage_keys_ride_on_the_terminal_event(self):
        event = _terminal_seat_event(
            _unit(role="position"),
            self._outcome(),
            model="gpt-5.6-sol",
            lineage={"activity_id": "act_worker", "parent_activity_id": "act_stage", "dispatcher_activity_id": "act_stage"},
        )

        assert event["activity_id"] == "act_worker"
        assert event["parent_activity_id"] == "act_stage"
        assert event["dispatcher_activity_id"] == "act_stage"

    def test_an_event_without_lineage_is_byte_identical_to_the_old_one(self):
        # The additions are optional during rollout: an older client must parse
        # exactly the payload it parsed before.
        unit = _unit(role="position")
        with_none = _terminal_seat_event(unit, self._outcome(), model="gpt-5.6-sol", lineage=None)
        legacy = _terminal_seat_event(unit, self._outcome(), model="gpt-5.6-sol")

        assert with_none == legacy
        assert "activity_id" not in legacy

    def test_lineage_cannot_overwrite_the_seat_identity(self):
        event = _terminal_seat_event(
            _unit(role="chair"),
            self._outcome(),
            model="gpt-5.6-sol",
            meeting_stage="design",
            lineage={"activity_id": "act_worker"},
        )

        assert event["task_id"] == "dbtl-abc-1-experimental_design"
        assert event["council_seat"]["role"] == "chair"
        assert event["dbtl_stage"] == "design"
