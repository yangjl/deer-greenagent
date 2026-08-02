"""The agent-activity event envelope.

One concept, two names, matching the existing ``task_started`` /
``subagent.start`` precedent: :data:`ACTIVITY_STREAM_NAME` is what
``emit_custom_event`` dispatches on and what ``astream_events`` consumers match,
while :data:`ACTIVITY_EVENT_TYPE` is the persisted catalog name carrying the
storage category. Both are introduced together, because a projection and a
replay that describe different things are worse than either alone.

The envelope describes a *transition*, not a transcript. Its field set is closed
and every value is validated, so there is no channel through which a prompt,
chain-of-thought, tool argument, shell output, secret, or host path could reach
it — that is a property of the constructor rather than a rule someone has to
remember.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from deerflow.constants import (
    AGENT_ACTIVITY_EVENT_CATEGORY,
    AGENT_ACTIVITY_EVENT_TYPE,
    AGENT_ACTIVITY_STREAM_NAME,
)
from deerflow.runtime.activity.vocabulary import (
    OPERATION_LABELS,
    TERMINAL_STATES,
    TERMINAL_TRANSITIONS,
    ActivityState,
    ActivityTransition,
    ActorKind,
    resolve_display_name,
)

ACTIVITY_STREAM_NAME: Final[str] = AGENT_ACTIVITY_STREAM_NAME
ACTIVITY_EVENT_TYPE: Final[str] = AGENT_ACTIVITY_EVENT_TYPE
ACTIVITY_EVENT_CATEGORY: Final[str] = AGENT_ACTIVITY_EVENT_CATEGORY

ACTIVITY_ENVELOPE_VERSION: Final[int] = 1

MAX_DISPLAY_NAME_CHARS: Final[int] = 80
MAX_OPERATION_CHARS: Final[int] = 120
MAX_IDENTIFIER_CHARS: Final[int] = 128

#: The rendered operation labels as a closed set. The wire carries the label a
#: key resolved to, not the key itself, so re-validating an inbound payload
#: matches against the rendered values; which key produced a label does not
#: matter, that the label is one this server can produce does.
OPERATION_VALUES: Final[frozenset[str]] = frozenset(OPERATION_LABELS.values())

#: Every field a persisted activity record may carry. Anything else is dropped
#: at the parse boundary rather than stored.
ACTIVITY_EVENT_FIELDS: Final[tuple[str, ...]] = (
    "type",
    "version",
    "transition",
    "activity_id",
    "parent_activity_id",
    "dispatcher_activity_id",
    "run_id",
    "actor_kind",
    "actor_id",
    "display_name",
    "state",
    "operation",
    "scope",
)

ACTIVITY_SCOPE_FIELDS: Final[tuple[str, ...]] = ("cycle_id", "stage", "task_id")


@dataclass(frozen=True, slots=True)
class ActivityScope:
    """Identifiers needed to reconcile an activity with existing surfaces.

    Deliberately not a general metadata bag: only what the task ledger, the
    cycle rail, and the stage sheet already key on.
    """

    cycle_id: str | None = None
    stage: str | None = None
    task_id: str | None = None

    def as_payload(self) -> dict[str, str | None]:
        return {
            "cycle_id": _identifier_or_none(self.cycle_id),
            "stage": _identifier_or_none(self.stage),
            "task_id": _identifier_or_none(self.task_id),
        }


#: Identifier fields name things — an activity, a run, an actor, a cycle, a
#: stage, a work unit. They are never free text, so they are constrained by
#: shape and not merely by length: 128 characters is ample room for a truncated
#: credential, a host path, or a fragment of a tool argument to ride along in a
#: field nobody thinks of as a text field. ``display_name`` is deliberately
#: outside this rule; carrying a person-readable label is its whole job.
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._:@-]+$")


def _identifier_or_none(value: object) -> str | None:
    """Return a valid identifier, or ``None`` for absent *and* for malformed.

    Optional lineage degrades to ``None``: losing which activity dispatched this
    one costs a line of the tree, while refusing the whole event costs the row.
    """
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed or len(trimmed) > MAX_IDENTIFIER_CHARS or not _IDENTIFIER_PATTERN.match(trimmed):
        return None
    return trimmed


def _require_identifier(value: object, field: str) -> str:
    resolved = _identifier_or_none(value)
    if resolved is None:
        raise ValueError(f"agent activity {field} must be a bounded identifier, got {type(value).__name__}")
    return resolved


def _require_terminal_agreement(transition: ActivityTransition, state: ActivityState) -> None:
    """Refuse a transition and state that disagree about whether work has ended."""
    is_terminal_transition = transition in TERMINAL_TRANSITIONS
    is_terminal_state = state in TERMINAL_STATES
    if is_terminal_transition != is_terminal_state:
        raise ValueError(f"terminal mismatch: transition {transition.value!r} with state {state.value!r}")
    if is_terminal_transition and transition.value != state.value:
        raise ValueError(f"terminal mismatch: transition {transition.value!r} does not match state {state.value!r}")


def build_activity_event(
    *,
    transition: ActivityTransition | str,
    activity_id: str,
    run_id: str,
    actor_kind: ActorKind | str,
    actor_id: str,
    display_name: str,
    state: ActivityState | str,
    operation: str,
    parent_activity_id: str | None = None,
    dispatcher_activity_id: str | None = None,
    scope: ActivityScope | None = None,
) -> dict[str, Any]:
    """Build one validated, JSON-native activity payload.

    Raises ``ValueError`` for anything the projection cannot describe. Callers
    are expected to treat that as a programming error at the instrumentation
    site and to swallow it at the emission boundary — a malformed activity row
    must never end a run.
    """
    try:
        resolved_transition = ActivityTransition(transition)
    except ValueError as exc:
        raise ValueError(f"unknown agent activity transition: {transition!r}") from exc

    try:
        resolved_state = ActivityState(state)
    except ValueError as exc:
        raise ValueError(f"unknown agent activity state: {state!r}") from exc

    try:
        resolved_kind = ActorKind(actor_kind)
    except ValueError as exc:
        raise ValueError(f"unknown agent activity actor_kind: {actor_kind!r}") from exc

    if operation not in OPERATION_LABELS:
        raise ValueError(f"unregistered agent activity operation: {operation!r}")

    _require_terminal_agreement(resolved_transition, resolved_state)

    visible_name = (display_name or "").strip()[:MAX_DISPLAY_NAME_CHARS]
    if not visible_name:
        visible_name = resolve_display_name(resolved_kind)

    return {
        "type": ACTIVITY_STREAM_NAME,
        "version": ACTIVITY_ENVELOPE_VERSION,
        "transition": resolved_transition.value,
        "activity_id": _require_identifier(activity_id, "activity_id"),
        "parent_activity_id": _identifier_or_none(parent_activity_id),
        "dispatcher_activity_id": _identifier_or_none(dispatcher_activity_id),
        "run_id": _require_identifier(run_id, "run_id"),
        "actor_kind": resolved_kind.value,
        "actor_id": _require_identifier(actor_id, "actor_id"),
        "display_name": visible_name,
        "state": resolved_state.value,
        "operation": OPERATION_LABELS[operation][:MAX_OPERATION_CHARS],
        "scope": (scope or ActivityScope()).as_payload(),
    }


def parse_activity_event(payload: object) -> dict[str, Any] | None:
    """Rebuild a canonical activity payload from an untrusted mapping, or return ``None``.

    The constructor's guarantee — that no prompt, tool argument, or secret can
    reach a screen through this envelope — holds only for payloads *it* built.
    Anything crossing a boundary it did not author (a persisted record, a
    replayed frame, a chunk handed over by the stream loop) is rebuilt here from
    an explicit allowlist: every field is re-validated against the same closed
    vocabulary, and a field this projection does not describe is dropped rather
    than carried. Copying the inbound mapping instead would make the allowlist a
    property of whoever emitted it.

    Returns ``None`` for anything that is not a well-formed activity payload,
    because a boundary that raises would turn a malformed frame into a failed
    run.
    """
    if not isinstance(payload, Mapping) or payload.get("type") != ACTIVITY_STREAM_NAME:
        return None

    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return None

    try:
        transition = ActivityTransition(payload.get("transition"))
        state = ActivityState(payload.get("state"))
        actor_kind = ActorKind(payload.get("actor_kind"))
        _require_terminal_agreement(transition, state)
    except ValueError:
        return None

    operation = payload.get("operation")
    if operation not in OPERATION_VALUES:
        return None

    activity_id = _identifier_or_none(payload.get("activity_id"))
    run_id = _identifier_or_none(payload.get("run_id"))
    actor_id = _identifier_or_none(payload.get("actor_id"))
    if activity_id is None or run_id is None or actor_id is None:
        return None

    display_name = payload.get("display_name")
    visible_name = display_name.strip()[:MAX_DISPLAY_NAME_CHARS] if isinstance(display_name, str) else ""
    if not visible_name:
        visible_name = resolve_display_name(actor_kind)

    raw_scope = payload.get("scope")
    scope_source: Mapping[str, Any] = raw_scope if isinstance(raw_scope, Mapping) else {}

    return {
        "type": ACTIVITY_STREAM_NAME,
        "version": version,
        "transition": transition.value,
        "activity_id": activity_id,
        "parent_activity_id": _identifier_or_none(payload.get("parent_activity_id")),
        "dispatcher_activity_id": _identifier_or_none(payload.get("dispatcher_activity_id")),
        "run_id": run_id,
        "actor_kind": actor_kind.value,
        "actor_id": actor_id,
        "display_name": visible_name,
        "state": state.value,
        "operation": operation,
        "scope": {field: _identifier_or_none(scope_source.get(field)) for field in ACTIVITY_SCOPE_FIELDS},
    }
