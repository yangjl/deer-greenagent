/**
 * A meeting belongs to the run that held it, and stays where it happened.
 *
 * The debate panel used to be one floating block per *thread*: every council
 * seat in the conversation folded into a single `debateRounds` call, and the
 * result was spliced into the transcript at a computed index and bound to
 * `latestRunId`. Three failures came out of that. A second meeting merged into
 * the first's rounds instead of being its own. Starting any new run moved the
 * panel off the meeting it described. And a meeting the browser did not watch
 * live — a deck-started round, a reload, a background run — had nothing to
 * render at all.
 *
 * Grouping by run is what fixes all three: the run is the durable identity a
 * meeting already has (`subagent.start` carries it), it is what the transcript
 * is ordered by, and it is stable across reloads.
 */

import { describe, expect, it } from "@rstest/core";

import {
  meetingAnchorIndices,
  meetingsByRun,
} from "@/core/tasks/meeting-timeline";
import type { Subtask } from "@/core/tasks/types";

function seat(
  overrides: Partial<Subtask> & { id: string; runId?: string },
): Subtask {
  return {
    description: "seat",
    status: "completed",
    steps: [],
    ...overrides,
  } as Subtask;
}

function councilSeat(
  id: string,
  runId: string | undefined,
  role: string,
  round = 1,
  extra: Partial<Subtask> = {},
): Subtask {
  return seat({
    id,
    runId,
    councilSeat: { role, round, stage: "design" } as Subtask["councilSeat"],
    ...extra,
  });
}

describe("meetingAnchorIndices", () => {
  it("anchors a failed meeting above its ordinary assistant conclusion", () => {
    expect([
      ...meetingAnchorIndices([{ runId: "run-1", type: "assistant" }]),
    ]).toEqual([0]);
  });

  it("prefers the files group so a successful meeting stays above its deck", () => {
    expect([
      ...meetingAnchorIndices([
        { runId: "run-1", type: "assistant" },
        { runId: "run-1", type: "assistant:present-files" },
      ]),
    ]).toEqual([1]);
  });

  it("does not let a human group claim a meeting that is still unanchored", () => {
    expect([
      ...meetingAnchorIndices([{ runId: "run-1", type: "human" }]),
    ]).toEqual([]);
  });
});

describe("meetingsByRun", () => {
  it("keeps two meetings in one conversation apart", () => {
    const meetings = meetingsByRun([
      councilSeat("a", "run-1", "position"),
      councilSeat("b", "run-1", "red_team"),
      councilSeat("c", "run-2", "position"),
    ]);

    expect(meetings).toHaveLength(2);
    expect(meetings[0]!.runId).toBe("run-1");
    expect(meetings[0]!.seats).toHaveLength(2);
    expect(meetings[1]!.runId).toBe("run-2");
    expect(meetings[1]!.seats).toHaveLength(1);
  });

  it("groups a run's seats into its own rounds", () => {
    const meetings = meetingsByRun([
      councilSeat("a", "run-1", "position", 1),
      councilSeat("b", "run-1", "chair", 1),
      councilSeat("c", "run-1", "position", 2),
    ]);

    expect(meetings).toHaveLength(1);
    expect(meetings[0]!.rounds.map((round) => round.round)).toEqual([1, 2]);
  });

  it("ignores tasks that are not meeting seats", () => {
    const meetings = meetingsByRun([
      seat({ id: "worker", runId: "run-1" }),
      councilSeat("a", "run-1", "position"),
    ]);

    expect(meetings[0]!.seats.map((task) => task.id)).toEqual(["a"]);
  });

  it("returns nothing when no seat was recorded", () => {
    expect(meetingsByRun([seat({ id: "worker", runId: "run-1" })])).toEqual([]);
  });

  it("reports how far along a meeting is", () => {
    const meetings = meetingsByRun([
      councilSeat("a", "run-1", "position", 1, { status: "completed" }),
      councilSeat("b", "run-1", "red_team", 1, { status: "in_progress" }),
      councilSeat("c", "run-1", "chair", 1, { status: "in_progress" }),
    ]);

    expect(meetings[0]!.reported).toBe(1);
    expect(meetings[0]!.total).toBe(3);
    expect(meetings[0]!.isRunning).toBe(true);
  });

  it("reports a finished meeting as not running", () => {
    const meetings = meetingsByRun([
      councilSeat("a", "run-1", "position", 1, { status: "completed" }),
      councilSeat("b", "run-1", "chair", 1, { status: "failed" }),
    ]);

    expect(meetings[0]!.isRunning).toBe(false);
    expect(meetings[0]!.reported).toBe(2);
  });

  it("carries the stage so the block can name itself", () => {
    const meetings = meetingsByRun([councilSeat("a", "run-1", "position")]);

    expect(meetings[0]!.stage).toBe("design");
  });

  it("keeps seats with no run under their own bucket rather than dropping them", () => {
    /* A seat that arrived without a run id is still evidence a meeting
     * happened. Losing it silently is the failure this whole module replaces;
     * an unanchored bucket at least renders. */
    const meetings = meetingsByRun([
      councilSeat("a", undefined, "position"),
      councilSeat("b", "run-1", "position"),
    ]);

    expect(meetings).toHaveLength(2);
    expect(meetings.some((meeting) => meeting.runId === "")).toBe(true);
  });

  it("orders meetings by first appearance, so the transcript order holds", () => {
    const meetings = meetingsByRun([
      councilSeat("c", "run-2", "position"),
      councilSeat("a", "run-1", "position"),
      councilSeat("d", "run-2", "chair"),
    ]);

    expect(meetings.map((meeting) => meeting.runId)).toEqual([
      "run-2",
      "run-1",
    ]);
  });

  it("is stable when called twice with the same input", () => {
    const tasks = [
      councilSeat("a", "run-1", "position"),
      councilSeat("b", "run-1", "chair"),
    ];

    expect(JSON.stringify(meetingsByRun(tasks))).toBe(
      JSON.stringify(meetingsByRun(tasks)),
    );
  });
});
