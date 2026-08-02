/**
 * What a stream of transitions means to the browser.
 *
 * The browser sees a *different* stream than the server wrote: it reconnects,
 * replays, and joins mid-flight. So the three rules are restated here rather
 * than trusted from upstream, and the coalescing rule — which has no backend
 * counterpart — is what keeps the one moving thing on screen from stuttering.
 */

import { describe, expect, it } from "@rstest/core";

import {
  activeLeaves,
  activeRows,
  applyActivityEvent,
  asActivityEvent,
  closeOpenRows,
  dispatcherChain,
  reduceActivityEvents,
} from "@/core/activity/reducer";
import { EMPTY_ACTIVITY } from "@/core/activity/types";

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
    state: "preparing",
    operation: "Working on your request",
    scope: { cycle_id: null, stage: null, task_id: null },
    ...overrides,
  };
}

describe("one row follows its own transitions", () => {
  it("opens a row on started", () => {
    const projection = reduceActivityEvents([event()]);
    expect(projection.order).toEqual(["act_one"]);
    expect(projection.rows.act_one?.state).toBe("preparing");
  });

  it("moves state and operation on update", () => {
    const projection = reduceActivityEvents([
      event(),
      event({
        transition: "updated",
        state: "thinking",
        operation: "Delegating a subtask",
      }),
    ]);
    expect(projection.rows.act_one?.state).toBe("thinking");
    expect(projection.rows.act_one?.operation).toBe("Delegating a subtask");
  });

  it("settles on a terminal transition", () => {
    const projection = reduceActivityEvents([
      event(),
      event({ transition: "completed", state: "completed" }),
    ]);
    expect(activeRows(projection)).toEqual([]);
  });

  it("keeps identity fields from the opening event", () => {
    const projection = reduceActivityEvents([
      event({
        parent_activity_id: "act_parent",
        scope: { cycle_id: "cyc-1", stage: "build", task_id: "unit-2" },
      }),
    ]);
    const row = projection.rows.act_one;
    expect(row?.parentActivityId).toBe("act_parent");
    expect(row?.cycleId).toBe("cyc-1");
    expect(row?.stage).toBe("build");
    expect(row?.taskId).toBe("unit-2");
  });
});

describe("replay is not new information", () => {
  const frames = [
    event(),
    event({ transition: "updated", state: "thinking" }),
    event({ transition: "completed", state: "completed" }),
  ];

  it("folding twice equals folding once", () => {
    const once = reduceActivityEvents(frames, EMPTY_ACTIVITY, 1000);
    const twice = reduceActivityEvents(
      [...frames, ...frames],
      EMPTY_ACTIVITY,
      1000,
    );
    expect(twice.rows).toEqual(once.rows);
    expect(twice.timeline.length).toBe(once.timeline.length);
  });

  it("a prefix then the whole stream equals the whole stream", () => {
    const partial = reduceActivityEvents(
      frames.slice(0, 2),
      EMPTY_ACTIVITY,
      1000,
    );
    const resumed = reduceActivityEvents(frames, partial, 1000);
    const direct = reduceActivityEvents(frames, EMPTY_ACTIVITY, 1000);
    expect(resumed.rows).toEqual(direct.rows);
  });

  it("a repeated opening does not reset a live row", () => {
    const projection = reduceActivityEvents([
      event(),
      event({ transition: "updated", state: "computing" }),
      event(),
    ]);
    expect(projection.rows.act_one?.state).toBe("computing");
  });

  it("refuses an older durable record that arrives after a newer one", () => {
    const projection = reduceActivityEvents([
      { seq: 10, content: event() },
      {
        seq: 12,
        content: event({ transition: "updated", state: "computing" }),
      },
      {
        seq: 11,
        content: event({ transition: "updated", state: "thinking" }),
      },
    ]);
    expect(projection.rows.act_one?.state).toBe("computing");
    expect(projection.rows.act_one?.serverSeq).toBe(12);
  });

  it("uses the durable creation time in replayed history", () => {
    const projection = reduceActivityEvents([
      {
        seq: 10,
        created_at: "2026-08-01T12:00:00.000Z",
        content: event(),
      },
    ]);
    expect(projection.timeline[0]?.receivedAt).toBe(
      Date.parse("2026-08-01T12:00:00.000Z"),
    );
  });
});

describe("a settled row stays settled", () => {
  it("refuses a later started", () => {
    const projection = reduceActivityEvents([
      event(),
      event({ transition: "completed", state: "completed" }),
      event(),
    ]);
    expect(projection.rows.act_one?.state).toBe("completed");
  });

  it("refuses a stale update after the close", () => {
    const projection = reduceActivityEvents([
      event(),
      event({ transition: "failed", state: "failed" }),
      event({ transition: "updated", state: "thinking" }),
    ]);
    expect(projection.rows.act_one?.state).toBe("failed");
  });

  it("keeps the first recorded outcome", () => {
    const projection = reduceActivityEvents([
      event(),
      event({ transition: "cancelled", state: "cancelled" }),
      event({ transition: "completed", state: "completed" }),
    ]);
    expect(projection.rows.act_one?.state).toBe("cancelled");
  });
});

describe("a row must be opened before it can be updated", () => {
  it("drops an update for an unknown row", () => {
    const projection = reduceActivityEvents([
      event({ transition: "updated", state: "thinking" }),
    ]);
    expect(projection.order).toEqual([]);
  });

  it("drops a terminal for an unknown row", () => {
    const projection = reduceActivityEvents([
      event({ transition: "completed", state: "completed" }),
    ]);
    expect(projection.order).toEqual([]);
  });

  it("recovers the row once backfill supplies its opening", () => {
    const opened = reduceActivityEvents([event()]);
    const projection = reduceActivityEvents(
      [event({ transition: "updated", state: "thinking" })],
      opened,
    );
    expect(projection.rows.act_one?.state).toBe("thinking");
  });
});

