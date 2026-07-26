/**
 * TanStack Query hooks for the reconciliation bridge (Phase 6).
 *
 * `staleTime: 0` and `retry: false` throughout, matching `cycle-hooks.ts`: a
 * durable research record must not show a stale value, and a silently retried
 * mutation on a revision-checked endpoint is a second opinion nobody asked for.
 *
 * Invalidation reaches into the `dbtl-cycles` namespace as well as this one,
 * because a reconciliation decision changes the cycle's revision — every
 * subsequent mutation would otherwise be submitted against a stale revision and
 * come back 409.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { cyclesQueryKey } from "./cycle-hooks";
import {
  decideReconciliationRow,
  declareDataset,
  fetchReconciliation,
  fetchStageSpecs,
  fetchStageWorkers,
  openReconciliationRow,
} from "./reconciliation-api";

const ROOT = "dbtl-reconciliation";
const CYCLES_ROOT = "dbtl-cycles";

export function reconciliationQueryKey(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  return [ROOT, "matrix", projectId, cycleId] as const;
}

function useInvalidateReconciliation(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: [ROOT] });
    void queryClient.invalidateQueries({ queryKey: [CYCLES_ROOT] });
    void queryClient.invalidateQueries({ queryKey: cyclesQueryKey(projectId) });
    void queryClient.invalidateQueries({
      queryKey: reconciliationQueryKey(projectId, cycleId),
    });
  };
}

export function useReconciliation(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  return useQuery({
    queryKey: reconciliationQueryKey(projectId, cycleId),
    queryFn: () => fetchReconciliation(projectId!, cycleId!),
    enabled: Boolean(projectId) && Boolean(cycleId),
    staleTime: 0,
    retry: false,
  });
}

export function useStageSpecs(projectId: string | null | undefined) {
  return useQuery({
    queryKey: [ROOT, "stage-specs", projectId] as const,
    queryFn: () => fetchStageSpecs(projectId!),
    enabled: Boolean(projectId),
    // The registry only moves when the backend ships a new version, so this is
    // the one DBTL query that does not need to be refetched on every open.
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export function useStageWorkers(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
  stage: string | null | undefined,
) {
  return useQuery({
    queryKey: [ROOT, "workers", projectId, cycleId, stage] as const,
    queryFn: () => fetchStageWorkers(projectId!, cycleId!, stage!),
    enabled: Boolean(projectId) && Boolean(cycleId) && Boolean(stage),
    staleTime: 0,
    retry: false,
  });
}

export function useDeclareDataset(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  const invalidate = useInvalidateReconciliation(projectId, cycleId);
  return useMutation({
    mutationFn: (
      input: Omit<Parameters<typeof declareDataset>[0], "projectId" | "cycleId">,
    ) => declareDataset({ projectId: projectId!, cycleId: cycleId!, ...input }),
    onSuccess: invalidate,
  });
}

export function useOpenReconciliationRow(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  const invalidate = useInvalidateReconciliation(projectId, cycleId);
  return useMutation({
    mutationFn: (
      input: Omit<
        Parameters<typeof openReconciliationRow>[0],
        "projectId" | "cycleId"
      >,
    ) =>
      openReconciliationRow({
        projectId: projectId!,
        cycleId: cycleId!,
        ...input,
      }),
    onSuccess: invalidate,
  });
}

export function useDecideReconciliationRow(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  const invalidate = useInvalidateReconciliation(projectId, cycleId);
  return useMutation({
    mutationFn: (
      input: Omit<Parameters<typeof decideReconciliationRow>[0], "projectId">,
    ) => decideReconciliationRow({ projectId: projectId!, ...input }),
    onSuccess: invalidate,
  });
}
