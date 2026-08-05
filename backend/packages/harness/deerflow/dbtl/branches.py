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

The mapping from a route to a branch is deliberately thin. Anything that could
become a cycle goes to the confirmation branch first — the human decides
whether a durable research record should exist *before* being asked to describe
one. The gaps the classifier noticed ride along on the decision and are raised
after approval, by :mod:`deerflow.dbtl.setup_questions`, as questions the model
has already proposed answers to.

``CLARIFICATION`` is therefore no longer reachable from :func:`resolve_branch`:
it is the post-approval step, and only the graph can tell that approval has
happened, because that fact lives in an answered card rather than in the
request text this module sees.
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
    DISCOVERY = "discovery"


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
    is_new_conversation: bool = False
    project_cycle_count: int | None = None
    has_unfinished_cycles: bool | None = None
    #: The live cycle this conversation opened, resolved server-side from the
    #: durable record. Lets an unscoped "run the meeting again" reach the stage
    #: it obviously means, without making the composer's one-request scope
    #: sticky. See :mod:`deerflow.dbtl.routing` rung 2b.
    thread_cycle_id: str | None = None
    discovery_enabled: bool = False
    active_discovery_id: str | None = None
    discovery_classifier_entry: bool = False
    discovery_suppressed: bool = False
    discovery_auto_offer: bool = False
    policy_version: str = "greenagent-dbtl-v2-draft"


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
            is_new_conversation=context.is_new_conversation,
            project_cycle_count=context.project_cycle_count,
            has_unfinished_cycles=context.has_unfinished_cycles,
            thread_cycle_id=context.thread_cycle_id,
            discovery_enabled=context.discovery_enabled,
            active_discovery_id=context.active_discovery_id,
            discovery_classifier_entry=context.discovery_classifier_entry,
            discovery_suppressed=context.discovery_suppressed,
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

    if route.kind is RouteKind.DISCOVERY:
        return BranchDecision(branch=SupervisorBranch.DISCOVERY, route=route)

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

    # Confirmation comes first, always. Asking for details before asking
    # whether to start a cycle at all inverts the human gate: it makes someone
    # fill in a research record's fields to find out they are being offered a
    # research record. The gaps are still carried on the decision — they seed
    # the questions raised *after* approval, which is where answering them is
    # work the person has already agreed to do.
    return BranchDecision(
        branch=SupervisorBranch.CYCLE_SETUP,
        route=route,
        objective=objective,
        missing_fields=tuple(missing),
        cycle_id=route.cycle_id,
    )
