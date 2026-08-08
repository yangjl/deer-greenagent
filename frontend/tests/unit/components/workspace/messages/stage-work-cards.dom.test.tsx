import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

rs.mock("@/core/tasks/api", () => ({
  fetchSubtaskSteps: rs.fn().mockResolvedValue([]),
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      subtasks: {
        in_progress: "Running",
        completed: "Completed",
        failed: "Failed",
        progressReport: "Progress report",
        failureReport: "Failure report",
        stageTokenCapped: "Stopped at the token budget.",
        stageTurnCapped: "Stopped at the turn limit.",
        stageLoopCapped: "Stopped at the loop guard.",
      },
      tokenUsage: {
        label: "tokens",
        collecting: "Collecting",
        unavailableShort: "Unavailable",
      },
      toolCalls: {
        lessSteps: "Less steps",
        moreSteps: (count: number) => `${count} more step`,
      },
    },
  }),
}));

rs.mock("@/core/models/hooks", () => ({
  useModels: () => ({ models: [], tokenUsageEnabled: false }),
}));

import { StageWorkCards } from "@/components/workspace/messages/stage-work-panel";
import { SubtaskContext } from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";

afterEach(cleanup);

describe("StageWorkCards native presentation", () => {
  it("renders native stage steps without the stage-worker header", () => {
    const task: Subtask = {
      id: "build-worker",
      runId: "run-1",
      dbtlStage: "build",
      status: "failed",
      subagent_type: "subagent",
      description: "Build work: software and workflow engineering",
      prompt: "",
      error: "The package audit failed.",
      steps: [
        {
          kind: "ai",
          message_index: 0,
          text: "Prepared the correction workspace.",
          truncated: false,
        },
        {
          kind: "ai",
          message_index: 1,
          text: "Ran the package validation audit.",
          truncated: false,
        },
      ],
    };
    const tasks = { [task.id]: task };

    render(
      <SubtaskContext.Provider
        value={{
          tasks,
          tasksRef: { current: tasks },
          setTasks: () => {
            /* static test fixture */
          },
        }}
      >
        <StageWorkCards threadId="thread-1" runId="run-1" />
      </SubtaskContext.Provider>,
    );

    expect(screen.queryByText("Failure report")).toBeNull();
    expect(screen.queryByText("The package audit failed.")).toBeNull();

    expect(
      screen.queryByRole("button", {
        name: /Build work: software and workflow engineering/i,
      }),
    ).toBeNull();
    expect(screen.getByText("1 more step")).toBeTruthy();
    expect(screen.getByText("Ran the package validation audit.")).toBeTruthy();
    expect(screen.queryByText("Prepared the correction workspace.")).toBeNull();
    expect(screen.queryByText("The package audit failed.")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /1 more step/i }));
    expect(screen.getByText("Prepared the correction workspace.")).toBeTruthy();
  });
});
