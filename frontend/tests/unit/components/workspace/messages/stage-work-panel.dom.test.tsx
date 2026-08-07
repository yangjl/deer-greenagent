import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

rs.mock("@/core/tasks/api", () => ({
  fetchStageWorkers: rs.fn(),
  StageWorkerFetchError: class StageWorkerFetchError extends Error {
    constructor(public readonly status: number) {
      super(`Failed to load stage workers: ${status}`);
    }
  },
}));

rs.mock("@/components/workspace/messages/subtask-card", () => ({
  SubtaskCard: ({ taskId }: { taskId: string }) => <div>{taskId}</div>,
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      subtasks: {
        stageTokenCapped: "Stopped at the token budget.",
        stageTurnCapped: "Stopped at the turn limit.",
        stageLoopCapped: "Stopped at the loop guard.",
      },
    },
  }),
}));

import { StageWorkPanel } from "@/components/workspace/messages/stage-work-panel";
import { fetchStageWorkers, StageWorkerFetchError } from "@/core/tasks/api";
import { SubtaskContext, SubtasksProvider } from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";

const mockedFetchStageWorkers = rs.mocked(fetchStageWorkers);

function worker(taskId: string, runId: string) {
  return {
    taskId,
    runId,
    description: "Build worker",
    dbtlStage: "build",
    status: "completed" as const,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function SeededTasks({
  tasks,
  children,
}: {
  tasks: Record<string, Subtask>;
  children: React.ReactNode;
}) {
  return (
    <SubtaskContext.Provider
      value={{
        tasks,
        tasksRef: { current: tasks },
        setTasks: () => {
          /* static test fixture */
        },
      }}
    >
      {children}
    </SubtaskContext.Provider>
  );
}

afterEach(() => {
  cleanup();
  mockedFetchStageWorkers.mockReset();
  rs.useRealTimers();
});

describe("StageWorkPanel durable hydration", () => {
  it("retries a failed hydration without waiting for unrelated state", async () => {
    mockedFetchStageWorkers
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce([worker("worker-retried", "run-1")]);

    render(
      <SubtasksProvider>
        <StageWorkPanel threadId="thread-1" runId="run-1" isLoading={false} />
      </SubtasksProvider>,
    );
    await waitFor(() =>
      expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(1),
    );
    expect(
      await screen.findByText("worker-retried", {}, { timeout: 2_500 }),
    ).toBeTruthy();
    expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(2);
  });

  it("does not retry a permanent client error", async () => {
    mockedFetchStageWorkers.mockRejectedValueOnce(
      new StageWorkerFetchError(403),
    );

    render(
      <SubtasksProvider>
        <StageWorkPanel threadId="thread-1" runId="run-1" isLoading={false} />
      </SubtasksProvider>,
    );
    await waitFor(() =>
      expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(1),
    );
    await act(
      () => new Promise((resolve) => globalThis.setTimeout(resolve, 700)),
    );
    expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(1);
  });

  it("retries a transient timeout or rate-limit response", async () => {
    mockedFetchStageWorkers
      .mockRejectedValueOnce(new StageWorkerFetchError(429))
      .mockResolvedValueOnce([worker("worker-after-rate-limit", "run-1")]);

    render(
      <SubtasksProvider>
        <StageWorkPanel threadId="thread-1" runId="run-1" isLoading={false} />
      </SubtasksProvider>,
    );

    expect(
      await screen.findByText(
        "worker-after-rate-limit",
        {},
        { timeout: 2_000 },
      ),
    ).toBeTruthy();
    expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(2);
  });

  it("ignores a hydration result from the previous thread epoch", async () => {
    const oldRequest =
      deferred<Awaited<ReturnType<typeof fetchStageWorkers>>>();
    mockedFetchStageWorkers
      .mockReturnValueOnce(oldRequest.promise)
      .mockResolvedValueOnce([worker("worker-new", "run-new")]);

    const view = render(
      <SubtasksProvider>
        <StageWorkPanel
          threadId="thread-old"
          runId="run-old"
          isLoading={false}
        />
      </SubtasksProvider>,
    );
    await waitFor(() =>
      expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(1),
    );

    view.rerender(
      <SubtasksProvider>
        <StageWorkPanel
          threadId="thread-new"
          runId="run-new"
          isLoading={false}
        />
      </SubtasksProvider>,
    );
    await waitFor(() =>
      expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(2),
    );
    expect(await screen.findByText("worker-new")).toBeTruthy();

    oldRequest.resolve([worker("worker-stale", "run-old")]);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByText("worker-stale")).toBeNull();
  });

  it("rehydrates after a live run cancels an in-flight read in the same epoch", async () => {
    const staleRequest =
      deferred<Awaited<ReturnType<typeof fetchStageWorkers>>>();
    mockedFetchStageWorkers
      .mockReturnValueOnce(staleRequest.promise)
      .mockResolvedValueOnce([worker("worker-settled", "run-1")]);

    const view = render(
      <SubtasksProvider>
        <StageWorkPanel threadId="thread-1" runId="run-1" isLoading={false} />
      </SubtasksProvider>,
    );
    await waitFor(() =>
      expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(1),
    );

    view.rerender(
      <SubtasksProvider>
        <StageWorkPanel threadId="thread-1" runId="run-1" isLoading />
      </SubtasksProvider>,
    );
    staleRequest.resolve([worker("worker-stale", "run-1")]);

    view.rerender(
      <SubtasksProvider>
        <StageWorkPanel threadId="thread-1" runId="run-1" isLoading={false} />
      </SubtasksProvider>,
    );

    expect(await screen.findByText("worker-settled")).toBeTruthy();
    expect(screen.queryByText("worker-stale")).toBeNull();
    expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(2);
  });
});

describe("StageWorkPanel live run selection", () => {
  it("places each worker's model prose directly after its own card", () => {
    mockedFetchStageWorkers.mockResolvedValue([]);
    const implementation: Subtask = {
      id: "implementation",
      status: "completed",
      subagent_type: "subagent",
      description: "Implementation specialist",
      prompt: "",
      dbtlStage: "build",
      runId: "run-1",
      displaySummary: "Implemented the exact fixture.",
    };
    const audit: Subtask = {
      id: "audit",
      status: "completed",
      subagent_type: "subagent",
      description: "Independent audit specialist",
      prompt: "",
      dbtlStage: "build",
      runId: "run-1",
      displaySummary: "The independent rerun passed.",
    };

    const { container } = render(
      <SeededTasks tasks={{ implementation, audit }}>
        <StageWorkPanel threadId="thread-1" runId="run-1" isLoading={false} />
      </SeededTasks>,
    );

    expect(container.textContent).toMatch(
      /implementation.*Implemented the exact fixture\..*audit.*The independent rerun passed\./s,
    );
  });

  it("does not narrate a worker before it finishes", () => {
    const running: Subtask = {
      id: "running-phase",
      status: "in_progress",
      subagent_type: "subagent",
      description: "Implementation specialist",
      prompt: "",
      dbtlStage: "build",
      runId: "run-1",
      displaySummary: "Partial output must not read as a result.",
    };

    render(
      <SeededTasks tasks={{ running }}>
        <StageWorkPanel threadId="thread-1" runId="run-1" isLoading />
      </SeededTasks>,
    );

    expect(
      screen.queryByText("Partial output must not read as a result."),
    ).toBeNull();
  });

  it("narrates the bounded stop reason instead of partial-success prose", () => {
    mockedFetchStageWorkers.mockResolvedValue([]);
    const capped: Subtask = {
      id: "capped-phase",
      status: "failed",
      subagent_type: "subagent",
      description: "Implementation specialist",
      prompt: "",
      dbtlStage: "build",
      runId: "run-1",
      stopReason: "token_capped",
      error: "Implemented every requested output.",
    };

    render(
      <SeededTasks tasks={{ capped }}>
        <StageWorkPanel threadId="thread-1" runId="run-1" isLoading={false} />
      </SeededTasks>,
    );

    expect(screen.getByText("Stopped at the token budget.")).toBeTruthy();
    expect(
      screen.queryByText("Implemented every requested output."),
    ).toBeNull();
  });

  it("follows the streamed worker instead of the previous transcript run", async () => {
    const oldTask: Subtask = {
      id: "old-planner",
      status: "completed",
      subagent_type: "subagent",
      description: "Build planner",
      prompt: "",
      dbtlStage: "build",
      runId: "run-old",
    };
    const liveTask: Subtask = {
      id: "live-phase",
      status: "in_progress",
      subagent_type: "subagent",
      description: "Run phase",
      prompt: "",
      dbtlStage: "build",
      runId: "run-live",
    };
    const view = render(
      <SeededTasks tasks={{ old: oldTask, live: liveTask }}>
        <StageWorkPanel threadId="thread-1" runId="run-old" isLoading />
      </SeededTasks>,
    );

    expect(screen.getByText("live-phase")).toBeTruthy();
    expect(screen.queryByText("old-planner")).toBeNull();
    await waitFor(() => expect(screen.getByText("live-phase")).toBeTruthy());

    view.rerender(
      <SeededTasks
        tasks={{
          old: oldTask,
          live: {
            ...liveTask,
            status: "failed",
            error: "Stopped",
          },
        }}
      >
        <StageWorkPanel threadId="thread-1" runId="run-old" isLoading />
      </SeededTasks>,
    );

    expect(screen.getByText("live-phase")).toBeTruthy();
    expect(screen.queryByText("old-planner")).toBeNull();
  });
});
