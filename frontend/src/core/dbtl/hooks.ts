import { useQuery } from "@tanstack/react-query";

import { fetchDbtlFeature } from "@/core/features/api";

import { fetchDbtlReadiness } from "./api";

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
