"""The durable DBTL cycle state machine.

Five stages run in order, and each is gated by a human review:

    design → reconciliation → [ready_for_build] → build → test → learn

``ready_for_build`` is an explicit state rather than an inference, because it
is the thing a reviewer approves. Reaching it requires **two independent
approvals** — Design and Data Reconciliation — which is the central Phase 3
rule: a design that nobody has reconciled against real data must not be
buildable.

Everything here is pure and immutable. The same functions answer "may this
happen?" for a human clicking a button today and for the Supervisor Graph
later, so the two can never drift into different notions of a legal cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

STAGE_ORDER: tuple[str, ...] = ("design", "reconciliation", "build", "test", "learn")

CYCLE_STATES: tuple[str, ...] = (
    "design",
    "reconciliation",
    "ready_for_build",
    "build",
    "test",
    "learn",
    "completed",
    "abandoned",
)

TERMINAL_CYCLE_STATES: frozenset[str] = frozenset({"completed", "abandoned"})

# States that are checkpoints between stages rather than stages themselves.
_NON_STAGE_STATES: frozenset[str] = frozenset({"ready_for_build", *TERMINAL_CYCLE_STATES})


class TransitionRefused(ValueError):
    """A requested move is not legal from the current state.

    Refusal is always explicit: the machine never falls back to a default
    state, because guessing here would silently advance a research record.
    """


class CycleClass(StrEnum):
    """What kind of research cycle this is."""

    SEASON_PROGRAM = "season/program"
    COMPUTATIONAL = "computational"
    OTHER = "other"


class StageStatus(StrEnum):
    """Where one stage stands."""

    LOCKED = "locked"
    IN_PROGRESS = "in_progress"
    AWAITING_REVIEW = "awaiting_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewDecision(StrEnum):
    """The three verdicts a reviewer may return, each requiring a rationale."""

    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


# Only a stage that has been submitted may be judged. Reviewing a locked or
# in-progress stage would let an approval arrive out of band.
_REVIEWABLE_STATUSES: frozenset[StageStatus] = frozenset({StageStatus.AWAITING_REVIEW})

_REVIEW_RESULT: Mapping[ReviewDecision, StageStatus] = MappingProxyType(
    {
        ReviewDecision.APPROVE: StageStatus.APPROVED,
        ReviewDecision.REQUEST_CHANGES: StageStatus.CHANGES_REQUESTED,
        ReviewDecision.REJECT: StageStatus.REJECTED,
    }
)

# Stages whose approval a target stage depends on. Build depends on *both*
# earlier approvals; the rest depend only on their immediate predecessor.
_STAGE_PREREQUISITES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "design": (),
        "reconciliation": ("design",),
        "build": ("design", "reconciliation"),
        "test": ("build",),
        "learn": ("test",),
    }
)

# The cycle state a stage must be reachable from.
_ENTRY_STATES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "design": frozenset({"design"}),
        "reconciliation": frozenset({"design", "reconciliation"}),
        "build": frozenset({"ready_for_build", "build"}),
        "test": frozenset({"build", "test"}),
        "learn": frozenset({"test", "learn"}),
    }
)

# Forward moves, each keyed by the stage approvals it requires.
_FORWARD_TRANSITIONS: Mapping[str, tuple[str, tuple[str, ...]]] = MappingProxyType(
    {
        "design": ("reconciliation", ("design",)),
        "reconciliation": ("ready_for_build", ("design", "reconciliation")),
        "ready_for_build": ("build", ("design", "reconciliation")),
        "build": ("test", ("build",)),
        "test": ("learn", ("test",)),
        "learn": ("completed", ("learn",)),
    }
)

# The same three tables with Data Reconciliation lifted out of the path, used
# when a deployment sets ``dbtl.reconciliation_required = false``. Design
# approval then opens Build directly.
#
# Reconciliation is *skipped*, not deleted: the stage row still exists, its
# endpoints still work, and a project may still declare datasets and settle
# rows. What changes is only whether Build waits for it. Expressing that as a
# second set of tables rather than as branches inside the functions keeps the
# legal moves readable as data — the property this module exists to make
# checkable.
_STAGE_PREREQUISITES_WITHOUT_RECONCILIATION: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "design": (),
        "reconciliation": ("design",),
        "build": ("design",),
        "test": ("build",),
        "learn": ("test",),
    }
)

_ENTRY_STATES_WITHOUT_RECONCILIATION: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "design": frozenset({"design"}),
        "reconciliation": frozenset({"design", "reconciliation"}),
        "build": frozenset({"ready_for_build", "build"}),
        "test": frozenset({"build", "test"}),
        "learn": frozenset({"test", "learn"}),
    }
)

_FORWARD_TRANSITIONS_WITHOUT_RECONCILIATION: Mapping[str, tuple[str, tuple[str, ...]]] = MappingProxyType(
    {
        "design": ("ready_for_build", ("design",)),
        # Retained so a cycle that *did* work reconciliation before the flag
        # changed still has a legal move forward. A cycle mid-flight must not
        # become unadvanceable because an operator flipped a switch.
        "reconciliation": ("ready_for_build", ("design",)),
        "ready_for_build": ("build", ("design",)),
        "build": ("test", ("build",)),
        "test": ("learn", ("test",)),
        "learn": ("completed", ("learn",)),
    }
)


def _tables(reconciliation_required: bool):
    """The three legal-move tables for this deployment's reconciliation rule."""
    if reconciliation_required:
        return _STAGE_PREREQUISITES, _ENTRY_STATES, _FORWARD_TRANSITIONS
    return (
        _STAGE_PREREQUISITES_WITHOUT_RECONCILIATION,
        _ENTRY_STATES_WITHOUT_RECONCILIATION,
        _FORWARD_TRANSITIONS_WITHOUT_RECONCILIATION,
    )


