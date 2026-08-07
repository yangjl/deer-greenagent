"""The deck renders the chair's decision as real option cards — still inert.

The persisted file must be safe to open from anywhere: a downloaded copy, an
email attachment, a page that framed it. So every control ships disabled and the
deck says where a real answer is given. Activation is the authenticated parent's
job; nothing here may anticipate it by shipping an enabled control.
"""

from __future__ import annotations

import re

from deerflow.dbtl.council_deck import render_council_deck, render_stage_review_controls
from deerflow.dbtl.decision_request import parse_decision_request

QUESTION = "Which population structure should define validation?"

_CONSENSUS = {
    "agreements": ["The lineage is a restricted benchmark."],
    "disagreements": [{"topic": "Scope of inference", "positions": ["not decidable here", "decided upstream"], "resolution": ""}],
    "open_questions": [],
}


def _request(**overrides: object) -> object:
    payload: dict[str, object] = {
        "version": 1,
        "id": "validation-population",
        "question": QUESTION,
        "options": [
            {"id": "family_holdout", "label": "Family holdout", "value": "Use family-level holdout.", "description": "Stricter across related groups."},
            {"id": "random_split", "label": "Random split", "value": "Use a random split.", "description": "Weaker structure control."},
        ],
        "recommended_option_id": "family_holdout",
        "recommendation": "Matches the stated deployment population.",
    }
    payload.update(overrides)
    parsed = parse_decision_request(payload, question=QUESTION)
    assert parsed.request is not None
    return parsed.request


def _deck(*, decision: object | None = None, question: str = QUESTION, surface_mode: str = "") -> str:
    return render_council_deck(
        cycle_title="Genomic selection in maize",
        stage_title="Design meeting",
        round_number=2,
        results=[{"summary": "Run a matched-model benchmark.", "consensus": _CONSENSUS}],
        package_path="/mnt/user-data/outputs/dbtl/x/design/design-review-rev2-abc123.md",
        clarification_question=question,
        decision_request=decision,
        surface_mode=surface_mode,
        surface_id="dfs-chair-choice" if surface_mode else "",
    )


class TestTheOptionsRenderAsAChoice:
    def test_each_option_becomes_a_radio_in_one_group(self) -> None:
        html = _deck(decision=_request())

        assert "<fieldset" in html
        assert html.count('type="radio"') == 2
        assert html.count('name="validation-population"') == 2
        assert 'value="family_holdout"' in html
        assert 'value="random_split"' in html

    def test_the_recorded_question_is_the_legend(self) -> None:
        html = _deck(decision=_request())

        assert f"<legend>{QUESTION}</legend>" in html

    def test_labels_and_descriptions_are_shown(self) -> None:
        html = _deck(decision=_request())

        assert "Family holdout" in html
        assert "Stricter across related groups." in html

    def test_nothing_is_preselected_not_even_the_recommendation(self) -> None:
        """A default that becomes the answer is a decision nobody made."""
        html = _deck(decision=_request())

        assert re.search(r"<input\b[^>]*\bchecked(?:\s|=|>)", html) is None

    def test_the_recommendation_is_labelled_as_the_chair_s_view(self) -> None:
        html = _deck(decision=_request())

        assert '<span class="badge">Recommended</span>' in html
        assert "Matches the stated deployment population." in html

    def test_a_recommendation_is_not_invented_when_the_chair_gave_none(self) -> None:
        html = _deck(decision=_request(recommended_option_id="", recommendation=""))

        assert '<span class="badge">Recommended</span>' not in html
        assert "Why the chair leans this way" not in html


class TestThePersistedFileIsInert:
    def test_every_control_ships_disabled(self) -> None:
        html = _deck(decision=_request())

        assert html.count("disabled") >= html.count('type="radio"')
        assert "<form" not in html

    def test_it_says_where_a_real_answer_is_given(self) -> None:
        html = _deck(decision=_request())

        assert "Open this deck in DeerFlow to respond." in html

    def test_deck_navigation_yields_to_a_focused_control(self) -> None:
        """Space selects a radio; a deck that always paged would break the choice."""
        html = _deck(decision=_request())

        assert "inControl(event.target)" in html
        assert "input|textarea|select|button|a" in html
        # ...but the deck's own arrows stay usable after they are clicked.
        assert "closest('.bar')" in html

    def test_it_still_fetches_nothing(self) -> None:
        html = _deck(decision=_request())

        assert "http://" not in html
        assert "https://" not in html
        assert "<script src" not in html


