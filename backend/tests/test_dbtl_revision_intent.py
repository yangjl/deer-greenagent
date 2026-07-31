"""Requesting changes chooses a route; it does not reconvene by reflex.

The failure this replaces: clicking "Revise" dispatched a fresh roster, a red
team, and a chair the moment it was clicked, whatever the objection said and
without telling anyone. Most objections are corrections the chair can fold into
the synthesis it already wrote.

Two properties are load-bearing. The cheap route is the default on every
failure path, because spending a meeting nobody asked for is the bug. And the
reviewer's words reach the prompt verbatim, because a paraphrase of an
objection is not the objection.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.revision_intent import (
    RevisionRoute,
    build_revision_prompt,
    interpret_revision,
    parse_revision_verdict,
)


class TestTheCheapRouteIsTheDefault:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "I think you should reconvene the meeting.",  # prose, no JSON
            '{"route": "start_over"}',  # unknown route
            '{"route": null}',
            "{not json at all",
            '["reconvene"]',  # not an object
        ],
    )
    def test_an_unreadable_verdict_revises_rather_than_reconvenes(self, raw):
        verdict = parse_revision_verdict(raw)

        assert verdict.route is RevisionRoute.CHAIR_ONLY
        assert verdict.reconvenes is False
        # The failure is stated rather than silent: the chat message says the
        # reading did not happen.
        assert verdict.reason
        assert verdict.interpreted is False

    @pytest.mark.anyio
    async def test_no_interpreter_configured_revises(self):
        verdict = await interpret_revision("Use 4,000 individuals.", interpreter=None)

        assert verdict.route is RevisionRoute.CHAIR_ONLY
        assert verdict.interpreted is False

    @pytest.mark.anyio
    async def test_a_provider_outage_revises_rather_than_failing_the_click(self):
        async def broken(_prompt: str) -> str:
            raise RuntimeError("provider down")

        verdict = await interpret_revision("Use 4,000 individuals.", interpreter=broken)

        assert verdict.route is RevisionRoute.CHAIR_ONLY
        assert "could not be reached" in verdict.reason

    @pytest.mark.anyio
    async def test_an_empty_objection_never_reaches_the_reader(self):
        called: list[str] = []

        async def reader(prompt: str) -> str:
            called.append(prompt)
            return '{"route": "reconvene"}'

        verdict = await interpret_revision("   ", interpreter=reader)

        assert verdict.route is RevisionRoute.CHAIR_ONLY
        assert called == []


class TestAReadVerdictIsHonoured:
    @pytest.mark.anyio
    async def test_reconvene_is_honoured_and_carries_its_roster_note(self):
        async def reader(_prompt: str) -> str:
            return '{"route": "reconvene", "reason": "Nobody argued population structure.", "roster_note": "Seat someone on population structure and relatedness."}'

        verdict = await interpret_revision("You ignored population structure entirely.", interpreter=reader)

        assert verdict.route is RevisionRoute.RECONVENE
        assert verdict.reconvenes is True
        assert verdict.roster_note == "Seat someone on population structure and relatedness."
        assert verdict.interpreted is True

    @pytest.mark.anyio
    async def test_a_fenced_reply_still_parses(self):
        async def reader(_prompt: str) -> str:
            return 'Here you go:\n```json\n{"route": "chair_only", "reason": "A parameter change."}\n```'

        verdict = await interpret_revision("Use 4,000 individuals.", interpreter=reader)

        assert verdict.route is RevisionRoute.CHAIR_ONLY
        assert verdict.reason == "A parameter change."
        assert verdict.interpreted is True

    def test_a_chair_only_verdict_carries_no_roster_note(self):
        """A roster note on a route that seats nobody would describe a roster
        that never runs, and would read as one that did."""
        verdict = parse_revision_verdict('{"route": "chair_only", "roster_note": "Seat a statistician."}')

        assert verdict.route is RevisionRoute.CHAIR_ONLY
        assert verdict.roster_note == ""


class TestTheObjectionReachesThePromptVerbatim:
    def test_the_reviewers_words_are_not_paraphrased_or_reordered(self):
        objection = "Use 4,000 individuals, not 1,000 — and drop the second site."

        prompt = build_revision_prompt(objection, positions=("Argued for 1,000 individuals across two sites.",))

        assert objection in prompt
        assert "Argued for 1,000 individuals" in prompt

    def test_an_enormous_objection_is_bounded_rather_than_dropped(self):
        prompt = build_revision_prompt("x" * 10_000)

        assert len(prompt) < 6_000
        assert "xxxx" in prompt

    def test_no_recorded_positions_is_stated_rather_than_left_blank(self):
        prompt = build_revision_prompt("Change the threshold.")

        assert "No prior positions were recorded" in prompt
