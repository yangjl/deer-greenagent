import { describe, expect, test } from "@rstest/core";

import {
  CYCLE_STATE_LABELS,
  CYCLE_WEIGHT_LABELS,
  DBTL_STAGES,
  STAGE_LABELS,
  STATUS_LABELS,
  canReviewStage,
  canSubmitReview,
  canSubmitStage,
  decisionConsequence,
  defaultSelectedCycle,
  describeActivity,
  isLive,
  isTerminal,
  latestArtifacts,
  latestArtifactsForStage,
  openWorkItems,
  stageBlockReason,
  stageOf,
} from "@/core/dbtl/cycle-view";
import type {
  ActivityEvent,
  CycleRecord,
  StageStatus,
} from "@/core/dbtl/cycle-view";

function cycle(
  statuses: Partial<Record<(typeof DBTL_STAGES)[number], StageStatus>> = {},
  overrides: Partial<CycleRecord> = {},
): CycleRecord {
  return {
    id: "cycle-1",
    project_id: "project-1",
    parent_cycle_id: null,
    title: "Drought screen",
    cycle_class: "computational",
    cycle_weight: "full",
    state: "design",
    db_revision: 1,
    research_question: "Which lines hold yield?",
    objective: "Rank 200 lines",
    success_criteria: "Reproducible across two sites",
    created_by: "user-1",
    created_at: "2026-07-25T00:00:00Z",
    updated_at: "2026-07-25T00:00:00Z",
    stages: DBTL_STAGES.map((stage, index) => ({
      id: `cycle-1-${stage}`,
      stage,
      status: statuses[stage] ?? (index === 0 ? "in_progress" : "locked"),
      attempt_number: 1,
      db_revision: 1,
      updated_at: "2026-07-25T00:00:00Z",
    })),
    ...overrides,
  };
}

describe("vocabulary", () => {
  test("stage order matches the backend contract", () => {
    expect([...DBTL_STAGES]).toEqual([
      "design",
      "reconciliation",
      "build",
      "test",
      "learn",
    ]);
  });

  test("every status and state has a word, not just a colour", () => {
    const statuses: StageStatus[] = [
      "locked",
      "in_progress",
      "awaiting_review",
      "changes_requested",
      "approved",
      "rejected",
    ];
    for (const status of statuses) {
      expect(STATUS_LABELS[status].length).toBeGreaterThan(0);
    }
    expect(STAGE_LABELS.reconciliation).toBe("Data reconciliation");
    expect(CYCLE_STATE_LABELS.ready_for_build).toBe("Ready for build");
    expect(CYCLE_WEIGHT_LABELS.retroactive).toBe("Retroactive");
  });

  test("terminal states are recognised", () => {
    expect(isTerminal("completed")).toBe(true);
    expect(isTerminal("abandoned")).toBe(true);
    expect(isTerminal("design")).toBe(false);
    expect(isLive(cycle({}, { state: "completed" }))).toBe(false);
    expect(isLive(cycle())).toBe(true);
  });
});

describe("controls follow the durable status only", () => {
  test("a stage can be submitted while it is workable", () => {
    expect(canSubmitStage("in_progress")).toBe(true);
    expect(canSubmitStage("changes_requested")).toBe(true);
    expect(canSubmitStage("locked")).toBe(false);
    expect(canSubmitStage("awaiting_review")).toBe(false);
    expect(canSubmitStage("approved")).toBe(false);
    expect(canSubmitStage(undefined)).toBe(false);
  });

  test("only a submitted stage can be reviewed", () => {
    expect(canReviewStage("awaiting_review")).toBe(true);
    for (const status of [
      "locked",
      "in_progress",
      "changes_requested",
      "approved",
      "rejected",
    ] as StageStatus[]) {
      expect(canReviewStage(status)).toBe(false);
    }
  });

  test("a verdict always needs a rationale", () => {
    expect(canSubmitReview("")).toBe(false);
    expect(canSubmitReview("   ")).toBe(false);
    expect(canSubmitReview("Evidence supports it.")).toBe(true);
  });

  test("each verdict states its consequence", () => {
    expect(decisionConsequence("approve")).toContain("opens the next stage");
    expect(decisionConsequence("request_changes")).toContain(
      "Returns the stage to work",
    );
    expect(decisionConsequence("reject")).toContain("stays where it is");
  });
});

describe("why a stage is locked", () => {
  test("build names both outstanding approvals", () => {
    const reason = stageBlockReason(cycle(), "build");

    expect(reason.blocked).toBe(true);
    expect(reason.reason).toContain("Design");
    expect(reason.reason).toContain("Data reconciliation");
    expect(reason.reason).toContain("are approved");
  });

  test("build names only the one still outstanding", () => {
    const reason = stageBlockReason(cycle({ design: "approved" }), "build");

    expect(reason.reason).toContain("Data reconciliation");
    expect(reason.reason).not.toContain("Design and");
    expect(reason.reason).toContain("is approved");
  });

  test("an unlocked stage reports no blockage", () => {
    expect(stageBlockReason(cycle(), "design").blocked).toBe(false);
  });

  test("later stages name their immediate predecessor", () => {
    expect(stageBlockReason(cycle(), "test").reason).toContain("Build");
    expect(stageBlockReason(cycle(), "learn").reason).toContain("Test");
  });

  test("a missing cycle is blocked rather than silently permissive", () => {
    expect(stageBlockReason(null, "design").blocked).toBe(true);
    expect(stageOf(null, "design")).toBeNull();
  });
});

