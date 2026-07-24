import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  ProjectCandidatesResponse,
  ThreadProjectResponse,
  WorkspaceFilesListResponse,
} from "./types";

async function readErrorDetail(
  response: Response,
  fallback: string,
): Promise<string> {
  const error = (await response.json().catch(() => ({ detail: fallback }))) as {
    detail?: string;
  };
  return typeof error.detail === "string" ? error.detail : fallback;
}

export async function fetchWorkspaceFiles({
  threadId,
  path,
}: {
  threadId: string;
  path: string;
}): Promise<WorkspaceFilesListResponse> {
  const query = new URLSearchParams({ path });
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(
      threadId,
    )}/files?${query}`,
  );

  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "Failed to load files."));
  }

  return response.json() as Promise<WorkspaceFilesListResponse>;
}

function threadProjectURL(threadId: string) {
  return `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/project`;
}

export async function fetchThreadProject(
  threadId: string,
): Promise<ThreadProjectResponse> {
  const response = await fetch(threadProjectURL(threadId));
  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Failed to load project link."),
    );
  }
  return response.json() as Promise<ThreadProjectResponse>;
}

export async function updateThreadProject(
  threadId: string,
  containerPath: string,
): Promise<ThreadProjectResponse> {
  const response = await fetch(threadProjectURL(threadId), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ container_path: containerPath }),
  });
  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Failed to link project."),
    );
  }
  return response.json() as Promise<ThreadProjectResponse>;
}

export async function clearThreadProject(
  threadId: string,
): Promise<ThreadProjectResponse> {
  const response = await fetch(threadProjectURL(threadId), {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Failed to unlink project."),
    );
  }
  return response.json() as Promise<ThreadProjectResponse>;
}

export async function fetchProjectCandidates(
  threadId: string,
): Promise<ProjectCandidatesResponse> {
  const response = await fetch(`${threadProjectURL(threadId)}/candidates`);
  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Failed to load project candidates."),
    );
  }
  return response.json() as Promise<ProjectCandidatesResponse>;
}
