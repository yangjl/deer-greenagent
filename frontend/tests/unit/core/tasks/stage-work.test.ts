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

import { stageWorkTasks } from "@/core/tasks/stage-work";
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

describe("a task belongs to the run it was observed in", () => {
  it("keeps the run id it was stamped with", () => {
    const adopted = stageWorkTasks([
      task({ id: "unit-1", dbtlStage: "build", runId: "run-7" }),
    ]);
    // Backfilling is addressed by (thread, run, task); the thread's latest run
    // is the wrong answer for every task from an earlier turn.
    expect(adopted[0]?.runId).toBe("run-7");
  });

  it("tolerates a task recorded before the run was known", () => {
    const adopted = stageWorkTasks([
      task({ id: "unit-1", dbtlStage: "build" }),
    ]);
    expect(adopted[0]?.runId).toBeUndefined();
  });

  it("does not render historical Build workers under a later run", () => {
    const adopted = stageWorkTasks(
      [
        task({ id: "old-build", dbtlStage: "build", runId: "run-old" }),
        task({ id: "current-build", dbtlStage: "build", runId: "run-current" }),
      ],
      "run-current",
    );

    expect(adopted.map((entry) => entry.id)).toEqual(["current-build"]);
  });

  it("shows nothing for a run that was ordinary Lead Agent work", () => {
    expect(
      stageWorkTasks(
        [task({ id: "old-build", dbtlStage: "build", runId: "run-old" })],
        "run-lead",
      ),
    ).toEqual([]);
  });
});
