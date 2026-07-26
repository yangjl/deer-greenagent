"""The no-record DBTL upgrade proposal contract (Phase 4).

A proposal is an *offer*, and this module is where that stays true. It is a
frozen value object with no field that could say "created", built from a
routing decision and nothing else. There is no repository here to write with —
pinned by a test that reads this module's own source — so the phase's no-go,
"classifier confidence can bypass confirmation", has no expressible form.

The wording lives here rather than in the frontend because it is part of the
contract the human exit review approves: the reviewer signs off on the exact
sentence a user reads before deciding.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from deerflow.dbtl.classifier import ConfidenceBand
from deerflow.dbtl.routing import RouteKind, RoutingDecision

NO_RECORD_NOTICE = "No cycle has been created yet."

CONFIRMATION_REQUIRED_NOTICE = "Starting this cycle creates a durable research record. Nothing is written until you confirm."

# Every gate a human must pass before Build, named on the confirmation so the
# commitment is visible before it is made rather than discovered later.
REQUIRED_GATES: tuple[str, ...] = ("Design", "Data reconciliation")

RECORD_EFFECT = "Creates a durable research record in this project: it appears in the activity log with your name, and is retired rather than deleted."

_PROPOSABLE_KINDS = frozenset({RouteKind.PROPOSAL, RouteKind.CYCLE_SETUP, RouteKind.CYCLE_CONTINUATION})
_EXPLICIT_SETUP_FIELDS: tuple[str, ...] = (
    "research objective",
    "target trait",
    "season range",
    "validation expectation",
    "population scope",
)


class ProposalOutcome(StrEnum):
    """What the human did with a proposal.

    ``DISMISSED`` is a real outcome rather than the absence of one: a card
    ignored or closed is exactly the signal the false-upgrade rate is made of,
    and it would be invisible if only the three buttons were recorded.
    """

    START_SETUP = "start_setup"
    KEEP_ORDINARY = "keep_ordinary"
    NOT_SURE = "not_sure"
    DISMISSED = "dismissed"
    CONTINUE_CYCLE = "continue_cycle"


@dataclass(frozen=True, slots=True)
class UpgradeProposal:
    """An offer to upgrade ordinary work into a research cycle.

    ``creates_record`` is a constant ``False``. It exists to be read — by the
    API schema, by the card, and by a reviewer — not to be set.
    """

    kind: RouteKind
    proposed_objective: str
    missing_fields: tuple[str, ...]
    band: ConfidenceBand
    confidence: float
    project_name: str
    cycle_id: str | None = None
    creates_record: bool = False
    requires_confirmation: bool = True
    notice: str = NO_RECORD_NOTICE


@dataclass(frozen=True, slots=True)
class ConfirmationSummary:
    """What the final confirmation step shows before anything is written."""

    project_name: str
    cycle_class: str
    objective: str
    required_gates: tuple[str, ...]
    record_effect: str
    notice: str = CONFIRMATION_REQUIRED_NOTICE


def build_proposal(decision: RoutingDecision, *, project_name: str) -> UpgradeProposal | None:
    """Build the offer a routing decision justifies, or ``None``.

    Ordinary routing returns ``None`` rather than an empty proposal, so a
    caller cannot accidentally render a card for work nobody flagged.
    """
    if decision.kind not in _PROPOSABLE_KINDS:
        return None

    classifier = decision.classifier
    missing_fields = classifier.missing_fields if classifier else ()
    if decision.kind is RouteKind.CYCLE_SETUP and classifier is None:
        # Explicit "start a DBTL cycle" bypasses classification by design,
        # but setup must still collect the scientific intent before the final
        # confirmation can create a record.
        missing_fields = _EXPLICIT_SETUP_FIELDS
    return UpgradeProposal(
        kind=decision.kind,
        proposed_objective=classifier.proposed_objective if classifier else "",
        missing_fields=missing_fields,
        band=classifier.band if classifier else ConfidenceBand.LOW,
        confidence=classifier.confidence if classifier else 0.0,
        project_name=project_name,
        cycle_id=decision.cycle_id,
    )


def confirmation_summary(proposal: UpgradeProposal, *, cycle_class: str) -> ConfirmationSummary:
    """The last screen before a durable record exists."""
    return ConfirmationSummary(
        project_name=proposal.project_name,
        cycle_class=cycle_class,
        objective=proposal.proposed_objective,
        required_gates=REQUIRED_GATES,
        record_effect=RECORD_EFFECT,
    )
