import { beforeEach, describe, expect, rs, test } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "/backend",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import { fetchStageWorkers, fetchSubtaskSteps } from "@/core/tasks/api";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: status >= 400 ? "Error" : "OK",
    headers: { "Content-Type": "application/json" },
  });
}

function stepEvent(seq: number, messageIndex: number, toolName: string) {
  return {
    event_type: "subagent.step",
    seq,
    content: {
      task_id: "A",
      message_index: messageIndex,
      kind: "tool",
      text: "",
      tool_name: toolName,
    },
    metadata: { task_id: "A", message_index: messageIndex },
  };
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("fetchSubtaskSteps", () => {
  test("scopes the request to the task and only fetches subagent.step", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, []));

    await fetchSubtaskSteps("thread 1", "run/1", "task-A");

    const url = mockedFetch.mock.calls[0]![0] as string;
    expect(url).toContain(
      "/backend/api/threads/thread%201/runs/run%2F1/events",
    );
    expect(url).toContain("task_id=task-A");
    expect(url).toContain("event_types=subagent.step");
    expect(url).toContain("limit=");
    expect(url).not.toContain("after_seq");
  });

  test("pages forward with after_seq until a short page, accumulating in order", async () => {
    mockedFetch
      .mockResolvedValueOnce(
        jsonResponse(200, [
          stepEvent(10, 0, "web_search"),
          stepEvent(11, 1, "read_file"),
        ]),
      )
      .mockResolvedValueOnce(jsonResponse(200, [stepEvent(12, 2, "bash")]));

    const steps = await fetchSubtaskSteps("t", "r", "A", 2);

    expect(steps.map((s) => s.message_index)).toEqual([0, 1, 2]);
    expect(steps.map((s) => s.tool_name)).toEqual([
      "web_search",
      "read_file",
      "bash",
    ]);
    expect(mockedFetch).toHaveBeenCalledTimes(2);
    expect(mockedFetch.mock.calls[0]![0] as string).not.toContain("after_seq");
    expect(mockedFetch.mock.calls[1]![0] as string).toContain("after_seq=11");
  });

  test("stops after a single page when it is shorter than the page size", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, [stepEvent(10, 0, "web_search")]),
    );

    const steps = await fetchSubtaskSteps("t", "r", "A", 500);

    expect(steps).toHaveLength(1);
    expect(mockedFetch).toHaveBeenCalledTimes(1);
  });

  test("throws when a page request fails", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(500, { detail: "boom" }));

    await expect(fetchSubtaskSteps("t", "r", "A")).rejects.toThrow();
  });
});

