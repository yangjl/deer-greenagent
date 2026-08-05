import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  EvaluationResponse,
  EvaluationRow,
  EvaluationStats,
  DiscoveryOutcomeRow,
  DiscoveryStats,
  DiscoveryStatusResponse,
  ProposalOutcome,
} from "./proposal-view";

export interface EvaluationListResponse {
  project_id: string;
  evaluations: EvaluationRow[];
  stats: EvaluationStats;
  discoveries: DiscoveryOutcomeRow[];
  discovery_stats: DiscoveryStats;
}

function base(projectId: string) {
  return `${getBackendBaseURL()}/api/projects/${encodeURIComponent(projectId)}/dbtl/proposals`;
}

async function parseError(response: Response, fallback: string) {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
  } | null;
  return body?.detail ?? `${fallback}: ${response.statusText}`;
}

/**
 * Classify one request in shadow mode.
 *
 * This creates no DBTL record — it records an observation and may return a
 * proposal to show. Creating a cycle remains the separate, confirmed call in
 * `cycles-api.ts`.
 */
export async function evaluateRequest(input: {
  projectId: string;
  text: string;
  threadId?: string | null;
  selectedCycleId?: string | null;
  explicitChoice?: "ordinary" | "start_cycle" | "continue_cycle" | null;
  isNewConversation?: boolean;
  idempotencyKey: string;
}): Promise<EvaluationResponse> {
  const response = await fetch(`${base(input.projectId)}/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: input.text,
      thread_id: input.threadId ?? null,
      selected_cycle_id: input.selectedCycleId ?? null,
      explicit_choice: input.explicitChoice ?? null,
      is_new_conversation: input.isNewConversation ?? false,
      idempotency_key: input.idempotencyKey,
    }),
  });
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to evaluate request"));
  }
  return (await response.json()) as EvaluationResponse;
}

export async function recordProposalOutcome(input: {
  projectId: string;
  evaluationId: string;
  outcome: ProposalOutcome;
}): Promise<EvaluationRow> {
  const response = await fetch(
    `${base(input.projectId)}/${encodeURIComponent(input.evaluationId)}/outcome`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outcome: input.outcome }),
    },
  );
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to record outcome"));
  }
  return (await response.json()) as EvaluationRow;
}

/** The internal evaluation drawer. Administrator-only on the server. */
export async function fetchEvaluations(
  projectId: string,
): Promise<EvaluationListResponse> {
  const response = await fetch(`${base(projectId)}/evaluations`);
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load evaluations"));
  }
  return (await response.json()) as EvaluationListResponse;
}

/** Fresh server-owned discovery state for the composer's quiet indicator. */
export async function fetchDiscoveryStatus(input: {
  projectId: string;
  threadId: string;
}): Promise<DiscoveryStatusResponse> {
  const query = new URLSearchParams({ thread_id: input.threadId });
  const response = await fetch(
    `${getBackendBaseURL()}/api/projects/${encodeURIComponent(input.projectId)}/dbtl/discovery/status?${query}`,
  );
  if (!response.ok) {
    throw new Error(
      await parseError(response, "Failed to load discovery status"),
    );
  }
  return (await response.json()) as DiscoveryStatusResponse;
}