def validate_cycle_class(value: str) -> CycleClass:
    """Return the :class:`CycleClass` for *value*, or raise ``ValueError``."""
    try:
        return CycleClass(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in CycleClass)
        raise ValueError(f"Unknown cycle class {value!r}; expected one of: {allowed}") from exc


def is_terminal(state: str) -> bool:
    """True when no further transition is possible."""
    return state in TERMINAL_CYCLE_STATES


def stage_for_state(state: str) -> str | None:
    """The stage being worked in *state*, or ``None`` for a checkpoint state."""
    if state in _NON_STAGE_STATES or state not in CYCLE_STATES:
        return None
    return state


def initial_stage_statuses() -> dict[str, StageStatus]:
    """A fresh cycle: Design is open, everything after it is locked."""
    return {stage: (StageStatus.IN_PROGRESS if stage == STAGE_ORDER[0] else StageStatus.LOCKED) for stage in STAGE_ORDER}


def _approved(statuses: Mapping[str, StageStatus], stages: tuple[str, ...]) -> bool:
    return all(statuses.get(stage) is StageStatus.APPROVED for stage in stages)


def can_enter_stage(
    stage: str,
    current_state: str,
    statuses: Mapping[str, StageStatus],
    *,
    reconciliation_required: bool = True,
) -> bool:
    """Whether *stage* may be worked, given the cycle state and approvals.

    Both conditions must hold: every prerequisite stage is approved, and the
    cycle is in a state from which this stage is reachable. Checking only the
    approvals would let a completed cycle re-open a stage.
    """
    prerequisites, entry_states, _forward = _tables(reconciliation_required)
    stage_prerequisites = prerequisites.get(stage)
    if stage_prerequisites is None or is_terminal(current_state):
        return False
    if not _approved(statuses, stage_prerequisites):
        return False
    return current_state in entry_states[stage]


def next_cycle_state(
    current_state: str,
    statuses: Mapping[str, StageStatus],
    *,
    reconciliation_required: bool = True,
) -> str:
    """The single legal forward state, or raise :class:`TransitionRefused`."""
    _prerequisites, _entry_states, forward = _tables(reconciliation_required)
    move = forward.get(current_state)
    if move is None:
        raise TransitionRefused(f"No forward transition exists from state {current_state!r}.")
    target, required = move
    if not _approved(statuses, required):
        missing = [stage for stage in required if statuses.get(stage) is not StageStatus.APPROVED]
        raise TransitionRefused(f"Cannot advance to {target!r}: awaiting approval of {', '.join(missing)}.")
    return target


def apply_review(
    statuses: Mapping[str, StageStatus],
    stage: str,
    decision: ReviewDecision,
    *,
    reconciliation_required: bool = True,
) -> dict[str, StageStatus]:
    """Return a **new** status map with *decision* applied to *stage*.

    An approval also opens the next stage, but only that one — approving
    Design must not make Build workable while Reconciliation is outstanding.
    When reconciliation is not required, the successor of Design is Build, and
    reconciliation is stepped over rather than opened: opening a stage nothing
    waits for would leave every cycle showing permanent outstanding work.
    """
    if stage not in STAGE_ORDER:
        raise TransitionRefused(f"Unknown stage {stage!r}.")
    current = statuses.get(stage)
    if current not in _REVIEWABLE_STATUSES:
        raise TransitionRefused(f"Stage {stage!r} is {current} and is not awaiting review.")

    prerequisites, _entry_states, _forward = _tables(reconciliation_required)
    updated = dict(statuses)
    updated[stage] = _REVIEW_RESULT[decision]

    if decision is ReviewDecision.APPROVE:
        successor = _successor_stage(stage, reconciliation_required=reconciliation_required)
        if successor is not None and _approved(updated, prerequisites[successor]) and updated[successor] is StageStatus.LOCKED:
            updated[successor] = StageStatus.IN_PROGRESS
    return updated


def _successor_stage(stage: str, *, reconciliation_required: bool) -> str | None:
    """The stage an approval of *stage* opens, if any."""
    index = STAGE_ORDER.index(stage)
    for candidate in STAGE_ORDER[index + 1 :]:
        if candidate == "reconciliation" and not reconciliation_required:
            continue
        return candidate
    return None
