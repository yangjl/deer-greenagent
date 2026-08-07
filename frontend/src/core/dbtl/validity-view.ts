/** Pure Phase 7 Build/Test review vocabulary and validity projection. */

export const VALIDITY_OUTCOMES = [
  "supported",
  "not_supported",
  "inconclusive",
  "invalidated",
] as const;
export type ValidityOutcome = (typeof VALIDITY_OUTCOMES)[number];
export type ValidityTone = "positive" | "neutral" | "pending" | "critical";
export type CheckStatus = "passed" | "failed" | "missing" | "not_applicable";
export type WorkflowRecommendation =
  | "advance_to_learn"
  | "learn_from_invalidated_evidence"
  | "repeat_test"
  | "return_to_build"
  | "return_to_reconciliation"
  | "return_to_design"
  | "close_cycle";

export const OUTCOME_LABELS: Record<ValidityOutcome, string> = {
  supported: "Supported",
  not_supported: "Not supported",
  inconclusive: "Inconclusive",
  invalidated: "Invalidated",
};

export const VALIDITY_CHECK_LABELS: Record<string, string> = {
  structure_null: "Structure-null comparison",
  fold_composition: "Fold composition and separation",
  predictive_ceiling: "Biological / predictive ceiling",
  direction: "Direction checks",
  leakage: "Train–test leakage",
  tester_holdout: "Tester-controlled holdout",
  within_group: "Within-population / environment",
  duplicates_relatedness: "Duplicates and relatedness",
  reproducibility: "Reproducible execution",
  reconciled_inputs: "Bound input provenance",
};

export const CHECK_STATUS_LABELS: Record<CheckStatus, string> = {
  passed: "Passed",
  failed: "Failed",
  missing: "Missing evidence",
  not_applicable: "Not applicable",
};

export const RECOMMENDATION_LABELS: Record<WorkflowRecommendation, string> = {
  advance_to_learn: "Advance to Learn",
  learn_from_invalidated_evidence: "Learn from invalid evidence",
  repeat_test: "Repeat Test",
  return_to_build: "Return to Build",
  return_to_reconciliation: "Return to Data reconciliation",
  return_to_design: "Return to Design",
  close_cycle: "Close cycle",
};

export interface HeadlineMetric {
  name: string;
  value: number;
  threshold: number;
  criterion: "gte" | "lte";
  plausible_max: number | null;
  unit: string;
  meets_threshold?: boolean;
}

export interface ValidityCheck {
  check: string;
  status: CheckStatus;
  detail: string;
  evidence_refs: string[];
}

export interface BuildLineage {
  id: string;
  stage_attempt_id: string;
  lineage_revision: number;
  stage_spec_key: string;
  dataset_fingerprint: string;
  code_revision: string;
  config_revision: string;
  environment: Record<string, unknown>;
  input_artifacts: string[];
  output_artifacts: Array<{
    uri: string;
    content_hash: string;
    revision: number;
  }>;
  deviations: string[];
  logs_uri: string;
  recorded_by: string;
  db_revision: number;
  created_at: string;
}

export interface ValidityAssessment {
  id: string;
  assessment_revision: number;
  validity_pack_key: string;
  headline_metrics: HeadlineMetric[];
  checks: ValidityCheck[];
  outcome: ValidityOutcome;
  recommendation: WorkflowRecommendation;
  reason_codes: string[];
  limitations: string[];
  rationale: string;
  reviewer_user_id: string;
  reviewer_project_role: string;
  db_revision: number;
  created_at: string;
}

export interface BuildTestView {
  cycle_id: string;
  db_revision: number;
  validity_pack: {
    pack_key: string;
    title: string;
    provisional: boolean;
    required_checks: string[];
  };
  build_lineage: BuildLineage | null;
  validity_assessment: ValidityAssessment | null;
}

export interface ProjectedValidity {
  outcome: ValidityOutcome;
  reason_codes: string[];
  headline_success: boolean;
}

