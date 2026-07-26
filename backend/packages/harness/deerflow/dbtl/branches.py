"""Which terminal branch of the supervisor one request belongs to (Phase 5).

Pure, so the supervisor graph and any future caller resolve a branch the same
way, and so the interesting question — *did routing send this to the right
place?* — is answerable without building a graph or a checkpointer.

It is a thin layer over :mod:`deerflow.dbtl.routing` on purpose. Phase 4 already
settled the precedence ladder (explicit choice, then a typed request, then the
selected cycle, then the project, and only then the classifier), and the
Upgrade Proposal card is built from it. A supervisor that re-derived that order
would be a second opinion about the same question, and the two would drift; the
first symptom would be a card offering one thing while the graph did another.

The mapping from a route to a branch adds exactly one decision: whether enough
is known to show a confirmation at all. A request to start a cycle that names
no trait, season, population, or validation criterion cannot produce an
honest confirmation summary, so it becomes a question instead of a dialog with
blanks in it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from deerflow.dbtl.classifier import derive_objective, missing_clarification_fields
from deerflow.dbtl.routing import (
    ExplicitChoice,
    RouteKind,
    RoutingDecision,
    RoutingRequest,
    route_request,
)


class SupervisorBranch(StrEnum):
    """The supervisor's terminal branches.

    Every branch is terminal: the supervisor routes once per request and then
    the graph ends. It is not a planner and does not loop between branches,
    which is what keeps "thin" true and keeps one request from silently
    becoming several.
    """

    ORDINARY = "ordinary"
    CLARIFICATION = "clarification"
    CYCLE_SETUP = "cycle_setup"
    CYCLE_CONTINUATION = "cycle_continuation"


SUPERVISOR_BRANCHES: tuple[SupervisorBranch, ...] = tuple(SupervisorBranch)


@dataclass(frozen=True, slots=True)
class SupervisorContext:
    """The runtime context routing is allowed to see.

    Deliberately small and explicit. ``project_id`` and ``selected_cycle_id``
    decide which folder and which research record a request can touch, so they
    are passed as values rather than read from ambient state.
    """

    project_id: str | None
    project_name: str = ""
    selected_cycle_id: str | None = None
    explicit_choice: ExplicitChoice | None = None


@dataclass(frozen=True, slots=True)
class BranchDecision:
    """Where one request goes, and what the branch needs to say.

    Carries no authority to create anything: see :attr:`creates_record`.
    """

    branch: SupervisorBranch
    route: RoutingDecision
    objective: str = ""
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    cycle_id: str | None = None

    @property
    def creates_record(self) -> bool:
        """Always ``False``.

        A property rather than a field so "Phase 5 writes nothing" is a
        property of the type instead of a value someone can pass in. Cycle
        creation is Phase 3's authenticated endpoint, reached only after a
        human confirms; nothing on this path may anticipate that.
        """
        return False


def resolve_branch(text: str, context: SupervisorContext) -> BranchDecision:
    """Resolve one request to a terminal branch. Pure and side-effect free."""
    route = route_request(
        RoutingRequest(
            text=text,
            project_id=context.project_id,
            selected_cycle_id=context.selected_cycle_id,
            explicit_choice=context.explicit_choice,
        )
    )

    if route.kind is RouteKind.ORDINARY:
        return BranchDecision(branch=SupervisorBranch.ORDINARY, route=route)

    if route.kind is RouteKind.CYCLE_CONTINUATION:
        return BranchDecision(
            branch=SupervisorBranch.CYCLE_CONTINUATION,
            route=route,
            cycle_id=route.cycle_id,
        )

    # CYCLE_SETUP and PROPOSAL both mean "this could become a cycle", and both
    # are gated on the same human confirmation. Reuse the classifier's own
    # findings when it ran; recompute them the same way when routing was
    # deterministic and it did not.
    if route.classifier is not None:
        missing = route.classifier.missing_fields
        objective = route.classifier.proposed_objective
    else:
        missing = missing_clarification_fields(text)
        objective = derive_objective(text)

    # A classifier-sourced proposal keeps its card even with gaps — Phase 4's
    # card already opens its own clarification step, and interrupting an
    # unasked-for suggestion with a bare question would be worse. An explicit
    # request, by contrast, is someone waiting on an answer.
    needs_clarification = bool(missing) and route.kind is RouteKind.CYCLE_SETUP
    branch = SupervisorBranch.CLARIFICATION if needs_clarification else SupervisorBranch.CYCLE_SETUP

    return BranchDecision(
        branch=branch,
        route=route,
        objective=objective,
        missing_fields=tuple(missing),
        cycle_id=route.cycle_id,
    )
