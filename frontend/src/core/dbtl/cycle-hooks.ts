import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  abandonCycle,
  attachArtifact,
  createCycle,
  createWorkItem,
  fetchCycle,
  fetchCycleActivity,
  fetchCycles,
  resolveWorkItem,
  reviewStage,
  submitStage,
} from "./cycles-api";

const ROOT = "dbtl-cycles";

export function cyclesQueryKey(projectId: string | null | undefined) {
  return [ROOT, "list", projectId] as const;
}

export function useProjectCycles(projectId: string | null | undefined) {
  return useQuery({
    queryKey: cyclesQueryKey(projectId),
    queryFn: () => fetchCycles(projectId!),
    enabled: Boolean(projectId),
    staleTime: 0,
    retry: false,
  });
}

export function useCycleDetail(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  return useQuery({
    queryKey: [ROOT, "detail", projectId, cycleId],
    queryFn: () => fetchCycle(projectId!, cycleId!),
    enabled: Boolean(projectId && cycleId),
    staleTime: 0,
    retry: false,
  });
}

export function useCycleActivity(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  return useQuery({
    queryKey: [ROOT, "activity", projectId, cycleId],
    queryFn: () => fetchCycleActivity(projectId!, cycleId!),
    enabled: Boolean(projectId && cycleId),
    staleTime: 0,
    retry: false,
  });
}

/**
 * Invalidate everything for one project after a mutation.
 *
 * Deliberately broad: a stage review can change the cycle list, the detail,
 * and the activity feed at once, and showing any of them stale would misstate
 * a durable research record.
 */
function useInvalidateProject(projectId: string | null | undefined) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: [ROOT] });
    void queryClient.invalidateQueries({ queryKey: cyclesQueryKey(projectId) });
  };
}

export function useCreateCycle(projectId: string | null | undefined) {
  const invalidate = useInvalidateProject(projectId);
  return useMutation({
    mutationFn: (input: Omit<Parameters<typeof createCycle>[0], "projectId">) =>
      createCycle({ projectId: projectId!, ...input }),
    onSuccess: invalidate,
  });
}

export function useAbandonCycle(projectId: string | null | undefined) {
  const invalidate = useInvalidateProject(projectId);
  return useMutation({
    mutationFn: (
      input: Omit<Parameters<typeof abandonCycle>[0], "projectId">,
    ) => abandonCycle({ projectId: projectId!, ...input }),
    onSuccess: invalidate,
  });
}

export function useSubmitStage(projectId: string | null | undefined) {
  const invalidate = useInvalidateProject(projectId);
  return useMutation({
    mutationFn: (input: Omit<Parameters<typeof submitStage>[0], "projectId">) =>
      submitStage({ projectId: projectId!, ...input }),
    onSuccess: invalidate,
  });
}

export function useReviewStage(projectId: string | null | undefined) {
  const invalidate = useInvalidateProject(projectId);
  return useMutation({
    mutationFn: (input: Omit<Parameters<typeof reviewStage>[0], "projectId">) =>
      reviewStage({ projectId: projectId!, ...input }),
    onSuccess: invalidate,
  });
}

export function useAttachArtifact(projectId: string | null | undefined) {
  const invalidate = useInvalidateProject(projectId);
  return useMutation({
    mutationFn: (
      input: Omit<Parameters<typeof attachArtifact>[0], "projectId">,
    ) => attachArtifact({ projectId: projectId!, ...input }),
    onSuccess: invalidate,
  });
}

export function useCreateWorkItem(projectId: string | null | undefined) {
  const invalidate = useInvalidateProject(projectId);
  return useMutation({
    mutationFn: (
      input: Omit<Parameters<typeof createWorkItem>[0], "projectId">,
    ) => createWorkItem({ projectId: projectId!, ...input }),
    onSuccess: invalidate,
  });
}

export function useResolveWorkItem(projectId: string | null | undefined) {
  const invalidate = useInvalidateProject(projectId);
  return useMutation({
    mutationFn: (
      input: Omit<Parameters<typeof resolveWorkItem>[0], "projectId">,
    ) => resolveWorkItem({ projectId: projectId!, ...input }),
    onSuccess: invalidate,
  });
}
