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

import { StageWorkHydrator } from "@/components/workspace/messages/stage-work-panel";
import { fetchStageWorkers, StageWorkerFetchError } from "@/core/tasks/api";
import { SubtasksProvider, useSubtaskContext } from "@/core/tasks/context";

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

/**
 * What the hydrator actually produces: task ids in the store.
 *
 * The hydrator renders nothing itself, so these cases assert on the store
 * rather than on card markup — the guarantee under test is that a reload
 * rebuilds the workers, not how they are drawn.
 */
function HydratedTaskIds() {
  const { tasks } = useSubtaskContext();
  return (
    <>
      {Object.keys(tasks).map((id) => (
        <span key={id}>{id}</span>
      ))}
    </>
  );
}

afterEach(() => {
  cleanup();
  mockedFetchStageWorkers.mockReset();
  rs.useRealTimers();
});

describe("StageWorkHydrator durable hydration", () => {
  it("retries a failed hydration without waiting for unrelated state", async () => {
    mockedFetchStageWorkers
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce([worker("worker-retried", "run-1")]);

    render(
      <SubtasksProvider>
        <StageWorkHydrator threadId="thread-1" isLoading={false} />
        <HydratedTaskIds />
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
        <StageWorkHydrator threadId="thread-1" isLoading={false} />
        <HydratedTaskIds />
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
        <StageWorkHydrator threadId="thread-1" isLoading={false} />
        <HydratedTaskIds />
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
        <StageWorkHydrator threadId="thread-old" isLoading={false} />
        <HydratedTaskIds />
      </SubtasksProvider>,
    );
    await waitFor(() =>
      expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(1),
    );

    view.rerender(
      <SubtasksProvider>
        <StageWorkHydrator threadId="thread-new" isLoading={false} />
        <HydratedTaskIds />
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
      .mockResolvedValue([worker("worker-settled", "run-1")]);

    const view = render(
      <SubtasksProvider>
        <StageWorkHydrator threadId="thread-1" isLoading={false} />
        <HydratedTaskIds />
      </SubtasksProvider>,
    );
    await waitFor(() =>
      expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(1),
    );

    view.rerender(
      <SubtasksProvider>
        <StageWorkHydrator threadId="thread-1" isLoading />
        <HydratedTaskIds />
      </SubtasksProvider>,
    );
    staleRequest.resolve([worker("worker-stale", "run-1")]);

    view.rerender(
      <SubtasksProvider>
        <StageWorkHydrator threadId="thread-1" isLoading={false} />
        <HydratedTaskIds />
      </SubtasksProvider>,
    );

    expect(await screen.findByText("worker-settled")).toBeTruthy();
    expect(screen.queryByText("worker-stale")).toBeNull();
    expect(mockedFetchStageWorkers).toHaveBeenCalledTimes(2);
  });
});
