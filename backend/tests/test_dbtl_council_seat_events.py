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

from deerflow.agents.dbtl.stage_execution import _seat_description, _seat_identity
from deerflow.dbtl.stage_runner import WorkUnit


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
