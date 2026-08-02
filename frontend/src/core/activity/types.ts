/**
 * The runtime agent-activity projection, as the browser sees it.
 *
 * Server-authored throughout: every string here is drawn from a closed
 * vocabulary the backend owns (`deerflow/runtime/activity/vocabulary.py`), so
 * the UI renders values rather than composing them. Unknown values are kept
 * verbatim instead of dropped — a state this client has not been taught yet
 * should degrade to its raw word, never to a blank row.
 */

/** Lifecycle edge one event records. */
export type ActivityTransition =
  | "started"
  | "updated"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

/** Which runtime component a row describes. */
export type ActorKind =
  | "lead_agent"
  | "dbtl_supervisor"
  | "stage_adapter"
  | "subagent"
  | "stage_worker";

/** What that component is honestly doing. */
export type ActivityState =
  | "routing"
  | "preparing"
  | "thinking"
  | "computing"
  | "dispatching"
  | "coordinating"
  | "recording"
  | "waiting"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export const TERMINAL_ACTIVITY_STATES: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
]);

export interface ActivityScope {
  cycle_id: string | null;
  stage: string | null;
  task_id: string | null;
}

/** One `agent_activity` custom event, exactly as the stream delivers it. */
export interface ActivityEvent {
  type: "agent_activity";
  version: number;
  transition: ActivityTransition;
  activity_id: string;
  parent_activity_id: string | null;
  dispatcher_activity_id: string | null;
  run_id: string;
  actor_kind: ActorKind;
  actor_id: string;
  display_name: string;
  state: ActivityState;
  operation: string;
  scope: ActivityScope;
}

/** Durable run-event wrapper returned by the conversation activity endpoint. */
export interface PersistedActivityEvent {
  seq: number;
  created_at?: string | null;
  content: ActivityEvent;
}

/** One actor's current presence, folded from its transitions. */
export interface ActivityRow {
  activityId: string;
  runId: string;
  actorKind: ActorKind;
  actorId: string;
  displayName: string;
  state: ActivityState;
  operation: string;
  parentActivityId: string | null;
  dispatcherActivityId: string | null;
  cycleId: string | null;
  stage: string | null;
  taskId: string | null;
  /** Latest meaningful client arrival. Active-leaf ties use the newest row. */
  seq: number;
  /** Last authoritative durable sequence folded for this row, if any. */
  serverSeq: number | null;
}

/** One entry in the ordered history the expanded surface pages through. */
export interface ActivityTimelineEntry {
  seq: number;
  serverSeq: number | null;
  runId: string;
  activityId: string;
  displayName: string;
  actorKind: ActorKind;
  transition: ActivityTransition;
  state: ActivityState;
  operation: string;
  cycleId: string | null;
  stage: string | null;
  /** Durable creation time when replayed; client receipt time while live. */
  receivedAt: number;
}

export interface ActivityProjection {
  /** Insertion-ordered by first appearance, which is the order work began. */
  rows: Readonly<Record<string, ActivityRow>>;
  order: readonly string[];
  timeline: readonly ActivityTimelineEntry[];
  /** Monotonic arrival counter; also the tie-break for the active leaf. */
  seq: number;
}

export const EMPTY_ACTIVITY: ActivityProjection = {
  rows: {},
  order: [],
  timeline: [],
  seq: 0,
};

export function isTerminalActivityState(state: string): boolean {
  return TERMINAL_ACTIVITY_STATES.has(state);
}
