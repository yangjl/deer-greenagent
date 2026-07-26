import type { HeadlineMetric, ValidityCheck } from "./validity-view";

export interface Phase7DemoCase {
  id: string;
  label: string;
  eyebrow: string;
  explanation: string;
  expectedRoute: string;
  metrics: HeadlineMetric[];
  checks: ValidityCheck[];
}

const CHECK_NAMES = [
  "structure_null",
  "fold_composition",
  "predictive_ceiling",
  "direction",
  "leakage",
  "tester_holdout",
  "within_group",
  "duplicates_relatedness",
  "reproducibility",
  "reconciled_inputs",
] as const;

function checks(
  overrides: Partial<Record<(typeof CHECK_NAMES)[number], ValidityCheck["status"]>>,
): ValidityCheck[] {
  return CHECK_NAMES.map((check) => {
    const status = overrides[check] ?? "passed";
    return {
      check,
      status,
      detail:
        status === "passed"
          ? "Evidence is present and reviewable."
          : status === "failed"
            ? "The review found a validity-breaking contradiction."
            : "The required evidence was not provided.",
      evidence_refs: status === "passed" ? [`demo://${check}`] : [],
    };
  });
}

const STRONG_METRIC: HeadlineMetric = {
  name: "held-out accuracy",
  value: 0.92,
  threshold: 0.7,
  criterion: "gte",
  plausible_max: 0.95,
  unit: "",
};

export const PHASE7_DEMO_CASES: readonly Phase7DemoCase[] = [
  {
    id: "invalidated",
    label: "High score, invalid science",
    eyebrow: "Validity overrides performance",
    explanation:
      "The headline clears its threshold, but train–test leakage invalidates the scientific conclusion.",
    expectedRoute: "Return to Build or Data reconciliation",
    metrics: [STRONG_METRIC],
    checks: checks({ leakage: "failed" }),
  },
  {
    id: "inconclusive",
    label: "Missing evidence",
    eyebrow: "Absence stays visible",
    explanation:
      "No failure is invented, but a missing structure-null comparison prevents a conclusion.",
    expectedRoute: "Repeat Test or return upstream",
    metrics: [STRONG_METRIC],
    checks: checks({ structure_null: "missing" }),
  },
  {
    id: "supported",
    label: "Supported result",
    eyebrow: "Performance and validity agree",
    explanation:
      "The headline clears its threshold and every required validity check has evidence.",
    expectedRoute: "Present Advance to Learn for human approval",
    metrics: [STRONG_METRIC],
    checks: checks({}),
  },
] as const;
