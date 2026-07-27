import { describe, expect, it } from "@rstest/core";

import {
  activePublicationTargets,
  candidateCanBeReviewed,
  candidateNotice,
  promotionReviewReady,
  publicationChoices,
  publicationReviewReady,
  retractionPreview,
  type KnowledgeCandidate,
  type KnowledgeView,
} from "@/core/dbtl/knowledge-view";

const candidate: KnowledgeCandidate = {
  id: "candidate-1",
  project_id: "project-1",
  cycle_id: "cycle-1",
  statement: "A bounded claim",
  summary: "Learn synthesis",
  limitations: ["One season"],
  grade: "supported",
  evidence: [{ reference: "artifact://test" }],
  test_outcome: "supported",
  status: "proposed",
  created_by: "agent:learn",
  created_at: "2026-07-26T00:00:00Z",
};

const view: KnowledgeView = {
  project_id: "project-1",
  cycle_id: "cycle-1",
  candidates: [candidate],
  claims: [],
  publications: [
    {
      id: "publication-1",
      claim_id: "claim-1",
      source_project_id: "project-1",
      target_project_id: "project-2",
      status: "active",
      pointer: {},
      published_by: "user-1",
      authorization_reference: "manual",
      created_at: "2026-07-26T00:00:00Z",
      retracted_at: null,
    },
    {
      id: "publication-2",
      claim_id: "claim-1",
      source_project_id: "project-1",
      target_project_id: "project-3",
      status: "retracted",
      pointer: {},
      published_by: "user-1",
      authorization_reference: "manual",
      created_at: "2026-07-26T00:00:00Z",
      retracted_at: "2026-07-26T01:00:00Z",
    },
  ],
  events: [],
};

describe("Learn candidate vocabulary", () => {
  it("never presents a provisional candidate as validated knowledge", () => {
    expect(candidateNotice(candidate)).toContain("Provisional candidate");
    expect(candidateCanBeReviewed(candidate)).toBe(true);
    expect(candidateCanBeReviewed({ ...candidate, status: "promoted" })).toBe(
      false,
    );
  });

  it("requires evidence, reviewed text, and rationale for promotion", () => {
    expect(
      promotionReviewReady(candidate, candidate.statement, "Reviewed."),
    ).toBe(true);
    expect(promotionReviewReady(candidate, candidate.statement, " ")).toBe(
      false,
    );
    expect(
      promotionReviewReady(
        { ...candidate, evidence: [] },
        candidate.statement,
        "Reviewed.",
      ),
    ).toBe(false);
  });
});

describe("publication scope", () => {
  it("counts only active selected-project publications in retraction scope", () => {
    expect(activePublicationTargets(view, "claim-1")).toEqual(["project-2"]);
    expect(retractionPreview(view, "claim-1")).toContain("1 published project");
  });

  it("offers only explicit unpublished targets and requires a rationale", () => {
    expect(
      publicationChoices(
        [{ id: "project-1" }, { id: "project-2" }, { id: "project-3" }],
        {
          sourceProjectId: "project-1",
          activeTargetIds: ["project-2"],
        },
      ),
    ).toEqual([{ id: "project-3" }]);
    expect(publicationReviewReady(["project-3"], "Same protocol.")).toBe(true);
    expect(publicationReviewReady([], "Same protocol.")).toBe(false);
  });
});
