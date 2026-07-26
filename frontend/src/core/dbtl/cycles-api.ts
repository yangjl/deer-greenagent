import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  ActivityEvent,
  CycleClass,
  CycleRecord,
  CycleWeight,
  DbtlStage,
  ReviewDecision,
} from "./cycle-view";

export interface CycleListResponse {
  project_id: string;
  stages: DbtlStage[];
  cycles: CycleRecord[];
}

async function parseError(response: Response, fallback: string) {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
  } | null;
  return body?.detail ?? `${fallback}: ${response.statusText}`;
}

function base(projectId: string) {
  return `${getBackendBaseURL()}/api/projects/${encodeURIComponent(projectId)}/dbtl`;
}

async function post<T>(
  url: string,
  body: unknown,
  fallback: string,
): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await parseError(response, fallback));
  }
  return (await response.json()) as T;
}

export async function fetchCycles(
  projectId: string,
): Promise<CycleListResponse> {
  const response = await fetch(`${base(projectId)}/cycles`);
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load cycles"));
  }
  return (await response.json()) as CycleListResponse;
}

export async function fetchCycle(
  projectId: string,
  cycleId: string,
): Promise<CycleRecord> {
  const response = await fetch(
    `${base(projectId)}/cycles/${encodeURIComponent(cycleId)}`,
  );
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load cycle"));
  }
  return (await response.json()) as CycleRecord;
}

export async function fetchCycleActivity(
  projectId: string,
  cycleId: string,
): Promise<ActivityEvent[]> {
  const response = await fetch(
    `${base(projectId)}/cycles/${encodeURIComponent(cycleId)}/activity`,
  );
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load activity"));
  }
  return ((await response.json()) as { events: ActivityEvent[] }).events;
}

export interface CreateCycleInput {
  projectId: string;
  title: string;
  cycleClass: CycleClass;
  cycleWeight: CycleWeight;
  researchQuestion: string;
  objective?: string;
  successCriteria?: string;
  parentCycleId?: string | null;
  /** Makes a retried submit resolve to the same durable record. */
  idempotencyKey: string;
}

export async function createCycle(
  input: CreateCycleInput,
): Promise<CycleRecord> {
  return post<CycleRecord>(
    `${base(input.projectId)}/cycles`,
    {
      title: input.title,
      cycle_class: input.cycleClass,
      cycle_weight: input.cycleWeight,
      research_question: input.researchQuestion,
      objective: input.objective ?? "",
      success_criteria: input.successCriteria ?? "",
      parent_cycle_id: input.parentCycleId ?? null,
      idempotency_key: input.idempotencyKey,
    },
    "Could not start the cycle",
  );
}

export async function submitStage(input: {
  projectId: string;
  cycleId: string;
  stage: DbtlStage;
  expectedDbRevision: number;
  idempotencyKey: string;
}): Promise<CycleRecord> {
  return post<CycleRecord>(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/stages/${input.stage}/submit`,
    {
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not submit the stage",
  );
}

export async function reviewStage(input: {
  projectId: string;
  cycleId: string;
  stage: DbtlStage;
  decision: ReviewDecision;
  rationale: string;
  expectedDbRevision: number;
  idempotencyKey: string;
}): Promise<CycleRecord> {
  return post<CycleRecord>(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/stages/${input.stage}/review`,
    {
      decision: input.decision,
      rationale: input.rationale,
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not record the review",
  );
}

export async function attachArtifact(input: {
  projectId: string;
  cycleId: string;
  stage: DbtlStage;
  artifactType: string;
  uri: string;
  contentHash: string;
  expectedDbRevision: number;
  idempotencyKey: string;
}) {
  return post(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/artifacts`,
    {
      stage: input.stage,
      artifact_type: input.artifactType,
      uri: input.uri,
      content_hash: input.contentHash,
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not attach the artifact",
  );
}

export async function createWorkItem(input: {
  projectId: string;
  cycleId: string;
  title: string;
  kind: "blocker" | "task" | "question";
  expectedDbRevision: number;
  idempotencyKey: string;
}) {
  return post(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/work-items`,
    {
      title: input.title,
      kind: input.kind,
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not record the blocker",
  );
}

export async function resolveWorkItem(input: {
  projectId: string;
  workItemId: string;
  resolution: string;
  expectedDbRevision: number;
  expectedWorkItemRevision: number;
  idempotencyKey: string;
}) {
  return post(
    `${base(input.projectId)}/work-items/${encodeURIComponent(input.workItemId)}/resolve`,
    {
      resolution: input.resolution,
      expected_db_revision: input.expectedDbRevision,
      expected_work_item_revision: input.expectedWorkItemRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not resolve the blocker",
  );
}
