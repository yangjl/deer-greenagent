"""Precedence tests for deterministic-first DBTL routing (Phase 4).

The plan's rule is that explicit user choice, the selected cycle, and the
current project are all evaluated *before* any model classification. These
tests pin the order, because the failure they prevent is subtle: a classifier
that runs first and happens to be confident would quietly overrule a person
who already said what they wanted.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.routing import (
    ExplicitChoice,
    RouteKind,
    RouteSource,
    RoutingRequest,
    route_request,
)

RESEARCH_TEXT = "Design and validate a genomic-selection experiment"
ORDINARY_TEXT = "Explain this README"


def _request(**overrides) -> RoutingRequest:
    base = {
        "text": RESEARCH_TEXT,
        "project_id": "proj-1",
        "selected_cycle_id": None,
        "explicit_choice": None,
        "is_new_conversation": False,
        "project_cycle_count": None,
        "has_unfinished_cycles": None,
    }
    return RoutingRequest(**{**base, **overrides})


# ── Precedence 1: explicit user choice ───────────────────────────────────


def test_explicit_ordinary_choice_beats_a_confident_classifier() -> None:
    """The whole point of "Keep as ordinary chat" is that it is final."""
    decision = route_request(_request(explicit_choice=ExplicitChoice.ORDINARY))
    assert decision.kind is RouteKind.ORDINARY
    assert decision.source is RouteSource.EXPLICIT_CHOICE
    assert decision.classifier is None, "the classifier must not even run once the user has chosen"


def test_explicit_start_choice_routes_to_setup_even_on_ordinary_text() -> None:
    decision = route_request(_request(text=ORDINARY_TEXT, explicit_choice=ExplicitChoice.START_CYCLE))
    assert decision.kind is RouteKind.CYCLE_SETUP
    assert decision.source is RouteSource.EXPLICIT_CHOICE
    assert decision.classifier is None


def test_explicit_continue_choice_carries_the_selected_cycle() -> None:
    decision = route_request(
        _request(
            text=ORDINARY_TEXT,
            selected_cycle_id="cycle-7",
            explicit_choice=ExplicitChoice.CONTINUE_CYCLE,
        )
    )
    assert decision.kind is RouteKind.CYCLE_CONTINUATION
    assert decision.cycle_id == "cycle-7"


def test_continue_without_a_selected_cycle_falls_back_to_ordinary() -> None:
    """A continuation with nothing to continue must not invent a cycle."""
    decision = route_request(_request(explicit_choice=ExplicitChoice.CONTINUE_CYCLE, selected_cycle_id=None))
    assert decision.kind is RouteKind.ORDINARY
    assert decision.cycle_id is None


# ── Precedence 2: a typed request to start ───────────────────────────────


def test_typing_start_a_dbtl_cycle_is_deterministic_not_classified() -> None:
    decision = route_request(_request(text="start a DBTL cycle for the drought work"))
    assert decision.kind is RouteKind.CYCLE_SETUP
    assert decision.source is RouteSource.EXPLICIT_REQUEST
    assert decision.classifier is None


def test_typing_start_a_cycle_is_deterministic_even_with_existing_cycles() -> None:
    decision = route_request(
        _request(
            text="let's start a cycle to understand the simulation",
            project_cycle_count=2,
            has_unfinished_cycles=True,
        )
    )
    assert decision.kind is RouteKind.CYCLE_SETUP
    assert decision.source is RouteSource.EXPLICIT_REQUEST
    assert decision.classifier is None


@pytest.mark.parametrize(
    "text",
    [
        "strat a new cycle for the drought work",
        "Sttart a DBTL cycle please.",
        "star a new research cycle",
        "creat a new cycle",
        "opne a new cycle",
        "begni a new dbtl cycle",
    ],
)
def test_a_misspelled_start_request_is_still_a_typed_start(text: str) -> None:
    """A slip in the verb must not silently demote an explicit instruction.

    Missing this rung drops the request to the classifier, which may well call
    it ordinary — so the person who asked, in words, to start a cycle is simply
    ignored.
    """
    decision = route_request(_request(text=text))
    assert decision.kind is RouteKind.CYCLE_SETUP
    assert decision.source is RouteSource.EXPLICIT_REQUEST


@pytest.mark.parametrize(
    "text",
    [
        "start the analysis",
        "start the gateway",
        "create a chart of the yields",
    ],
)
def test_a_start_that_names_no_cycle_is_not_a_typed_start(text: str) -> None:
    decision = route_request(_request(text=text))
    assert decision.source is not RouteSource.EXPLICIT_REQUEST


# ── Precedence 3: the selected cycle ─────────────────────────────────────


def test_a_selected_cycle_produces_a_continuation_not_a_new_cycle() -> None:
    """The demo path's last case: never propose a second cycle over an open one."""
    decision = route_request(_request(selected_cycle_id="cycle-3"))
    assert decision.kind is RouteKind.CYCLE_CONTINUATION
    assert decision.source is RouteSource.SELECTED_CYCLE
    assert decision.cycle_id == "cycle-3"
    assert decision.classifier is None


