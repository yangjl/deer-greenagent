/**
 * Which work needs a transcript anchor of its own.
 *
 * A DBTL stage worker is dispatched by the stage adapter, not by an assistant
 * turn, so it has no `task` tool call to render from and used to appear
 * nowhere at all. The rule that decides what to adopt has to be narrow in both
 * directions: adopt too little and a Build runs invisibly again; adopt too much
 * and a meeting seat renders twice, once here and once in the debate panel.
 */

import { describe, expect, it } from "@rstest/core";

import {
  runningStageWorkRunId,
  stageLabel,
  stageWorkGroups,
  stageWorkIsRunning,
  stageWorkTasks,
} from "@/core/tasks/stage-work";
import type { Subtask } from "@/core/tasks/types";

function task(overrides: Partial<Subtask> & { id: string }): Subtask {
  return {
    status: "in_progress",
    subagent_type: "general-purpose",
    description: "Build work",
    prompt: "",
    ...overrides,
  };
}

describe("stage work is what the server labelled with a stage", () => {
  it("adopts a worker carrying a stage", () => {
    const adopted = stageWorkTasks([
      task({ id: "unit-1", dbtlStage: "build" }),
    ]);
    expect(adopted.map((entry) => entry.id)).toEqual(["unit-1"]);
  });

  it("leaves an ordinary delegated subtask alone", () => {
    // It has an assistant message to render from already.
    expect(stageWorkTasks([task({ id: "call-1" })])).toEqual([]);
  });

  it("leaves a meeting seat to the debate panel", () => {
    const seat = task({
      id: "seat-1",
      dbtlStage: "design",
      councilSeat: {
        stage: "design",
        role: "chair",
        roleLabel: "Chair",
        focus: "",
        capability: "experimental_design",
        agentName: "general-purpose",
        viaGeneralist: true,
        model: "m",
        round: 1,
        countsTowardStageOutput: true,
      },
    });

    expect(stageWorkTasks([seat])).toEqual([]);
  });
});

describe("one run can touch more than one stage", () => {
  const tasks = [
    task({ id: "b1", dbtlStage: "build", status: "completed" }),
    task({ id: "t1", dbtlStage: "test" }),
    task({ id: "b2", dbtlStage: "build", status: "completed" }),
  ];

  it("groups workers by their stage", () => {
    const groups = stageWorkGroups(tasks);
    expect(groups.map((group) => group.stage)).toEqual(["build", "test"]);
    expect(groups[0]?.tasks.map((entry) => entry.id)).toEqual(["b1", "b2"]);
  });

  it("keeps the order each stage first appeared", () => {
    const groups = stageWorkGroups([...tasks].reverse());
    expect(groups.map((group) => group.stage)).toEqual(["build", "test"]);
  });

  it("reports a group as running only while one of its workers is", () => {
    const groups = stageWorkGroups(tasks);
    expect(stageWorkIsRunning(groups[0]!.tasks)).toBe(false);
    expect(stageWorkIsRunning(groups[1]!.tasks)).toBe(true);
  });

  it("has nothing to show for a chat with no stage work", () => {
    expect(stageWorkGroups([task({ id: "call-1" })])).toEqual([]);
  });
});

describe("the stage reads as a name, not an identifier", () => {
  it.each([
    ["build", "Build"],
    ["test", "Test"],
    ["reconciliation", "Reconciliation"],
    ["ready_for_build", "Ready For Build"],
  ])("renders %s as %s", (stage, expected) => {
    expect(stageLabel(stage)).toBe(expected);
  });

  it("falls back rather than rendering an empty heading", () => {
    expect(stageLabel("")).toBe("Stage");
    expect(stageLabel("   ")).toBe("Stage");
  });
});

describe("a task belongs to the run it was observed in", () => {
  it("keeps the run id it was stamped with", () => {
    const adopted = stageWorkTasks([
      task({ id: "unit-1", dbtlStage: "build", runId: "run-7" }),
    ]);
    // Backfilling is addressed by (thread, run, task); the thread's latest run
    // is the wrong answer for every task from an earlier turn.
    expect(adopted[0]?.runId).toBe("run-7");
  });

  it("selects the newest actively reporting governed-work run", () => {
    expect(
      runningStageWorkRunId([
        task({
          id: "old-build",
          dbtlStage: "build",
          runId: "run-old",
          status: "completed",
        }),
        task({
          id: "live-build",
          dbtlStage: "build",
          runId: "run-live",
        }),
      ]),
    ).toBe("run-live");
  });

  it("does not treat a meeting seat as the active stage-work lane", () => {
    expect(
      runningStageWorkRunId([
        task({
          id: "seat",
          dbtlStage: "design",
          runId: "run-meeting",
          councilSeat: {
            stage: "design",
            role: "chair",
            roleLabel: "Chair",
            focus: "",
            capability: "experimental_design",
            agentName: "general-purpose",
            viaGeneralist: true,
            model: "m",
            round: 1,
            countsTowardStageOutput: true,
          },
        }),
      ]),
    ).toBeUndefined();
  });

  it("tolerates a task recorded before the run was known", () => {
    const adopted = stageWorkTasks([
      task({ id: "unit-1", dbtlStage: "build" }),
    ]);
    expect(adopted[0]?.runId).toBeUndefined();
  });

  it("does not render historical Build workers under a later run", () => {
    const groups = stageWorkGroups(
      [
        task({ id: "old-build", dbtlStage: "build", runId: "run-old" }),
        task({ id: "current-build", dbtlStage: "build", runId: "run-current" }),
      ],
      "run-current",
    );

    expect(groups[0]?.tasks.map((entry) => entry.id)).toEqual([
      "current-build",
    ]);
  });

  it("shows no stage panel when the latest run was ordinary Lead Agent work", () => {
    expect(
      stageWorkGroups(
        [task({ id: "old-build", dbtlStage: "build", runId: "run-old" })],
        "run-lead",
      ),
    ).toEqual([]);
  });
});
