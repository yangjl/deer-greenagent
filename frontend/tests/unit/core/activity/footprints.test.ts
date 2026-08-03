import { describe, expect, it } from "@rstest/core";

import { activityFootprints } from "@/core/activity/footprints";
import { reduceActivityEvents } from "@/core/activity/reducer";

function event(overrides: Record<string, unknown> = {}) {
  return {
    type: "agent_activity",
    version: 1,
    transition: "started",
    activity_id: "act_super",
    parent_activity_id: null,
    dispatcher_activity_id: null,
    run_id: "run-1",
    actor_kind: "dbtl_supervisor",
    actor_id: "dbtl-supervisor",
    display_name: "Cycle supervisor",
    state: "routing",
    operation: "Selecting the next action",
    scope: { cycle_id: null, stage: null, task_id: null },
    ...overrides,
  };
}

describe("run activity footprints", () => {
  it("collapses transitions and names the actor that did the work", () => {
    const projection = reduceActivityEvents([
      event(),
      event({ transition: "updated", state: "dispatching" }),
      event({ transition: "completed", state: "completed" }),
      event({
        activity_id: "act_lead",
        actor_kind: "lead_agent",
        actor_id: "lead-agent",
        display_name: "Lead agent",
        state: "preparing",
        operation: "Working on your request",
      }),
      event({
        activity_id: "act_lead",
        actor_kind: "lead_agent",
        actor_id: "lead-agent",
        display_name: "Lead agent",
        transition: "updated",
        state: "thinking",
        operation: "Working on your request",
      }),
      event({
        activity_id: "act_lead",
        actor_kind: "lead_agent",
        actor_id: "lead-agent",
        display_name: "Lead agent",
        transition: "completed",
        state: "completed",
        operation: "Working on your request",
      }),
    ]);

    expect(activityFootprints(projection)).toEqual([
      expect.objectContaining({
        runId: "run-1",
        displayName: "Lead agent",
        operation: "Working on your request",
        state: "completed",
        dispatchedBy: "Cycle supervisor",
      }),
    ]);
  });

  it("returns one footprint for each run, newest first", () => {
    const projection = reduceActivityEvents([
      event({ run_id: "run-1" }),
      event({ run_id: "run-1", transition: "completed", state: "completed" }),
      event({ run_id: "run-2", activity_id: "act_super_2" }),
      event({
        run_id: "run-2",
        activity_id: "act_super_2",
        transition: "completed",
        state: "completed",
      }),
    ]);

    expect(activityFootprints(projection).map((item) => item.runId)).toEqual([
      "run-2",
      "run-1",
    ]);
  });
});