class TestUntrustedTextIsEscaped:
    def test_option_text_cannot_inject_markup(self) -> None:
        html = _deck(
            decision=_request(
                options=[
                    {"id": "evil", "label": "<img src=x onerror=alert(1)>", "value": "v", "description": "<script>alert(2)</script>"},
                    {"id": "fine", "label": "Fine", "value": "f"},
                ],
                recommended_option_id="",
            )
        )

        assert "<img src=x" not in html
        assert "<script>alert(2)</script>" not in html
        assert "&lt;img src=x" in html

    def test_the_question_cannot_inject_markup(self) -> None:
        html = _deck(decision=None, question="<script>alert(3)</script>")

        assert "<script>alert(3)</script>" not in html


class TestFallbackAndOrdering:
    def test_without_options_the_question_still_appears(self) -> None:
        """A malformed decision costs the cards, never the question."""
        html = _deck(decision=None)

        assert QUESTION in html
        assert 'type="radio"' not in html

    def test_a_registered_chair_deck_collects_the_exact_question_as_text(self) -> None:
        html = render_council_deck(
            cycle_title="Genomic selection in maize",
            stage_title="Design meeting",
            round_number=2,
            results=[
                {
                    "summary": "Run a matched-model benchmark.",
                    "consensus": _CONSENSUS,
                }
            ],
            clarification_question=QUESTION,
            surface_id="dfs-free-text",
            surface_mode="chair_feedback",
        )

        assert f"<legend>{QUESTION}</legend>" in html
        assert 'data-deck-action="chair_text"' in html
        assert "data-deck-comment" in html

    def test_a_paused_chairs_decision_is_the_final_slide(self) -> None:
        html = _deck(decision=_request(), surface_mode="chair_feedback")

        titles = re.findall(r"<h2>(.*?)</h2>", html)
        assert titles[-1] == "Needs your decision"
        assert html.index(">Contested<") < html.index(">Conclusions<") < html.index(">Needs your decision<")

    def test_the_same_inputs_render_the_same_bytes(self) -> None:
        """The deck is hashed and bound to a review, so it must be stable."""
        from datetime import UTC, datetime

        stamp = datetime(2026, 7, 29, 1, 50, tzinfo=UTC)
        common = {
            "cycle_title": "Genomic selection in maize",
            "stage_title": "Design meeting",
            "round_number": 2,
            "results": [{"summary": "Run a benchmark.", "consensus": _CONSENSUS}],
            "clarification_question": QUESTION,
            "decision_request": _request(),
            "generated_at": stamp,
        }

        assert render_council_deck(**common) == render_council_deck(**common)


class TestFormalReviewControls:
    def test_a_completed_design_has_separate_submit_and_verdict_controls(self) -> None:
        rendered = render_council_deck(
            cycle_title="Drought",
            stage_title="Design meeting",
            round_number=1,
            results=[{"summary": "Use family holdout.", "consensus": _CONSENSUS}],
            surface_id="dfs-review",
            surface_mode="stage_review",
        )

        assert 'data-deck-action="submit_for_review"' in rendered
        assert 'data-deck-action="approve"' in rendered
        assert 'data-deck-action="request_changes"' in rendered
        assert 'data-deck-action="reject"' in rendered
        assert "Nothing here approves anything" in rendered

    def test_request_changes_selects_only_topics_the_chair_recorded(self) -> None:
        rendered = render_council_deck(
            cycle_title="Drought",
            stage_title="Design meeting",
            round_number=1,
            results=[{"summary": "Use family holdout.", "consensus": _CONSENSUS}],
            surface_id="dfs-review",
            surface_mode="stage_review",
        )

        assert "data-deck-issue" in rendered
        assert 'value="issue-1"' in rendered

    def test_exception_gate_is_visibly_red_flagged_and_requires_a_comment(self) -> None:
        rendered = render_stage_review_controls(
            "build",
            {
                "evidence_exception": {
                    "condition": "untrusted",
                    "content_hash": "a" * 64,
                    "scientific_effect": "invalidates_support",
                }
            },
        )

        assert 'data-deck-action="continue_with_red_flag"' in rendered
        assert "this evidence remains failed or untrusted" in rendered
        assert "cannot be reported as scientific support" in rendered
        assert "required" in rendered

    def test_learn_keeps_upstream_exception_visible_without_offering_exception_actions(self) -> None:
        rendered = render_stage_review_controls(
            "learn",
            {
                "evidence_exception": {
                    "condition": "untrusted",
                    "content_hash": "a" * 64,
                    "scientific_effect": "invalidates_support",
                }
            },
        )

        assert "this evidence remains failed or untrusted" in rendered
        assert 'data-deck-action="retry_with_guidance"' not in rendered
        assert 'data-deck-action="continue_with_red_flag"' not in rendered
