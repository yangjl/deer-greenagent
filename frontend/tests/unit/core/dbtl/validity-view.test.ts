import { describe, expect, test } from "@rstest/core";

import {
  OUTCOME_LABELS,
  VALIDITY_CHECK_LABELS,
  VALIDITY_OUTCOMES,
  assessmentHeadline,
  outcomeTone,
  projectedValidity,
  recommendationOptions,
} from "@/core/dbtl/validity-view";
import type { HeadlineMetric, ValidityCheck } from "@/core/dbtl/validity-view";

function checks(
  overrides: Partial<Record<string, ValidityCheck["status"]>> = {},
) {
  return Object.keys(VALIDITY_CHECK_LABELS).map((check) => ({
    check,
    status: overrides[check] ?? "passed",
    detail: `${check} evidence`,
    evidence_refs:
      (overrides[check] ?? "passed") === "passed"
        ? [`artifact://${check}`]
        : [],
  })) as ValidityCheck[];
}

const highMetric: HeadlineMetric = {
  name: "Accuracy",
  value: 0.94,
  threshold: 0.7,
  criterion: "gte",
  plausible_max: null,
  unit: "",
};

describe("validity vocabulary", () => {
  test("every outcome and check has a visible label", () => {
    for (const outcome of VALIDITY_OUTCOMES) {
      expect(OUTCOME_LABELS[outcome]).toBeTruthy();
    }
    expect(Object.keys(VALIDITY_CHECK_LABELS)).toHaveLength(10);
  });

  test("only scientifically supported evidence has a positive tone", () => {
    expect(outcomeTone("supported")).toBe("positive");
    expect(outcomeTone("not_supported")).toBe("neutral");
    expect(outcomeTone("inconclusive")).toBe("pending");
    expect(outcomeTone("invalidated")).toBe("critical");
  });
});

describe("metric and validity separation", () => {
  test("high accuracy with leakage renders invalidated, never positive", () => {
    const result = projectedValidity(
      [highMetric],
      checks({ leakage: "failed" }),
    );
    expect(result.outcome).toBe("invalidated");
    expect(result.headline_success).toBe(true);
    expect(outcomeTone(result.outcome)).toBe("critical");
    expect(assessmentHeadline(result)).toContain("does not override");
    expect(recommendationOptions(result).map((item) => item.id)).toContain(
      "learn_from_invalidated_evidence",
    );
    expect(recommendationOptions(result).map((item) => item.id)).not.toContain(
      "return_to_reconciliation",
    );
  });

  test("missing holdout is inconclusive", () => {
    const result = projectedValidity(
      [highMetric],
      checks({ tester_holdout: "missing" }),
    );
    expect(result.outcome).toBe("inconclusive");
    expect(recommendationOptions(result).map((item) => item.id)).not.toContain(
      "advance_to_learn",
    );
    expect(recommendationOptions(result).map((item) => item.id)).not.toContain(
      "return_to_reconciliation",
    );
  });

  test("a valid negative can advance to Learn", () => {
    const result = projectedValidity(
      [{ ...highMetric, value: 0.51 }],
      checks(),
    );
    expect(result.outcome).toBe("not_supported");
    expect(recommendationOptions(result).map((item) => item.id)).toContain(
      "advance_to_learn",
    );
  });

  test("checks outside the server-owned pack do not become invented gates", () => {
    const required = Object.keys(VALIDITY_CHECK_LABELS).filter(
      (check) => check !== "duplicates_relatedness",
    );
    const result = projectedValidity(
      [highMetric],
      checks().filter((check) => required.includes(check.check)),
      required,
    );

    expect(result.outcome).toBe("supported");
    expect(result.reason_codes).not.toContain(
      "missing_duplicates_relatedness",
    );
  });
});
