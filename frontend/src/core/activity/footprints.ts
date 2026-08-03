/**
 * Condense transition-level activity into one human-readable footprint per run.
 *
 * The durable feed keeps every lifecycle edge because replay and recovery need
 * them. The history surface does not: "Routing", "Dispatching", and "Done"
 * are three facts about one supervisor invocation, not three pieces of work.
 */

import {
  type ActorKind,
  type ActivityProjection,
  type ActivityState,
  type ActivityTimelineEntry,
} from "./types";

export interface ActivityFootprint {
  runId: string;
  actorKind: ActorKind;
  displayName: string;
  state: ActivityState;
  operation: string;
  receivedAt: number;
  cycleId: string | null;
  /** Routing provenance, kept secondary to the actor that did the work. */
  dispatchedBy?: string;
}

// Prefer the coordinator for governed stage runs and the Lead agent for
// ordinary runs. Individual workers remain available in the live tree and
// audit surfaces; a run footprint names the actor responsible for the whole
// unit of work rather than whichever parallel worker happened to finish last.
const ACTOR_PRIORITY: Record<ActorKind, number> = {
  stage_adapter: 50,
  lead_agent: 40,
  stage_worker: 30,
  subagent: 20,
  dbtl_supervisor: 10,
};

function mainEntry(entries: readonly ActivityTimelineEntry[]) {
  const latestByActivity = new Map<string, ActivityTimelineEntry>();
  for (const entry of entries) latestByActivity.set(entry.activityId, entry);
  return [...latestByActivity.values()].sort((left, right) => {
    const priority =
      ACTOR_PRIORITY[right.actorKind] - ACTOR_PRIORITY[left.actorKind];
    return priority || right.seq - left.seq;
  })[0];
}

export function activityFootprints(
  projection: ActivityProjection,
): readonly ActivityFootprint[] {
  const byRun = new Map<string, ActivityTimelineEntry[]>();
  for (const entry of projection.timeline) {
    byRun.set(entry.runId, [...(byRun.get(entry.runId) ?? []), entry]);
  }

  const footprints: Array<ActivityFootprint & { latestSeq: number }> = [];
  for (const [runId, entries] of byRun) {
    const main = mainEntry(entries);
    if (!main) continue;
    const supervisor = [...entries]
      .reverse()
      .find((entry) => entry.actorKind === "dbtl_supervisor");
    const cycle = [...entries]
      .reverse()
      .find((entry) => entry.cycleId)?.cycleId;
    footprints.push({
      runId,
      actorKind: main.actorKind,
      displayName: main.displayName,
      state: main.state,
      operation: main.operation,
      receivedAt: Math.max(...entries.map((entry) => entry.receivedAt)),
      latestSeq: Math.max(...entries.map((entry) => entry.seq)),
      cycleId: main.cycleId ?? cycle ?? null,
      dispatchedBy:
        main.actorKind !== "dbtl_supervisor"
          ? supervisor?.displayName
          : undefined,
    });
  }

  return footprints
    .sort(
      (left, right) =>
        right.receivedAt - left.receivedAt || right.latestSeq - left.latestSeq,
    )
    .map(({ latestSeq: _latestSeq, ...footprint }) => footprint);
}
