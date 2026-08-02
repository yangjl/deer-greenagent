"""The Build work meeting: convened by a person, advisory to the end."""

from __future__ import annotations

import json

from deerflow.agents.dbtl.live_stage.build_meeting import (
    BOUNDARIES,
    BUILD_WORK_MEETING_CONTRACT,
    MAX_OPTIONS,
    MeetingContext,
    meeting_units,
    parse_recommendation,
)

CONTEXT = MeetingContext(
    question="Should the holdout be by family or by year?",
    step_key="execute_phases",
    cycle_title="Drought model",
    research_question="Does the model generalize?",
    objective="Test an independent population",
    success_criteria="Accuracy >= 0.7",
    design_uri="/mnt/user-data/outputs/dbtl/design/design-rev1.md",
    design_hash="a" * 64,
    workspace_note="Read files under /mnt/user-data.",
    manifest=({"path": "/mnt/user-data/yield.csv", "bytes": 42},),
)


def _chair(**overrides) -> str:
    return json.dumps(
        {
            "outcome": "recommendation_ready",
            "summary": "Both holdouts are defensible; family holdout answers the stated question more directly.",
            "options": [
                {"label": "Family holdout", "consequence": "Answers generalization across families.", "evidence": "Design names family structure."},
                {"label": "Year holdout", "consequence": "Answers generalization across seasons.", "evidence": "Two seasons are declared."},
            ],
            "recommended": "Family holdout",
            "reasoning": "The research question is about independent populations.",
            "cannot_decide": ["Whether the 2024 trial counts as independent."],
            **overrides,
        }
    )


class TestTheMeetingIsThreeSeatsNotOneReviewer:
    def test_it_seats_a_position_a_red_team_and_a_chair(self) -> None:
        units = meeting_units(attempt_id="a1", agent_name="general-purpose", model="m", via_generalist=True, context=CONTEXT)

        assert [unit.role for unit in units] == ["position", "red_team", "chair"]

    def test_every_seat_reads_the_same_question(self) -> None:
        units = meeting_units(attempt_id="a1", agent_name="general-purpose", model="m", via_generalist=True, context=CONTEXT)

        assert all("Should the holdout be by family or by year?" in unit.prompt for unit in units)

    def test_every_seat_is_told_what_it_may_not_do(self) -> None:
        units = meeting_units(attempt_id="a1", agent_name="general-purpose", model="m", via_generalist=True, context=CONTEXT)

        assert all(BOUNDARIES in unit.prompt for unit in units)
        assert all("does not resume the build" in unit.prompt for unit in units)

    def test_only_the_chair_is_asked_for_a_structured_result(self) -> None:
        units = meeting_units(attempt_id="a1", agent_name="general-purpose", model="m", via_generalist=True, context=CONTEXT)

        structured = [unit.role for unit in units if '"recommended"' in unit.prompt]
        assert structured == ["chair"]

    def test_the_approved_design_is_named_by_hash(self) -> None:
        units = meeting_units(attempt_id="a1", agent_name="general-purpose", model="m", via_generalist=True, context=CONTEXT)

        assert "a" * 64 in units[0].prompt


class TestTheChairAdvisesAndNeverDecides:
    def test_it_returns_the_options_a_person_chooses_between(self) -> None:
        recommendation = parse_recommendation(_chair())

        assert recommendation.usable
        assert [option.label for option in recommendation.options] == ["Family holdout", "Year holdout"]
        assert recommendation.recommended == "Family holdout"

    def test_options_are_read_before_the_recommendation(self) -> None:
        briefing = parse_recommendation(_chair()).as_briefing()

        assert briefing.index("Family holdout:") < briefing.index("leans towards")

    def test_what_it_could_not_settle_is_said_out_loud(self) -> None:
        briefing = parse_recommendation(_chair()).as_briefing()

        assert "Whether the 2024 trial counts as independent." in briefing

    def test_a_recommendation_naming_nothing_on_the_table_is_dropped(self) -> None:
        recommendation = parse_recommendation(_chair(recommended="Something else entirely"))

        assert recommendation.recommended == ""
        assert len(recommendation.options) == 2

    def test_it_is_bounded_rather_than_a_survey(self) -> None:
        many = [{"label": f"Option {index}", "consequence": "c"} for index in range(MAX_OPTIONS + 3)]
        recommendation = parse_recommendation(_chair(options=many))

        assert len(recommendation.options) == MAX_OPTIONS

    def test_returning_to_design_is_a_recognized_outcome(self) -> None:
        assert parse_recommendation(_chair(outcome="return_to_design")).outcome == "return_to_design"

    def test_an_unrecognized_outcome_does_not_invent_one(self) -> None:
        assert parse_recommendation(_chair(outcome="approve")).outcome == "recommendation_ready"


class TestAMeetingThatCouldNotConcludeIsStillAMeeting:
    def test_prose_keeps_the_transcript_and_says_it_could_not_be_read(self) -> None:
        recommendation = parse_recommendation("I think family holdout is better, honestly.")

        assert not recommendation.usable
        assert recommendation.refusal
        assert "family holdout" in recommendation.summary

    def test_an_empty_answer_is_reported_rather_than_raised(self) -> None:
        recommendation = parse_recommendation("")

        assert recommendation.refusal == "The meeting chair returned nothing."

    def test_a_json_array_is_refused_rather_than_coerced(self) -> None:
        assert parse_recommendation("[1, 2, 3]").refusal


class TestTheRecordNamesItsContract:
    def test_the_pinned_contract_travels_with_the_result(self) -> None:
        assert parse_recommendation(_chair()).as_dict()["contract"] == BUILD_WORK_MEETING_CONTRACT
