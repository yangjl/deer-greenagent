import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  BuildTestView,
  HeadlineMetric,
  ValidityCheck,
  WorkflowRecommendation,
} from "./validity-view";

function base(projectId: string) {
  return `${getBackendBaseURL()}/api/projects/${encodeURIComponent(projectId)}/dbtl`;
}

async function parseError(response: Response, fallback: string) {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
  } | null;
  return body?.detail ?? `${fallback}: ${response.statusText}`;
}

export async function fetchBuildTest(
  projectId: string,
  cycleId: string,
): Promise<BuildTestView> {
  const response = await fetch(
    `${base(projectId)}/cycles/${encodeURIComponent(cycleId)}/build-test`,
  );
  if (!response.ok) {
    throw new Error(await parseError(response, "Could not load Build and Test"));
  }
  return (await response.json()) as BuildTestView;
}

export async function recordValidityAssessment(input: {
  projectId: string;
  cycleId: string;
  metrics: HeadlineMetric[];
  checks: ValidityCheck[];
  recommendation: WorkflowRecommendation;
  limitations: string[];
  rationale: string;
  expectedDbRevision: number;
  idempotencyKey: string;
}) {
  const response = await fetch(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/test/assessment`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        metrics: input.metrics,
        checks: input.checks,
        recommendation: input.recommendation,
        limitations: input.limitations,
        rationale: input.rationale,
        expected_db_revision: input.expectedDbRevision,
        idempotency_key: input.idempotencyKey,
      }),
    },
  );
  if (!response.ok) {
    throw new Error(
      await parseError(response, "Could not record the validity assessment"),
    );
  }
  return response.json();
}