def test_a_question_about_a_selected_cycle_stays_ordinary_chat() -> None:
    """Cycle identity supplies context; it is not permission to run workers.

    Follow-up questions are answered by the lead agent unless the person
    explicitly scopes the request to the cycle. Otherwise asking what the last
    meeting captured can accidentally convene the meeting again.
    """
    decision = route_request(
        _request(
            text="What parameters did the design stage capture for the simulation?",
            selected_cycle_id="cycle-3",
        )
    )

    assert decision.kind is RouteKind.ORDINARY
    assert decision.source is RouteSource.CLASSIFIER
    assert decision.classifier is not None
    assert any(hit.rule_id == "ordinary.explain" for hit in decision.classifier.rule_hits)


@pytest.mark.parametrize(
    "text",
    [
        "Waht parameters did the design stage capture?",
        "wat did the meeting decide about the drought sites",
        "Hwo does this handle the missing phenotypes?",
        "Explian the design stage output.",
        "Descrbie what the meeting captured.",
        "Summarise the meeting please.",  # British spelling, already supported
        "Sohw me the design package.",
        "lsit the declared datasets",
    ],
)
def test_a_misspelled_question_about_a_selected_cycle_stays_ordinary(text: str) -> None:
    """A typo in a read-only question must not convene a meeting.

    This is the expensive direction: missing the ordinary rule promotes a
    question into a cycle continuation, which can spend a whole council's
    budget answering something the lead agent should have read out.
    """
    decision = route_request(_request(text=text, selected_cycle_id="cycle-3"))

    assert decision.kind is RouteKind.ORDINARY
    assert decision.source is RouteSource.CLASSIFIER


# ── Precedence 4: the current project ────────────────────────────────────


def test_a_projectless_conversation_is_never_upgraded() -> None:
    """A cycle belongs to a project, so there is nothing to propose without one."""
    decision = route_request(_request(project_id=None))
    assert decision.kind is RouteKind.ORDINARY
    assert decision.source is RouteSource.NO_PROJECT
    assert decision.classifier is None


# ── Precedence 5: the classifier, last ───────────────────────────────────


def test_the_classifier_only_runs_when_nothing_deterministic_applied() -> None:
    decision = route_request(_request())
    assert decision.kind is RouteKind.PROPOSAL
    assert decision.source is RouteSource.CLASSIFIER
    assert decision.classifier is not None
    assert decision.classifier.rule_hits


def test_classifier_proposal_enters_discovery_only_when_its_rollout_flag_is_enabled() -> None:
    decision = route_request(
        _request(
            discovery_enabled=True,
            discovery_classifier_entry=True,
        )
    )

    assert decision.kind is RouteKind.DISCOVERY
    assert decision.source is RouteSource.CLASSIFIER
    assert decision.classifier is not None


def test_ordinary_opt_out_suppresses_classifier_reentry_but_not_an_explicit_start() -> None:
    suppressed = route_request(
        _request(
            discovery_enabled=True,
            discovery_classifier_entry=True,
            discovery_suppressed=True,
        )
    )
    explicit = route_request(
        _request(
            text="start a new cycle to validate the trial",
            discovery_enabled=True,
            discovery_classifier_entry=True,
            discovery_suppressed=True,
        )
    )

    assert suppressed.kind is RouteKind.ORDINARY
    assert suppressed.source is RouteSource.CLASSIFIER
    assert explicit.kind is RouteKind.DISCOVERY
    assert explicit.source is RouteSource.EXPLICIT_REQUEST


def test_ordinary_text_in_a_project_stays_ordinary() -> None:
    decision = route_request(_request(text=ORDINARY_TEXT))
    assert decision.kind is RouteKind.ORDINARY
    assert decision.source is RouteSource.CLASSIFIER
    assert decision.classifier is not None


def test_fresh_project_context_raises_the_proposal_prior() -> None:
    established = route_request(_request(text="Evaluate the trial"))
    fresh = route_request(
        _request(
            text="Evaluate the trial",
            is_new_conversation=True,
            project_cycle_count=0,
            has_unfinished_cycles=False,
        )
    )

    assert established.kind is RouteKind.ORDINARY
    assert fresh.kind is RouteKind.PROPOSAL
    assert fresh.classifier is not None
    assert any(hit.rule_id == "context.new_project" for hit in fresh.classifier.rule_hits)


def test_selected_cycle_still_wins_over_fresh_context() -> None:
    decision = route_request(
        _request(
            text="Evaluate the trial",
            selected_cycle_id="cycle-3",
            is_new_conversation=True,
            project_cycle_count=0,
            has_unfinished_cycles=False,
        )
    )

    assert decision.kind is RouteKind.CYCLE_CONTINUATION
    assert decision.classifier is None


def test_routing_never_reports_a_proposal_without_classifier_evidence() -> None:
    """A proposal card shows a reason; a reasonless proposal cannot be reviewed."""
    for text in (RESEARCH_TEXT, ORDINARY_TEXT, "look at the drought data"):
        decision = route_request(_request(text=text))
        if decision.kind is RouteKind.PROPOSAL:
            assert decision.classifier is not None
            assert decision.classifier.rule_hits


def test_routing_is_deterministic() -> None:
    request = _request()
    first = route_request(request)
    for _ in range(5):
        assert route_request(request) == first
