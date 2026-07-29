"""The deck's half of the sandboxed bridge.

The persisted file is opened from places nobody controls: a download, an email
attachment, a page that framed it. So it ships inert and stays inert unless an
authenticated parent completes a handshake naming the surface the server
registered. Everything here is about what the deck refuses to do on its own.

These tests read the rendered script rather than executing it. That is a real
limit — they pin the protocol's shape and its refusals, not its runtime
behaviour, which belongs to the frontend unit tests and the browser suite.
"""

from __future__ import annotations

import re

from deerflow.dbtl.council_deck import (
    BRIDGE_PROTOCOL_VERSION,
    DECK_MESSAGE_SOURCE,
    INERT_NOTICE,
    render_council_deck,
)
from deerflow.dbtl.decision_request import parse_decision_request

QUESTION = "Which population structure should define validation?"
SURFACE_ID = "dfs-0123456789abcdef"

_CONSENSUS = {
    "agreements": ["The lineage is a restricted benchmark."],
    "disagreements": [{"topic": "Scope", "positions": ["a", "b"], "resolution": ""}],
    "open_questions": [],
}


def _request():
    parsed = parse_decision_request(
        {
            "version": 1,
            "id": "validation-population",
            "question": QUESTION,
            "options": [
                {"id": "family_holdout", "label": "Family holdout", "value": "Use family-level holdout."},
                {"id": "random_split", "label": "Random split", "value": "Use a random split."},
            ],
        },
        question=QUESTION,
    )
    assert parsed.request is not None
    return parsed.request


def _deck(*, surface_id: str = SURFACE_ID, decision=None, question: str = QUESTION) -> str:
    return render_council_deck(
        cycle_title="Genomic selection in maize",
        stage_title="Design meeting",
        round_number=2,
        results=[{"summary": "Run a benchmark.", "consensus": _CONSENSUS}],
        package_path="/mnt/user-data/outputs/dbtl/x/design/design-review-rev2-abc123.md",
        clarification_question=question,
        decision_request=decision,
        surface_id=surface_id,
    )


class TestTheDeckShipsInert:
    def test_controls_are_disabled_in_the_persisted_file(self) -> None:
        html = _deck(decision=_request())

        for match in re.finditer(r"<input[^>]*type=\"radio\"[^>]*>", html):
            assert "disabled" in match.group(0)

    def test_the_submit_control_is_disabled_too(self) -> None:
        html = _deck(decision=_request())

        submit = re.search(r"<button[^>]*data-deck-submit[^>]*>", html)
        assert submit is not None
        assert "disabled" in submit.group(0)

    def test_it_says_where_a_real_answer_is_given(self) -> None:
        html = _deck(decision=_request())

        assert INERT_NOTICE in html

    def test_a_deck_with_no_surface_carries_no_bridge_at_all(self) -> None:
        """A legacy or unregistered deck has nothing to activate against."""
        html = _deck(surface_id="", decision=_request())

        assert DECK_MESSAGE_SOURCE not in html
        assert "submit_intent" not in html


class TestTheHandshake:
    def test_the_deck_announces_itself_with_its_surface_and_protocol(self) -> None:
        html = _deck(decision=_request())

        assert DECK_MESSAGE_SOURCE in html
        assert f'"{SURFACE_ID}"' in html
        assert f"var PROTOCOL = {BRIDGE_PROTOCOL_VERSION};" in html
        assert "send('ready')" in html

    def test_it_speaks_only_to_its_own_parent(self) -> None:
        html = _deck(decision=_request())

        assert "window.parent" in html
        # A deck opened directly (no framing parent) must not post to itself.
        assert "window.parent === window" in html or "window.parent !== window" in html

    def test_it_rejects_a_message_that_did_not_come_from_that_parent(self) -> None:
        html = _deck(decision=_request())

        assert "event.source !== window.parent" in html

    def test_it_checks_the_channel_the_parent_issued(self) -> None:
        html = _deck(decision=_request())

        assert "channel" in html
        assert "data.channel !== channel" in html

    def test_it_checks_the_surface_and_protocol_on_every_message(self) -> None:
        html = _deck(decision=_request())

        assert "data.surfaceId !== SURFACE_ID" in html
        assert "data.protocol !== PROTOCOL" in html


class TestTheVocabularyIsClosed:
    def test_the_deck_sends_only_the_intents_the_plan_allows(self) -> None:
        html = _deck(decision=_request())
        sent = set(re.findall(r"send\('([a-z_]+)'", html))

        assert sent <= {"ready", "submit_intent", "open_evidence_intent", "open_originating_conversation_intent"}
        assert "ready" in sent
        assert "submit_intent" in sent

    def test_the_deck_accepts_only_the_states_the_plan_allows(self) -> None:
        html = _deck(decision=_request())
        handled = set(re.findall(r"data\.type === '([a-z_]+)'", html))

        assert handled <= {"initialize", "pending", "accepted", "stale", "failed"}
        assert "initialize" in handled

    def test_no_endpoint_or_credential_crosses_the_boundary(self) -> None:
        html = _deck(decision=_request())

        assert "/api/" not in html
        assert "fetch(" not in html
        assert "XMLHttpRequest" not in html
        assert "document.cookie" not in html
        assert "localStorage" not in html


class TestAccessibility:
    def test_submission_state_is_announced(self) -> None:
        html = _deck(decision=_request())

        assert 'aria-live="polite"' in html

    def test_the_choice_is_a_real_radio_group(self) -> None:
        html = _deck(decision=_request())

        assert "<fieldset" in html
        assert "<legend>" in html


class TestStillSelfContained:
    def test_the_bridge_adds_no_network_dependency(self) -> None:
        html = _deck(decision=_request())

        assert "http://" not in html
        assert "https://" not in html
        assert "<script src" not in html

    def test_the_same_inputs_still_render_the_same_bytes(self) -> None:
        from datetime import UTC, datetime

        common = {
            "cycle_title": "Genomic selection in maize",
            "stage_title": "Design meeting",
            "round_number": 2,
            "results": [{"summary": "Run a benchmark.", "consensus": _CONSENSUS}],
            "clarification_question": QUESTION,
            "decision_request": _request(),
            "surface_id": SURFACE_ID,
            "generated_at": datetime(2026, 7, 29, 1, 50, tzinfo=UTC),
        }

        assert render_council_deck(**common) == render_council_deck(**common)

    def test_the_surface_id_is_escaped_into_the_script(self) -> None:
        """A surface id is server-generated, but the renderer never assumes it."""
        html = _deck(surface_id="dfs-</script><script>alert(1)</script>", decision=_request())

        assert "<script>alert(1)</script>" not in html
