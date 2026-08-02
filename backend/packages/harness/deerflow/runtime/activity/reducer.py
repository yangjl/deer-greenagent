"""Fold a stream of activity transitions into the set of rows a reader sees.

This is the consumer half of the contract the producer already obeys, written
once here so the durable projection, a reconnecting client, and the eventual
rail cannot each invent their own answer to "what does this sequence mean?".
It is pure: no store, no clock, no IO.

Three rules carry the weight, and each exists because of a specific way a live
stream lies to whoever is reading it.

**Replay is not new information.** A reconnect re-delivers events the reader has
already folded, and a durable backfill overlaps the live tail by design. So
reduction is idempotent: folding a sequence twice, or folding a prefix and then
the whole thing, yields the same rows. Nothing here counts, appends, or
accumulates.

**A settled row is closed for good.** Once a terminal transition lands, later
frames for that id are refused — including another ``started``. A row that could
reopen is a spinner that returns after the work finished, and the id is the
thing being reused; the producer already mints a fresh id per invocation, so a
repeated id is either a replay (harmless, refused) or a bug (visible, refused).

**A row must be opened before it can be updated.** An ``updated`` frame for an
unknown id is dropped rather than promoted into a row. Mid-stream joins are
normal — a client subscribing late sees updates whose opening it missed — and
inventing a row from one would show work whose beginning, parent, and actor the
reader never learned. Backfill supplies the opening; guessing does not.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Final

from deerflow.runtime.activity.envelope import parse_activity_event
from deerflow.runtime.activity.vocabulary import (
    TERMINAL_TRANSITIONS,
    ActivityState,
    ActivityTransition,
    ActorKind,
)

_TERMINAL_TRANSITION_VALUES: Final[frozenset[str]] = frozenset(member.value for member in TERMINAL_TRANSITIONS)


@dataclass(frozen=True, slots=True)
class ActivityRow:
    """One actor's current presence, as folded from its transitions."""

    activity_id: str
    run_id: str
    actor_kind: ActorKind
    actor_id: str
    display_name: str
    state: ActivityState
    operation: str
    parent_activity_id: str | None = None
    dispatcher_activity_id: str | None = None
    cycle_id: str | None = None
    stage: str | None = None
    task_id: str | None = None
    last_seq: int | None = None

    @property
    def settled(self) -> bool:
        """Whether this row has closed and can no longer change."""
        return self.state in {ActivityState.COMPLETED, ActivityState.FAILED, ActivityState.CANCELLED, ActivityState.INTERRUPTED}

    @property
    def active(self) -> bool:
        return not self.settled


def _persisted_payload(event: object) -> tuple[dict[str, Any] | None, int | None]:
    """Return a canonical payload and its durable sequence, when present."""
    if isinstance(event, Mapping):
        seq = event.get("seq")
        content = event.get("content")
        if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0 and isinstance(content, Mapping):
            return parse_activity_event(content), seq
    return parse_activity_event(event), None


def _row_from_payload(payload: Mapping[str, Any], *, seq: int | None = None) -> ActivityRow:
    scope = payload["scope"]
    return ActivityRow(
        activity_id=payload["activity_id"],
        run_id=payload["run_id"],
        actor_kind=ActorKind(payload["actor_kind"]),
        actor_id=payload["actor_id"],
        display_name=payload["display_name"],
        state=ActivityState(payload["state"]),
        operation=payload["operation"],
        parent_activity_id=payload["parent_activity_id"],
        dispatcher_activity_id=payload["dispatcher_activity_id"],
        cycle_id=scope.get("cycle_id"),
        stage=scope.get("stage"),
        task_id=scope.get("task_id"),
        last_seq=seq,
    )


def apply_activity_event(rows: Mapping[str, ActivityRow], event: object) -> dict[str, ActivityRow]:
    """Fold one event into ``rows`` and return the new mapping.

    Immutable by construction — the input mapping is never modified, so a caller
    holding an earlier snapshot keeps it. Insertion order is the order rows were
    first opened, which is the order a reader watched the work begin in.
    """
    payload, seq = _persisted_payload(event)
    if payload is None:
        return dict(rows)

    activity_id = payload["activity_id"]
    existing = rows.get(activity_id)
    transition = payload["transition"]

    if existing is not None and seq is not None and existing.last_seq is not None and seq <= existing.last_seq:
        # A reconnect or overlapping page can deliver an older durable record
        # after a newer one. Sequence is server authority; arrival order is not.
        return dict(rows)

    if existing is not None and existing.settled:
        # Refused, not applied: a closed row is a fact about work that ended.
        return dict(rows)

    if transition == ActivityTransition.STARTED.value:
        if existing is not None:
            # A replayed opening for a row already open changes nothing; taking
            # the newer payload would let a stale replay overwrite a live state.
            return dict(rows)
        updated = dict(rows)
        updated[activity_id] = _row_from_payload(payload, seq=seq)
        return updated

    if existing is None:
        # An update or a close for a row this reader never saw open. Backfill
        # supplies the opening; a synthesised row would misreport its lineage.
        return dict(rows)

    updated = dict(rows)
    updated[activity_id] = replace(
        existing,
        state=ActivityState(payload["state"]),
        operation=payload["operation"],
        last_seq=seq if seq is not None else existing.last_seq,
    )
    return updated


def reduce_activity_events(events: Iterable[object], *, rows: Mapping[str, ActivityRow] | None = None) -> dict[str, ActivityRow]:
    """Fold a sequence of events, optionally continuing from an earlier snapshot."""
    current: dict[str, ActivityRow] = dict(rows or {})
    for event in events:
        current = apply_activity_event(current, event)
    return current


def active_rows(rows: Mapping[str, ActivityRow]) -> list[ActivityRow]:
    """Return the rows still working, in the order they opened."""
    return [row for row in rows.values() if row.active]


def active_leaves(rows: Mapping[str, ActivityRow]) -> list[ActivityRow]:
    """Return the active rows that have no active child.

    What a reader wants from "who is working now" is the innermost answer: a
    supervisor that has dispatched a stage is waiting, and naming it instead of
    the stage describes the tree rather than the work.
    """
    parents = {row.parent_activity_id for row in rows.values() if row.active and row.parent_activity_id}
    return [row for row in active_rows(rows) if row.activity_id not in parents]


def close_open_rows(rows: Mapping[str, ActivityRow], *, state: ActivityState = ActivityState.INTERRUPTED) -> dict[str, ActivityRow]:
    """Settle every still-open row.

    A run ends whether or not each actor got to say so — a lease loss, a hard
    cancellation, or a process death leaves rows that nothing will ever close.
    The honest terminal there is ``interrupted``: the work neither finished nor
    demonstrably failed, it stopped being observed.
    """
    return {activity_id: (row if row.settled else replace(row, state=state)) for activity_id, row in rows.items()}
