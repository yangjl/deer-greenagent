/** Types and summaries for DBTL classifier telemetry and discovery. */

export type RouteKind =
  | "ordinary"
  | "cycle_setup"
  | "cycle_continuation"
  | "proposal"
  | "discovery";

export type RouteSource =
  | "explicit_choice"
  | "explicit_request"
  | "selected_cycle"
  | "thread_cycle"
  | "no_project"
  | "classifier"
  | "active_discovery";

export type ConfidenceBand = "low" | "medium" | "high";

export type ProposalOutcome =
  | "start_setup"
  | "keep_ordinary"
  | "not_sure"
  | "dismissed"
  | "continue_cycle";

export interface EvaluationResponse {
  evaluation_id: string;
  route_kind: RouteKind;
  route_source: RouteSource;
}

export interface EvaluationRow {
  evaluation_id: string;
  thread_id: string | null;
  user_id: string;
  route_kind: RouteKind;
  route_source: RouteSource;
  band: ConfidenceBand;
  confidence: number;
  rule_hits: { rule_id: string; weight: number; evidence: string }[];
  missing_fields: string[];
  proposed_objective: string;
  human_choice: ProposalOutcome | null;
  decided_at: string | null;
  created_at: string;
}

export interface EvaluationStats {
  total: number;
  proposed: number;
  classifier_ordinary: number;
  decided: number;
  false_upgrades: number;
  missed_cycles: number;
}

export type DiscoveryLifecycleStatus =
  | "gathering"
  | "ready"
  | "offered"
  | "confirmed"
  | "declined"
  | "superseded"
  | "expired";

export interface DiscoveryStatusItem {
  id: string;
  status: Extract<DiscoveryLifecycleStatus, "gathering" | "ready" | "offered">;
  trigger: "explicit" | "classifier";
  revision: number;
  turn_count: number;
  updated_at: string;
}

export interface DiscoveryStatusResponse {
  enabled: boolean;
  discovery: DiscoveryStatusItem | null;
}

export interface DiscoveryOutcomeRow {
  id: string;
  thread_id: string;
  trigger: "explicit" | "classifier";
  status: DiscoveryLifecycleStatus;
  revision: number;
  turn_count: number;
  offered: boolean;
  cycle_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface DiscoveryStats {
  total: number;
  classifier_entries: number;
  confirmed: number;
  declined: number;
  active: number;
}

export function discoveryStatusLabel(
  status: DiscoveryStatusItem["status"],
): string {
  switch (status) {
    case "gathering":
      return "Shaping a cycle brief";
    case "ready":
      return "Cycle brief ready";
    case "offered":
      return "Awaiting your decision";
  }
}

export interface EvaluationSummary {
  falseUpgradeRate: number;
  missedCycleRate: number;
  decidedLabel: string;
}

/**
 * The two rates the human exit review approves thresholds against.
 *
 * A false upgrade is measured against what was proposed; a missed cycle
 * against what was not. Using one denominator for both would make the pair
 * move together and hide the trade-off between them.
 */
export function summarizeEvaluations(
  stats: EvaluationStats,
): EvaluationSummary {
  return {
    falseUpgradeRate:
      stats.proposed > 0 ? stats.false_upgrades / stats.proposed : 0,
    missedCycleRate:
      stats.classifier_ordinary > 0
        ? stats.missed_cycles / stats.classifier_ordinary
        : 0,
    decidedLabel: `${stats.decided} of ${stats.total} decided`,
  };
}
