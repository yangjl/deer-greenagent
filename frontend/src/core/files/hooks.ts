import { useQuery } from "@tanstack/react-query";

import {
  fetchProjectCandidates,
  fetchThreadProject,
  fetchWorkspaceFiles,
} from "./api";
import type {
  ProjectCandidatesResponse,
  ThreadProjectResponse,
  WorkspaceFilesListResponse,
} from "./types";

export function workspaceFilesQueryKey(
  threadId: string | undefined,
  path: string,
) {
  return ["workspace-files", threadId, path] as const;
}

export function useWorkspaceFiles({
  threadId,
  path,
  enabled = true,
}: {
  threadId?: string;
  path: string;
  enabled?: boolean;
}) {
  return useQuery<WorkspaceFilesListResponse>({
    queryKey: workspaceFilesQueryKey(threadId, path),
    queryFn: () => {
      if (!threadId) {
        throw new Error("threadId is required");
      }
      return fetchWorkspaceFiles({ threadId, path });
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    staleTime: 30 * 1000,
    refetchOnWindowFocus: false,
  });
}

export function threadProjectQueryKey(threadId: string | undefined) {
  return ["thread-project", threadId] as const;
}

export function useThreadProject({
  threadId,
  enabled = true,
}: {
  threadId?: string;
  enabled?: boolean;
}) {
  return useQuery<ThreadProjectResponse>({
    queryKey: threadProjectQueryKey(threadId),
    queryFn: () => {
      if (!threadId) {
        throw new Error("threadId is required");
      }
      return fetchThreadProject(threadId);
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    staleTime: 30 * 1000,
    refetchOnWindowFocus: false,
  });
}

export function useProjectCandidates({
  threadId,
  enabled = true,
}: {
  threadId?: string;
  enabled?: boolean;
}) {
  return useQuery<ProjectCandidatesResponse>({
    queryKey: ["thread-project-candidates", threadId],
    queryFn: () => {
      if (!threadId) {
        throw new Error("threadId is required");
      }
      return fetchProjectCandidates(threadId);
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    staleTime: 60 * 1000,
    refetchOnWindowFocus: false,
  });
}
