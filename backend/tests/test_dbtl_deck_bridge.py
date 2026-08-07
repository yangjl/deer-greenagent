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
    extract_commentable_slides,
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


class TestActivationReachesEveryControl:
    """A control ships disabled individually, so enabling the fieldset is not enough.

    `<fieldset disabled>` disables its descendants, but an `<input disabled>`
    inside an *enabled* fieldset stays disabled. Every radio and issue checkbox
    is rendered with its own `disabled` attribute so the persisted file is inert
    wherever it is opened — which means activation has to clear each one. An
    end-to-end run caught the omission: the parent verified the bytes, the
    server allowed `chair_option`, the submit button came alive, and no option
    could be selected, so `selected()` returned `''` and the deck answered every
    click with "Choose one option first."
    """

    @staticmethod
    def _enable_body(html: str) -> str:
        return html.split("function setEnabled")[1].split("\n  }")[0]

    def test_activation_clears_each_radio_not_just_the_fieldset(self) -> None:
        body = self._enable_body(_deck(decision=_request()))

        assert "choices" in body
        assert "choice.disabled = !on" in body

    def test_the_enable_path_reaches_the_issue_checkboxes_too(self) -> None:
        """`request_changes` reads `[data-deck-issue]:checked`; none could be checked."""
        html = render_council_deck(
            cycle_title="Genomic selection in maize",
            stage_title="Design meeting",
            round_number=2,
            results=[{"summary": "Run a benchmark.", "consensus": _CONSENSUS}],
            surface_id=SURFACE_ID,
            surface_mode="stage_review",
        )

        declaration = html.split("var choices =")[1].split(";")[0]
        assert "data-deck-issue" in declaration
        assert "choice.disabled = !on" in self._enable_body(html)

    def test_deactivation_freezes_the_choice_again(self) -> None:
        """Freezing while a submission is in flight has to freeze the options too."""
        body = self._enable_body(_deck(decision=_request()))

        # Driven by `on`, never set unconditionally true.
        assert "choice.disabled = true" not in body


class TestSlideSpecificComments:
    def test_commentable_slides_have_stable_ids(self) -> None:
        html = render_council_deck(
            cycle_title="Genomic selection in maize",
            stage_title="Design meeting",
            round_number=2,
            results=[{"summary": "Run a benchmark.", "consensus": _CONSENSUS}],
            surface_id=SURFACE_ID,
            research_question="Does it generalize?",
            objective="Estimate holdout accuracy.",
            success_criteria=["R2 >= 0.7"],
        )

        assert 'data-slide-id="background"' in html
        assert 'data-slide-id="objectives"' in html
        assert {item["id"] for item in extract_commentable_slides(html)} >= {"background", "objectives"}

    def test_submit_intents_carry_the_comment_map_and_visible_slide(self) -> None:
        html = _deck(decision=_request())

        assert "function slideComments()" in html
        assert "slideComments: slideComments()" in html
        assert "activeSlideId: activeSlideId()" in html

    def test_failed_initialization_restores_comments_to_their_slides(self) -> None:
        html = _deck(decision=_request())

        assert "data.slideComments" in html
        assert "notesBySlide" in html


