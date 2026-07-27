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

export function candidateNotice(candidate: KnowledgeCandidate): string {
  if (candidate.status === "promoted")
    return "Promoted through a recorded human review.";
  if (candidate.status === "discarded")
    return "Discarded; retained only in the audit trail.";
  return "Provisional candidate — not validated project knowledge.";
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
