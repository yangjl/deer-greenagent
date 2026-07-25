import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";


import {
  createProject,
  createWorkspace,
  fetchProject,
  fetchProjects,
  fetchWorkspaces,
} from "./api";
import { fetchProjectFiles, fetchProjectThreads } from "./project-files-api";
import { projectConversationsQueryKey } from "./project-threads";
import type { ProjectCreatePayload, WorkspaceCreatePayload } from "./types";

export function useWorkspaces() {
  return useQuery({
    queryKey: ["workspaces"],
    queryFn: fetchWorkspaces,
  });
}

export function useProjects(workspaceId: string | null | undefined) {
  return useQuery({
    queryKey: ["workspaces", workspaceId, "projects"],
    queryFn: () => fetchProjects(workspaceId ?? ""),
    enabled: Boolean(workspaceId),
  });
}

export function useProject(projectId: string | null | undefined) {
  return useQuery({
    queryKey: ["projects", projectId],
    queryFn: () => fetchProject(projectId ?? ""),
    enabled: Boolean(projectId),
  });
}

/**
 * Projects of the user's first (active) workspace, for the project-first
 * sidebar. Auto-selects the first workspace, matching the projects page.
 */
export function useActiveWorkspaceProjects() {
  const workspaces = useWorkspaces();
  const [workspaceId, setWorkspaceId] = useState("");
  useEffect(() => {
    if (!workspaceId && workspaces.data?.[0]) {
      setWorkspaceId(workspaces.data[0].id);
    }
  }, [workspaceId, workspaces.data]);
  const projects = useProjects(workspaceId || null);
  return { workspaceId, workspaces, projects };
}

/**
 * Resolves a `/workspace/<slug>` segment to its project through the active
 * workspace's project list, so URLs carry the readable project name instead
 * of an opaque id.
 */
export function useProjectBySlug(slug: string | null | undefined) {
  const { workspaceId, workspaces, projects } = useActiveWorkspaceProjects();
  const project = slug
    ? (projects.data?.find((item) => item.slug === slug) ?? null)
    : null;
  return {
    project,
    workspaceId,
    isLoading: workspaces.isLoading || projects.isLoading,
    error: workspaces.error ?? projects.error ?? null,
  };
}

export function useProjectConversations(projectId: string | null | undefined) {
  return useQuery({
    queryKey: projectConversationsQueryKey(projectId ?? ""),
    queryFn: () => fetchProjectThreads(projectId ?? ""),
    enabled: Boolean(projectId),
  });
}

export function projectFilesQueryKey(projectId: string, path: string) {
  return ["projects", projectId, "files", path] as const;
}

/** One directory of the project's own file tree; no conversation needed. */
export function useProjectFiles(
  projectId: string | null | undefined,
  path: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: projectFilesQueryKey(projectId ?? "", path),
    queryFn: () => fetchProjectFiles(projectId ?? "", path),
    enabled: (options.enabled ?? true) && Boolean(projectId),
    staleTime: 30 * 1000,
    retry: false,
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorkspaceCreatePayload) => createWorkspace(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });
}

export function useCreateProject(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ProjectCreatePayload) =>
      createProject(workspaceId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["workspaces", workspaceId, "projects"],
      });
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });
}
