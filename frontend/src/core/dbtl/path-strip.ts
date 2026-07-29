/**
 * Pure derivation of the read-only path strip (progressive gate, Phase 0).
 *
 * Nothing here reads storage, the network, or the clock — the strip is a
 * function of the durable transition walk plus the cycle's state, so every
 * rendered item is traceable to a server record. Deterministic on purpose:
 * the same transitions always yield the same strip.
 */

import type { DbtlStageTransition } from "./cycle-view";

export type PathStripStatus =
  | "passed"
  | "revised"
  | "invalidated"
  | "closed"
  | "current";

export interface PathStripItem {
  stage: string;
  attempt: number;
  status: PathStripStatus;
  backfilled: boolean;
}

const GRAPH_STAGES: ReadonlySet<string> = new Set([
  "design",
  "build",
  "test",
  "learn",
]);

const TERMINAL_CYCLE_STATES: ReadonlySet<string> = new Set([
  "completed",
  "abandoned",
]);

/** Routes that advanced the walk; everything else sent the stage back to work. */
const PASSED_ROUTES: ReadonlySet<string> = new Set([
  "approve",
  "advance_to_learn",
]);

function decidedStatus(chosenRoute: string): PathStripStatus {
  if (PASSED_ROUTES.has(chosenRoute)) return "passed";
  if (chosenRoute === "close_cycle") return "closed";
  return "revised";
}

/** The stage a transition-less cycle is currently at, when its state names one. */
function headStageFromCycleState(cycleState: string): string | null {
  if (cycleState === "ready_for_build") return "build";
  return GRAPH_STAGES.has(cycleState) ? cycleState : null;
}

/**
 * Folds the ordered transition walk into strip items, one per decided stage
 * attempt, plus a trailing "current" head while the cycle can still move.
 *
 * A backward edge into Design invalidates every earlier "passed" item —
 * approvals recorded forward of the old revision no longer stand once the
 * design they were granted against is reopened.
 */
export function derivePathStrip(
  transitions: DbtlStageTransition[],
  cycleState: string,
): PathStripItem[] {
  const ordered = [...transitions].sort((left, right) => left.seq - right.seq);

  // The displayed number is which round of that stage this was, counted from
  // the walk itself — not the durable `attempt_number`, which stays at 1 while
  // a changes-requested stage is re-submitted on the same attempt row and
  // would print two different rounds as the same item.
  const rounds = new Map<string, number>();
  let items: PathStripItem[] = [];
  for (const transition of ordered) {
    if (transition.to_stage === "design") {
      items = items.map((item) =>
        item.status === "passed"
          ? { ...item, status: "invalidated" as const }
          : item,
      );
    }
    const round = (rounds.get(transition.from_stage) ?? 0) + 1;
    rounds.set(transition.from_stage, round);
    items = [
      ...items,
      {
        stage: transition.from_stage,
        attempt: round,
        status: decidedStatus(transition.chosen_route),
        backfilled: transition.backfilled,
      },
    ];
  }

  if (TERMINAL_CYCLE_STATES.has(cycleState)) {
    return items;
  }

  const last = ordered.at(-1);
  const headStage = last
    ? GRAPH_STAGES.has(last.to_stage)
      ? last.to_stage
      : null
    : headStageFromCycleState(cycleState);
  if (!headStage) {
    return items;
  }

  const priorAttempts = items.filter((item) => item.stage === headStage).length;
  return [
    ...items,
    {
      stage: headStage,
      attempt: priorAttempts + 1,
      status: "current",
      backfilled: false,
    },
  ];
}
