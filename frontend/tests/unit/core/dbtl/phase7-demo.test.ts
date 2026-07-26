import { describe, expect, it } from "@rstest/core";

import { PHASE7_DEMO_CASES } from "@/core/dbtl/phase7-demo";
import { projectedValidity } from "@/core/dbtl/validity-view";

describe("Phase 7 one-click demo fixtures", () => {
  it("shows the three review decisions a human needs to eyeball", () => {
    expect(
      PHASE7_DEMO_CASES.map((fixture) =>
        projectedValidity(fixture.metrics, fixture.checks).outcome,
      ),
    ).toEqual(["invalidated", "inconclusive", "supported"]);
  });

  it("keeps headline performance separate from scientific validity", () => {
    const invalidated = PHASE7_DEMO_CASES[0]!;
    const result = projectedValidity(
      invalidated.metrics,
      invalidated.checks,
    );

    expect(result.headline_success).toBe(true);
    expect(result.outcome).toBe("invalidated");
  });
});