const FAILURE_REASON: Record<string, string> = {
  structure_null: "population_structure_artifact",
  fold_composition: "invalid_fold_composition",
  predictive_ceiling: "implausible_high_accuracy",
  direction: "direction_check_failure",
  leakage: "train_test_leakage",
  tester_holdout: "holdout_failure",
  within_group: "within_group_failure",
  duplicates_relatedness: "duplicates_or_relatedness_artifact",
  reproducibility: "irreproducible_execution",
  reconciled_inputs: "unreconciled_data",
};

export function outcomeTone(outcome: ValidityOutcome): ValidityTone {
  switch (outcome) {
    case "supported":
      return "positive";
    case "invalidated":
      return "critical";
    case "inconclusive":
      return "pending";
    default:
      return "neutral";
  }
}

export function metricMeetsThreshold(metric: HeadlineMetric): boolean {
  return metric.criterion === "gte"
    ? metric.value >= metric.threshold
    : metric.value <= metric.threshold;
}

export function projectedValidity(
  metrics: HeadlineMetric[],
  checks: ValidityCheck[],
  requiredChecks: readonly string[] = Object.keys(VALIDITY_CHECK_LABELS),
): ProjectedValidity {
  const headlineSuccess =
    metrics.length > 0 && metrics.every(metricMeetsThreshold);
  const implausible = metrics.some(
    (metric) =>
      metric.plausible_max !== null && metric.value > metric.plausible_max,
  );
  const failed = checks.filter((check) => check.status === "failed");
  const missing = requiredChecks.filter((name) => {
    const check = checks.find((item) => item.check === name);
    return (
      !check || check.status === "missing" || check.status === "not_applicable"
    );
  });

  if (implausible || failed.length > 0) {
    return {
      outcome: "invalidated",
      reason_codes: [
        ...(implausible ? ["implausible_high_accuracy"] : []),
        ...failed.map((item) => FAILURE_REASON[item.check] ?? item.check),
      ].filter((item, index, all) => all.indexOf(item) === index),
      headline_success: headlineSuccess,
    };
  }
  if (metrics.length === 0 || missing.length > 0) {
    return {
      outcome: "inconclusive",
      reason_codes: [
        ...(metrics.length === 0 ? ["missing_headline_metrics"] : []),
        ...missing.map((item) => `missing_${item}`),
      ],
      headline_success: headlineSuccess,
    };
  }
  return {
    outcome: headlineSuccess ? "supported" : "not_supported",
    reason_codes: headlineSuccess ? [] : ["headline_criterion_not_met"],
    headline_success: headlineSuccess,
  };
}

export function assessmentHeadline(result: ProjectedValidity): string {
  switch (result.outcome) {
    case "invalidated":
      return result.headline_success
        ? "Headline performance does not override failed validity checks."
        : "Failed validity checks invalidate this result.";
    case "inconclusive":
      return "The available evidence is insufficient for a scientific conclusion.";
    case "not_supported":
      return "The protocol is valid, but the stated headline criterion was not met.";
    case "supported":
      return "The headline criterion and every required validity check are satisfied.";
  }
}

export interface RecommendationOption {
  id: WorkflowRecommendation;
  label: string;
}

export function recommendationOptions(
  result: ProjectedValidity,
): RecommendationOption[] {
  const ids: WorkflowRecommendation[] =
    result.outcome === "supported" || result.outcome === "not_supported"
      ? ["advance_to_learn", "repeat_test", "close_cycle"]
      : result.outcome === "invalidated"
        ? [
            "learn_from_invalidated_evidence",
            "repeat_test",
            "return_to_build",
            "return_to_reconciliation",
            "return_to_design",
            "close_cycle",
          ]
      : [
          "repeat_test",
          "return_to_build",
          "return_to_reconciliation",
          "return_to_design",
          "close_cycle",
        ];
  return ids.map((id) => ({ id, label: RECOMMENDATION_LABELS[id] }));
}
