import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  draftCycleSetup,
  evaluateRequest,
  fetchDiscoveryStatus,
  fetchEvaluations,
  recordProposalOutcome,
} from "./proposals-api";

const ROOT = "dbtl-proposals";

/**
 * Evaluate one request in shadow mode.
 *
 * A mutation rather than a query on purpose: this is a fire-once observation
 * tied to a specific message the user just sent, not cacheable state to be
 * refetched. It deliberately does not invalidate the cycle caches — an
 * evaluation changes no durable record, and invalidating them would suggest
 * otherwise.
 */
export function useEvaluateRequest(projectId: string | null | undefined) {
  return useMutation({
    mutationFn: (
      input: Omit<Parameters<typeof evaluateRequest>[0], "projectId">,
    ) => evaluateRequest({ projectId: projectId!, ...input }),
  });
}

export function useDiscoveryStatus(
  projectId: string | null | undefined,
  threadId: string | null | undefined,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: [ROOT, "discovery", projectId, threadId],
    queryFn: () =>
      fetchDiscoveryStatus({ projectId: projectId!, threadId: threadId! }),
    enabled: Boolean(projectId && threadId) && (options.enabled ?? true),
    staleTime: 0,
    retry: false,
  });
}

export function useRecordProposalOutcome(projectId: string | null | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      input: Omit<Parameters<typeof recordProposalOutcome>[0], "projectId">,
    ) => recordProposalOutcome({ projectId: projectId!, ...input }),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: [ROOT, "evaluations", projectId],
      }),
  });
}

/**
 * The internal evaluation drawer.
 *
 * `enabled` is caller-controlled and defaults to off so the admin-only
 * endpoint is not requested — and 403-logged — for every ordinary user who
 * opens a project.
 */
export function useProposalEvaluations(
  projectId: string | null | undefined,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: [ROOT, "evaluations", projectId],
    queryFn: () => fetchEvaluations(projectId!),
    enabled: Boolean(projectId) && (options.enabled ?? false),
    staleTime: 0,
    retry: false,
  });
}

/**
 * Draft the setup form's contents.
 *
 * A mutation, not a query: it is fired once when the setup step opens, against
 * the specific message the user just sent. Caching it under the project would
 * let a later, unrelated request inherit an earlier draft.
 */
export function useDraftCycleSetup(projectId: string | null | undefined) {
  return useMutation({
    mutationFn: (
      input: Omit<Parameters<typeof draftCycleSetup>[0], "projectId">,
    ) => draftCycleSetup({ projectId: projectId!, ...input }),
  });
}