class TestProgressiveTransitionGate:
    @staticmethod
    def _progressive_deck() -> str:
        return render_council_deck(
            cycle_title="Genomic selection in maize",
            stage_title="Design meeting",
            round_number=2,
            results=[{"summary": "Run a benchmark.", "consensus": _CONSENSUS}],
            surface_id=SURFACE_ID,
            surface_mode="stage_review",
            transition_gate={
                "stage": "design",
                "assessment": {
                    "difficulty": "high_stakes",
                    "rationale": "The intervention is irreversible.",
                    "source": "model",
                },
                "routes": [
                    {
                        "slug": "advance",
                        "to_stage": "build",
                        "label": "Continue to Build",
                        "value": "Open Build.",
                    },
                    {
                        "slug": "park",
                        "to_stage": "design",
                        "label": "Park — work with the lead agent",
                        "value": "Keep Design open.",
                    },
                ],
            },
        )

    def test_the_gate_is_three_named_choices_and_ships_inert(self) -> None:
        """One question, three answers: approve, revise, park."""
        html = self._progressive_deck()

        assert "Agent assessment: high stakes" in html
        assert "The intervention is irreversible." in html
        for value, label in (("approve", "Approve"), ("revise", "Revise"), ("park", "Park")):
            assert re.search(rf'<input type="radio" id="gate-{value}" name="design-gate" value="{value}"[^>]*disabled', html)
            assert label in html
        assert re.search(r"<button[^>]*data-deck-gate-submit[^>]*disabled", html)
        # The old controls are gone from the rendered gate: no depth override,
        # no per-route buttons, no contested-topic checkboxes. The shared
        # bridge script still names them, because a legacy deck renders them.
        controls = html.split("<script>")[0]
        assert "data-deck-difficulty" not in controls
        assert "data-deck-issue" not in controls
        assert 'data-deck-action="advance"' not in controls
        assert 'data-deck-action="reject"' not in controls

    def test_a_stale_blocked_build_edge_cannot_restore_the_removed_gate(self) -> None:
        html = render_council_deck(
            cycle_title="Genomic selection in maize",
            stage_title="Design meeting",
            round_number=2,
            results=[{"summary": "Run a benchmark.", "consensus": _CONSENSUS}],
            surface_id=SURFACE_ID,
            surface_mode="stage_review",
            transition_gate={
                "stage": "design",
                "assessment": {"difficulty": "standard", "rationale": "Ordinary review."},
                "routes": [
                    {
                        "slug": "advance",
                        "to_stage": "build",
                        "label": "Continue to Build",
                        "blocked": True,
                        "blocked_reason": "Two reconciliation rows are unsettled.",
                    },
                    {"slug": "park", "to_stage": "design", "label": "Park"},
                ],
            },
        )

        controls = html.split("<script>")[0]
        assert "Two reconciliation rows are unsettled." not in controls
        assert "data-route-blocked" not in controls
        assert "Accept this Design and open Build." in controls

    def test_the_gate_submit_maps_one_choice_to_one_intent(self) -> None:
        html = self._progressive_deck()

        assert "value === 'revise' ? 'request_changes'" in html
        assert "allowed.indexOf('advance') !== -1 ? 'advance' : 'approve'" in html
        assert "difficultyOverride: ''" in html
        # A high-stakes approval cannot be recorded with no rationale.
        assert "effective === 'high_stakes' && !text" in html


class TestNonDesignStageGate:
    def test_a_test_deck_shows_the_independent_deliverable_audit_as_a_commentable_slide(self) -> None:
        html = render_council_deck(
            cycle_title="Genomic selection in maize",
            stage_title="Test",
            stage="test",
            round_number=1,
            results=[
                {
                    "summary": "The rerun completed.",
                    "provenance": {
                        "deliverable_audit": {
                            "version": 1,
                            "items": [
                                {
                                    "deliverable_id": "replay-notebook",
                                    "verdict": "pass",
                                    "notes": "Executed from a clean kernel.",
                                }
                            ],
                        }
                    },
                }
            ],
            surface_id=SURFACE_ID,
            surface_mode="stage_review",
        )

        assert "Deliverable audit" in html
        assert "replay-notebook — pass" in html
        assert 'data-slide-id="test-deliverable-audit"' in html

    def test_a_test_deck_can_emit_the_convene_intent_the_server_offers(self) -> None:
        html = render_council_deck(
            cycle_title="Genomic selection in maize",
            stage_title="Test meeting",
            stage="test",
            round_number=1,
            results=[{"summary": "The validity pack needs red-team review.", "consensus": _CONSENSUS}],
            surface_id=SURFACE_ID,
            surface_mode="stage_review",
            transition_gate={
                "stage": "test",
                "assessment": {
                    "difficulty": "high_stakes",
                    "rationale": "The metric sits near the threshold.",
                    "source": "model",
                },
                "routes": [],
            },
        )

        controls = html.split("<script>")[0]
        assert "Review the Test" in controls
        assert "Agent assessment: high stakes" in controls
        assert 'data-deck-action="convene_review_meeting"' in controls
        assert "Convene review meeting" in controls
        assert "Move this Design" not in controls
        assert "Recording your Test decision..." in html
        assert "Submit this Test evidence when it is ready for human review." in html

    def test_build_and_learn_decks_render_their_real_verdict_controls(self) -> None:
        for stage in ("build", "learn"):
            html = render_council_deck(
                cycle_title="Genomic selection in maize",
                stage_title=f"{stage.title()} meeting",
                stage=stage,
                round_number=1,
                results=[{"summary": "Review the evidence.", "consensus": _CONSENSUS}],
                surface_id=SURFACE_ID,
                surface_mode="stage_review",
            )
            controls = html.split("<script>")[0]
            assert 'data-deck-action="submit_for_review"' in controls
            assert 'data-deck-action="approve"' in controls
            assert 'data-deck-action="request_changes"' in controls
            assert 'data-deck-action="reject"' in controls


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
