"""The meeting deck has one built-in editorial presentation style."""

from __future__ import annotations

from datetime import UTC, datetime

from deerflow.config.dbtl_config import DbtlConfig
from deerflow.dbtl.council_deck import render_council_deck

_CONSENSUS = {
    "agreements": ["Use a family-wise holdout."],
    "disagreements": [{"topic": "Strength of inference", "positions": ["a", "b"]}],
    "open_questions": [],
}


def _deck() -> str:
    return render_council_deck(
        cycle_title="Genomic selection in maize",
        stage_title="Design meeting",
        round_number=1,
        results=[{"summary": "Run the pilot.", "consensus": _CONSENSUS}],
        generated_at=datetime(2026, 7, 30, 9, 0, tzinfo=UTC),
    )


def test_editorial_style_is_built_into_every_deck() -> None:
    html = _deck()

    assert "--bg: #fbf9f4" in html
    assert "--accent: #8a3324" in html
    assert 'ui-serif, Georgia, "Iowan Old Style"' in html
    assert ".slide--title h2" in html
    assert "font-style: italic" in html
    assert "border-left: 3px solid var(--accent)" in html


def test_deck_style_has_no_runtime_theme_branch() -> None:
    html = _deck()

    assert "data-deck-theme" not in html
    assert "council_deck_theme_skill" not in DbtlConfig.model_fields


def test_canonical_style_renders_deterministic_bytes() -> None:
    assert _deck() == _deck()
