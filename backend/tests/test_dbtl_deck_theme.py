"""An operator may restyle the meeting deck; nothing may restructure it.

The deck's bytes are hash-registered as the actionable gate surface, so a theme
is allowed to change appearance and nothing else. These tests pin the two halves
of that: the parser refuses anything that could introduce markup or script, and
the loader treats every failure as "render it unthemed" rather than as a reason
the meeting's already-committed results cannot be presented.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl import stage_execution
from deerflow.dbtl.council_deck import (
    DECK_THEME_MAX_CHARS,
    parse_deck_theme,
    render_council_deck,
)

THEME = ":root { --accent: #8a3324; }"

_CONSENSUS = {
    "agreements": ["Use a family-wise holdout."],
    "disagreements": [{"topic": "Strength of inference", "positions": ["a", "b"]}],
    "open_questions": [],
}


def _deck(**overrides) -> str:
    params = {
        "cycle_title": "Genomic selection in maize",
        "stage_title": "Design meeting",
        "round_number": 1,
        "results": [{"summary": "Run the pilot.", "consensus": _CONSENSUS}],
    }
    params.update(overrides)
    return render_council_deck(**params)


class TestWhatCountsAsATheme:
    def test_plain_css_is_accepted(self) -> None:
        parsed = parse_deck_theme(THEME)

        assert parsed.accepted
        assert parsed.css == THEME
        assert parsed.refusal == ""

    @pytest.mark.parametrize("absent", [None, "", "   ", "\n"])
    def test_no_theme_is_not_a_refusal(self, absent) -> None:
        """Absent and malformed are different, and only one deserves a warning."""
        parsed = parse_deck_theme(absent)

        assert not parsed.accepted
        assert parsed.refusal == ""

    @pytest.mark.parametrize(
        "hostile",
        [
            "body {} </style><script>fetch('//x')</script><style>",
            "body {} </STYLE >",
            "<script src='//x'></script>",
            "body { background: url(javascript:alert(1)); }",
            "body {} <!-- comment -->",
        ],
    )
    def test_markup_bearing_css_is_refused_not_escaped(self, hostile: str) -> None:
        """Inside a <style> element there is no escaping — only refusal.

        A closing tag ends the stylesheet and everything after it becomes
        document content, in a file whose exact bytes are registered as the
        surface a person answers the gate through.
        """
        parsed = parse_deck_theme(hostile)

        assert not parsed.accepted
        assert parsed.refusal

    def test_an_oversized_theme_is_refused(self) -> None:
        parsed = parse_deck_theme("a" * (DECK_THEME_MAX_CHARS + 1))

        assert not parsed.accepted
        assert str(DECK_THEME_MAX_CHARS) in parsed.refusal

    def test_a_non_string_theme_is_refused_rather_than_coerced(self) -> None:
        parsed = parse_deck_theme({"css": THEME})

        assert not parsed.accepted
        assert parsed.refusal


class TestWhatTheRendererDoesWithIt:
    def test_the_theme_lands_after_the_built_in_stylesheet(self) -> None:
        """Cascade order is the override mechanism, so position is the contract."""
        html = _deck(theme_css=THEME)

        assert THEME in html
        assert html.index("</style>") < html.index(THEME)
        assert html.index(THEME) < html.index("</head>")

    def test_an_unthemed_deck_is_byte_identical_to_one_from_before_themes(self) -> None:
        """A re-render must not change the hash merely because themes exist."""
        from datetime import UTC, datetime

        stamp = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)

        assert _deck(generated_at=stamp) == _deck(generated_at=stamp, theme_css="")
        assert "data-deck-theme" not in _deck(generated_at=stamp)

    def test_a_refused_theme_still_produces_a_deck(self) -> None:
        html = _deck(theme_css="body {} </style><script>x()</script>")

        assert "data-deck-theme" not in html
        assert "<script>x()" not in html
        assert "Genomic selection in maize" in html

    def test_the_same_theme_renders_the_same_bytes(self) -> None:
        from datetime import UTC, datetime

        stamp = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)

        assert _deck(theme_css=THEME, generated_at=stamp) == _deck(theme_css=THEME, generated_at=stamp)


def _skill_storage(monkeypatch, skills):
    monkeypatch.setattr(
        stage_execution,
        "get_or_new_user_skill_storage",
        lambda user_id: SimpleNamespace(load_skills=lambda *, enabled_only=False: skills),
    )


def _skill(tmp_path: Path, name: str, *, css: str | None = THEME) -> SimpleNamespace:
    skill_dir = tmp_path / name
    if css is not None:
        (skill_dir / "assets").mkdir(parents=True)
        (skill_dir / "assets" / "deck-theme.css").write_text(css, encoding="utf-8")
    else:
        skill_dir.mkdir(parents=True)
    return SimpleNamespace(name=name, skill_dir=skill_dir)


class TestLoadingItFromASkill:
    def test_an_enabled_skill_supplies_its_stylesheet(self, tmp_path: Path, monkeypatch) -> None:
        _skill_storage(monkeypatch, [_skill(tmp_path, "deck-theme")])

        assert stage_execution._load_deck_theme("deck-theme", user_id="u1") == THEME

    def test_no_configured_skill_means_no_theme(self, tmp_path: Path, monkeypatch) -> None:
        _skill_storage(monkeypatch, [_skill(tmp_path, "deck-theme")])

        assert stage_execution._load_deck_theme("", user_id="u1") == ""

    def test_a_skill_that_is_not_enabled_does_not_theme_decks(self, tmp_path: Path, monkeypatch) -> None:
        """Disabling the skill is how an operator turns the theme off."""
        _skill_storage(monkeypatch, [_skill(tmp_path, "something-else")])

        assert stage_execution._load_deck_theme("deck-theme", user_id="u1") == ""

    def test_a_skill_with_no_asset_renders_unthemed(self, tmp_path: Path, monkeypatch) -> None:
        _skill_storage(monkeypatch, [_skill(tmp_path, "deck-theme", css=None)])

        assert stage_execution._load_deck_theme("deck-theme", user_id="u1") == ""

    def test_a_refused_asset_renders_unthemed(self, tmp_path: Path, monkeypatch) -> None:
        _skill_storage(monkeypatch, [_skill(tmp_path, "deck-theme", css="x {} </style><script>y()</script>")])

        assert stage_execution._load_deck_theme("deck-theme", user_id="u1") == ""

    def test_a_broken_registry_costs_styling_not_the_deck(self, monkeypatch) -> None:
        def explode(user_id):
            raise RuntimeError("skills unavailable")

        monkeypatch.setattr(stage_execution, "get_or_new_user_skill_storage", explode)

        assert stage_execution._load_deck_theme("deck-theme", user_id="u1") == ""

    def test_a_symlinked_asset_cannot_read_outside_the_skill(self, tmp_path: Path, monkeypatch) -> None:
        """'Read this skill's stylesheet' must not become an arbitrary file read."""
        secret = tmp_path / "secret.css"
        secret.write_text("/* not yours */", encoding="utf-8")
        skill_dir = tmp_path / "deck-theme"
        (skill_dir / "assets").mkdir(parents=True)
        (skill_dir / "assets" / "deck-theme.css").symlink_to(secret)
        _skill_storage(monkeypatch, [SimpleNamespace(name="deck-theme", skill_dir=skill_dir)])

        assert stage_execution._load_deck_theme("deck-theme", user_id="u1") == ""


class TestTheShippedExample:
    def test_the_bundled_theme_skill_is_loadable_and_accepted(self) -> None:
        """The worked example must actually work, or it is documentation debt."""
        repo = Path(__file__).resolve().parents[2]
        css = (repo / "skills" / "public" / "dbtl-deck-theme" / "assets" / "deck-theme.css").read_text(encoding="utf-8")

        parsed = parse_deck_theme(css)

        assert parsed.accepted, parsed.refusal
        assert "--accent" in parsed.css
