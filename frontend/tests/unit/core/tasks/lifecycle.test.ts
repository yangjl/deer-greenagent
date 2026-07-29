import { describe, expect, it } from "@rstest/core";

import { taskEventToSubtaskUpdate } from "@/core/tasks/lifecycle";

describe("taskEventToSubtaskUpdate", () => {
  it("maps a task-start event to the effective model for that task", () => {
    expect(
      taskEventToSubtaskUpdate({
        type: "task_started",
        task_id: "call-1",
        description: "Research auth",
        model_name: "claude-3-7-sonnet",
      }),
    ).toEqual({
      id: "call-1",
      status: "in_progress",
      description: "Research auth",
      prompt: "",
      subagent_type: "subagent",
      modelName: "claude-3-7-sonnet",
    });
  });

  it("maps a running event to its cumulative token snapshot", () => {
    expect(
      taskEventToSubtaskUpdate({
        type: "task_running",
        task_id: "call-1",
        model_name: "claude-3-7-sonnet",
        usage: {
          input_tokens: 100,
          output_tokens: 20,
          total_tokens: 120,
        },
      }),
    ).toEqual({
      id: "call-1",
      modelName: "claude-3-7-sonnet",
      usage: {
        inputTokens: 100,
        outputTokens: 20,
        totalTokens: 120,
      },
    });
  });

  it("marks a council seat completed from its terminal custom event", () => {
    expect(
      taskEventToSubtaskUpdate({
        type: "task_completed",
        task_id: "chair-1",
        result:
          '{"status":"completed","summary":"Use two seasons.","consensus":{"agreements":["Use checks."],"disagreements":[],"open_questions":[]}}',
        council_seat: {
          role: "chair",
          role_label: "Council chair",
          focus: "synthesis",
          capability: "design_council_chair",
          agent_name: "general-purpose",
          via_generalist: true,
          model: "gpt-5.6-sol",
          round: 1,
          counts_toward_stage_output: true,
        },
        usage: {
          input_tokens: 1200,
          output_tokens: 300,
          total_tokens: 1500,
        },
      }),
    ).toMatchObject({
      id: "chair-1",
      status: "completed",
      result: expect.stringContaining("Use two seasons"),
      councilSeat: {
        role: "chair",
        agentName: "general-purpose",
      },
      usage: {
        inputTokens: 1200,
        outputTokens: 300,
        totalTokens: 1500,
      },
    });
  });

  it("marks a council seat failed and preserves the actionable reason", () => {
    expect(
      taskEventToSubtaskUpdate({
        type: "task_failed",
        task_id: "red-team-1",
        error: "The worker returned prose instead of a structured result.",
        stop_reason: "turn_capped",
      }),
    ).toEqual({
      id: "red-team-1",
      status: "failed",
      error: "The worker returned prose instead of a structured result.",
      stopReason: "turn_capped",
    });
  });
});
