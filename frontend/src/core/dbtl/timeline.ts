/**
 * Pure derivation of the non-linear cycle timeline (progressive gate, Phase 4).
 *
 * This replaces the Phase 0 path strip, which could say a cycle looped but not
 * why any edge was taken. Everything the audit view needs is already on the
 * durable transition row — the assessment, an explicit human override, the
 * routes that were offered, who decided, and which deck and evidence the
 * decision bound. The strip simply dropped it.
 *
 * Nothing here reads storage, the network, or the clock: the same transitions
 * always yield the same timeline, so a reloaded browser and a restored
 * checkpoint reconstruct the same path.
 */

import type { DbtlStageTransition } from "./cycle-view";

export type TimelineStatus =
  | "passed"
  | "revised"
  | "invalidated"
  | "exception"
  | "closed"
  | "current";

export interface CycleTimelineEntry {
  /** The stage this attempt worked. */
  stage: string;
  /** Which round of that stage it was, counted from the walk. */
  round: number;
  status: TimelineStatus;
  /** The route a person chose, or null for the undecided head. */
  chosenRoute: string | null;
  /** The durable transition id, so one edge can be cited. Null for the head. */
  recordId: string | null;
  assessedDifficulty: string | null;
  assessmentRationale: string | null;
  humanOverride: string | null;
  /** True only when the human recorded a difficulty the agent did not assess. */
  overrodeAssessment: boolean;
  offeredRoutes: string[];
  decidedBy: string | null;
  decidedAt: string | null;
  decisionSurfaceId: string | null;
  /** Where the deciding deck lives, so the audit view can open it. */
  decidedInThreadId: string | null;
  evidenceHash: string | null;
  /** Synthesized by migration 0024 for a decision taken before the graph. */
  backfilled: boolean;
}

export interface CycleTimeline {
  entries: CycleTimelineEntry[];
  /** True while the newest decision was a park nothing has resumed. */
  parked: boolean;
  parkedStage: string | null;
  /** True when a backward edge into Design invalidated forward approvals. */
  hasInvalidatedWork: boolean;
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

function decidedStatus(chosenRoute: string): TimelineStatus {
  if (PASSED_ROUTES.has(chosenRoute)) return "passed";
  if (
    chosenRoute === "advanced_with_exception" ||
    chosenRoute === "learn_from_invalidated_evidence"
  )
    return "exception";
  if (chosenRoute === "close_cycle") return "closed";
  return "revised";
}

/** The stage a transition-less cycle is currently at, when its state names one. */
function headStageFromCycleState(cycleState: string): string | null {
  if (cycleState === "ready_for_build") return "build";
  return GRAPH_STAGES.has(cycleState) ? cycleState : null;
}

function entryFrom(
  transition: DbtlStageTransition,
  round: number,
): CycleTimelineEntry {
  const assessed = transition.assessed_difficulty;
  const override = transition.human_override;
  return {
    stage: transition.from_stage,
    round,
    status: decidedStatus(transition.chosen_route),
    chosenRoute: transition.chosen_route,
    recordId: transition.id,
    assessedDifficulty: assessed,
    assessmentRationale: transition.assessment_rationale,
    humanOverride: override,
    // Recording the same value the agent assessed is agreement, not a dispute.
    // Counting it as an override would inflate the one telemetry number the
    // default-on decision is gated behind.
    overrodeAssessment: Boolean(override) && override !== assessed,
    offeredRoutes: transition.offered_routes ?? [],
    decidedBy: transition.decided_by,
    decidedAt: transition.decided_at,
    decisionSurfaceId: transition.decision_surface_id,
    decidedInThreadId: transition.decided_in_thread_id ?? null,
    evidenceHash: transition.evidence_hash,
    backfilled: transition.backfilled,
  };
}

/**
 * Folds the ordered transition walk into one entry per decided stage attempt,
 * plus a trailing "current" head while the cycle can still move.
 *
 * Park is deliberately not an attempt: nobody worked a stage round by parking
 * one, so rendering it as an attempt would show a phantom revised round. It is
 * reported on the timeline itself instead, because a parked cycle is otherwise
 * indistinguishable from an idle one.
 */
export function deriveCycleTimeline(
  transitions: DbtlStageTransition[],
  cycleState: string,
): CycleTimeline {
  const ordered = [...transitions].sort((left, right) => left.seq - right.seq);
  const newest = ordered.at(-1);
  const attemptTransitions = ordered.filter(
    (transition) => transition.chosen_route !== "park",
  );

  // The displayed number is which round of that stage this was, counted from
  // the walk itself — not the durable `attempt_number`, which stays at 1 while
  // a changes-requested stage is re-submitted on the same attempt row and
  // would print two different rounds as the same entry.
  const rounds = new Map<string, number>();
  let entries: CycleTimelineEntry[] = [];
  let hasInvalidatedWork = false;
  for (const transition of attemptTransitions) {
    if (transition.to_stage === "design") {
      // Approvals recorded forward of this point were granted against a design
      // that is now reopened, so they no longer stand.
      entries = entries.map((entry) => {
        if (entry.status !== "passed") return entry;
        hasInvalidatedWork = true;
        return { ...entry, status: "invalidated" as const };
      });
    }
    const round = (rounds.get(transition.from_stage) ?? 0) + 1;
    rounds.set(transition.from_stage, round);
    entries = [...entries, entryFrom(transition, round)];
  }

  const parked = newest?.chosen_route === "park";
  const timeline: CycleTimeline = {
    entries,
    parked,
    parkedStage: parked ? newest.from_stage : null,
    hasInvalidatedWork,
  };

  if (TERMINAL_CYCLE_STATES.has(cycleState)) {
    return timeline;
  }

  const last = attemptTransitions.at(-1);
  const headStage = last
    ? GRAPH_STAGES.has(last.to_stage)
      ? last.to_stage
      : null
    : headStageFromCycleState(cycleState);
  if (!headStage) {
    return timeline;
  }

  const priorRounds = entries.filter(
    (entry) => entry.stage === headStage,
  ).length;
  return {
    ...timeline,
    entries: [
      ...entries,
      {
        stage: headStage,
        round: priorRounds + 1,
        status: "current",
        chosenRoute: null,
        recordId: null,
        assessedDifficulty: null,
        assessmentRationale: null,
        humanOverride: null,
        overrodeAssessment: false,
        offeredRoutes: [],
        decidedBy: null,
        decidedAt: null,
        decisionSurfaceId: null,
        decidedInThreadId: null,
        evidenceHash: null,
        backfilled: false,
      },
    ],
  };
}
