"""Golden-set tests for the DBTL request classifier (Phase 4).

The classifier's job is narrow and its failure modes are asymmetric: a *false
upgrade* interrupts ordinary work with a card nobody wanted, while a *missed
cycle* silently loses the research framing. Both are measured against the same
golden set here so the exit review has numbers rather than impressions.

Nothing in this module may create a durable record — that is the Phase 4
no-go, and it is pinned structurally in ``test_dbtl_proposal_contract.py``.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.classifier import (
    ClassifierDecision,
    ConfidenceBand,
    classify_request,
)

# ── Golden set ───────────────────────────────────────────────────────────
#
# Every entry is a real sentence a plant scientist might type. The demo path
# in the phase plan is included verbatim so the reviewed examples and the
# tested examples cannot drift.

ORDINARY_REQUESTS = [
    "Explain this README",
    "Create a small chart",
    "What does the third column in this csv mean?",
    "Rename these output files so they sort by date",
    "Summarize the notes I uploaded",
    "Fix the broken path in my plotting script",
    "Read design.md and tell me if the formatting is consistent",
    "Make a bar plot of the yield column",
    "Convert this spreadsheet to csv",
    "Show me the files in the workspace",
]

RESEARCH_REQUESTS = [
    "Design and validate a genomic-selection experiment",
    "Compare drought-response models across G2F environments",
    "Evaluate whether our prediction model generalizes to new environments",
    "Benchmark three genomic prediction models on the 2024 trial data and validate the winner",
    "Design an experiment to test whether population structure is leaking into our accuracy estimates",
    "Investigate which lines hold yield under late-season drought and validate across sites",
    "Design an autoresearch genomic selection plan using debate mode and test it with simulated data",
]


@pytest.mark.parametrize("text", ORDINARY_REQUESTS)
def test_ordinary_work_is_not_proposed_as_a_cycle(text: str) -> None:
    """A false upgrade is the more damaging error, so ordinary work is the default."""
    result = classify_request(text)
    assert result.decision is ClassifierDecision.ORDINARY, f"false upgrade on {text!r}: {result.rule_hits}"


@pytest.mark.parametrize("text", RESEARCH_REQUESTS)
def test_multi_step_research_work_is_proposed(text: str) -> None:
    result = classify_request(text)
    assert result.decision is ClassifierDecision.PROPOSE_CYCLE, f"missed cycle on {text!r}: {result.rule_hits}"


def test_file_and_artifact_work_stays_ordinary_even_with_a_research_noun() -> None:
    """The negative rules must beat a bare domain noun.

    "Plot the genomic prediction accuracies" mentions the domain but asks for
    one artifact. Without this the classifier would fire on every message in a
    breeding project.
    """
    result = classify_request("Plot the genomic prediction accuracies from results.csv")
    assert result.decision is ClassifierDecision.ORDINARY


def test_a_decision_carries_the_rules_that_produced_it() -> None:
    """The evaluation drawer shows rule hits, so they must be real evidence."""
    result = classify_request("Design and validate a genomic-selection experiment")
    assert result.rule_hits, "a proposal with no rule hits cannot be reviewed"
    for hit in result.rule_hits:
        assert hit.rule_id
        assert hit.evidence
        assert hit.evidence.lower() in result.normalized_text


def test_confidence_and_band_agree() -> None:
    for text in RESEARCH_REQUESTS + ORDINARY_REQUESTS:
        result = classify_request(text)
        assert 0.0 <= result.confidence <= 1.0
        if result.band is ConfidenceBand.HIGH:
            assert result.confidence >= 0.7
        elif result.band is ConfidenceBand.MEDIUM:
            assert 0.4 <= result.confidence < 0.7
        else:
            assert result.confidence < 0.4


def test_an_ambiguous_request_lands_in_the_low_or_medium_band() -> None:
    """Ambiguity must be visible, not rounded up into a confident proposal."""
    result = classify_request("I want to look at the drought data")
    assert result.band is not ConfidenceBand.HIGH


def test_missing_fields_are_named_for_the_clarification_step() -> None:
    result = classify_request("Design and validate a genomic-selection experiment")
    assert "target trait" in result.missing_fields
    assert "validation expectation" in result.missing_fields


def test_a_request_that_already_names_a_trait_does_not_ask_for_it_again() -> None:
    result = classify_request("Design and validate a genomic-selection experiment for grain yield across the 2023-2024 seasons, validated on held-out environments")
    assert "target trait" not in result.missing_fields
    assert "season range" not in result.missing_fields


def test_the_proposed_objective_is_derived_from_the_user_text() -> None:
    """The card shows an objective back to the user; it must be their words."""
    result = classify_request("Compare drought-response models across G2F environments")
    assert "drought-response models" in result.proposed_objective
    assert len(result.proposed_objective) <= 240


def test_empty_and_whitespace_requests_are_ordinary() -> None:
    for text in ("", "   ", "\n\t"):
        assert classify_request(text).decision is ClassifierDecision.ORDINARY


def test_classification_is_deterministic() -> None:
    """The same text must classify identically every time.

    Shadow-mode telemetry is only comparable across runs if this holds.
    """
    text = "Design and validate a genomic-selection experiment"
    first = classify_request(text)
    for _ in range(5):
        assert classify_request(text) == first


def test_a_very_long_request_is_bounded() -> None:
    result = classify_request("design and validate an experiment " * 500)
    assert len(result.proposed_objective) <= 240
    assert len(result.rule_hits) <= 16
