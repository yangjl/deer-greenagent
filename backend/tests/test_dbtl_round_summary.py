"""The sentence that introduces a finished Design round in chat.

Two rules carry the risk here: the sentence may report but never claim a
verdict, and no failure in writing it may cost the reply to a meeting that
already ran.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.consensus import parse_consensus
from deerflow.dbtl.round_summary import (
    MAX_SUMMARY_CHARS,
    build_round_summary_prompt,
    fallback_summary,
    parse_round_summary,
)

CONSENSUS = parse_consensus(
    {
        "agreements": ["Group the split by genotype", "Report held-out performance"],
        "disagreements": [
            {"topic": "What counts as success", "positions": ["tie it to held-out", "no threshold defined"], "resolved": False},
            {"topic": "Whether days_to_flower belongs", "positions": ["leave open", "exclude it"], "resolved": True, "resolution": "Excluded."},
        ],
        "open_questions": [],
    }
)


class TestItReportsButNeverDecides:
    @pytest.mark.parametrize(
        "claim",
        [
            "The design is approved and ready for Build.",
            "The council approved the design.",
            "This design passes the gate.",
            "The meeting signed off on the grouped holdout.",
            "The plan is cleared for implementation.",
            "The design was rejected by the red team.",
        ],
    )
    def test_a_verdict_claim_is_refused_whole(self, claim: str) -> None:
        # Refused rather than edited: a sentence with the verdict cut out still
        # carries the judgement that produced it.
        summary = parse_round_summary(claim, consensus=CONSENSUS)
        assert summary == fallback_summary(CONSENSUS)
        assert "approved" not in summary and "rejected" not in summary

    def test_an_ordinary_report_survives(self) -> None:
        sentence = "The meeting settled on a genotype-grouped holdout but has not fixed the correlation threshold that would count as success."
        assert parse_round_summary(sentence, consensus=CONSENSUS) == sentence

    def test_a_long_reply_is_bounded(self) -> None:
        summary = parse_round_summary("word " * 400, consensus=CONSENSUS)
        assert len(summary) <= MAX_SUMMARY_CHARS


class TestNothingHereFailsLoudly:
    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_an_unusable_reply_reports_the_recorded_counts(self, raw: object) -> None:
        summary = parse_round_summary(raw, consensus=CONSENSUS)  # type: ignore[arg-type]
        assert "2 points agreed" in summary
        assert "1 settled in discussion" in summary
        assert "1 still contested" in summary

    def test_the_fallback_states_quantities_rather_than_substance(self) -> None:
        # This path runs when nothing readable came back; characterising the
        # meeting here would be inventing the summary it stands in for.
        summary = fallback_summary(CONSENSUS)
        assert "genotype" not in summary
        assert summary.startswith("The meeting recorded ")

    def test_a_paused_chair_is_mentioned_in_the_fallback(self) -> None:
        summary = fallback_summary(CONSENSUS, clarification_question="Which threshold?")
        assert "question for you" in summary

    def test_no_consensus_at_all_still_produces_a_sentence(self) -> None:
        summary = fallback_summary(None)
        assert summary
        assert "no structured consensus" in summary

    def test_singular_counts_read_correctly(self) -> None:
        one = parse_consensus({"agreements": ["Only this"], "disagreements": [], "open_questions": []})
        assert "1 point agreed" in fallback_summary(one)


class TestThePromptAsksForTheRightSentence:
    def test_it_carries_the_meetings_own_material(self) -> None:
        prompt = build_round_summary_prompt(
            research_question="Can height predict yield?",
            chair_summary="The council converged on a grouped holdout.",
            consensus=CONSENSUS,
            clarification_question="Which threshold?",
        )
        assert "Can height predict yield?" in prompt
        assert "grouped holdout" in prompt
        assert "Group the split by genotype" in prompt
        assert "What counts as success" in prompt
        assert "Which threshold?" in prompt

    def test_a_settled_disagreement_is_not_listed_as_contested(self) -> None:
        prompt = build_round_summary_prompt(research_question="q", chair_summary="s", consensus=CONSENSUS)
        contested_block = prompt.split("What is still contested")[1]
        assert "What counts as success" in contested_block
        assert "days_to_flower" not in contested_block

    def test_it_forbids_a_verdict_and_keeps_next_steps_out(self) -> None:
        prompt = build_round_summary_prompt(research_question="q", chair_summary="s", consensus=None)
        assert "Report only" in prompt
        assert "approved" in prompt
        # The reply says what to do next separately, so the sentence must not.
        assert "Do not mention slide decks" in prompt

    def test_an_empty_meeting_still_produces_a_usable_prompt(self) -> None:
        prompt = build_round_summary_prompt(research_question="", chair_summary="", consensus=None)
        assert "(not recorded)" in prompt
        assert "(none recorded)" in prompt
