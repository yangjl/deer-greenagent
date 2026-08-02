"""Runtime agent-activity projection: who is working, and under whose authority.

This package owns the presence-and-lineage stream the project rail renders. It
is deliberately not an observability console: the envelope's field set is closed
and validated (see :mod:`~deerflow.runtime.activity.envelope`), so a prompt,
chain-of-thought, tool argument, shell output, secret, or host path has no
channel through which it could reach a screen.
"""

from deerflow.runtime.activity.envelope import (
    ACTIVITY_ENVELOPE_VERSION,
    ACTIVITY_EVENT_CATEGORY,
    ACTIVITY_EVENT_TYPE,
    ACTIVITY_STREAM_NAME,
    ActivityScope,
    build_activity_event,
)
from deerflow.runtime.activity.run_event import activity_run_event
from deerflow.runtime.activity.vocabulary import (
    OPERATION_LABELS,
    RUNTIME_ROLE_ACTORS,
    TERMINAL_STATES,
    TERMINAL_TRANSITIONS,
    ActivityState,
    ActivityTransition,
    ActorKind,
    resolve_display_name,
    stage_label,
)

__all__ = [
    "ACTIVITY_ENVELOPE_VERSION",
    "ACTIVITY_EVENT_CATEGORY",
    "ACTIVITY_EVENT_TYPE",
    "ACTIVITY_STREAM_NAME",
    "OPERATION_LABELS",
    "RUNTIME_ROLE_ACTORS",
    "TERMINAL_STATES",
    "TERMINAL_TRANSITIONS",
    "ActivityScope",
    "ActivityState",
    "ActivityTransition",
    "ActorKind",
    "activity_run_event",
    "build_activity_event",
    "resolve_display_name",
    "stage_label",
]
