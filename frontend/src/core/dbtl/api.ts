import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export type DbtlMode = "disabled" | "audit_only" | "manual" | "graph_enabled";

export type DbtlReadinessClassification =
  | "compatible"
  | "repairable"
  | "invalid_or_ambiguous"
  | "safe_to_supersede";

export interface DbtlInventoryItem {
  path: string;
  record_type: "cycle" | "handoff";
  classification: DbtlReadinessClassification;
  reason: string;
  record_id: string | null;
}

export interface DbtlReadinessCheck {
  id: string;
  status: "ready" | "attention";
  summary: string;
}

export interface DbtlReadinessReport {
  mode: DbtlMode;
  mutations_enabled: boolean;
  graph_execution_enabled: boolean;
  reason: string;
  policy_version: string;
  test_outcomes: string[];
  knowledge_candidate_statuses: string[];
  counts: Record<DbtlReadinessClassification, number>;
  items: DbtlInventoryItem[];
  checks: DbtlReadinessCheck[];
}

export interface DbtlGovernanceCheck {
  id: string;
  title: string;
  status: "passed" | "blocked" | "waiting";
  detail: string;
}

export interface DbtlGovernanceReport {
  generated_at: string;
  database_backend: string;
  schema_revision: string | null;
  technical_ready: boolean;
  passed_checks: number;
  total_checks: number;
  checks: DbtlGovernanceCheck[];
  projection_mismatches: Array<{
    cycle_id: string;
    project_id: string;
    stored_hash: string;
    computed_hash: string;
  }>;
  legacy: {
    counts: Record<DbtlReadinessClassification, number>;
    items: DbtlInventoryItem[];
  };
  rollback_posture: string;
  last_validation: {
    id: string;
    created_at: string;
    evidence_hash: string;
  } | null;
  cutover: {
    id: string;
    created_at: string;
    approved_by: string;
  } | null;
  operator_can_approve: boolean;
  validation_id?: string;
  evidence_hash?: string;
  validated_at?: string;
}

async function parseError(response: Response, fallback: string) {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
  } | null;
  return body?.detail ?? `${fallback}: ${response.statusText}`;
}

export async function fetchDbtlReadiness(): Promise<DbtlReadinessReport> {
  const response = await fetch(`${getBackendBaseURL()}/api/dbtl/readiness`);
  if (!response.ok) {
    throw new Error(`Failed to load DBTL readiness: ${response.statusText}`);
  }
  return (await response.json()) as DbtlReadinessReport;
}

export async function fetchDbtlGovernance(): Promise<DbtlGovernanceReport> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/dbtl/governance/readiness`,
  );
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load DBTL governance"));
  }
  return (await response.json()) as DbtlGovernanceReport;
}

export async function validateDbtlGovernance(): Promise<DbtlGovernanceReport> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/dbtl/governance/validate`,
    { method: "POST" },
  );
  if (!response.ok) {
    throw new Error(await parseError(response, "DBTL validation failed"));
  }
  return (await response.json()) as DbtlGovernanceReport;
}

export async function approveDbtlCutover(validationId: string) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/dbtl/governance/cutover`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ validation_id: validationId }),
    },
  );
  if (!response.ok) {
    throw new Error(await parseError(response, "DBTL cutover approval failed"));
  }
  return (await response.json()) as {
    id: string;
    validation_id: string;
    status: "approved";
  };
}
