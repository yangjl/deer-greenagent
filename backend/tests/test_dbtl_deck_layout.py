"""The redesigned Design deck: sections, pagination, notes, and gate placement.

The deck is a renderer with no sentence of its own, so every rule here is about
*arrangement* — what appears, in what order, on how many screens — and about the
one thing the redesign adds that can reach a durable record: a note box on each
content slide, whose text is folded into the comment of whatever decision the
reviewer actually takes.
"""

from __future__ import annotations

import re

from deerflow.dbtl.consensus import MAX_CONSENSUS_ITEMS
from deerflow.dbtl.council_deck import (
    BULLETS_PER_SLIDE,
    MAX_SLIDES_PER_SECTION,
    render_council_deck,
)

CYCLE = {
    "research_question": "Can plant height and leaf count predict grain yield well enough to pre-screen genotypes?",
    "objective": "Build a pre-harvest screen that ranks genotypes without waiting for grain.",
    "success_criteria": ["Held-out genotype correlation above 0.6", "No leakage across blocks"],
}


def _chair(**overrides: object) -> dict:
    result = {
        "role": "chair",
        "summary": "Para one.\nPara two.\nPara three.\nPara four.",
        "limitations": ["Single season", "Two sites only"],
        "recommended_next_actions": ["Fit the baseline model"],
        "consensus": {
            "agreements": ["Group the split by genotype", "Report held-out performance"],
            "disagreements": [
                {"topic": "What counts as success", "positions": ["Design says tie it to held-out performance", "Red team says no threshold is defined"], "resolved": False},
                {"topic": "Whether days_to_flower belongs in the model", "positions": ["Design left it open", "Red team says exclude it"], "resolved": False},
            ],
            "open_questions": [],
        },
    }
    result.update(overrides)
    return result


def _deck(**kwargs: object) -> str:
    params = {
        "cycle_title": "Pre-harvest screen",
        "stage_title": "Design meeting",
        "round_number": 1,
        "results": [_chair()],
        "surface_mode": "stage_review",
        "surface_id": "dfs-layout-001",
        "stage": "design",
        "transition_gate": {"assessment": {"difficulty": "standard", "rationale": "Ordinary."}, "routes": []},
        **CYCLE,
    }
    params.update(kwargs)
    return render_council_deck(**params)  # type: ignore[arg-type]


def _titles(deck: str) -> list[str]:
    return re.findall(r"<h2>(.*?)</h2>", deck)


class TestTheDeckOpensWithTheScience:
    def test_background_quotes_the_cycles_own_question(self) -> None:
        deck = _deck()
        assert "Background" in _titles(deck)
        assert "pre-screen genotypes" in deck, "the background slide must quote the cycle's research question"

    def test_objectives_carry_the_objective_and_its_success_criteria(self) -> None:
        deck = _deck()
        assert "Objectives" in _titles(deck)
        assert "ranks genotypes without waiting" in deck
        assert "Held-out genotype correlation above 0.6" in deck

    def test_a_cycle_with_no_recorded_question_simply_omits_those_slides(self) -> None:
        # Absence is meaningful and must not become an empty ceremonial slide.
        deck = _deck(research_question="", objective="", success_criteria=())
        titles = _titles(deck)
        assert "Background" not in titles
        assert "Objectives" not in titles

    def test_a_success_criterion_given_as_one_string_is_not_split_into_letters(self) -> None:
        deck = _deck(success_criteria="Held-out correlation above 0.6")
        assert "<li>H</li>" not in deck, "a str is a Sequence[str]; iterating one yields characters"
        assert "Held-out correlation above 0.6" in deck


class TestSectionsRunOntoAsManyScreensAsTheyNeed:
    def test_a_long_agreement_list_paginates_instead_of_being_cut(self) -> None:
        agreements = [f"Agreement number {index}" for index in range(BULLETS_PER_SLIDE * 2)]
        deck = _deck(results=[_chair(consensus={"agreements": agreements, "disagreements": [], "open_questions": []})])
        assert _titles(deck).count("Agreed") == 1
        assert "Agreed (cont.)" in _titles(deck)
        assert f"Agreement number {BULLETS_PER_SLIDE * 2 - 1}" in deck, "the tail must survive onto a later slide"

    def test_contested_topics_get_their_own_screens(self) -> None:
        deck = _deck()
        # Two disagreements at CARDS_PER_SLIDE=2 stay on one screen; the point
        # is that both appear, and the layout no longer stacks them tightly.
        assert _titles(deck).count("Contested") == 1
        assert "What counts as success" in deck
        assert "days_to_flower" in deck

    def test_a_bounded_section_says_what_it_dropped(self) -> None:
        # Limitations come straight off the chair result with no upstream cap,
        # so this is where the deck's own bound actually bites.
        overflow = BULLETS_PER_SLIDE * MAX_SLIDES_PER_SECTION + 3
        deck = _deck(results=[_chair(limitations=[f"Limitation {index}" for index in range(overflow)])])
        assert "3 further entries in the full review package." in deck, "silent truncation reads as 'that was all of it'"

    def test_consensus_sections_are_already_bounded_upstream(self) -> None:
        # parse_consensus caps agreements at 12, below the deck's own display
        # bound, so the deck can never be the thing that drops one. Pinned
        # because raising the parser's cap without raising the deck's would
        # start losing agreements silently.
        agreements = [f"Agreement {index}" for index in range(40)]
        deck = _deck(results=[_chair(consensus={"agreements": agreements, "disagreements": [], "open_questions": []})])
        assert MAX_CONSENSUS_ITEMS <= BULLETS_PER_SLIDE * MAX_SLIDES_PER_SECTION
        assert f"Agreement {MAX_CONSENSUS_ITEMS - 1}" in deck
        assert "further entries in the full review package" not in deck

    def test_the_synthesis_is_titled_conclusions_and_paginates(self) -> None:
        deck = _deck()
        assert "Conclusions" in _titles(deck)
        assert "Para one." in deck and "Para four." in deck


