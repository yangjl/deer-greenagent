import { useQuery } from "@tanstack/react-query";

import { fetchFeatures } from "@/core/features/api";

export interface AgentActivityFeature {
  /** Whether the rail renders the block at all. Off during rollout. */
  enabled: boolean;
  /**
   * Whether rows survive a restart.
   *
   * False on the in-memory run-event backend, which is the default development
   * install. The rail must render that as "Status unavailable" rather than as
   * an idle agent — a claim it has no rows to support.
   */
  durable: boolean;
}

const UNAVAILABLE: AgentActivityFeature = { enabled: false, durable: false };

/**
 * Fail closed on a missing or failed feature read.
 *
 * An older backend publishes no `agent_activity` key at all, and guessing
 * "enabled" there would render an empty block that never fills in.
 */
export function useAgentActivityFeature(): AgentActivityFeature {
  const query = useQuery({
    queryKey: ["features", "agent-activity"],
    queryFn: async () => (await fetchFeatures()).agent_activity ?? UNAVAILABLE,
    staleTime: 60_000,
    retry: false,
  });
  return query.data ?? UNAVAILABLE;
}
