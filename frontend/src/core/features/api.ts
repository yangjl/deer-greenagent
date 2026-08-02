import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export interface FeaturesResponse {
  agents_api: { enabled: boolean };
  browser_control?: { enabled: boolean };
  /** Absent on a backend older than the runtime-activity projection. */
  agent_activity?: { enabled: boolean; durable: boolean };
  dbtl?: DbtlFeature;
}

export interface DbtlFeature {
  mode: "disabled" | "audit_only" | "manual" | "graph_enabled";
  mutations_enabled: boolean;
  graph_execution_enabled: boolean;
  design_deck_feedback?: boolean;
  progressive_gate?: boolean;
  /** False when an approved Design opens Build directly. */
  reconciliation_required?: boolean;
  stage_meetings?: { build: boolean; test: boolean; learn: boolean };
  reason: string;
}

const DISABLED_DBTL_FEATURE: DbtlFeature = {
  mode: "disabled",
  mutations_enabled: false,
  graph_execution_enabled: false,
  design_deck_feedback: false,
  progressive_gate: false,
  // Strict when the backend says nothing, matching the server default.
  reconciliation_required: true,
  stage_meetings: { build: false, test: false, learn: false },
  reason:
    "The backend did not publish a DBTL safety contract, so workflow controls are disabled.",
};

export async function fetchFeatures(): Promise<FeaturesResponse> {
  const res = await fetch(`${getBackendBaseURL()}/api/features`);
  if (!res.ok) {
    throw new Error(`Failed to load features: ${res.statusText}`);
  }
  return (await res.json()) as FeaturesResponse;
}

export async function fetchAgentsApiEnabled(): Promise<boolean> {
  return (await fetchFeatures()).agents_api.enabled;
}

export async function fetchBrowserControlEnabled(): Promise<boolean> {
  return (await fetchFeatures()).browser_control?.enabled ?? false;
}

export async function fetchDbtlFeature(): Promise<DbtlFeature> {
  return (await fetchFeatures()).dbtl ?? DISABLED_DBTL_FEATURE;
}
