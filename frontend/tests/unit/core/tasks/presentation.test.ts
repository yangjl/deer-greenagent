import { describe, expect, it } from "@rstest/core";

import {
  formatSubtaskTokenUsage,
  isDbtlStructuredResult,
  resolveSubtaskModelLabel,
  shouldHideTrailingDbtlContract,
  subtaskResultForDisplay,
} from "@/core/tasks/presentation";
import type { Subtask } from "@/core/tasks/types";

function task(overrides: Partial<Subtask>): Subtask {
  return {
    id: "worker-1",
    status: "completed",
    subagent_type: "subagent",
    description: "Build work",
    prompt: "",
    ...overrides,
  };
}

describe("subtaskResultForDisplay", () => {
  it("uses the bounded server summary instead of governed raw JSON", () => {
    expect(
      subtaskResultForDisplay(
        task({
          dbtlStage: "build",
          result: '{"status":"completed","summary":"raw"}',
          displaySummary: "Built and validated the simulator.",
        }),
      ),
    ).toBe("Built and validated the simulator.");
  });

  it("derives a safe summary for older structured records", () => {
    expect(
      subtaskResultForDisplay(
        task({
          dbtlStage: "build",
          result: '{"status":"completed","summary":"Built it."}',
        }),
      ),
    ).toBe("Built it.");
  });

  it("never falls back to malformed governed JSON-like output", () => {
    expect(
      subtaskResultForDisplay(
        task({ dbtlStage: "build", result: '{"status": broken}' }),
      ),
    ).toBeUndefined();
  });
});

describe("isDbtlStructuredResult", () => {
  it("recognizes planner and stage-worker contracts", () => {
    expect(
      isDbtlStructuredResult(
        JSON.stringify({ feasibility: "planned", phases: [] }),
      ),
    ).toBe(true);
    expect(
      isDbtlStructuredResult(
        JSON.stringify({
          status: "completed",
          summary: "Built it",
          artifacts: [],
        }),
      ),
    ).toBe(true);
  });

  it("does not classify arbitrary JSON progress as a terminal contract", () => {
    expect(isDbtlStructuredResult('{"progress":"reading inputs"}')).toBe(
      false,
    );
    expect(isDbtlStructuredResult("not json")).toBe(false);
  });
});

describe("task card presentation helpers", () => {
  it("keeps model labels and token formatting covered", () => {
    expect(
      resolveSubtaskModelLabel("model-a", [
        {
          id: "model-a-id",
          name: "model-a",
          model: "provider/model-a",
          display_name: "Model A",
        },
      ]),
    ).toBe("Model A");
    expect(resolveSubtaskModelLabel("unknown", [])).toBe("unknown");
    expect(
      formatSubtaskTokenUsage({
        inputTokens: 1_000,
        outputTokens: 250,
        totalTokens: 1_250,
      }),
    ).toBe("1,250");
  });

  it("hides a structured trailing answer even when terminal delivery was missed", () => {
    const running = task({
      status: "in_progress",
      dbtlStage: "build",
      steps: [
        {
          kind: "ai",
          message_index: 1,
          text: JSON.stringify({ feasibility: "planned", phases: [] }),
          truncated: false,
        },
      ],
    });

    expect(
      shouldHideTrailingDbtlContract(running.dbtlStage, running.steps),
    ).toBe(true);
    expect(shouldHideTrailingDbtlContract(undefined, running.steps)).toBe(
      false,
    );
  });
});