class TestEveryContentSlideTakesANote:
    def test_each_section_carries_its_own_labelled_box(self) -> None:
        deck = _deck()
        labels = set(re.findall(r'data-note-label="([^"]+)"', deck))
        assert {"Background", "Objectives", "Agreed", "Contested", "Conclusions", "Limitations", "Next steps"} <= labels

    def test_every_note_box_ships_disabled(self) -> None:
        deck = _deck()
        boxes = re.findall(r"<textarea[^>]*data-deck-note[^>]*>", deck)
        assert boxes, "the redesign is meant to add note boxes"
        for box in boxes:
            assert "disabled" in box, "a persisted deck must be inert wherever it is opened"

    def test_the_gate_slide_has_no_second_box_of_its_own(self) -> None:
        # It already owns the comment field the notes are folded into. Scope to
        # the rendered section: the bridge script names these attributes as CSS
        # selectors, so a whole-document search matches its own plumbing.
        deck = _deck()
        gate = deck.split('<section class="slide slide--review"')[-1].split("</section>")[0]
        assert "data-deck-note" not in gate
        assert "data-deck-comment" in gate, "the gate keeps the single comment the notes fold into"

    def test_a_paginated_section_gives_each_screen_a_distinct_box(self) -> None:
        agreements = [f"Agreement {index}" for index in range(BULLETS_PER_SLIDE * 2)]
        deck = _deck(results=[_chair(consensus={"agreements": agreements, "disagreements": [], "open_questions": []})])
        ids = re.findall(r'id="note-(agreed[^"]*)"', deck)
        assert len(ids) == len(set(ids)) == 2, "duplicate ids would make one box unreachable by its label"


class TestTheApprovalIsTheLastThingYouReach:
    def test_the_human_gate_is_the_final_slide(self) -> None:
        deck = _deck()
        assert _titles(deck)[-1] == "Review the Design"

    def test_next_comes_before_the_gate(self) -> None:
        titles = _titles(deck := _deck())
        assert titles.index("Next") < titles.index("Review the Design")
        assert "Full review package" not in deck or titles[-1] == "Review the Design"

    def test_a_read_only_deck_has_no_gate_at_all(self) -> None:
        deck = _deck(surface_mode="read_only")
        assert "Review the Design" not in _titles(deck)
        # The bridge references the selector regardless; what must be absent is
        # a control rendered into the document.
        assert '<button type="button" data-deck-gate-submit' not in deck
        assert '<fieldset class="review"' not in deck

    def test_the_gate_tells_the_reviewer_their_notes_travel_with_it(self) -> None:
        deck = _deck()
        assert "notes from the earlier slides are sent with this decision" in deck


class TestNotesReachTheRecordThroughTheDecision:
    def test_the_bridge_folds_labelled_notes_into_the_comment(self) -> None:
        deck = _deck()
        assert "data-deck-note" in deck
        assert "function withNotes" in deck
        assert "withNotes(comment ? comment.value.trim() : '')" in deck, "the gate must record what the reviewer actually wrote"

    def test_activation_reaches_the_note_boxes(self) -> None:
        deck = _deck()
        assert "notes.forEach(function (note) { note.disabled = !on; });" in deck, "an enabled fieldset does not clear a control's own disabled attribute"

    def test_a_restored_comment_clears_the_boxes_so_notes_are_not_folded_twice(self) -> None:
        deck = _deck()
        assert "clearNotes()" in deck
        assert "if (data.comment) { clearNotes(); }" in deck


class TestEveryOpenQuestionCanBeAnswered:
    """A slide headed "Needs your decision" must offer every decision."""

    def _decide_slide(self, deck: str) -> str:
        return deck.split('<section class="slide slide--decide"')[-1].split("</section>")[0]

    def test_each_open_question_gets_its_own_box(self) -> None:
        deck = _deck(
            results=[
                _chair(
                    consensus={
                        "agreements": ["Group by genotype"],
                        "disagreements": [
                            {"topic": "Prediction timing versus measurement stage", "positions": ["a", "b"], "resolved": False},
                            {"topic": "Validation metric specificity", "positions": ["a", "b"], "resolved": False},
                        ],
                        "open_questions": ["What threshold counts as good enough?"],
                    }
                )
            ],
        )
        decide = self._decide_slide(deck)
        boxes = re.findall(r"<textarea[^>]*data-deck-note[^>]*>", decide)
        assert len(boxes) == 3, "one box per open question, not one for the slide"
        for box in boxes:
            assert "disabled" in box

    def test_a_box_is_labelled_with_its_own_question(self) -> None:
        deck = _deck(
            results=[_chair(consensus={"agreements": [], "disagreements": [], "open_questions": ["What threshold counts as good enough?"]})],
        )
        decide = self._decide_slide(deck)
        # The label is what the folded comment files the answer under, so it
        # must be the question rather than the slide or a position index.
        assert 'data-note-label="What threshold counts as good enough?"' in decide

    def test_open_questions_are_no_longer_inert_bullets(self) -> None:
        deck = _deck(
            results=[_chair(consensus={"agreements": [], "disagreements": [], "open_questions": ["Which metric?"]})],
        )
        decide = self._decide_slide(deck)
        assert "Which metric?" in decide
        assert "<li>Which metric?</li>" not in decide, "an unanswerable bullet is what this replaced"
