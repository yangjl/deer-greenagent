/**
 * Fold activity transitions into the rows the rail shows.
 *
 * The same three rules the backend reducer enforces, for the same reasons:
 * replay is not new information, a settled row is closed for good, and a row
 * must be opened before it can be updated. They are restated here rather than
 * trusted from the server because the browser sees a *different* stream — one
 * that reconnects, replays, and joins mid-flight.
 *
 * **Coalescing belongs here, not in the view.** An update that changes neither
 * actor, state, operation, nor lineage returns the *same state object*, so
 * React bails out of the render entirely. Without that, a token-rate stream
 * re-renders the block many times a second to paint an identical spinner — and
 * that is not only wasted work: a remounted `animate-spin` restarts its
 * rotation, so the one moving thing on screen stutters.
 */

import {
  type ActivityEvent,
  type ActivityProjection,
  type ActivityRow,
  type ActivityTimelineEntry,
  EMPTY_ACTIVITY,
  isTerminalActivityState,
} from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

/**
 * Narrow an unknown stream frame to an activity event.
 *
 * Deliberately permissive about *values* and strict about *shape*: the server
 * already validated the vocabulary against a closed field set, so re-checking
 * every enum member here would mean a backend that learns a new state silently
 * loses its rows in every older client. What must be present is the identity a
 * row cannot be folded without.
 */
export function asActivityEvent(frame: unknown): ActivityEvent | null {
  if (!isRecord(frame) || frame.type !== "agent_activity") return null;
  const { activity_id, run_id, transition, state, display_name } = frame;
  if (typeof activity_id !== "string" || !activity_id) return null;
  if (typeof run_id !== "string" || !run_id) return null;
  if (typeof transition !== "string" || !transition) return null;
  if (typeof state !== "string" || !state) return null;
  if (typeof display_name !== "string" || !display_name) return null;
  return frame as unknown as ActivityEvent;
}

interface NormalizedActivityFrame {
  event: ActivityEvent;
  serverSeq: number | null;
  receivedAt: number;
}

function normalizeActivityFrame(
  frame: unknown,
  now: number,
): NormalizedActivityFrame | null {
  if (
    isRecord(frame) &&
    typeof frame.seq === "number" &&
    Number.isInteger(frame.seq) &&
    frame.seq > 0
  ) {
    const event = asActivityEvent(frame.content);
    if (!event) return null;
    const parsedCreatedAt =
      typeof frame.created_at === "string"
        ? Date.parse(frame.created_at)
        : Number.NaN;
    return {
      event,
      serverSeq: frame.seq,
      receivedAt: Number.isNaN(parsedCreatedAt) ? now : parsedCreatedAt,
    };
  }
  const event = asActivityEvent(frame);
  return event ? { event, serverSeq: null, receivedAt: now } : null;
}

function rowFromEvent(
  event: ActivityEvent,
  seq: number,
  serverSeq: number | null,
): ActivityRow {
  const scope = isRecord(event.scope) ? event.scope : {};
  return {
    activityId: event.activity_id,
    runId: event.run_id,
    actorKind: event.actor_kind,
    actorId: event.actor_id,
    displayName: event.display_name,
    state: event.state,
    operation: event.operation,
    parentActivityId: optionalString(event.parent_activity_id),
    dispatcherActivityId: optionalString(event.dispatcher_activity_id),
    cycleId: optionalString((scope as Record<string, unknown>).cycle_id),
    stage: optionalString((scope as Record<string, unknown>).stage),
    taskId: optionalString((scope as Record<string, unknown>).task_id),
    seq,
    serverSeq,
  };
}

function timelineEntry(
  event: ActivityEvent,
  seq: number,
  serverSeq: number | null,
  receivedAt: number,
): ActivityTimelineEntry {
  const scope = isRecord(event.scope)
    ? (event.scope as Record<string, unknown>)
    : {};
  return {
    seq,
    serverSeq,
    runId: event.run_id,
    activityId: event.activity_id,
    displayName: event.display_name,
    actorKind: event.actor_kind,
    transition: event.transition,
    state: event.state,
    operation: event.operation,
    cycleId: optionalString(scope.cycle_id),
    stage: optionalString(scope.stage),
    receivedAt,
  };
}

