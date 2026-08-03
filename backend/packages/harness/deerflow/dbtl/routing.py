"""Deterministic-first routing for DBTL requests (Phase 4).

Five things are consulted, in this order, and the first one that answers wins:

1. **An explicit user choice.** Someone clicked "Keep as ordinary chat" or
   "Start DBTL setup". That is final.
2. **A typed request to start a cycle.** "start a DBTL cycle" is a request,
   not a hint to be scored.
3. **The selected cycle.** Work inside an open cycle continues it, except for
   a clearly ordinary read/explain question. Cycle identity gives that question
   context; it does not authorize another worker run.
4. **The current project.** A cycle belongs to a project, so a projectless
   conversation has nothing to propose.
5. **The classifier**, last, and only for what the first four left open.

The ordering is the point. A classifier consulted first would occasionally be
confident enough to overrule a person who had already said what they wanted,
and that is precisely the failure the phase's no-go names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from deerflow.dbtl.classifier import (
    ClassifierContext,
    ClassifierDecision,
    ClassifierResult,
    classify_request,
)
from deerflow.dbtl.meeting_intent import wants_new_debate


class RouteKind(StrEnum):
    """Where one request should go."""

    ORDINARY = "ordinary"
    CYCLE_SETUP = "cycle_setup"
    CYCLE_CONTINUATION = "cycle_continuation"
    PROPOSAL = "proposal"


class RouteSource(StrEnum):
    """Which rung of the precedence ladder answered.

    Recorded in telemetry so the exit review can tell a deterministic route
    from a classified one without re-deriving it.
    """

    EXPLICIT_CHOICE = "explicit_choice"
    EXPLICIT_REQUEST = "explicit_request"
    SELECTED_CYCLE = "selected_cycle"
    THREAD_CYCLE = "thread_cycle"
    NO_PROJECT = "no_project"
    CLASSIFIER = "classifier"


class ExplicitChoice(StrEnum):
    """What the user pressed on a previous card, if anything."""

    ORDINARY = "ordinary"
    START_CYCLE = "start_cycle"
    CONTINUE_CYCLE = "continue_cycle"


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    """Everything routing is allowed to look at."""

    text: str
    project_id: str | None
    selected_cycle_id: str | None
    explicit_choice: ExplicitChoice | None
    is_new_conversation: bool = False
    project_cycle_count: int | None = None
    has_unfinished_cycles: bool | None = None
    #: The live cycle this conversation itself opened, resolved server-side
    #: from ``dbtl_cycles.originating_thread_id``. Never client-supplied: it
    #: decides which research record a request may touch, so it is derived
    #: from the durable record rather than accepted from the caller.
    thread_cycle_id: str | None = None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The routing result. Carries no authority to create anything."""

    kind: RouteKind
    source: RouteSource
    classifier: ClassifierResult | None = None
    cycle_id: str | None = None


# One-slip misspellings of the start verbs, enumerated the same narrow way the
# classifier recognizes "similate": each is a literal, never a fuzzy match, so
# the rung stays auditable. A slip here is expensive in a quiet way — the rung
# is skipped, the classifier gets the request, and someone who asked in words to
# start a cycle is told nothing happened. The verb still has to be followed by
# "cycle"/"dbtl" below, so a misspelled verb alone trips nothing.
_START_VERB_TYPOS = r"strat|sttart|star|statr|creat|craete|crate|opne|oepn|begni|bgin|beign"

# A typed request to start. Deliberately narrow: it must name a cycle or DBTL
# explicitly, so "start the analysis" does not trip it.
_EXPLICIT_START_PATTERN = re.compile(
    r"\b(?:start|open|begin|create|" + _START_VERB_TYPOS + r")\s+(?:a\s+|the\s+|new\s+)*(?:dbtl\s+cycle|dbtl\s+workflow|research\s+cycle|learning\s+cycle|cycle|dbtl)\b",
    re.IGNORECASE,
)


def is_explicit_start_request(text: str) -> bool:
    """Whether the user asked, in words, to start a cycle."""
    return bool(_EXPLICIT_START_PATTERN.search(text or ""))


