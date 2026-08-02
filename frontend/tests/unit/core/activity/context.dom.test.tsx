import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

rs.mock("@/core/activity/api", () => ({
  fetchActivityPage: rs.fn(),
}));

import { fetchActivityPage } from "@/core/activity/api";
import { ActivityProvider, useActivityContext } from "@/core/activity/context";

const mockedFetchActivityPage = rs.mocked(fetchActivityPage);

afterEach(() => {
  cleanup();
  mockedFetchActivityPage.mockReset();
});

function event() {
  return {
    type: "agent_activity" as const,
    version: 1,
    transition: "started" as const,
    activity_id: "act_one",
    parent_activity_id: null,
    dispatcher_activity_id: null,
    run_id: "run-1",
    actor_kind: "lead_agent" as const,
    actor_id: "lead-agent",
    display_name: "Lead agent",
    state: "thinking" as const,
    operation: "Working on your request",
    scope: { cycle_id: null, stage: null, task_id: null },
  };
}

function State() {
  const { projection, dataAvailable } = useActivityContext();
  return (
    <div>
      <span>{projection.rows.act_one?.state ?? "empty"}</span>
      <span>{dataAvailable ? "available" : "unavailable"}</span>
    </div>
  );
}

describe("ActivityProvider durable reconciliation", () => {
  test("hydrates the conversation projection on mount", async () => {
    mockedFetchActivityPage.mockResolvedValueOnce({
      events: [{ seq: 1, content: event() }],
      nextBeforeSeq: null,
    });

    render(
      <ActivityProvider threadId="thread-1">
        <State />
      </ActivityProvider>,
    );

    await waitFor(() => expect(screen.getByText("thinking")).toBeTruthy());
    expect(mockedFetchActivityPage).toHaveBeenCalledWith("thread-1");
  });

  test("distinguishes a failed authoritative read from idle", async () => {
    mockedFetchActivityPage.mockRejectedValueOnce(new Error("offline"));

    render(
      <ActivityProvider threadId="thread-1">
        <State />
      </ActivityProvider>,
    );

    await waitFor(() => expect(screen.getByText("unavailable")).toBeTruthy());
  });
});
