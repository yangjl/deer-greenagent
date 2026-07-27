/** Pure Phase 8 Learn and governed-knowledge review vocabulary. */

export type ClaimGrade =
  | "supported"
  | "valid_negative"
  | "methodological"
  | "qa_lesson";

export type KnowledgeCandidate = {
  id: string;
  project_id: string;
  cycle_id: string | null;
  statement: string;
  summary: string;
  limitations: string[];
  grade: ClaimGrade;
  evidence: unknown[];
  test_outcome: string | null;
  status: "proposed" | "working_memory" | "promoted" | "discarded";
  created_by: string;
  created_at: string;
};

export type KnowledgeClaim = {
  id: string;
  project_id: string;
  statement: string;
  evidence: unknown[];
  limitations: string[];
  review: Record<string, unknown>;
  rendered_uri: string | null;
  source_candidate_id: string | null;
  grade: ClaimGrade;
  status: "active" | "superseded" | "retracted";
  created_at: string;
};

export type KnowledgePublication = {
  id: string;
  claim_id: string;
  source_project_id: string;
  target_project_id: string;
  status: "active" | "superseded" | "retracted";
  pointer: Record<string, unknown>;
  published_by: string;
  authorization_reference: string;
  created_at: string;
  retracted_at: string | null;
};

export type KnowledgeEvent = {
  id: string;
  event_type: string;
  actor_user_id: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type KnowledgeView = {
  project_id: string;
  cycle_id: string | null;
  candidates: KnowledgeCandidate[];
  claims: KnowledgeClaim[];
  publications: KnowledgePublication[];
  events: KnowledgeEvent[];
};

export const GRADE_LABELS: Record<ClaimGrade, string> = {
  supported: "Supported result",
  valid_negative: "Valid negative result",
  methodological: "Methodological knowledge",
  qa_lesson: "Quality-assurance lesson",
};

/**
 * Claim status in words.
 *
 * The plan requires knowledge detail to distinguish validated project
 * knowledge from superseded or retracted history, so each status carries its
 * own label rather than the raw enum. `active` is spelled out in full because
 * it is the only status that means "current project knowledge" — and a
 * retracted claim sitting under that heading reads as current to anyone
 * scanning headings.
 */
export const CLAIM_STATUS_LABELS: Record<KnowledgeClaim["status"], string> = {
  active: "Validated project knowledge",
  superseded: "Superseded",
  retracted: "Retracted",
};

export function candidateNotice(candidate: KnowledgeCandidate): string {
  if (candidate.status === "promoted")
    return "Promoted through a recorded human review.";
  if (candidate.status === "discarded")
    return "Discarded; retained only in the audit trail.";
  if (candidate.status === "working_memory")
    return "Kept as provisional working memory — not validated project knowledge.";
  return "Provisional candidate — not validated project knowledge.";
}

/**
 * Split claims into what is current and what is history.
 *
 * Rendering one flat list under a "Validated project knowledge" heading makes
 * a retracted claim read as current, which is exactly what the plan's exit
 * review checks for.
 */
export function partitionClaims(claims: KnowledgeClaim[]): {
  current: KnowledgeClaim[];
  history: KnowledgeClaim[];
} {
  return {
    current: claims.filter((claim) => claim.status === "active"),
    history: claims.filter((claim) => claim.status !== "active"),
  };
}

export function candidateCanBeReviewed(candidate: KnowledgeCandidate): boolean {
  return (
    candidate.status === "proposed" || candidate.status === "working_memory"
  );
}

export function promotionReviewReady(
  candidate: KnowledgeCandidate,
  statement: string,
  rationale: string,
): boolean {
  return (
    candidateCanBeReviewed(candidate) &&
    Boolean(statement.trim()) &&
    Boolean(rationale.trim()) &&
    candidate.evidence.length > 0
  );
}

export function publicationChoices<T extends { id: string }>(
  projects: T[],
  {
    sourceProjectId,
    activeTargetIds,
  }: { sourceProjectId: string; activeTargetIds: string[] },
): T[] {
  return projects.filter(
    (project) =>
      project.id !== sourceProjectId && !activeTargetIds.includes(project.id),
  );
}

export function publicationReviewReady(
  targetProjectIds: string[],
  rationale: string,
): boolean {
  return targetProjectIds.length > 0 && Boolean(rationale.trim());
}

export function activePublicationTargets(
  view: KnowledgeView,
  claimId: string,
): string[] {
  return view.publications
    .filter((item) => item.claim_id === claimId && item.status === "active")
    .map((item) => item.target_project_id);
}

export function retractionPreview(
  view: KnowledgeView,
  claimId: string,
): string {
  const count = activePublicationTargets(view, claimId).length;
  return count === 0
    ? "Retracts the source-project claim. No project publications are active."
    : `Retracts the source-project claim and removes it from ${count} published project${count === 1 ? "" : "s"}.`;
}

/**
 * Every scope that loses current retrieval, named.
 *
 * The plan asks for a preview of *every selected-project scope*, not a count:
 * retraction is destructive to retrieval, and "2 projects" does not let a
 * reviewer check that the two are the ones they meant. Only `active`
 * publications are listed — an already-retracted one has nothing left to lose.
 * An unresolvable target degrades to its id rather than vanishing, so the
 * preview can never under-report what is about to change.
 */
export function retractionScopePreview(
  view: KnowledgeView,
  claimId: string,
  projects: { id: string; name: string }[],
): { targets: { id: string; name: string }[]; summary: string } {
  const targets = activePublicationTargets(view, claimId).map((id) => ({
    id,
    name: projects.find((project) => project.id === id)?.name ?? id,
  }));
  return { targets, summary: retractionPreview(view, claimId) };
}