describe("fetchStageWorkers", () => {
  test("pages across runs and folds durable terminal details", async () => {
    mockedFetch
      .mockResolvedValueOnce(
        jsonResponse(200, {
          events: [
            {
              seq: 20,
              run_id: "run-2",
              event_type: "subagent.start",
              content: {
                task_id: "build-2",
                description: "Build phase two",
                dbtl_stage: "build",
              },
            },
            {
              seq: 21,
              run_id: "run-2",
              event_type: "subagent.end",
              content: {
                task_id: "build-2",
                status: "completed",
                result: '{"status":"completed"}',
                display_summary: "Validated the population output.",
                model_name: "gpt-5.6-sol",
                usage: {
                  input_tokens: 10,
                  output_tokens: 4,
                  total_tokens: 14,
                },
              },
            },
          ],
          next_before_seq: 20,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          events: [
            {
              seq: 3,
              run_id: "run-1",
              event_type: "subagent.start",
              content: {
                task_id: "build-1",
                description: "Build phase one",
                dbtl_stage: "build",
              },
            },
            {
              seq: 4,
              run_id: "run-1",
              event_type: "subagent.end",
              content: {
                task_id: "build-1",
                status: "failed",
                error: "Execution stopped.",
              },
            },
            {
              seq: 5,
              run_id: "run-1",
              event_type: "subagent.start",
              content: { task_id: "ordinary", description: "Research" },
            },
          ],
          next_before_seq: null,
        }),
      );

    const workers = await fetchStageWorkers("thread 1", 2);

    expect(workers).toHaveLength(2);
    expect(workers[0]).toMatchObject({
      taskId: "build-1",
      runId: "run-1",
      status: "failed",
      error: "Execution stopped.",
    });
    expect(workers[1]).toMatchObject({
      taskId: "build-2",
      runId: "run-2",
      status: "completed",
      displaySummary: "Validated the population output.",
      modelName: "gpt-5.6-sol",
      usage: { inputTokens: 10, outputTokens: 4, totalTokens: 14 },
    });
    expect(mockedFetch.mock.calls[0]![0] as string).toContain(
      "/backend/api/threads/thread%201/stage-worker-events?limit=2",
    );
    expect(mockedFetch.mock.calls[1]![0] as string).toContain(
      "before_seq=20",
    );
  });

  test("keeps a persisted meeting seat, tagged so the stage lane can skip it", async () => {
    /* Dropping seats here is what made a meeting unrecoverable: it exists in
     * the database, and the one path that reads the database threw it away on
     * the grounds that the live-only DebatePanel would draw it. A reload, a
     * deck-started round, or any run the browser did not watch then had
     * nothing to render. The record now carries its seat identity and the
     * stage lane filters on that (`stageWorkGroups` already excludes any task
     * with a `councilSeat`), so one participant is still never drawn twice. */
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        events: [
          {
            seq: 1,
            run_id: "run-design",
            event_type: "subagent.start",
            content: {
              task_id: "chair-1",
              description: "Chair synthesis",
              dbtl_stage: "design",
              council_seat: {
                stage: "design",
                role: "chair",
                role_label: "Chair",
                agent_name: "general-purpose",
                round: 1,
              },
            },
          },
        ],
        next_before_seq: null,
      }),
    );

    const workers = await fetchStageWorkers("thread-1");

    expect(workers).toHaveLength(1);
    expect(workers[0]!.taskId).toBe("chair-1");
    expect(workers[0]!.runId).toBe("run-design");
    expect(workers[0]!.councilSeat?.role).toBe("chair");
  });

  test("shows an unreadable seat as stage work rather than losing it", async () => {
    /* `readCouncilSeat` is strict (a seat needs a role and an agent). A seat
     * it cannot read used to be discarded with every other seat; it now falls
     * through to the stage lane, because a worker in the wrong lane is
     * recoverable and a worker nobody can see is not. */
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        events: [
          {
            seq: 1,
            run_id: "run-design",
            event_type: "subagent.start",
            content: {
              task_id: "broken-1",
              description: "Malformed seat",
              dbtl_stage: "design",
              council_seat: { role: "chair" },
            },
          },
        ],
        next_before_seq: null,
      }),
    );

    const workers = await fetchStageWorkers("thread-1");

    expect(workers).toHaveLength(1);
    expect(workers[0]!.councilSeat).toBeUndefined();
  });

  test("still ignores an ordinary delegated subtask with no stage", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        events: [
          {
            seq: 1,
            run_id: "run-design",
            event_type: "subagent.start",
            content: {
              task_id: "chair-1",
              description: "Chair synthesis",
            },
          },
        ],
        next_before_seq: null,
      }),
    );

    await expect(fetchStageWorkers("thread-1")).resolves.toEqual([]);
  });

  test("keeps only the newest execution when a later run reuses a task id", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        events: [
          {
            seq: 1,
            run_id: "run-old",
            event_type: "subagent.start",
            content: {
              task_id: "attempt-plan",
              description: "Build planner",
              dbtl_stage: "build",
            },
          },
          {
            seq: 2,
            run_id: "run-old",
            event_type: "subagent.end",
            content: { task_id: "attempt-plan", status: "completed" },
          },
          {
            seq: 10,
            run_id: "run-new",
            event_type: "subagent.start",
            content: {
              task_id: "attempt-plan",
              description: "Build planner",
              dbtl_stage: "build",
            },
          },
        ],
        next_before_seq: null,
      }),
    );

    await expect(fetchStageWorkers("thread-1")).resolves.toEqual([
      expect.objectContaining({
        taskId: "attempt-plan",
        runId: "run-new",
        status: "in_progress",
      }),
    ]);
  });
});