/** Fold one frame. Returns the *same* projection when nothing changed. */
export function applyActivityEvent(
  projection: ActivityProjection,
  frame: unknown,
  now: number = Date.now(),
): ActivityProjection {
  const normalized = normalizeActivityFrame(frame, now);
  if (!normalized) return projection;
  const { event, serverSeq, receivedAt } = normalized;

  const existing = projection.rows[event.activity_id];

  if (
    existing?.serverSeq !== null &&
    existing?.serverSeq !== undefined &&
    serverSeq !== null &&
    serverSeq <= existing.serverSeq
  ) {
    return projection;
  }

  // A closed row is a fact about work that ended. Refusing here is what stops a
  // replayed opening from putting a finished actor back on screen.
  if (existing && isTerminalActivityState(existing.state)) return projection;

  if (event.transition === "started") {
    // A replayed opening for a row already open changes nothing; taking the
    // newer payload would let a stale replay overwrite a live state.
    if (existing) return projection;
  } else if (!existing) {
    // An update or close for a row this client never saw open. Backfill
    // supplies the opening; a synthesised row would misreport its lineage.
    return projection;
  }

  const seq = projection.seq + 1;
  const next = rowFromEvent(
    event,
    seq,
    serverSeq ?? existing?.serverSeq ?? null,
  );

  if (
    existing?.state === next.state &&
    existing.operation === next.operation &&
    existing.displayName === next.displayName &&
    existing.parentActivityId === next.parentActivityId &&
    existing.dispatcherActivityId === next.dispatcherActivityId
  ) {
    // A newer durable sequence still advances the replay watermark, but does
    // not become a visible history step or change active-leaf recency.
    return serverSeq !== null && serverSeq !== existing.serverSeq
      ? {
          ...projection,
          rows: {
            ...projection.rows,
            [event.activity_id]: {
              ...existing,
              serverSeq,
            },
          },
        }
      : projection;
  }

  const timeline = [
    ...projection.timeline,
    timelineEntry(event, seq, serverSeq, receivedAt),
  ];
  return {
    rows: { ...projection.rows, [event.activity_id]: next },
    order: existing
      ? projection.order
      : [...projection.order, event.activity_id],
    timeline,
    seq,
  };
}

export function reduceActivityEvents(
  frames: readonly unknown[],
  projection: ActivityProjection = EMPTY_ACTIVITY,
  now: number = Date.now(),
): ActivityProjection {
  return frames.reduce<ActivityProjection>(
    (current, frame) => applyActivityEvent(current, frame, now),
    projection,
  );
}

/** Rows still working, in the order they opened. */
export function activeRows(
  projection: ActivityProjection,
): readonly ActivityRow[] {
  const rows: ActivityRow[] = [];
  for (const id of projection.order) {
    const row = projection.rows[id];
    if (row && !isTerminalActivityState(row.state)) rows.push(row);
  }
  return rows;
}

/**
 * The active rows with no active child.
 *
 * "Who is working now" wants the innermost answer: a supervisor that has
 * dispatched a stage is waiting, and naming it describes the tree rather than
 * the work.
 */
export function activeLeaves(
  projection: ActivityProjection,
): readonly ActivityRow[] {
  const active = activeRows(projection);
  const parents = new Set(
    active
      .map((row) => row.parentActivityId)
      .filter((id): id is string => Boolean(id)),
  );
  return active.filter((row) => !parents.has(row.activityId));
}

/** The chain from a row up to its root, nearest ancestor first. */
export function dispatcherChain(
  projection: ActivityProjection,
  row: ActivityRow | undefined,
): readonly ActivityRow[] {
  const chain: ActivityRow[] = [];
  const seen = new Set<string>();
  let current = row?.parentActivityId
    ? projection.rows[row.parentActivityId]
    : undefined;
  while (current && !seen.has(current.activityId)) {
    seen.add(current.activityId);
    chain.push(current);
    current = current.parentActivityId
      ? projection.rows[current.parentActivityId]
      : undefined;
  }
  return chain;
}

/**
 * Settle every still-open row.
 *
 * A run ends whether or not each actor said so — a cancelled run, a lost lease,
 * a dropped connection. `interrupted` is the honest terminal: the work neither
 * finished nor demonstrably failed, it stopped being observed.
 */
export function closeOpenRows(
  projection: ActivityProjection,
  state: ActivityRow["state"] = "interrupted",
): ActivityProjection {
  const open = activeRows(projection);
  if (open.length === 0) return projection;
  const rows = { ...projection.rows };
  for (const row of open) {
    rows[row.activityId] = { ...row, state };
  }
  return { ...projection, rows };
}
