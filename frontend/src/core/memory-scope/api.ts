import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  MigrationCounts,
  MigrationDecision,
  MigrationSuggestion,
} from "./review";

export interface MigrationOverview {
  project_id: string;
  counts: MigrationCounts;
}

export interface MigrationQueue {
  project_id: string;
  suggestions: MigrationSuggestion[];
}

export interface MigrationManifest {
  version: number;
  storage_root: string;
  buckets: Array<{
    bucket_id: string;
    classification: string;
    user_id: string | null;
    project_id: string | null;
    agent_names: string[];
    fact_count: number;
    reason: string;
  }>;
  facts: Array<{
    bucket_id: string;
    agent_name: string;
    fact_id: string;
    relative_path: string;
    sha256: string;
    size_bytes: number;
  }>;
  skipped_paths: string[];
}

export interface DecisionResult {
  fact_id: string;
  decision: MigrationDecision;
  outcome:
    | "applied"
    | "already_applied"
    | "recorded"
    | "source_changed"
    | "source_missing";
}

async function parseError(response: Response, fallback: string) {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
  } | null;
  return body?.detail ?? `${fallback}: ${response.statusText}`;
}

function base(projectId: string) {
  return `${getBackendBaseURL()}/api/projects/${encodeURIComponent(projectId)}/memory/migration`;
}

export async function fetchMigrationOverview(
  projectId: string,
): Promise<MigrationOverview> {
  const response = await fetch(base(projectId));
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load memory scope"));
  }
  return (await response.json()) as MigrationOverview;
}

export async function fetchMigrationQueue(
  projectId: string,
): Promise<MigrationQueue> {
  const response = await fetch(`${base(projectId)}/suggestions`);
  if (!response.ok) {
    throw new Error(
      await parseError(response, "Failed to load your review queue"),
    );
  }
  return (await response.json()) as MigrationQueue;
}

export async function fetchMigrationManifest(
  projectId: string,
): Promise<MigrationManifest> {
  const response = await fetch(`${base(projectId)}/manifest`);
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load manifest"));
  }
  return (await response.json()) as MigrationManifest;
}

/**
 * Submit one decision about one fact.
 *
 * The API takes a single `fact_id` on purpose — there is no bulk variant to
 * call, so "share everything" cannot be added by accident on the client.
 */
export async function submitMigrationDecision(input: {
  projectId: string;
  factId: string;
  agentName: string;
  sourceSha256: string;
  decision: MigrationDecision;
  editedContent?: string;
}): Promise<DecisionResult> {
  const response = await fetch(`${base(input.projectId)}/decisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      fact_id: input.factId,
      agent_name: input.agentName,
      source_sha256: input.sourceSha256,
      decision: input.decision,
      ...(input.editedContent ? { edited_content: input.editedContent } : {}),
    }),
  });
  if (!response.ok) {
    throw new Error(
      await parseError(response, "Could not record your decision"),
    );
  }
  return (await response.json()) as DecisionResult;
}

export async function rollbackMigration(
  projectId: string,
): Promise<{ project_id: string; reverted: number }> {
  const response = await fetch(`${base(projectId)}/rollback`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(await parseError(response, "Rollback failed"));
  }
  return (await response.json()) as { project_id: string; reverted: number };
}
