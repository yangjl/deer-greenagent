import { throwGatewayApiError } from "@/core/api/errors";
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  Project,
  ProjectCreatePayload,
  ProjectFolderListing,
  Workspace,
  WorkspaceCreatePayload,
} from "./types";

function apiUrl(path: string): string {
  return `${getBackendBaseURL()}/api${path}`;
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    await throwGatewayApiError(response, fallback);
  }
  return response.json() as Promise<T>;
}

export async function fetchWorkspaces(): Promise<Workspace[]> {
  return readJson<Workspace[]>(
    await fetch(apiUrl("/workspaces")),
    "Failed to load workspaces.",
  );
}

export async function createWorkspace(
  payload: WorkspaceCreatePayload,
): Promise<Workspace> {
  return readJson<Workspace>(
    await fetch(apiUrl("/workspaces"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
    "Failed to create workspace.",
  );
}

export async function fetchProjects(workspaceId: string): Promise<Project[]> {
  return readJson<Project[]>(
    await fetch(
      apiUrl(`/workspaces/${encodeURIComponent(workspaceId)}/projects`),
    ),
    "Failed to load projects.",
  );
}

export async function createProject(
  workspaceId: string,
  payload: ProjectCreatePayload,
): Promise<Project> {
  return readJson<Project>(
    await fetch(
      apiUrl(`/workspaces/${encodeURIComponent(workspaceId)}/projects`),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    ),
    "Failed to create project.",
  );
}

export async function fetchProjectFolders(
  path?: string,
): Promise<ProjectFolderListing> {
  const query = new URLSearchParams();
  if (path) {
    query.set("path", path);
  }
  const suffix = query.size ? `?${query.toString()}` : "";
  return readJson<ProjectFolderListing>(
    await fetch(apiUrl(`/project-folders${suffix}`)),
    "Failed to browse local project folders.",
  );
}

export async function fetchProject(projectId: string): Promise<Project> {
  return readJson<Project>(
    await fetch(apiUrl(`/projects/${encodeURIComponent(projectId)}`)),
    "Failed to load project.",
  );
}
