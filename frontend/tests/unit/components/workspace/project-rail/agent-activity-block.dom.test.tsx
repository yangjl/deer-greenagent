/**
 * The compact block, rendered.
 *
 * Three of these rules are the ones a refactor breaks silently: exactly one
 * spinner, the block is never itself a live region, and every line truncates so
 * the block's height cannot change as work moves between actors.
 */

import { afterEach, describe, expect, it } from "@rstest/core";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

import { AgentActivityBlock } from "@/components/workspace/project-rail/agent-activity-block";
import { ActivityProvider, useActivityContext } from "@/core/activity";

// Rstest does not auto-unmount between tests, and `screen` queries the whole
// document — without this every later query matches several renders at once.
afterEach(cleanup);

function event(overrides: Record<string, unknown> = {}) {
  return {
    type: "agent_activity",
    version: 1,
    transition: "started",
    activity_id: "act_one",
    parent_activity_id: null,
    dispatcher_activity_id: null,
    run_id: "run-1",
    actor_kind: "lead_agent",
    actor_id: "lead-agent",
    display_name: "Lead agent",
    state: "thinking",
    operation: "Working on your request",
    scope: { cycle_id: null, stage: null, task_id: null },
    ...overrides,
  };
}

/** Exposes the provider's `push` so a test can feed frames inside `act`. */
function Feed({
  onReady,
}: {
  onReady: (push: (frame: unknown) => void) => void;
}) {
  const { push } = useActivityContext();
  onReady(push);
  return null;
}

function renderBlock(
  frames: readonly unknown[] = [],
  props: Partial<React.ComponentProps<typeof AgentActivityBlock>> = {},
) {
  let push: (frame: unknown) => void = () => undefined;
  const result = render(
    <ActivityProvider>
      <Feed
        onReady={(fn) => {
          push = fn;
        }}
      />
      <AgentActivityBlock renderSheet={false} {...props} />
    </ActivityProvider>,
  );
  act(() => {
    for (const frame of frames) push(frame);
  });
  return result;
}

describe("exactly one thing moves", () => {
  it("spins for an actor that is genuinely working", () => {
    const { container } = renderBlock([event({ state: "computing" })]);
    expect(container.querySelectorAll(".animate-spin").length).toBe(1);
  });

  it("does not spin while waiting", () => {
    const { container } = renderBlock([event({ state: "waiting" })]);
    expect(container.querySelectorAll(".animate-spin").length).toBe(0);
  });

  it("does not spin once the work has settled", () => {
    const { container } = renderBlock([
      event(),
      event({ transition: "completed", state: "completed" }),
    ]);
    expect(container.querySelectorAll(".animate-spin").length).toBe(0);
  });

  it("carries the reduced-motion opt-out on every animated indicator", () => {
    // Under reduced motion the state word alone carries the meaning, which it
    // already does for every other reader.
    const { container } = renderBlock([event({ state: "computing" })]);
    for (const node of container.querySelectorAll(".animate-spin")) {
      expect(node.className).toContain("motion-reduce:animate-none");
    }
  });

  it("shows one spinner even with several workers running", () => {
    const { container } = renderBlock([
      event({ activity_id: "act_a", state: "computing", display_name: "W1" }),
      event({ activity_id: "act_b", state: "computing", display_name: "W2" }),
      event({ activity_id: "act_c", state: "computing", display_name: "W3" }),
    ]);
    expect(container.querySelectorAll(".animate-spin").length).toBe(1);
  });

  it("animates only the selected leaf in the expanded activity sheet", () => {
    renderBlock(
      [
        event({ activity_id: "act_a", state: "computing", display_name: "W1" }),
        event({ activity_id: "act_b", state: "computing", display_name: "W2" }),
        event({ activity_id: "act_c", state: "computing", display_name: "W3" }),
      ],
      { renderSheet: true },
    );

    fireEvent.click(screen.getByRole("button", { name: /Activity · 3 steps/ }));
    expect(
      screen.getByRole("dialog").querySelectorAll(".animate-spin").length,
    ).toBe(1);
  });
});

describe("the block is never a live region", () => {
  it("puts announcements in a sibling status node, not the block", () => {
    const { container } = renderBlock([event()]);
    const block = screen.getByTestId("agent-activity-block");
    expect(block.getAttribute("aria-live")).toBeNull();
    expect(block.querySelector("[aria-live]")).toBeNull();

    const region = container.querySelector(
      '[role="status"][aria-live="polite"]',
    );
    expect(region).not.toBeNull();
    expect(block.contains(region)).toBe(false);
  });
});

describe("what the row says", () => {
  it("names the actor and its state in words", () => {
    renderBlock([
      event({ display_name: "Build worker 2", state: "computing" }),
    ]);
    expect(screen.getByText("Build worker 2")).toBeTruthy();
    expect(screen.getByText("Computing")).toBeTruthy();
  });

  it("states authority in words on its own line", () => {
    renderBlock([
      event({ activity_id: "act_stage", display_name: "Build stage" }),
      event({
        activity_id: "act_w1",
        display_name: "Build worker 1",
        parent_activity_id: "act_stage",
        state: "computing",
      }),
    ]);
    expect(screen.getByText(/via Build stage/)).toBeTruthy();
  });

  it("adds the cycle when the activity carries one", () => {
    renderBlock(
      [
        event({ activity_id: "act_stage", display_name: "Build stage" }),
        event({
          activity_id: "act_w1",
          display_name: "Build worker 1",
          parent_activity_id: "act_stage",
          state: "computing",
          scope: { cycle_id: "cyc-1", stage: "build", task_id: null },
        }),
      ],
      { cycleLabel: () => "Cycle 01" },
    );
    expect(screen.getByText(/via Build stage · Cycle 01/)).toBeTruthy();
  });

  it("summarizes one sibling by name and more by count", () => {
    renderBlock([
      event({ activity_id: "act_a", state: "computing", display_name: "W1" }),
      event({ activity_id: "act_b", state: "computing", display_name: "W2" }),
    ]);
    expect(screen.getByText("Also running: W1")).toBeTruthy();
  });

  it("says the deferred roster is still coming", () => {
    // A deferred feature must not be silently cancelled by a different one
    // shipping in its place.
    renderBlock();
    expect(
      screen.getByText(/Breeding agents are assigned per project/),
    ).toBeTruthy();
  });
});

describe("the four resting states each read differently", () => {
  it("idle names the lead agent with no spinner", () => {
    const { container } = renderBlock();
    expect(screen.getByText("Lead agent")).toBeTruthy();
    expect(screen.getByText("Idle")).toBeTruthy();
    expect(container.querySelectorAll(".animate-spin").length).toBe(0);
  });

  it("unavailable is stated, never guessed as active", () => {
    renderBlock([event({ state: "computing" })], { available: false });
    expect(screen.getByText("Status unavailable")).toBeTruthy();
  });
});

describe("the expand control", () => {
  it("carries aria-expanded and aria-controls", () => {
    renderBlock([event()]);
    const button = screen.getByRole("button", { name: /Activity/ });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(button.getAttribute("aria-controls")).toBeTruthy();
  });

  it("is disabled when there is nothing to show", () => {
    renderBlock();
    const button = screen.getByRole("button", { name: /No activity yet/ });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("counts the steps recorded so far", () => {
    renderBlock([
      event(),
      event({ transition: "updated", state: "computing" }),
    ]);
    expect(screen.getByRole("button", { name: /2 steps/ })).toBeTruthy();
  });
});
