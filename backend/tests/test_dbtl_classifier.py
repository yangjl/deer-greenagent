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
    ClassifierContext,
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
    "Simulate a coin flip 100 times",
    "Simulate an image of a maize field",
    "Rename these output files so they sort by date",
    "Summarize the notes I uploaded",
    "Fix the broken path in my plotting script",
    "Read design.md and tell me if the formatting is consistent",
    "Show me the files in the workspace",
]

DATA_REQUESTS = [
    "Simulate a small SNP dataset",
    "Link the SNP markers with the phenotype data",
    "What does the third column in this csv mean?",
    "Make a bar plot of the yield column",
    "Convert this spreadsheet to csv",
    "Plot the genomic prediction accuracies from results.csv",
    "I want to look at the drought data",
    "Can plant height and leaf count predict grain yield well enough to pre-screen genotypes before harvest? Data is trial_2025_yield.csv.",
]

RESEARCH_REQUESTS = [
    "Design and validate a genomic-selection experiment",
    "Simulate a population of 100 maize inbreds with 10 SNP markers for each and 1 plant height phenotype for each",
    "can you similate some maize genotype and phenotype data for me",
    "Compare drought-response models across G2F environments",
    "Evaluate whether our prediction model generalizes to new environments",
    "Benchmark three genomic prediction models on the 2024 trial data and validate the winner",
    "Design an experiment to test whether population structure is leaking into our accuracy estimates",
    "Investigate which lines hold yield under late-season drought and validate across sites",
    "Design an autoresearch genomic selection plan using debate mode and test it with simulated data",
]

# A DBTL cycle is not a breeding construct. Any job that needs a plan and a way
# to tell whether it worked — and that produces data, code, figures, or other
# artifacts along the way — has the same Design/Build/Test/Learn shape. These
# carry no domain nouns at all, so they fail unless the rules generalize.
GENERAL_WORK_REQUESTS = [
    "Build a data pipeline that ingests the sensor logs, cleans them, and verify the output against last month's totals",
    "Design and test a caching layer for the API, and measure the latency improvement",
    "Investigate why the nightly job fails intermittently and verify the fix holds over a week",
    "Benchmark three parsing libraries on our workload and validate the winner on production samples",
    "Plan an analysis of the customer churn data, produce the figures and a summary report, and check the conclusions hold on a held-out slice",
    "Optimize the training script's memory use and confirm accuracy is unchanged",
    "Develop a scoring model from the survey data and evaluate it on a held-out sample",
    "Set up an end-to-end workflow that generates the dataset, the code, and the figures for the paper",
    "Build a tool that scrapes the weekly reports and check the totals match the invoices",
]

# The generalization must not swallow ordinary work outside the breeding domain
# either. A false upgrade is still the more damaging error.
GENERAL_ORDINARY_REQUESTS = [
    "What's the difference between a list and a tuple?",
    "Add a docstring to this function",
    "Run the tests",
    "Install the dependencies",
    "Rename the output figures",
    "Show me the latency numbers from last night's run",
    "Write a script to rename files",
    "Fix the path then check it runs",
]


@pytest.mark.parametrize("text", ORDINARY_REQUESTS)
def test_ordinary_work_is_not_proposed_as_a_cycle(text: str) -> None:
    """A false upgrade is the more damaging error, so ordinary work is the default."""
    result = classify_request(text)
    assert result.decision is ClassifierDecision.ORDINARY, f"false upgrade on {text!r}: {result.rule_hits}"


@pytest.mark.parametrize("text", DATA_REQUESTS)
def test_data_work_is_a_high_confidence_cycle_candidate(text: str) -> None:
    result = classify_request(text)

    assert result.decision is ClassifierDecision.PROPOSE_CYCLE, f"missed data cycle on {text!r}: {result.rule_hits}"
    assert result.band is ConfidenceBand.HIGH
    assert any(hit.rule_id == "intent.data_task" for hit in result.rule_hits)


@pytest.mark.parametrize("text", RESEARCH_REQUESTS)
def test_multi_step_research_work_is_proposed(text: str) -> None:
    result = classify_request(text)
    assert result.decision is ClassifierDecision.PROPOSE_CYCLE, f"missed cycle on {text!r}: {result.rule_hits}"


@pytest.mark.parametrize("text", GENERAL_WORK_REQUESTS)
def test_plan_and_verify_work_is_proposed_outside_the_breeding_domain(text: str) -> None:
    """DBTL is a way of working, not a crop science vocabulary.

    Every entry here needs a plan, produces artifacts, and states how it will
    be checked — the same shape the domain examples have — so a classifier
    that only recognizes traits and markers misses all of them.
    """
    result = classify_request(text)
    assert result.decision is ClassifierDecision.PROPOSE_CYCLE, f"missed cycle on {text!r}: {result.rule_hits}"


@pytest.mark.parametrize("text", GENERAL_ORDINARY_REQUESTS)
def test_ordinary_work_outside_the_breeding_domain_is_not_proposed(text: str) -> None:
    result = classify_request(text)
    assert result.decision is ClassifierDecision.ORDINARY, f"false upgrade on {text!r}: {result.rule_hits}"


