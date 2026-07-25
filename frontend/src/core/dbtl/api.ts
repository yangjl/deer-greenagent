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

export async function fetchDbtlReadiness(): Promise<DbtlReadinessReport> {
  const response = await fetch(`${getBackendBaseURL()}/api/dbtl/readiness`);
  if (!response.ok) {
    throw new Error(`Failed to load DBTL readiness: ${response.statusText}`);
  }
  return (await response.json()) as DbtlReadinessReport;
}
