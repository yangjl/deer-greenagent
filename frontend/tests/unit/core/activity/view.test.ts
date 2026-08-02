/**
 * The one derived value the compact block reads.
 *
 * Its job is to answer §1's four questions from a projection without the
 * component re-walking the tree, and to distinguish the four resting states —
 * idle, waiting, settled, unavailable — which the old static green dot
 * collapsed into "looks active".
 */

import { describe, expect, it } from "@rstest/core";

import { reduceActivityEvents } from "@/core/activity/reducer";
import { EMPTY_ACTIVITY } from "@/core/activity/types";
import { activityView } from "@/core/activity/view";

function event(overrides: Record<string, unknown> = {}) {
  return {
    type: "agent_activity",
    version: 1,
    transition: "started",
    activity_id: "act_one",
    parent_activity_id: null,
    dispatcher_activity_id: null,
    run_id: "run-1",
    actor_kind: "lead_agent",
    actor_id: "lead-agent",
    display_name: "Lead agent",
    state: "thinking",
    operation: "Working on your request",
    scope: { cycle_id: null, stage: null, task_id: null },
    ...overrides,
  };
}

describe("the four resting states are distinguishable", () => {
  it("no run at all is idle", () => {
    const view = activityView(EMPTY_ACTIVITY);
    expect(view.mode).toBe("idle");
    expect(view.leaf).toBeUndefined();
  });

  it("unreadable activity is unavailable, never a guessed active", () => {
    const live = reduceActivityEvents([event()]);
    expect(activityView(live, { available: false }).mode).toBe("unavailable");
  });

  it("a working actor is live", () => {
    expect(activityView(reduceActivityEvents([event()])).mode).toBe("live");
  });

  it("an actor waiting on a person is waiting, not live", () => {
    const waiting = reduceActivityEvents([event({ state: "waiting" })]);
    expect(activityView(waiting).mode).toBe("waiting");
  });

  it("one waiting actor beside one working actor is still live", () => {
    // "Waiting" is only honest when nothing is working; a run with a live
    // worker is not waiting merely because its coordinator is.
    const mixed = reduceActivityEvents([
      event({ activity_id: "act_a", state: "waiting" }),
      event({ activity_id: "act_b", state: "computing" }),
    ]);
    expect(activityView(mixed).mode).toBe("live");
  });

  it("a finished run holds its terminal actor and result", () => {
    const done = reduceActivityEvents([
      event(),
      event({ transition: "completed", state: "completed" }),
    ]);
    const view = activityView(done);
    expect(view.mode).toBe("settled");
    expect(view.leaf?.state).toBe("completed");
  });
});

describe("the leaf is the deepest actor genuinely working", () => {
  const governed = reduceActivityEvents([
    event({
      activity_id: "act_super",
      display_name: "Cycle supervisor",
      state: "waiting",
    }),
    event({
      activity_id: "act_stage",
      display_name: "Build stage",
      parent_activity_id: "act_super",
      state: "coordinating",
      scope: { cycle_id: "cyc-1", stage: "build", task_id: null },
    }),
    event({
      activity_id: "act_w1",
      display_name: "Build worker 1",
      parent_activity_id: "act_stage",
      state: "computing",
    }),
    event({
      activity_id: "act_w2",
      display_name: "Build worker 2",
      parent_activity_id: "act_stage",
      state: "computing",
    }),
  ]);

  it("names a worker rather than the supervisor above it", () => {
    // Ties break by highest sequence: when two actors are equally deep, the one
    // that most recently reported is the one a reader is watching.
    expect(activityView(governed).leaf?.displayName).toBe("Build worker 2");
  });

  it("states the dispatcher in words", () => {
    expect(activityView(governed).chain[0]?.displayName).toBe("Build stage");
  });

  it("reports the other active workers as siblings", () => {
    expect(
      activityView(governed).siblings.map((row) => row.displayName),
    ).toEqual(["Build worker 1"]);
  });

  it("keeps the block's height fixed as parallelism grows", () => {
    // The block renders at most one sibling line whatever the count, so a
    // fourth worker starting cannot change how tall the section is.
    const wide = reduceActivityEvents(
      [3, 4, 5].map((n) =>
        event({
          activity_id: `act_w${n}`,
          display_name: `Build worker ${n}`,
          parent_activity_id: "act_stage",
          state: "computing",
        }),
      ),
      governed,
    );
    expect(activityView(wide).siblings.length).toBe(4);
  });
});

describe("the selector is memoized on the projection object", () => {
  it("returns the identical view for an unchanged projection", () => {
    const projection = reduceActivityEvents([event()]);
    expect(activityView(projection)).toBe(activityView(projection));
  });

  it("recomputes when the projection changes", () => {
    const first = reduceActivityEvents([event()]);
    const second = reduceActivityEvents(
      [event({ transition: "updated", state: "computing" })],
      first,
    );
    expect(activityView(second)).not.toBe(activityView(first));
  });
});
