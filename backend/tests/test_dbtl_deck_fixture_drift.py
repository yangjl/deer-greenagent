"""The browser suite's deck fixture must be the deck this renderer produces.

Playwright cannot call the Python renderer, so the deck it drives is committed
as a fixture. A fixture that silently drifts from its renderer is worse than no
fixture at all: the browser suite would keep passing against a deck the product
stopped producing, and every guarantee it reports would be about a file nobody
ships.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _design_deck_fixture import FIXTURE_PATH, render_fixture_deck  # noqa: E402

REGENERATE = "cd backend && PYTHONPATH=.:tests uv run python -c 'import _design_deck_fixture as f; f.FIXTURE_PATH.write_text(f.render_fixture_deck())'"


def test_the_committed_fixture_matches_the_current_renderer() -> None:
    assert FIXTURE_PATH.exists(), f"The browser suite's deck fixture is missing. Regenerate it:\n    {REGENERATE}"

    assert FIXTURE_PATH.read_text() == render_fixture_deck(), (
        f"The deck renderer changed but frontend/tests/e2e/fixtures/design-deck.html did not.\nRegenerate it so the browser suite drives the deck this renderer actually produces:\n    {REGENERATE}"
    )


@pytest.mark.parametrize(
    "marker",
    [
        "deerflow-design-deck",
        "dfs-e2e-surface-001",
        "data-deck-submit",
        "data-deck-status",
        'type="radio"',
    ],
)
def test_the_fixture_carries_what_the_browser_suite_drives(marker: str) -> None:
    """Named explicitly so removing one breaks here, not mysteriously in Playwright."""
    assert marker in FIXTURE_PATH.read_text()