def test_a_stated_verification_beats_the_single_artifact_veto() -> None:
    """ "Write a script" alone is one deliverable; with a test it is a cycle.

    The negative rules key on request *shape*, so without an override the
    make-an-artifact opening would veto exactly the plan-and-test work this
    classifier exists to find.
    """
    one_deliverable = classify_request("Write a script that generates a README")
    with_verification = classify_request("Write a script that generates a README and the figures, then verify the totals against the source export")

    assert one_deliverable.decision is ClassifierDecision.ORDINARY
    assert with_verification.decision is ClassifierDecision.PROPOSE_CYCLE


def test_generic_work_is_asked_domain_neutral_questions() -> None:
    """Asking a software job for its "target trait" reads as not listening."""
    result = classify_request("Design and test a caching layer for the API, and measure the latency improvement")

    assert "target trait" not in result.missing_fields
    assert "season range" not in result.missing_fields
    assert "population scope" not in result.missing_fields
    assert result.missing_fields, "a generic proposal still needs its gaps named"


def test_breeding_work_keeps_its_domain_questions() -> None:
    result = classify_request("Design and validate a genomic-selection experiment")
    assert "target trait" in result.missing_fields
    assert "population scope" in result.missing_fields


def test_a_generic_request_that_names_its_inputs_is_not_asked_for_them() -> None:
    named = classify_request("Benchmark three parsing libraries on the production sample logs and validate the winner against the current baseline")
    assert "input data" not in named.missing_fields
    assert "validation expectation" not in named.missing_fields


def test_the_decisive_rule_survives_hit_truncation() -> None:
    """A decision whose own evidence was truncated away cannot be reviewed."""
    crowded = " ".join(
        [
            "explain the dataset, the figures, the tables, the report, the model,",
            "the scripts, the notebooks, the pipeline, the workflow, the baseline,",
            "the latency, the accuracy, the throughput, the trials, the environments,",
            "the phenotypes, the genotypes, the markers, the heritability",
        ]
    )
    result = classify_request(crowded)

    assert result.decision is ClassifierDecision.PROPOSE_CYCLE
    assert len(result.rule_hits) <= 16
    assert any(hit.rule_id == "override.data_task" for hit in result.rule_hits), "the override that decided this is missing from its own evidence"


def test_data_artifact_work_overrides_the_single_artifact_veto() -> None:
    """A one-plot request remains governed when its source is structured data."""
    result = classify_request("Plot the genomic prediction accuracies from results.csv")
    assert result.decision is ClassifierDecision.PROPOSE_CYCLE
    assert result.band is ConfidenceBand.HIGH
    assert any(hit.rule_id == "override.data_task" for hit in result.rule_hits)


def test_a_decision_carries_the_rules_that_produced_it() -> None:
    """The evaluation drawer shows rule hits, so they must be real evidence."""
    result = classify_request("Design and validate a genomic-selection experiment")
    assert result.rule_hits, "a proposal with no rule hits cannot be reviewed"
    for hit in result.rule_hits:
        assert hit.rule_id
        assert hit.evidence
        if not hit.rule_id.startswith("context."):
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


def test_an_ambiguous_data_request_is_still_high_confidence() -> None:
    """Data work is governed even when its requested operation is underspecified."""
    result = classify_request("I want to look at the drought data")
    assert result.decision is ClassifierDecision.PROPOSE_CYCLE
    assert result.band is ConfidenceBand.HIGH


def test_fresh_project_context_promotes_a_borderline_research_request() -> None:
    text = "Evaluate the trial"

    established = classify_request(text)
    fresh = classify_request(
        text,
        context=ClassifierContext(
            is_new_conversation=True,
            project_cycle_count=0,
            has_unfinished_cycles=False,
        ),
    )

    assert established.decision is ClassifierDecision.ORDINARY
    assert fresh.decision is ClassifierDecision.PROPOSE_CYCLE
    assert {hit.rule_id for hit in fresh.rule_hits if hit.rule_id.startswith("context.")} == {
        "context.new_conversation",
        "context.no_unfinished_cycles",
        "context.new_project",
    }


def test_context_priors_raise_confidence_without_replacing_research_evidence() -> None:
    text = "Evaluate the trial"
    established = classify_request(text)
    no_open_cycle = classify_request(
        text,
        context=ClassifierContext(has_unfinished_cycles=False),
    )

    assert no_open_cycle.confidence > established.confidence
    assert any(not hit.rule_id.startswith("context.") for hit in no_open_cycle.rule_hits)


def test_fresh_context_does_not_upgrade_text_without_a_research_signal() -> None:
    result = classify_request(
        "Hello there",
        context=ClassifierContext(
            is_new_conversation=True,
            project_cycle_count=0,
            has_unfinished_cycles=False,
        ),
    )

    assert result.decision is ClassifierDecision.ORDINARY
    assert result.confidence == 0.0
    assert not result.rule_hits


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
