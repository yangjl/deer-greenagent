import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { cyclesQueryKey } from "./cycle-hooks";
import {
  decideCandidate,
  fetchKnowledge,
  promoteCandidate,
  publishClaim,
  retractClaim,
} from "./knowledge-api";

const ROOT = "dbtl-knowledge";

export function knowledgeQueryKey(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  return [ROOT, projectId, cycleId ?? null] as const;
}

export function useKnowledge(
  projectId: string | null | undefined,
  cycleId: string | null | undefined,
) {
  return useQuery({
    queryKey: knowledgeQueryKey(projectId, cycleId),
    queryFn: () => fetchKnowledge(projectId!, cycleId),
    enabled: Boolean(projectId && cycleId),
    staleTime: 0,
    retry: false,
  });
}

function useKnowledgeMutation<T>(
  projectId: string | null | undefined,
  mutationFn: (input: T) => Promise<unknown>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [ROOT] });
      void queryClient.invalidateQueries({
        queryKey: cyclesQueryKey(projectId),
      });
    },
  });
}

export function useDecideCandidate(projectId: string | null | undefined) {
  return useKnowledgeMutation(
    projectId,
    (input: Omit<Parameters<typeof decideCandidate>[0], "projectId">) =>
      decideCandidate({ projectId: projectId!, ...input }),
  );
}

export function usePromoteCandidate(projectId: string | null | undefined) {
  return useKnowledgeMutation(
    projectId,
    (input: Omit<Parameters<typeof promoteCandidate>[0], "projectId">) =>
      promoteCandidate({ projectId: projectId!, ...input }),
  );
}

export function usePublishClaim(projectId: string | null | undefined) {
  return useKnowledgeMutation(
    projectId,
    (input: Omit<Parameters<typeof publishClaim>[0], "projectId">) =>
      publishClaim({ projectId: projectId!, ...input }),
  );
}

export function useRetractClaim(projectId: string | null | undefined) {
  return useKnowledgeMutation(
    projectId,
    (input: Omit<Parameters<typeof retractClaim>[0], "projectId">) =>
      retractClaim({ projectId: projectId!, ...input }),
  );
}
