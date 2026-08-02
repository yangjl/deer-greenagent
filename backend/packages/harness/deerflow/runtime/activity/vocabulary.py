"""Closed vocabulary for the runtime agent-activity projection.

Two vocabularies live here and they are deliberately separate. ``ActorKind`` and
``ActivityState`` are the *internal* wire contract, stable across releases and
matched by consumers. ``resolve_display_name`` and ``OPERATION_LABELS`` are the
*visible* strings, and no visible string may be an internal identifier: a class
name, an enum member, or architecture vocabulary such as "adapter". The
council/meeting split already established this pattern — the protocol keeps its
identifiers while the product speaks plain words.
"""

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Final


class ActorKind(StrEnum):
    """Which runtime component an activity row describes."""

    LEAD_AGENT = "lead_agent"
    DBTL_SUPERVISOR = "dbtl_supervisor"
    STAGE_ADAPTER = "stage_adapter"
    SUBAGENT = "subagent"
    STAGE_WORKER = "stage_worker"


class ActivityState(StrEnum):
    """What that component is honestly doing right now.

    The supervisor and the stage adapter are never ``THINKING``: they are
    deterministic routers and coordinators, and a bounded interpretation model
    call inside them stays hidden behind ``ROUTING`` per the DBTL ``nostream``
    contract.
    """

    ROUTING = "routing"
    PREPARING = "preparing"
    THINKING = "thinking"
    COMPUTING = "computing"
    DISPATCHING = "dispatching"
    COORDINATING = "coordinating"
    RECORDING = "recording"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ActivityTransition(StrEnum):
    """Lifecycle edge an event records."""

    STARTED = "started"
    UPDATED = "updated"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_STATES: Final[frozenset[ActivityState]] = frozenset(
    {
        ActivityState.COMPLETED,
        ActivityState.FAILED,
        ActivityState.CANCELLED,
        ActivityState.INTERRUPTED,
    }
)

TERMINAL_TRANSITIONS: Final[frozenset[ActivityTransition]] = frozenset(
    {
        ActivityTransition.COMPLETED,
        ActivityTransition.FAILED,
        ActivityTransition.CANCELLED,
        ActivityTransition.INTERRUPTED,
    }
)

#: Actors that are runtime machinery rather than autonomous models. The UI marks
#: them so the Agents section does not imply that every row is an agent.
RUNTIME_ROLE_ACTORS: Final[frozenset[ActorKind]] = frozenset({ActorKind.DBTL_SUPERVISOR, ActorKind.STAGE_ADAPTER})


#: Server-owned operation templates. ``operation`` is a display label chosen from
#: this table, never arbitrary model prose, which is what keeps a prompt or a
#: tool argument out of the projection by construction.
OPERATION_LABELS: Final[Mapping[str, str]] = {
    "lead.respond": "Working on your request",
    "lead.delegate": "Delegating a subtask",
    "lead.wait": "Waiting for a subtask",
    "supervisor.route": "Selecting the next action",
    "supervisor.dispatch": "Starting the selected work",
    "stage.prepare": "Preparing",
    "stage.coordinate": "Coordinating stage work",
    "stage.record": "Recording evidence",
    "stage.wait_human": "Waiting for your answer",
    "stage.wait_worker": "Waiting for stage work",
    "subagent.run": "Running a delegated task",
    "worker.run": "Running stage work",
    "meeting.participate": "Taking part in the meeting",
}


#: Human-facing stage names. Sourced from the product vocabulary, not the
#: persisted stage keys — ``reconciliation`` is "Data reconciliation" everywhere
#: a person can see it.
_STAGE_LABELS: Final[Mapping[str, str]] = {
    "design": "Design",
    "reconciliation": "Data reconciliation",
    "build": "Build",
    "test": "Test",
    "learn": "Learn",
}

_ACTOR_DEFAULT_NAMES: Final[Mapping[ActorKind, str]] = {
    ActorKind.LEAD_AGENT: "Lead agent",
    ActorKind.DBTL_SUPERVISOR: "Cycle supervisor",
    ActorKind.STAGE_ADAPTER: "Cycle coordinator",
    ActorKind.SUBAGENT: "Subagent",
    ActorKind.STAGE_WORKER: "Cycle worker",
}


#: A registered agent name may become a visible label, so it is shape-checked
#: first. The value comes from the subagent registry (built-ins plus operator
#: config), never from model prose — but the model chooses *which* one to invoke,
#: so an unresolvable or oddly-shaped name falls back to the generic label rather
#: than printing whatever arrived.
_AGENT_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$")


def subagent_label(agent_name: str | None) -> str | None:
    """Return a delegated subagent's visible name, or ``None`` when unusable.

    ``general-purpose`` reads as "General-purpose subagent": the registry name
    carries the meaning a person needs, and inventing a friendlier one would put
    a second vocabulary between the rail and the agent inventory screen.
    """
    if not isinstance(agent_name, str):
        return None
    trimmed = agent_name.strip()
    if not trimmed or not _AGENT_NAME_PATTERN.match(trimmed):
        return None
    readable = trimmed.replace("_", " ")
    readable = readable[:1].upper() + readable[1:]
    if readable.lower().endswith(("agent", "subagent")):
        return readable
    return f"{readable} subagent"


def stage_label(stage: str | None) -> str | None:
    """Return the visible name of a DBTL stage, or ``None`` when unknown."""
    if not isinstance(stage, str):
        return None
    return _STAGE_LABELS.get(stage.strip().lower())


def resolve_display_name(
    actor_kind: ActorKind | str,
    *,
    stage: str | None = None,
    index: int | None = None,
    label: str | None = None,
) -> str:
    """Return the visible name for one actor.

    ``label`` wins when supplied: a delegated subagent and a validated meeting
    seat already have names a person chose or a contract validated, and
    replacing them with a generated one would hide which seat is speaking.

    This never raises. An activity row that cannot be named is still worth
    showing, and instrumentation must not be the reason a run fails.
    """
    try:
        kind = ActorKind(actor_kind)
    except ValueError:
        return "Runtime component"

    chosen = (label or "").strip()
    if chosen:
        return chosen

    visible_stage = stage_label(stage)
    if kind is ActorKind.STAGE_ADAPTER and visible_stage:
        return f"{visible_stage} coordinator"
    if kind is ActorKind.STAGE_WORKER and visible_stage:
        return f"{visible_stage} worker {index}" if isinstance(index, int) else f"{visible_stage} worker"

    return _ACTOR_DEFAULT_NAMES[kind]
