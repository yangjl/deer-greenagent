/**
 * State words for the activity rail.
 *
 * Only the *state* vocabulary lives here. Display names come from the server's
 * own registry — the supervisor is "Cycle supervisor", the adapter is
 * "`<Stage>` stage" — so the browser renders what it is given and cannot drift
 * into a second naming scheme. An unknown state degrades to its raw word rather
 * than blanking the row.
 *
 * Deliberately not in `core/dbtl`: activity describes every conversation,
 * including projectless ones with no cycle anywhere near them.
 */

import type { ActivityRow, ActivityState } from "./types";

export const ACTIVITY_STATE_LABELS: Record<ActivityState, string> = {
  routing: "Routing",
  preparing: "Preparing",
  thinking: "Thinking",
  computing: "Computing",
  dispatching: "Dispatching",
  coordinating: "Coordinating",
  recording: "Recording",
  waiting: "Waiting",
  completed: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

export function activityStateLabel(state: string): string {
  return ACTIVITY_STATE_LABELS[state as ActivityState] ?? state;
}

/**
 * The full sentence a screen reader hears and the collapsed rail shows on hover.
 *
 * The visual row splits this across three truncated lines, so the accessible
 * name is the only place the whole thing is stated.
 */
export function activitySentence(
  row: ActivityRow | undefined,
  dispatcher?: ActivityRow,
): string {
  if (!row) return "No agent activity";
  const parts = [row.displayName, activityStateLabel(row.state).toLowerCase()];
  if (dispatcher) parts.push(`dispatched by ${dispatcher.displayName}`);
  return parts.join(", ");
}
