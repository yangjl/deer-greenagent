import { afterEach, describe, expect, it, rs } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useRef, useState } from "react";

rs.mock("@/core/tasks/api", () => ({
  fetchSubtaskSteps: rs.fn(),
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
    },
  }),
}));

rs.mock("@/core/models/hooks", () => ({
  useModels: () => ({ models: [], tokenUsageEnabled: false }),
}));

import { SubtaskCard } from "@/components/workspace/messages/subtask-card";
import { fetchSubtaskSteps } from "@/core/tasks/api";
import { SubtaskContext } from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";

const mockedFetchSteps = rs.mocked(fetchSubtaskSteps);

function Harness() {
  const [tasks, setTasks] = useState<Record<string, Subtask>>({
    planner: {
      id: "planner",
      status: "completed",
      subagent_type: "subagent",
      description: "Build planner",
      prompt: "",
      dbtlStage: "build",
      runId: "run-old",
    },
  });
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;
  const task = tasks.planner!;
  return (
    <SubtaskContext.Provider value={{ tasks, tasksRef, setTasks }}>
      <button
        type="button"
        onClick={() =>
          setTasks({
            planner: {
              id: "planner",
              status: "in_progress",
              subagent_type: "subagent",
              description: "Build planner",
              prompt: "",
              dbtlStage: "build",
              runId: "run-new",
            },
          })
        }
      >
        Start newer run
      </button>
      <SubtaskCard
        taskId="planner"
        threadId="thread-1"
        runId={task.runId}
        isLoading={task.status === "in_progress"}
      />
    </SubtaskContext.Provider>
  );
}

function CardHarness({ task }: { task: Subtask }) {
  const tasks = { [task.id]: task };
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;
  return (
    <SubtaskContext.Provider
      value={{
        tasks,
        tasksRef,
        setTasks: () => {
          /* static test fixture */
        },
      }}
    >
      <SubtaskCard taskId={task.id} isLoading={false} />
    </SubtaskContext.Provider>
  );
}

function step(text: string, messageIndex: number) {
  return {
    kind: "ai" as const,
    message_index: messageIndex,
    text,
    truncated: false,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
  mockedFetchSteps.mockReset();
});

describe("SubtaskCard historical step backfill", () => {
  it("backfills again when the same task id starts in a newer run", async () => {
    mockedFetchSteps.mockResolvedValue([]);
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: /Build planner/i }));
    await waitFor(() => expect(mockedFetchSteps).toHaveBeenCalledTimes(1));
    expect(mockedFetchSteps.mock.calls[0]?.slice(0, 3)).toEqual([
      "thread-1",
      "run-old",
      "planner",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Start newer run" }));
    await waitFor(() => expect(mockedFetchSteps).toHaveBeenCalledTimes(2));
    expect(mockedFetchSteps.mock.calls[1]?.slice(0, 3)).toEqual([
      "thread-1",
      "run-new",
      "planner",
    ]);
  });

  it("ignores an old run backfill that resolves after a newer run starts", async () => {
    const oldFetch = deferred<Awaited<ReturnType<typeof fetchSubtaskSteps>>>();
    mockedFetchSteps
      .mockReturnValueOnce(oldFetch.promise)
      .mockResolvedValueOnce([step("new run step", 1)]);
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: /Build planner/i }));
    await waitFor(() => expect(mockedFetchSteps).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Start newer run" }));

    expect(await screen.findByText("new run step")).toBeTruthy();
    oldFetch.resolve([step("stale old run step", 99)]);
    await Promise.resolve();

    expect(screen.queryByText("stale old run step")).toBeNull();
  });
});

describe("SubtaskCard governed progress report", () => {
  it("surfaces a completed stage summary while the details stay collapsed", () => {
    render(
      <CardHarness
        task={{
          id: "planner-report",
          status: "completed",
          subagent_type: "subagent",
          description: "Build planner",
          prompt: "",
          dbtlStage: "build",
          displaySummary: "Build plan ready · 5 phases",
        }}
      />,
    );

    expect(screen.getByText("Progress report")).toBeTruthy();
    expect(screen.getByText("Build plan ready · 5 phases")).toBeTruthy();
  });

  it("surfaces a stopped stage report while the details stay collapsed", () => {
    render(
      <CardHarness
        task={{
          id: "failed-phase",
          status: "failed",
          subagent_type: "subagent",
          description: "Reproduce the pilot population",
          prompt: "",
          dbtlStage: "build",
          error: "Stopped after reaching the phase token budget.",
        }}
      />,
    );

    expect(screen.getByText("Failure report")).toBeTruthy();
    expect(
      screen.getByText("Stopped after reaching the phase token budget."),
    ).toBeTruthy();
  });

  it("does not paint a capped worker's partial-success summary as its failure", () => {
    render(
      <CardHarness
        task={{
          id: "capped-phase",
          status: "failed",
          subagent_type: "subagent",
          description: "Reproduce the pilot population",
          prompt: "",
          dbtlStage: "build",
          stopReason: "token_capped",
          error: "Implemented and verified every requested output.",
        }}
      />,
    );

    expect(screen.getByText("Stopped at the token budget.")).toBeTruthy();
    expect(
      screen.queryByText("Implemented and verified every requested output."),
    ).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: /Reproduce the pilot population/i }),
    );
    expect(screen.getByText("Stopped at the token budget.")).toBeTruthy();
    expect(
      screen.queryByText("Implemented and verified every requested output."),
    ).toBeNull();
  });

  it("keeps ordinary subtask results behind their disclosure", () => {
    render(
      <CardHarness
        task={{
          id: "ordinary-task",
          status: "completed",
          subagent_type: "subagent",
          description: "Ordinary delegated work",
          prompt: "",
          result: "Private detailed result",
        }}
      />,
    );

    expect(screen.queryByText("Progress report")).toBeNull();
    expect(screen.queryByText("Private detailed result")).toBeNull();
  });
});
