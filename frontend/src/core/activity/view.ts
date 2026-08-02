/**
 * The one derived value the compact block reads.
 *
 * Kept out of the component so the block does not re-walk the tree on every
 * render, and memoized on the projection object — which the reducer keeps
 * identical whenever nothing a reader could see has changed.
 */

import { activeLeaves, dispatcherChain } from "./reducer";
import {
  type ActivityProjection,
  type ActivityRow,
  isTerminalActivityState,
} from "./types";

/** What the Agents section is, right now, in one word. */
export type ActivityMode =
  | "live" // something is genuinely working
  | "waiting" // the run is up, but it is waiting on a person or a worker
  | "settled" // the last run finished; the block holds its result
  | "idle" // no run has happened in this conversation
  | "unavailable"; // activity data cannot be read, which is not the same as idle

export interface ActivityView {
  mode: ActivityMode;
  /** The deepest actor genuinely working, or the last one to settle. */
  leaf: ActivityRow | undefined;
  /** Nearest ancestor first. Line 2 of the block names `chain[0]`. */
  chain: readonly ActivityRow[];
  /** Other active leaves, so the block can say "Also running: …". */
  siblings: readonly ActivityRow[];
  stepCount: number;
}

const EMPTY_VIEW: ActivityView = {
  mode: "idle",
  leaf: undefined,
  chain: [],
  siblings: [],
  stepCount: 0,
};

const cache = new WeakMap<ActivityProjection, ActivityView>();

/**
 * Choose the leaf and describe the surrounding state.
 *
 * Ties break by highest sequence: when two actors are equally deep, the one
 * that most recently reported is the one a reader is watching.
 */
export function activityView(
  projection: ActivityProjection,
  options: { available?: boolean } = {},
): ActivityView {
  if (options.available === false) {
    return { ...EMPTY_VIEW, mode: "unavailable" };
  }
  const cached = cache.get(projection);
  if (cached) return cached;

  const leaves = [...activeLeaves(projection)].sort((a, b) => b.seq - a.seq);
  const stepCount = projection.timeline.length;

  let view: ActivityView;
  if (leaves.length > 0) {
    const [leaf, ...siblings] = leaves;
    view = {
      // "Waiting" is only honest when *nothing* is working. One actor waiting
      // on a person while another computes is still a live run.
      mode: leaves.every((row) => row.state === "waiting") ? "waiting" : "live",
      leaf,
      chain: dispatcherChain(projection, leaf),
      siblings,
      stepCount,
    };
  } else if (stepCount > 0) {
    // Hold the terminal actor until the next run starts, so a reader who looks
    // up a moment late still learns how the work ended.
    const settled = projection.order
      .map((id) => projection.rows[id])
      .filter((row): row is ActivityRow => Boolean(row))
      .filter((row) => isTerminalActivityState(row.state))
      .sort((a, b) => b.seq - a.seq);
    view = {
      mode: "settled",
      leaf: settled[0],
      chain: settled[0] ? dispatcherChain(projection, settled[0]) : [],
      siblings: [],
      stepCount,
    };
  } else {
    view = EMPTY_VIEW;
  }

  cache.set(projection, view);
  return view;
}
