import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { cyclesQueryKey } from "./cycle-hooks";
import {
  fetchBuildTest,
  recordValidityAssessment,
} from "./validity-api";

const ROOT = "dbtl-build-test";
const CYCLES_ROOT = "dbtl-cycles";

export function buildTestQueryKey(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  return [ROOT, projectId, cycleId] as const;
}

export function useBuildTest(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  return useQuery({
    queryKey: buildTestQueryKey(projectId, cycleId),
    queryFn: () => fetchBuildTest(projectId!, cycleId!),
    enabled: Boolean(projectId && cycleId),
    staleTime: 0,
    retry: false,
  });
}

export function useRecordValidityAssessment(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      input: Omit<
        Parameters<typeof recordValidityAssessment>[0],
        "projectId" | "cycleId"
      >,
    ) =>
      recordValidityAssessment({
        projectId: projectId!,
        cycleId: cycleId!,
        ...input,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [ROOT] });
      void queryClient.invalidateQueries({ queryKey: [CYCLES_ROOT] });
      void queryClient.invalidateQueries({
        queryKey: cyclesQueryKey(projectId),
      });
    },
  });
}
