import { beforeEach, describe, expect, it, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

import { fetch } from "@/core/api/fetcher";
import {
  abandonCycle,
  applyDesignFeedbackAction,
  type DesignFeedbackSurface,
} from "@/core/dbtl/cycles-api";

const mockedFetch = rs.mocked(fetch);

describe("DBTL cycle API", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  it("retires a cycle with its revision, rationale, and idempotency key", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ id: "cycle-1", state: "abandoned" }),
    } as Response);

    await abandonCycle({
      projectId: "project/1",
      cycleId: "cycle/1",
      rationale: "Duplicate setup test.",
      expectedDbRevision: 3,
      idempotencyKey: "abandon-1",
    });

    expect(mockedFetch.mock.calls[0]?.[0]).toContain(
      "/projects/project%2F1/dbtl/cycles/cycle%2F1/abandon",
    );
    expect(mockedFetch.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(JSON.parse(mockedFetch.mock.calls[0]?.[1]?.body as string)).toEqual({
      rationale: "Duplicate setup test.",
      expected_db_revision: 3,
      idempotency_key: "abandon-1",
    });
  });

  it("retries a failed approval handoff against the original reviewed revision", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ status: "review_recorded", replayed: true }),
    } as Response);
    const surface = {
      project_id: "project-1",
      cycle_id: "cycle-1",
      surface_id: "surface-1",
      current_db_revision: 12,
      deck_content_hash: "a".repeat(64),
      evidence_artifact_id: "artifact-1",
      evidence_artifact_revision: 2,
      evidence_content_hash: "b".repeat(64),
      receipt: {
        status: "handoff_failed",
        expected_db_revision: 11,
      },
    } as DesignFeedbackSurface;

    await applyDesignFeedbackAction({
      projectId: "project-1",
      surface,
      viewerThreadId: "thread-1",
      action: { kind: "approve", optionIds: [] },
      comment: "Approved.",
      clientSubmissionId: "approval-1",
    });

    expect(
      JSON.parse(mockedFetch.mock.calls[0]?.[1]?.body as string),
    ).toMatchObject({
      client_submission_id: "approval-1",
      expected_db_revision: 11,
      action: { kind: "approve" },
    });
  });
});
