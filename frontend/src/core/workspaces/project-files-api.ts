/**
 * A project's own file tree and conversations.
 *
 * Both are addressed by project id — no conversation required — because the
 * backend gives a project its own durable workspace
 * (`GET /api/projects/{id}/files`) and records conversation membership on the
 * thread's scope row (`GET/PUT/DELETE /api/projects/{id}/threads[/{thread}]`).
 */

import { throwGatewayApiError } from "@/core/api/errors";
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";
import type { WorkspaceFilesListResponse } from "@/core/files";

import type { ProjectConversation } from "./project-threads";

interface ProjectThreadRow {
  thread_id: string;
  display_name?: string | null;
  updated_at?: string;
}

function projectUrl(projectId: string, suffix: string): string {
  return `${getBackendBaseURL()}/api/projects/${encodeURIComponent(projectId)}${suffix}`;
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    await throwGatewayApiError(response, fallback);
  }
  return response.json() as Promise<T>;
}

export async function fetchProjectFiles(
  projectId: string,
  path: string,
): Promise<WorkspaceFilesListResponse> {
  const query = new URLSearchParams({ path });
  return readJson<WorkspaceFilesListResponse>(
    await fetch(projectUrl(projectId, `/files?${query}`)),
    "Failed to load project files.",
  );
}

export async function fetchProjectThreads(
  projectId: string,
): Promise<ProjectConversation[]> {
  const rows = await readJson<ProjectThreadRow[]>(
    await fetch(projectUrl(projectId, "/threads")),
    "Failed to load project conversations.",
  );
  return rows.map((row) => ({
    threadId: row.thread_id,
    title: row.display_name?.length ? row.display_name : null,
    updatedAt: row.updated_at ?? "",
  }));
}

/** File a conversation into a project (durable, owner-scoped write). */
export async function addThreadToProject(
  projectId: string,
  threadId: string,
): Promise<void> {
  const response = await fetch(
    projectUrl(projectId, `/threads/${encodeURIComponent(threadId)}`),
    { method: "PUT" },
  );
  if (!response.ok) {
    await throwGatewayApiError(response, "Failed to add the conversation to the project.");
  }
}

/** Return a conversation to the inbox. */
export async function removeThreadFromProject(
  projectId: string,
  threadId: string,
): Promise<void> {
  const response = await fetch(
    projectUrl(projectId, `/threads/${encodeURIComponent(threadId)}`),
    { method: "DELETE" },
  );
  if (!response.ok) {
    await throwGatewayApiError(response, "Failed to remove the conversation from the project.");
  }
}
