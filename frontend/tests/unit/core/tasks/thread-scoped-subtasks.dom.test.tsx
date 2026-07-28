import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import {
  ThreadScopedSubtasksProvider,
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
});
