"""The design meeting's outcome rendered as a slide deck."""

from __future__ import annotations

from deerflow.dbtl.council_deck import render_council_deck

_CONSENSUS = {
    "agreements": ["The lineage is a restricted benchmark."],
    "disagreements": [
        {
            "topic": "Scope of inference",
            "positions": ["not decidable here", "already decided upstream"],
            "resolution": "",
        },
        {
            "topic": "Marker count",
            "positions": ["10 is enough", "10 is too few"],
            "resolution": "Use 10 for the benchmark only.",
        },
    ],
    "open_questions": ["Which germplasm?"],
}


def _deck(**overrides: object) -> str:
    result = {
        "summary": "Run a matched-model benchmark before committing.",
        "limitations": ["Generation rules remain unfrozen."],
        "recommended_next_actions": ["Freeze the generation rules."],
        "consensus": _CONSENSUS,
        **overrides,
    }
    return render_council_deck(
        cycle_title="Genomic selection in maize",
        stage_title="Design meeting",
        round_number=2,
        results=[result],
        package_path="/mnt/user-data/outputs/dbtl/x/design/design-review-rev2-abc123.md",
        clarification_question=str(overrides.pop("clarification_question", "") or ""),
    )


def test_the_deck_is_one_self_contained_file() -> None:
    html = _deck()

    assert html.startswith("<!doctype html>")
    # A deck that fetches anything renders as unstyled text on the machine it is
    # presented from, which is the one moment it has to work.
    assert "http://" not in html
    assert "https://" not in html
    assert "<script src" not in html
    assert "<link" not in html
    # The CSS/JS braces survived templating rather than being eaten by format().
    assert "{{" not in html and "}}" not in html


def test_where_the_meeting_split_comes_before_the_synthesis() -> None:
    html = _deck()

    assert html.index(">Contested<") < html.index(">Where this lands<")
    assert html.index(">Agreed<") < html.index(">Contested<")


def test_an_unresolved_disagreement_says_so_rather_than_disappearing() -> None:
    html = _deck()

    assert "Scope of inference" in html
    assert "Not resolved" in html
    assert "Use 10 for the benchmark only." in html


def test_the_decision_slide_leads_with_the_chairs_own_question() -> None:
    html = render_council_deck(
        cycle_title="Genomic selection in maize",
        stage_title="Design meeting",
        round_number=1,
        results=[{"summary": "Paused.", "consensus": _CONSENSUS}],
        clarification_question="Toy benchmark or credible simulator?",
    )

    decisions = html.split('slide--decide"')[1].split("</section>")[0]
    assert decisions.index("Toy benchmark or credible simulator?") < decisions.index("Which germplasm?")
    # An unsettled disagreement is a decision someone has to make, so it belongs
    # on the slide that asks for decisions.
    assert "Scope of inference" in decisions


def test_no_outstanding_decision_means_no_decision_slide() -> None:
    html = render_council_deck(
        cycle_title="Settled",
        stage_title="Design meeting",
        round_number=1,
        results=[
            {
                "summary": "Everything settled.",
                "consensus": {
                    "agreements": ["Use the held-out site."],
                    "disagreements": [
                        {
                            "topic": "Marker count",
                            "positions": ["10", "20"],
                            "resolution": "20.",
                        }
                    ],
                    "open_questions": [],
                },
            }
        ],
    )

    assert "Needs your decision" not in html


def test_total_accord_is_flagged_rather_than_shown_as_a_clean_result() -> None:
    html = render_council_deck(
        cycle_title="Quiet meeting",
        stage_title="Design meeting",
        round_number=1,
        results=[{"summary": "Agreed.", "consensus": {"agreements": ["Do it."], "disagreements": [], "open_questions": []}}],
    )

    assert "no argument was written down" in html


def test_a_chair_with_no_structured_consensus_is_not_given_one() -> None:
    html = render_council_deck(
        cycle_title="Prose only",
        stage_title="Design meeting",
        round_number=1,
        results=[{"summary": "A paragraph and nothing structured."}],
    )

    assert "no structured consensus" in html
    assert "A paragraph and nothing structured." in html


def test_content_is_escaped_rather_than_injected() -> None:
    html = render_council_deck(
        cycle_title="<script>alert(1)</script>",
        stage_title="Design meeting",
        round_number=1,
        results=[{"summary": "ok", "consensus": {"agreements": ["<img onerror=x>"], "disagreements": [], "open_questions": []}}],
    )

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img onerror=x>" not in html


def test_the_deck_never_claims_to_be_the_approvable_document() -> None:
    html = _deck()

    assert "Nothing here approves anything" in html
    assert "design-review-rev2-abc123.md" in html
