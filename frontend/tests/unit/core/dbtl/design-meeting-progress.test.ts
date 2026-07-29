import { describe, expect, it } from "@rstest/core";

import {
  readDesignMeetingProgress,
  updateMeetingFromWorkers,
} from "@/core/dbtl/design-meeting-progress";
import type { StageWorkerRun } from "@/core/dbtl/reconciliation-api";

const payload = {
  dbtl_meeting_progress: {
    version: 1,
    project_id: "project-1",
    cycle_id: "cycle-1",
    surface_id: "dfs-1",
    run_id: "run-1",
    round: 1,
    state: "synthesizing",
    choice_label: "NAM-like RIL panel",
    comment: "",
    prior_chair_unit_id: "chair-paused",
    participants: [
      {
        id: "position-1",
        role: "position",
        role_label: "Independent position",
        agent_name: "experimental-design",
        via_generalist: false,
        model: "gpt-5",
        status: "completed",
        summary: "Use a bounded pilot.",
        total_tokens: 1200,
      },
      {
        id: "chair-paused",
        role: "chair",
        role_label: "Chair",
        agent_name: "experimental-design",
        via_generalist: false,
        model: "gpt-5",
        status: "in_progress",
        summary: "Revisiting the synthesis.",
        total_tokens: 0,
      },
    ],
  },
};

describe("Design meeting progress", () => {
  it("reads the server-owned snapshot and refuses the legacy boolean marker", () => {
    const parsed = readDesignMeetingProgress(payload);

    expect(parsed?.choiceLabel).toBe("NAM-like RIL panel");
    expect(parsed?.participants.map((item) => item.role)).toEqual([
      "position",
      "chair",
    ]);
    expect(
      readDesignMeetingProgress({ dbtl_meeting_progress: true }),
    ).toBeNull();
  });

  it("settles the chair only from a new durable chair worker", () => {
    const parsed = readDesignMeetingProgress(payload);
    if (!parsed) throw new Error("expected progress");
    const worker: StageWorkerRun = {
      id: "worker-2",
      unit_id: "chair-resumed",
      stage_spec_key: "generic:design:v2",
      capability: "design_council_chair",
      agent_name: "experimental-design",
      via_generalist: false,
      status: "completed",
      stop_reason: null,
      result: {
        status: "completed",
        summary: "The owner selected the NAM-like panel.",
        execution: { model: "gpt-5" },
        token_usage: { total_tokens: 3400 },
      },
      created_at: "2026-07-29T22:00:00Z",
    };

    const settled = updateMeetingFromWorkers(parsed, [worker]);

    expect(settled.state).toBe("settled");
    expect(settled.participants.at(-1)).toMatchObject({
      id: "chair-resumed",
      role: "chair",
      status: "completed",
      totalTokens: 3400,
      result: expect.objectContaining({
        summary: "The owner selected the NAM-like panel.",
      }),
    });
  });
});
