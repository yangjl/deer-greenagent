"""Legal next edges over the DBTL stage graph.

The graph's nodes are **Design, Build, Test, Learn only**. Data readiness /
reconciliation is deliberately not a node: it is dataset-scoped precondition
state that locks Build edges until every required matrix row is settled, so
where Phase 7's post-Test chooser offered "return to reconciliation" as a
destination, this module renders a **blocked Build edge carrying the reason**
instead. The human settles the matrix through the existing reconciliation
surface (outside the path) and the Build edge unlocks; no transition row is
ever written for data work.

Everything here is pure values. The same computation answers "which routes may
the deck offer?" and "which route did this recorded decision take?", so the
menu a person saw and the transition row the audit keeps cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

GRAPH_STAGES: tuple[str, ...] = ("design", "build", "test", "learn")

_NEXT_STAGE: dict[str, str] = {"design": "build", "build": "test", "test": "learn"}

#: Terminal path targets. ``completed`` is reached only through Learn; any
#: stage may close the cycle (recorded today as ``cycle.abandoned``).
COMPLETED = "completed"
ABANDONED = "abandoned"

UNRECONCILED_REASON = "Build is locked until every required reconciliation matrix row is settled."


class StageRoutesRefused(ValueError):
    """The supplied stage or outcome is not part of the stage graph."""


class RouteSlug(StrEnum):
    """Stable ids for the edges a transition deck may offer."""

    ADVANCE = "advance"
    REVISE_HERE = "revise_here"
    RETURN_TO_BUILD = "return_to_build"
    RETURN_TO_DESIGN = "return_to_design"
    PARK = "park"
    CLOSE_CYCLE = "close_cycle"


#: Review verdicts (design/build/learn gates) and computed Test outcomes are
#: both "outcomes" to this module; the caller supplies whichever its stage has.
_REVIEW_OUTCOMES: frozenset[str] = frozenset({"approve", "approved", "request_changes", "changes_requested", "reject", "rejected"})
_TEST_OUTCOMES: frozenset[str] = frozenset({"supported", "not_supported", "inconclusive", "invalidated"})


@dataclass(frozen=True)
class StageRoute:
    """One legal edge, in the ``decision_request`` option shape.

    A blocked route is still returned — the deck must be able to say *why*
    Build is not on offer — but a blocked edge is never choosable.
    """

    slug: str
    to_stage: str
    label: str
    value: str
    blocked: bool = False
    blocked_reason: str = ""

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "slug": self.slug,
            "to_stage": self.to_stage,
            "label": self.label,
            "value": self.value,
        }
        if self.blocked:
            payload["blocked"] = True
            payload["blocked_reason"] = self.blocked_reason
        return payload


@dataclass(frozen=True)
class RouteContext:
    """The durable facts a route menu is computed from."""

    stage: str
    outcome: str | None
    reconciliation_settled: bool = False


def _advance(stage: str, *, reconciliation_settled: bool) -> StageRoute:
    target = _NEXT_STAGE.get(stage, COMPLETED)
    if stage == "learn":
        return StageRoute(RouteSlug.ADVANCE, COMPLETED, "Conclude the cycle", "Record Learn's outcome and close the cycle as completed.")
    blocked = target == "build" and not reconciliation_settled
    return StageRoute(
        RouteSlug.ADVANCE,
        target,
        f"Continue to {target.capitalize()}",
        f"Open {target.capitalize()} as the next stage attempt.",
        blocked=blocked,
        blocked_reason=UNRECONCILED_REASON if blocked else "",
    )


def _revise(stage: str) -> StageRoute:
    return StageRoute(RouteSlug.REVISE_HERE, stage, f"Revise {stage.capitalize()}", f"Run another {stage.capitalize()} attempt in this cycle.")


def _close() -> StageRoute:
    return StageRoute(RouteSlug.CLOSE_CYCLE, ABANDONED, "Close the cycle", "End this cycle; all evidence and history are retained.")


def _park(stage: str) -> StageRoute:
    return StageRoute(
        RouteSlug.PARK,
        stage,
        "Park — work with the lead agent",
        "Keep this cycle open and discuss the bound, explicitly unapproved evidence with the lead agent.",
    )


def _return_to_build(*, reconciliation_settled: bool) -> StageRoute:
    blocked = not reconciliation_settled
    return StageRoute(
        RouteSlug.RETURN_TO_BUILD,
        "build",
        "Run another Build",
        "Add a Build attempt with changed inputs or parameters.",
        blocked=blocked,
        blocked_reason=UNRECONCILED_REASON if blocked else "",
    )


def _return_to_design() -> StageRoute:
    return StageRoute(RouteSlug.RETURN_TO_DESIGN, "design", "Go back to Design", "Reopen Design; downstream approvals from the old revision are invalidated.")


def compute_stage_routes(context: RouteContext) -> tuple[StageRoute, ...]:
    """The legal edges from *context*, blocked edges included.

    An unknown stage or outcome is refused rather than answered: a menu
    computed from a fact this module does not understand would offer edges
    nobody vetted.
    """
    stage = context.stage
    if stage not in GRAPH_STAGES:
        raise StageRoutesRefused(f"Unknown stage {stage!r}; the stage graph is {', '.join(GRAPH_STAGES)}.")

    outcome = context.outcome
    if outcome is None:
        return ()

    if stage == "test":
        if outcome not in _TEST_OUTCOMES:
            raise StageRoutesRefused(f"Unknown Test outcome {outcome!r}.")
        if outcome in {"supported", "not_supported"}:
            return (
                _advance("test", reconciliation_settled=context.reconciliation_settled),
                _revise("test"),
                _close(),
            )
        return (
            _revise("test"),
            _return_to_build(reconciliation_settled=context.reconciliation_settled),
            _return_to_design(),
            _close(),
        )

    if outcome not in _REVIEW_OUTCOMES:
        raise StageRoutesRefused(f"Unknown review outcome {outcome!r} for stage {stage!r}.")
    if outcome in {"approve", "approved"}:
        return (
            _advance(stage, reconciliation_settled=context.reconciliation_settled),
            *(() if stage == "learn" else (_revise(stage),)),
            *(() if stage == "learn" else (_park(stage),)),
            *(() if stage == "learn" else (_close(),)),
        )
    if outcome in {"request_changes", "changes_requested"}:
        return (_revise(stage), _close())
    return (_close(),)


def transition_target(stage: str, chosen_route: str) -> str:
    """The graph node (or terminal state) a recorded decision moved toward.

    This is what a transition row stores as ``to_stage``: the path target of
    the route, never an intermediate machine state — ``ready_for_build`` and
    the reconciliation checkpoint are preconditions, not places the path
    visits.
    """
    if stage not in GRAPH_STAGES:
        raise StageRoutesRefused(f"Unknown stage {stage!r}.")
    route = chosen_route.strip().lower()
    if route in {"approve", "approved", "advance", "advance_to_learn"}:
        return COMPLETED if stage == "learn" else _NEXT_STAGE[stage]
    if route in {"request_changes", "changes_requested", "revise_here", "repeat_test"}:
        return stage
    if route in {"reject", "rejected"}:
        return stage
    if route in {"return_to_build", "return_to_reconciliation"}:
        # Phase 7 offered "return to reconciliation" as a route; the stage
        # graph has no such node, so the recorded target is the Build edge the
        # reconciliation work exists to unlock.
        return "build"
    if route == "return_to_design":
        return "design"
    if route == "park":
        return stage
    if route in {"close_cycle", "abandon"}:
        return ABANDONED
    raise StageRoutesRefused(f"Unknown route {chosen_route!r} from stage {stage!r}.")
