"""The one definition of the deck fixture the browser suite drives.

The Playwright spec needs a real rendered deck, and Playwright cannot call the
Python renderer. So the deck is committed as a fixture — and a fixture that can
drift from its renderer is worse than no fixture, because the browser suite
would keep passing against a deck the product no longer produces.

Inputs live here, both the backend drift test and the regeneration command
import them, and the drift test compares bytes. When the renderer changes on
purpose, regenerate:

    cd backend && PYTHONPATH=. uv run python -m tests._design_deck_fixture
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from deerflow.dbtl.council_deck import render_council_deck
from deerflow.dbtl.decision_request import parse_decision_request

#: Repo-relative so the drift test and the command agree regardless of cwd.
FIXTURE_PATH = Path(__file__).resolve().parents[2] / "frontend" / "tests" / "e2e" / "fixtures" / "design-deck.html"

SURFACE_ID = "dfs-e2e-surface-001"
QUESTION = "Should the RIL family be produced by selfing to F5/F6, or by doubled-haploid production?"


def render_fixture_deck() -> str:
    """A deterministic deck: fixed timestamp, fixed surface, two real options."""
    decision = parse_decision_request(
        {
            "version": 1,
            "id": "inbreeding-route",
            "question": QUESTION,
            "options": [
                {
                    "id": "selfing_f5_f6",
                    "label": "Selfing to F5/F6",
                    "value": "Produce the RIL family by single-seed descent to F5/F6.",
                    "description": "Residual heterozygosity around 3-6%; the segregation checks stay as written.",
                },
                {
                    "id": "doubled_haploid",
                    "label": "Doubled-haploid production",
                    "value": "Produce the family by doubled-haploid production.",
                    "description": "Fully homozygous lines, but induction rate biases which families survive.",
                },
            ],
            "recommended_option_id": "selfing_f5_f6",
            "recommendation": "The owner said 'biparental' without an inbreeding route, and selfing keeps the stated map-length check valid.",
        },
        question=QUESTION,
    ).request

    return render_council_deck(
        cycle_title="Simulate a maize breeding population",
        stage_title="Design meeting",
        round_number=1,
        results=[
            {
                "summary": "Simulate the biparental family under an additive-plus-dominance model, then validate against the stated criteria.",
                "limitations": ["Generation rules stay unfrozen until the route is settled."],
                "recommended_next_actions": ["Freeze the generation rules once the route is chosen."],
                "consensus": {
                    "agreements": ["The family is biparental and the trait is flowering time."],
                    "disagreements": [
                        {
                            "topic": "Which base the acceptance checks are evaluated against",
                            "positions": ["Against the founder haplotypes", "Against the realized F2 sample"],
                            "resolution": "",
                        }
                    ],
                    "open_questions": ["How many seasons and replicates?"],
                },
            }
        ],
        package_path="/mnt/user-data/outputs/dbtl/x/design/design-review-rev1-fb18c0.md",
        clarification_question=QUESTION,
        decision_request=decision,
        surface_id=SURFACE_ID,
        generated_at=datetime(2026, 7, 29, 1, 50, tzinfo=UTC),
    )


if __name__ == "__main__":
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(render_fixture_deck())
    print(f"wrote {FIXTURE_PATH}")
