import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
