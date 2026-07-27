import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import { type ClaimGrade, type KnowledgeView } from "./knowledge-view";

function base(projectId: string) {
  return `${getBackendBaseURL()}/api/projects/${encodeURIComponent(projectId)}/dbtl`;
}

async function result(response: Response, fallback: string) {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `${fallback}: ${response.statusText}`);
  }
  return (await response.json()) as KnowledgeView;
}

export async function fetchKnowledge(
  projectId: string,
  cycleId?: string | null,
) {
  const query = cycleId ? `?cycle_id=${encodeURIComponent(cycleId)}` : "";
  return result(
    await fetch(`${base(projectId)}/knowledge${query}`),
    "Could not load Learn knowledge",
  );
}

async function post(projectId: string, path: string, body: object) {
  return result(
    await fetch(`${base(projectId)}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
    "Could not update project knowledge",
  );
}

export function decideCandidate(input: {
  projectId: string;
  candidateId: string;
  decision: "keep" | "discard";
  rationale: string;
  idempotencyKey: string;
}) {
  return post(
    input.projectId,
    `/candidates/${encodeURIComponent(input.candidateId)}/decision`,
    {
      decision: input.decision,
      rationale: input.rationale,
      idempotency_key: input.idempotencyKey,
    },
  );
}

export function promoteCandidate(input: {
  projectId: string;
  candidateId: string;
  statement: string;
  grade: ClaimGrade;
  limitations: string[];
  rationale: string;
  supersedesClaimId?: string;
  idempotencyKey: string;
}) {
  return post(
    input.projectId,
    `/candidates/${encodeURIComponent(input.candidateId)}/promote`,
    {
      statement: input.statement,
      grade: input.grade,
      limitations: input.limitations,
      rationale: input.rationale,
      supersedes_claim_id: input.supersedesClaimId ?? null,
      idempotency_key: input.idempotencyKey,
    },
  );
}

export function publishClaim(input: {
  projectId: string;
  claimId: string;
  targetProjectIds: string[];
  rationale: string;
  idempotencyKey: string;
}) {
  return post(
    input.projectId,
    `/claims/${encodeURIComponent(input.claimId)}/publish`,
    {
      target_project_ids: input.targetProjectIds,
      rationale: input.rationale,
      idempotency_key: input.idempotencyKey,
    },
  );
}

export function retractClaim(input: {
  projectId: string;
  claimId: string;
  rationale: string;
  idempotencyKey: string;
}) {
  return post(
    input.projectId,
    `/claims/${encodeURIComponent(input.claimId)}/retract`,
    {
      rationale: input.rationale,
      idempotency_key: input.idempotencyKey,
    },
  );
}
