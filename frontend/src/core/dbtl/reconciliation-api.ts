/**
 * Data readiness and reconciliation API client (Phase 6).
 *
 * Same contract as `cycles-api.ts`: the shadowing `fetch` from `@/core/api/fetcher`
 * (credentials + CSRF), every mutation carrying `expected_db_revision` and an
 * `idempotency_key`, and `detail` from the server surfaced verbatim because the
 * thrown `Error.message` is what the UI renders.
 *
 * There is deliberately no `actor_type` on the decision payload. A person's
 * decision is the only kind this endpoint accepts, and letting the browser
 * assert otherwise would make the human-decision rule a matter of what the
 * client chose to send.
 */

import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  BlockerKind,
  DatasetRecord,
  DatasetRole,
  HumanDecision,
  ReconciliationView,
} from "./reconciliation-view";

function base(projectId: string) {
  return `${getBackendBaseURL()}/api/projects/${encodeURIComponent(projectId)}/dbtl`;
}

async function parseError(response: Response, fallback: string) {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
  } | null;
  return body?.detail ?? `${fallback}: ${response.statusText}`;
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
  if (!response.ok) throw new Error(await parseError(response, fallback));
  return (await response.json()) as T;
}

export interface StageSpecSummary {
  spec_key: string;
  stage: string;
  domain_profile: string;
  version: number;
  title: string;
  purpose: string;
  required_inputs: string[];
  required_artifact_types: string[];
  output_schema: string;
  required_capabilities: string[];
  optional_capabilities: string[];
  validity_gates: string[];
  transition_policy: string;
  allows_agent_approval: boolean;
  budget: {
    max_workers: number;
    max_turns: number;
    max_tokens: number;
    timeout_seconds: number;
  };
}

export interface StageSpecsResponse {
  project_id: string;
  executable_stages: string[];
  specs: StageSpecSummary[];
  reconciliation_checks: { id: string; label: string }[];
}

export interface StageWorkerRun {
  id: string;
  unit_id: string;
  stage_spec_key: string;
  capability: string;
  agent_name: string;
  via_generalist: boolean;
  status: string;
  stop_reason: string | null;
  result: Record<string, unknown>;
  created_at: string;
}

export async function fetchStageSpecs(
  projectId: string,
): Promise<StageSpecsResponse> {
  const response = await fetch(`${base(projectId)}/stage-specs`);
  if (!response.ok) {
    throw new Error(
      await parseError(response, "Could not load the stage contracts"),
    );
  }
  return (await response.json()) as StageSpecsResponse;
}

export async function fetchReconciliation(
  projectId: string,
  cycleId: string,
): Promise<ReconciliationView> {
  const response = await fetch(
    `${base(projectId)}/cycles/${encodeURIComponent(cycleId)}/reconciliation`,
  );
  if (!response.ok) {
    throw new Error(
      await parseError(response, "Could not load the reconciliation matrix"),
    );
  }
  return (await response.json()) as ReconciliationView;
}

export async function fetchStageWorkers(
  projectId: string,
  cycleId: string,
  stage: string,
): Promise<StageWorkerRun[]> {
  const response = await fetch(
    `${base(projectId)}/cycles/${encodeURIComponent(cycleId)}/stages/${encodeURIComponent(stage)}/workers`,
  );
  if (!response.ok) {
    throw new Error(
      await parseError(response, "Could not load the agent runs"),
    );
  }
  return ((await response.json()) as { workers: StageWorkerRun[] }).workers;
}

export async function declareDataset(input: {
  projectId: string;
  cycleId: string;
  sourceKey: string;
  uri: string;
  contentHash: string;
  declaredImmutable?: boolean;
  role?: DatasetRole;
  expectedDbRevision: number;
  idempotencyKey: string;
}): Promise<DatasetRecord> {
  return post<DatasetRecord>(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/datasets`,
    {
      source_key: input.sourceKey,
      uri: input.uri,
      content_hash: input.contentHash,
      declared_immutable: input.declaredImmutable ?? true,
      role: input.role ?? "raw",
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not declare the data source",
  );
}

export async function openReconciliationRow(input: {
  projectId: string;
  cycleId: string;
  check: string;
  fieldName: string;
  sourceALabel?: string;
  sourceAValue?: string;
  sourceBLabel?: string;
  sourceBValue?: string;
  required?: boolean;
  expectedDbRevision: number;
  idempotencyKey: string;
}) {
  return post(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/reconciliation/rows`,
    {
      check: input.check,
      field_name: input.fieldName,
      source_a_label: input.sourceALabel ?? "",
      source_a_value: input.sourceAValue ?? "",
      source_b_label: input.sourceBLabel ?? "",
      source_b_value: input.sourceBValue ?? "",
      required: input.required ?? true,
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not add the reconciliation row",
  );
}

export async function decideReconciliationRow(input: {
  projectId: string;
  rowId: string;
  status: HumanDecision;
  resolution: string;
  blockerKind?: BlockerKind | null;
  evidenceRefs?: string[];
  expectedDbRevision: number;
  expectedWorkItemRevision: number;
  idempotencyKey: string;
}) {
  return post(
    `${base(input.projectId)}/reconciliation/rows/${encodeURIComponent(input.rowId)}/decide`,
    {
      status: input.status,
      resolution: input.resolution,
      blocker_kind: input.blockerKind ?? null,
      evidence_refs: input.evidenceRefs ?? [],
      expected_db_revision: input.expectedDbRevision,
      expected_work_item_revision: input.expectedWorkItemRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not record the decision",
  );
}
