"""The structured decision a paused chair asks for.

These tests pin the two properties the feedback deck depends on. First, that a
malformed decision request costs the option cards and never the question — the
question is what the chair actually asked, and losing it to a parse failure
would strand the meeting. Second, that the rendered question is the recorded
``clarification_question`` rather than whatever the payload repeated beside it,
so the card and the audit record cannot describe different questions.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.decision_request import (
    MAX_OPTIONS,
    MIN_OPTIONS,
    DecisionOption,
    DecisionRequest,
    parse_decision_request,
)
from deerflow.dbtl.worker_result import StageWorkerResult, WorkerStatus, parse_worker_result

QUESTION = "Which population structure should define validation?"


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "id": "validation-population",
        "question": QUESTION,
        "options": [
            {
                "id": "family_holdout",
                "label": "Family holdout",
                "value": "Use family-level holdout.",
                "description": "Stricter generalization test across related groups.",
            },
            {
                "id": "random_split",
                "label": "Random split",
                "value": "Use a random split.",
                "description": "More observations per fold but weaker structure control.",
            },
        ],
        "recommended_option_id": "family_holdout",
        "recommendation": "Matches the stated deployment population.",
    }
    payload.update(overrides)
    return payload


class TestAWellFormedRequest:
    def test_parses_into_ordered_options(self) -> None:
        parsed = parse_decision_request(_payload(), question=QUESTION)

        assert parsed.refusal == ""
        request = parsed.request
        assert request is not None
        assert [option.id for option in request.options] == ["family_holdout", "random_split"]
        assert request.options[0].label == "Family holdout"
        assert request.recommended_option_id == "family_holdout"

    def test_renders_as_cards(self) -> None:
        request = parse_decision_request(_payload(), question=QUESTION).request

        assert request is not None
        assert request.renders_as_cards is True

    def test_a_recommendation_is_optional(self) -> None:
        parsed = parse_decision_request(
            _payload(recommended_option_id="", recommendation=""),
            question=QUESTION,
        )

        assert parsed.request is not None
        assert parsed.request.recommended_option_id == ""

    def test_round_trips_through_as_dict(self) -> None:
        request = parse_decision_request(_payload(), question=QUESTION).request
        assert request is not None

        restored = parse_decision_request(request.as_dict(), question=QUESTION).request

        assert restored == request


class TestTheQuestionIsAuthoritative:
    def test_the_recorded_question_wins_over_the_payload(self) -> None:
        """The chair asked one question. Two copies must not drift apart."""
        parsed = parse_decision_request(
            _payload(question="something the chair did not ask"),
            question=QUESTION,
        )

        assert parsed.request is not None
        assert parsed.request.question == QUESTION

    def test_a_missing_payload_question_is_not_a_refusal(self) -> None:
        payload = _payload()
        del payload["question"]

        parsed = parse_decision_request(payload, question=QUESTION)

        assert parsed.request is not None
        assert parsed.request.question == QUESTION

    def test_no_recorded_question_means_no_request(self) -> None:
        parsed = parse_decision_request(_payload(), question="   ")

        assert parsed.request is None
        assert "question" in parsed.refusal


class TestRefusalIsExplicitAndNeverSilent:
    @pytest.mark.parametrize(
        ("payload", "expected_in_reason"),
        [
            (_payload(options="not a list"), "list"),
            (_payload(options=[]), "options"),
            (_payload(options=[{"id": "only", "label": "Only", "value": "One"}]), "options"),
            (
                _payload(
                    options=[
                        {"id": "same", "label": "A", "value": "a"},
                        {"id": "same", "label": "B", "value": "b"},
                    ]
                ),
                "unique",
            ),
            (_payload(recommended_option_id="not_an_option"), "recommended"),
            (_payload(id="not a slug"), "identifier"),
            (
                _payload(
                    options=[
                        {"id": "has space", "label": "A", "value": "a"},
                        {"id": "fine", "label": "B", "value": "b"},
                    ]
                ),
                "identifier",
            ),
            (
                _payload(
                    options=[
                        {"id": "unlabelled", "label": "", "value": "a"},
                        {"id": "fine", "label": "B", "value": "b"},
                    ]
                ),
                "label",
            ),
            (
                _payload(
                    options=[
                        {"id": "valueless", "label": "A", "value": ""},
                        {"id": "fine", "label": "B", "value": "b"},
                    ]
                ),
                "value",
            ),
        ],
    )
    def test_a_malformed_request_is_refused_with_a_reason(self, payload: object, expected_in_reason: str) -> None:
        parsed = parse_decision_request(payload, question=QUESTION)

        assert parsed.request is None
        assert parsed.refusal, "a refusal must say why; a silent drop is indistinguishable from a chair that asked nothing"
        assert expected_in_reason in parsed.refusal.lower()

    def test_a_non_mapping_is_refused_rather_than_coerced(self) -> None:
        parsed = parse_decision_request(["family_holdout", "random_split"], question=QUESTION)

        assert parsed.request is None
        assert parsed.refusal

    def test_absent_is_not_a_refusal(self) -> None:
        """A chair that asked a plain question did nothing wrong."""
        parsed = parse_decision_request(None, question=QUESTION)

        assert parsed.request is None
        assert parsed.refusal == ""

    def test_more_options_than_card_mode_allows_is_refused(self) -> None:
        options = [{"id": f"option_{index}", "label": f"Option {index}", "value": str(index)} for index in range(MAX_OPTIONS + 1)]

        parsed = parse_decision_request(_payload(options=options, recommended_option_id=""), question=QUESTION)

        assert parsed.request is None
        assert "options" in parsed.refusal.lower()

    def test_the_boundaries_themselves_are_accepted(self) -> None:
        for count in (MIN_OPTIONS, MAX_OPTIONS):
            options = [{"id": f"option_{index}", "label": f"Option {index}", "value": str(index)} for index in range(count)]

            parsed = parse_decision_request(_payload(options=options, recommended_option_id=""), question=QUESTION)

            assert parsed.request is not None, f"{count} options must be accepted"


class TestBoundsAreEnforced:
    def test_long_text_is_truncated_rather_than_refused(self) -> None:
        parsed = parse_decision_request(
            _payload(
                options=[
                    {"id": "long", "label": "L" * 5_000, "value": "V" * 5_000, "description": "D" * 5_000},
                    {"id": "fine", "label": "B", "value": "b"},
                ],
                recommended_option_id="",
                recommendation="R" * 5_000,
            ),
            question=QUESTION,
        )

        request = parsed.request
        assert request is not None
        assert len(request.options[0].label) < 5_000
        assert len(request.options[0].value) < 5_000
        assert len(request.options[0].description) < 5_000
        assert len(request.recommendation) < 5_000

    def test_an_over_long_option_id_is_refused_rather_than_truncated(self) -> None:
        """Truncating an id would silently merge two distinct options."""
        parsed = parse_decision_request(
            _payload(
                options=[
                    {"id": "x" * 500, "label": "A", "value": "a"},
                    {"id": "fine", "label": "B", "value": "b"},
                ],
                recommended_option_id="",
            ),
            question=QUESTION,
        )

        assert parsed.request is None
        assert "identifier" in parsed.refusal.lower()


class TestTheWorkerResultCarriesIt:
    def _needs_input_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": "needs_input",
            "summary": "The council could not settle the validation population.",
            "clarification_question": QUESTION,
            "decision_request": _payload(),
        }
        payload.update(overrides)
        return payload

    def test_a_needs_input_result_keeps_the_structured_decision(self) -> None:
        result = parse_worker_result(self._needs_input_payload(), capability="design_council_chair", agent_name="general-purpose")

        assert result.decision_request is not None
        assert result.decision_request.question == QUESTION
        assert result.as_dict()["decision_request"]["options"][0]["id"] == "family_holdout"

    def test_a_malformed_decision_costs_the_cards_not_the_question(self) -> None:
        result = parse_worker_result(
            self._needs_input_payload(decision_request={"options": []}),
            capability="design_council_chair",
            agent_name="general-purpose",
        )

        assert result.decision_request is None
        assert result.clarification_question == QUESTION
        assert result.status is WorkerStatus.NEEDS_INPUT

    def test_a_completed_result_cannot_carry_a_decision_request(self) -> None:
        """Only a paused chair is asking. A completed one is reporting."""
        result = parse_worker_result(
            {"status": "completed", "summary": "Design recorded.", "decision_request": _payload()},
            capability="design_council_chair",
            agent_name="general-purpose",
        )

        assert result.decision_request is None

    def test_as_dict_omits_an_absent_decision_rather_than_serializing_null(self) -> None:
        result = parse_worker_result(
            {"status": "completed", "summary": "Design recorded."},
            capability="design_council_chair",
            agent_name="general-purpose",
        )

        assert "decision_request" not in result.as_dict()

    def test_the_dataclass_refuses_a_decision_without_a_question(self) -> None:
        request = DecisionRequest(
            id="validation-population",
            question=QUESTION,
            options=(
                DecisionOption(id="a", label="A", value="a"),
                DecisionOption(id="b", label="B", value="b"),
            ),
        )

        with pytest.raises(ValueError):
            StageWorkerResult(
                status=WorkerStatus.COMPLETED,
                summary="Design recorded.",
                capability="design_council_chair",
                agent_name="general-purpose",
                decision_request=request,
            )
