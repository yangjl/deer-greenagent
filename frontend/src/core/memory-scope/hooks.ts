import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchMigrationManifest,
  fetchMigrationOverview,
  fetchMigrationQueue,
  rollbackMigration,
  submitMigrationDecision,
} from "./api";
import type { MigrationDecision } from "./review";

const ROOT = "memory-scope";

export function useMigrationOverview(projectId: string | null | undefined) {
  return useQuery({
    queryKey: [ROOT, "overview", projectId],
    queryFn: () => fetchMigrationOverview(projectId!),
    enabled: Boolean(projectId),
    staleTime: 0,
    retry: false,
  });
}

/**
 * The caller's own review queue.
 *
 * Not fetched until the user asks for it — the landing view is counts-only,
 * and fact bodies should not be pulled into the client before then.
 */
export function useMigrationQueue(
  projectId: string | null | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: [ROOT, "queue", projectId],
    queryFn: () => fetchMigrationQueue(projectId!),
    enabled: Boolean(projectId) && enabled,
    staleTime: 0,
    retry: false,
  });
}

export function useMigrationManifest(projectId: string | null | undefined) {
  return useMutation({
    mutationFn: () => fetchMigrationManifest(projectId!),
  });
}

export function useSubmitMigrationDecision(
  projectId: string | null | undefined,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      factId: string;
      agentName: string;
      sourceSha256: string;
      decision: MigrationDecision;
      editedContent?: string;
    }) => submitMigrationDecision({ projectId: projectId!, ...input }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [ROOT, "overview", projectId],
      });
      void queryClient.invalidateQueries({
        queryKey: [ROOT, "queue", projectId],
      });
    },
  });
}

export function useRollbackMigration(projectId: string | null | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => rollbackMigration(projectId!),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [ROOT] });
    },
  });
}