describe("coalescing returns the identical object", () => {
  it("a no-op update does not produce a new state", () => {
    const opened = reduceActivityEvents([event()]);
    const same = applyActivityEvent(
      opened,
      event({ transition: "updated", state: "preparing" }),
    );
    // Object identity, not deep equality: this is what makes React bail out of
    // the render, which is what stops the spinner remounting mid-rotation.
    expect(same).toBe(opened);
  });

  it("a real change produces a new state", () => {
    const opened = reduceActivityEvents([event()]);
    const changed = applyActivityEvent(
      opened,
      event({ transition: "updated", state: "thinking" }),
    );
    expect(changed).not.toBe(opened);
  });

  it("never mutates the projection it was given", () => {
    const opened = reduceActivityEvents([event()]);
    applyActivityEvent(
      opened,
      event({ transition: "completed", state: "completed" }),
    );
    expect(opened.rows.act_one?.state).toBe("preparing");
  });
});

describe("malformed frames cost the frame and nothing else", () => {
  it.each([
    null,
    undefined,
    "started",
    { type: "task_started", task_id: "t1" },
    { type: "agent_activity" },
    event({ activity_id: "" }),
    event({ run_id: 7 }),
    event({ display_name: null }),
  ])("ignores %o", (frame) => {
    const before = reduceActivityEvents([event({ activity_id: "act_kept" })]);
    expect(applyActivityEvent(before, frame)).toBe(before);
  });

  it("accepts a state this client has not been taught yet", () => {
    // A backend that learns a new state must not silently lose its rows in
    // every older client; the label layer degrades to the raw word instead.
    const projection = reduceActivityEvents([event({ state: "surveying" })]);
    expect(projection.rows.act_one?.state).toBe("surveying");
  });

  it("narrows a valid frame", () => {
    expect(asActivityEvent(event())?.activity_id).toBe("act_one");
    expect(asActivityEvent({ type: "other" })).toBeNull();
  });
});

describe("who is working now", () => {
  const tree = reduceActivityEvents([
    event({
      activity_id: "act_super",
      actor_kind: "dbtl_supervisor",
      display_name: "Cycle supervisor",
    }),
    event({
      activity_id: "act_stage",
      actor_kind: "stage_adapter",
      display_name: "Build stage",
      parent_activity_id: "act_super",
    }),
  ]);

  it("answers with the innermost active row", () => {
    expect(activeLeaves(tree).map((row) => row.displayName)).toEqual([
      "Build stage",
    ]);
  });

  it("prefers the active leaf that most recently changed", () => {
    const projection = reduceActivityEvents([
      event({ activity_id: "act_a", display_name: "Worker A" }),
      event({ activity_id: "act_b", display_name: "Worker B" }),
      event({
        activity_id: "act_a",
        transition: "updated",
        state: "computing",
      }),
    ]);
    expect(
      activeLeaves(projection).find((row) => row.activityId === "act_a")?.seq,
    ).toBe(3);
  });

  it("hands the answer back to the parent when the child closes", () => {
    const after = reduceActivityEvents(
      [
        event({
          activity_id: "act_stage",
          transition: "completed",
          state: "completed",
        }),
      ],
      tree,
    );
    expect(activeLeaves(after).map((row) => row.displayName)).toEqual([
      "Cycle supervisor",
    ]);
  });

  it("reports the chain nearest ancestor first", () => {
    const chain = dispatcherChain(tree, tree.rows.act_stage);
    expect(chain.map((row) => row.displayName)).toEqual(["Cycle supervisor"]);
  });

  it("survives a lineage cycle without hanging", () => {
    const looped = reduceActivityEvents([
      event({ activity_id: "act_a", parent_activity_id: "act_b" }),
      event({ activity_id: "act_b", parent_activity_id: "act_a" }),
    ]);
    expect(dispatcherChain(looped, looped.rows.act_a).length).toBeLessThan(3);
  });

  it("keeps parallel workers as siblings", () => {
    const parallel = reduceActivityEvents([
      event({ activity_id: "act_stage", display_name: "Build stage" }),
      event({
        activity_id: "act_w1",
        display_name: "Build worker 1",
        parent_activity_id: "act_stage",
      }),
      event({
        activity_id: "act_w2",
        display_name: "Build worker 2",
        parent_activity_id: "act_stage",
      }),
    ]);
    expect(activeLeaves(parallel).map((row) => row.displayName)).toEqual([
      "Build worker 1",
      "Build worker 2",
    ]);
  });
});

describe("a run ends whether or not its actors say so", () => {
  it("settles open rows as interrupted", () => {
    const closed = closeOpenRows(reduceActivityEvents([event()]));
    expect(closed.rows.act_one?.state).toBe("interrupted");
  });

  it("leaves a reported outcome alone", () => {
    const closed = closeOpenRows(
      reduceActivityEvents([
        event(),
        event({ transition: "failed", state: "failed" }),
      ]),
    );
    expect(closed.rows.act_one?.state).toBe("failed");
  });

  it("returns the same object when nothing is open", () => {
    const settled = reduceActivityEvents([
      event(),
      event({ transition: "completed", state: "completed" }),
    ]);
    expect(closeOpenRows(settled)).toBe(settled);
  });
});