describe("evidence and blockers", () => {
  test("only the newest revision per artifact type is offered", () => {
    const record = cycle(
      {},
      {
        artifacts: [
          {
            id: "a1",
            stage_attempt_id: "cycle-1-design",
            artifact_type: "design_package",
            revision: 1,
            content_hash: "a".repeat(64),
            uri: "/x",
            created_by: "u",
            created_at: "2026-07-25T00:00:00Z",
          },
          {
            id: "a2",
            stage_attempt_id: "cycle-1-design",
            artifact_type: "design_package",
            revision: 2,
            content_hash: "b".repeat(64),
            uri: "/x",
            created_by: "u",
            created_at: "2026-07-25T01:00:00Z",
          },
          {
            id: "a3",
            stage_attempt_id: "cycle-1-reconciliation",
            artifact_type: "debate",
            revision: 1,
            content_hash: "c".repeat(64),
            uri: "/y",
            created_by: "u",
            created_at: "2026-07-25T02:00:00Z",
          },
        ],
      },
    );

    expect(latestArtifacts(record).map((item) => item.id)).toEqual([
      "a3",
      "a2",
    ]);
  });

  test("no artifacts yields an empty list rather than throwing", () => {
    expect(latestArtifacts(cycle())).toEqual([]);
    expect(latestArtifacts(null)).toEqual([]);
  });

  test("review evidence is limited to the selected stage", () => {
    const record = cycle(
      {},
      {
        artifacts: [
          {
            id: "design",
            stage_attempt_id: "cycle-1-design",
            artifact_type: "package",
            revision: 1,
            content_hash: "a".repeat(64),
            uri: "/design",
            created_by: "u",
            created_at: "2026-07-25T00:00:00Z",
          },
          {
            id: "reconciliation",
            stage_attempt_id: "cycle-1-reconciliation",
            artifact_type: "package",
            revision: 2,
            content_hash: "b".repeat(64),
            uri: "/reconciliation",
            created_by: "u",
            created_at: "2026-07-25T01:00:00Z",
          },
        ],
      },
    );

    expect(
      latestArtifactsForStage(record, "design").map((item) => item.id),
    ).toEqual(["design"]);
  });

  test("open blockers are separated from resolved ones", () => {
    const record = cycle(
      {},
      {
        work_items: [
          {
            id: "w1",
            title: "Missing genotypes",
            status: "open",
            payload: { kind: "blocker" },
            db_revision: 1,
            created_at: "2026-07-25T00:00:00Z",
          },
          {
            id: "w2",
            title: "Fixed",
            status: "resolved",
            payload: { kind: "blocker", resolution: "Re-exported" },
            db_revision: 2,
            created_at: "2026-07-25T01:00:00Z",
          },
        ],
      },
    );

    expect(openWorkItems(record).map((item) => item.id)).toEqual(["w1"]);
  });
});

describe("activity", () => {
  function event(overrides: Partial<ActivityEvent>): ActivityEvent {
    return {
      id: "e1",
      sequence: 1,
      event_type: "cycle.created",
      actor_user_id: "user-1",
      payload: {},
      created_at: "2026-07-25T00:00:00Z",
      ...overrides,
    };
  }

  test("each event type reads as a sentence", () => {
    expect(describeActivity(event({}))).toBe("Opened the cycle");
    expect(
      describeActivity(
        event({
          event_type: "stage.reviewed",
          payload: { stage: "design", decision: "approve" },
        }),
      ),
    ).toBe("Reviewed design: approve");
    expect(
      describeActivity(
        event({
          event_type: "artifact.attached",
          payload: { artifact_type: "design_package", revision: 2 },
        }),
      ),
    ).toContain("revision 2");
  });

  test("an unknown event type degrades to its raw name", () => {
    expect(describeActivity(event({ event_type: "future.thing" }))).toBe(
      "future.thing",
    );
  });
});

describe("default selection", () => {
  test("prefers the live cycle over a finished one", () => {
    const done = cycle({}, { id: "old", state: "completed" });
    const live = cycle({}, { id: "live" });

    expect(defaultSelectedCycle([done, live])?.id).toBe("live");
  });

  test("falls back to the newest finished cycle", () => {
    const first = cycle({}, { id: "first", state: "completed" });
    const second = cycle({}, { id: "second", state: "abandoned" });

    expect(defaultSelectedCycle([first, second])?.id).toBe("second");
  });

  test("an empty project has no selection", () => {
    expect(defaultSelectedCycle([])).toBeNull();
  });
});
