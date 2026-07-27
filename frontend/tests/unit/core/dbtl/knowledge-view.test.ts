import { describe, expect, it } from "@rstest/core";

import {
  activePublicationTargets,
  candidateCanBeReviewed,
  candidateNotice,
  CLAIM_STATUS_LABELS,
  partitionClaims,
  retractionScopePreview,
  promotionReviewReady,
  publicationChoices,
  publicationReviewReady,
  retractionPreview,
  type KnowledgeCandidate,
  type KnowledgeClaim,
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

const claim: KnowledgeClaim = {
  id: "claim-1",
  project_id: "project-1",
  statement: "A validated claim",
  evidence: [{ reference: "artifact://test" }],
  limitations: ["One season"],
  review: {},
  rendered_uri: null,
  source_candidate_id: "candidate-1",
  grade: "supported",
  status: "active",
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

describe("scope labels (plan: Phase 8 knowledge detail must distinguish scopes)", () => {
  it("gives working memory its own label, distinct from an unreviewed candidate", () => {
    // The plan names "provisional working memory" as its own scope category.
    // Sharing one notice with `proposed` made [Keep as working memory] a
    // control whose success left the card byte-identical.
    const kept = candidateNotice({ ...candidate, status: "working_memory" });
    expect(kept).not.toBe(candidateNotice(candidate));
    expect(kept.toLowerCase()).toContain("working memory");
    // Still disclaims validation rather than implying it.
    expect(kept.toLowerCase()).toContain("not validated project knowledge");
  });

  it("labels every claim status in words, not a raw enum", () => {
    expect(CLAIM_STATUS_LABELS.active).toBe("Validated project knowledge");
    expect(CLAIM_STATUS_LABELS.superseded).toBe("Superseded");
    expect(CLAIM_STATUS_LABELS.retracted).toBe("Retracted");
    for (const label of Object.values(CLAIM_STATUS_LABELS)) {
      expect(label).not.toMatch(/^[a-z_]+$/);
    }
  });

  it("separates current knowledge from superseded or retracted history", () => {
    // A retracted claim listed under "Validated project knowledge" reads as
    // current to anyone scanning headings.
    const claims: KnowledgeClaim[] = [
      { ...claim, id: "c-active", status: "active" },
      { ...claim, id: "c-super", status: "superseded" },
      { ...claim, id: "c-retracted", status: "retracted" },
    ];
    const split = partitionClaims(claims);
    expect(split.current.map((c) => c.id)).toEqual(["c-active"]);
    expect(split.history.map((c) => c.id)).toEqual(["c-super", "c-retracted"]);
  });
});

describe("retraction preview (plan: preview every scope losing retrieval)", () => {
  it("names each project rather than only counting them", () => {
    const named = retractionScopePreview(view, "claim-1", [
      { id: "project-2", name: "Drought resistance" },
    ]);
    expect(named.targets).toEqual([
      { id: "project-2", name: "Drought resistance" },
    ]);
    // A retracted publication is already gone; it must not be re-previewed.
    expect(named.targets.map((t) => t.id)).not.toContain("project-3");
  });

  it("falls back to the id when a target project is not loaded", () => {
    const named = retractionScopePreview(view, "claim-1", []);
    expect(named.targets).toEqual([{ id: "project-2", name: "project-2" }]);
  });

  it("says plainly when nothing is published", () => {
    const none = retractionScopePreview(view, "claim-absent", []);
    expect(none.targets).toEqual([]);
    expect(none.summary.toLowerCase()).toContain("no project publications");
  });
});
