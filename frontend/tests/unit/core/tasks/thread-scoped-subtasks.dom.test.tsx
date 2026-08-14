import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import {
  ThreadScopedSubtasksProvider,
  useReconcileSubtasks,
  useSubtaskContext,
} from "@/core/tasks/context";

afterEach(cleanup);

function TaskLedgerHarness() {
  const { tasks, setTasks } = useSubtaskContext();
  return (
    <>
      <output aria-label="task count">{Object.keys(tasks).length}</output>
      <button
        type="button"
        onClick={() =>
          setTasks({
            chair: {
              id: "chair",
              status: "completed",
              subagent_type: "experimental-design",
              description: "Chair synthesis",
              prompt: "Synthesize.",
            },
          })
        }
      >
        Record meeting
      </button>
    </>
  );
}

function DurableReconciliationHarness() {
  const { tasks, setTasks } = useSubtaskContext();
  const reconcile = useReconcileSubtasks();
  const task = tasks.worker;
  return (
    <>
      <output aria-label="worker status">{task?.status ?? "missing"}</output>
      <button
        type="button"
        onClick={() =>
          setTasks({
            worker: {
              id: "worker",
              status: "in_progress",
              subagent_type: "subagent",
              description: "Build work",
              prompt: "",
              dbtlStage: "build",
            },
          })
        }
      >
        Stream start
      </button>
      <button
        type="button"
        onClick={() =>
          reconcile([
            {
              id: "worker",
              status: "completed",
              displaySummary: "Build finished.",
            },
          ])
        }
      >
        Reconcile terminal
      </button>
    </>
  );
}

function ScopedHarness({ threadId }: { threadId: string }) {
  return (
    <ThreadScopedSubtasksProvider scopeKey={threadId}>
      <TaskLedgerHarness />
    </ThreadScopedSubtasksProvider>
  );
}

describe("ThreadScopedSubtasksProvider", () => {
  it("keeps a meeting in its conversation and starts a new chat clean", () => {
    const view = render(<ScopedHarness threadId="thread-with-meeting" />);

    fireEvent.click(screen.getByRole("button", { name: "Record meeting" }));
    expect(screen.getByLabelText("task count").textContent).toBe("1");

    view.rerender(<ScopedHarness threadId="new" />);

    expect(screen.getByLabelText("task count").textContent).toBe("0");
  });

  it("retains live tasks while the route still represents the same chat", () => {
    const view = render(<ScopedHarness threadId="new" />);

    fireEvent.click(screen.getByRole("button", { name: "Record meeting" }));
    view.rerender(<ScopedHarness threadId="new" />);

    expect(screen.getByLabelText("task count").textContent).toBe("1");
  });

  it("publishes an async durable terminal transition immediately", () => {
    render(
      <ThreadScopedSubtasksProvider scopeKey="thread-build">
        <DurableReconciliationHarness />
      </ThreadScopedSubtasksProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Stream start" }));
    expect(screen.getByLabelText("worker status").textContent).toBe(
      "in_progress",
    );
    fireEvent.click(screen.getByRole("button", { name: "Reconcile terminal" }));
    expect(screen.getByLabelText("worker status").textContent).toBe(
      "completed",
    );
  });
});
