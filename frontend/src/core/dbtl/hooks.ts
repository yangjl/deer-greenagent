import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchDbtlFeature } from "@/core/features/api";

import {
  approveDbtlCutover,
  fetchDbtlGovernance,
  fetchDbtlReadiness,
  validateDbtlGovernance,
} from "./api";

export function useDbtlFeature() {
  const query = useQuery({
    queryKey: ["features", "dbtl"],
    queryFn: fetchDbtlFeature,
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
  });
  return {
    feature: query.data,
    isLoading: query.isPending,
    error: query.error,
  };
}

export function useDbtlReadiness() {
  return useQuery({
    queryKey: ["dbtl", "readiness"],
    queryFn: fetchDbtlReadiness,
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
  });
}

export function useDbtlGovernance() {
  return useQuery({
    queryKey: ["dbtl", "governance"],
    queryFn: fetchDbtlGovernance,
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
  });
}

export function useValidateDbtlGovernance() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: validateDbtlGovernance,
    onSuccess: (report) => {
      queryClient.setQueryData(["dbtl", "governance"], report);
    },
  });
}

export function useApproveDbtlCutover() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: approveDbtlCutover,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["dbtl", "governance"] });
    },
  });
}