def route_request(request: RoutingRequest) -> RoutingDecision:
    """Route one request. Pure, deterministic, and side-effect free."""

    # 1. An explicit choice is final and short-circuits classification.
    if request.explicit_choice is ExplicitChoice.ORDINARY:
        return RoutingDecision(kind=RouteKind.ORDINARY, source=RouteSource.EXPLICIT_CHOICE)
    if request.explicit_choice is ExplicitChoice.START_CYCLE:
        return RoutingDecision(kind=RouteKind.CYCLE_SETUP, source=RouteSource.EXPLICIT_CHOICE)
    if request.explicit_choice is ExplicitChoice.CONTINUE_CYCLE:
        if request.selected_cycle_id:
            return RoutingDecision(
                kind=RouteKind.CYCLE_CONTINUATION,
                source=RouteSource.EXPLICIT_CHOICE,
                cycle_id=request.selected_cycle_id,
            )
        # Continuing nothing is not a reason to invent a cycle.
        return RoutingDecision(kind=RouteKind.ORDINARY, source=RouteSource.EXPLICIT_CHOICE)

    # 2. A typed request to start, still deterministic.
    if is_explicit_start_request(request.text):
        return RoutingDecision(kind=RouteKind.CYCLE_SETUP, source=RouteSource.EXPLICIT_REQUEST)

    # 2b. The conversation that opened a cycle can be asked, in words, to run
    # its stage work. The composer's scope is next-request-only by design, so
    # the second consecutive cycle request arrives unscoped — and before this
    # rung it fell through to the classifier, which read "run the meeting
    # again" as ordinary chat and handed it to the lead agent, which then
    # wrote the design package itself.
    #
    # Deliberately narrow, so this does not become a sticky scope by the back
    # door: only the deterministic re-run phrases the stage adapter itself
    # uses, only in the conversation that opened the cycle, and only while
    # that cycle is live. It recovers a cycle; it never invents one. The same
    # principle already governs Human Input cards, whose replies recover their
    # cycle from the request the server emitted.
    if request.thread_cycle_id and wants_new_debate(request.text):
        return RoutingDecision(
            kind=RouteKind.CYCLE_CONTINUATION,
            source=RouteSource.THREAD_CYCLE,
            cycle_id=request.thread_cycle_id,
        )

    # 3. An open cycle continues, but it is not permission to run a stage in
    # response to a read-only follow-up. This distinction matters after Design:
    # "What parameters did the meeting capture?" used to convene the meeting
    # again merely because the cycle was still selected. An explicit
    # CONTINUE_CYCLE choice above still wins, so a person can deliberately
    # scope any wording to the cycle.
    if request.selected_cycle_id:
        follow_up = classify_request(
            request.text,
            context=ClassifierContext(
                is_new_conversation=request.is_new_conversation,
                project_cycle_count=request.project_cycle_count,
                has_unfinished_cycles=request.has_unfinished_cycles,
            ),
        )
        if any(hit.rule_id.startswith("ordinary.") for hit in follow_up.rule_hits):
            return RoutingDecision(
                kind=RouteKind.ORDINARY,
                source=RouteSource.CLASSIFIER,
                classifier=follow_up,
            )
        return RoutingDecision(
            kind=RouteKind.CYCLE_CONTINUATION,
            source=RouteSource.SELECTED_CYCLE,
            cycle_id=request.selected_cycle_id,
        )

    # 4. No project, nothing to propose against.
    if not request.project_id:
        return RoutingDecision(kind=RouteKind.ORDINARY, source=RouteSource.NO_PROJECT)

    # 5. Only now does the classifier get a say.
    result = classify_request(
        request.text,
        context=ClassifierContext(
            is_new_conversation=request.is_new_conversation,
            project_cycle_count=request.project_cycle_count,
            has_unfinished_cycles=request.has_unfinished_cycles,
        ),
    )
    kind = RouteKind.PROPOSAL if result.decision is ClassifierDecision.PROPOSE_CYCLE else RouteKind.ORDINARY
    return RoutingDecision(kind=kind, source=RouteSource.CLASSIFIER, classifier=result)
