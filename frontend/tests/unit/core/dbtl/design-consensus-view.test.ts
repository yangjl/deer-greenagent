import { describe, expect, it } from "@rstest/core";

import {
  parseDesignConsensusPackage,
  structuredPackagePath,
} from "@/core/dbtl/design-consensus-view";

describe("structuredPackagePath", () => {
  it("resolves the machine package beside the bound review document", () => {
    expect(
      structuredPackagePath(
        "outputs/dbtl/cycle-1/design/review-abc.md",
        [
          "## Machine record",
          "- Structured package: `package-def.json`",
          `- SHA-256: \`${"a".repeat(64)}\``,
        ].join("\n"),
      ),
    ).toBe("outputs/dbtl/cycle-1/design/package-def.json");
  });

  it("refuses a package reference that escapes the review directory", () => {
    expect(
      structuredPackagePath(
        "outputs/dbtl/cycle-1/design/review.md",
        "- Structured package: `../other.json`",
      ),
    ).toBeNull();
  });
});

describe("parseDesignConsensusPackage", () => {
  it("reads the chair's decision map without recomputing consensus", () => {
    const view = parseDesignConsensusPackage(
      JSON.stringify({
        results: [
          {
            capability: "design_council_chair",
            summary: "Use two seasons with a family-held-out validation set.",
            limitations: ["The third site is not confirmed."],
            quality_checks: [
              {
                name: "Leakage boundary",
                passed: false,
                detail: "Families must not cross folds.",
              },
            ],
            recommended_next_actions: ["Confirm irrigation at Site C."],
            consensus: {
              agreements: ["Hold families out together."],
              disagreements: [
                {
                  topic: "Season count",
                  positions: ["Two seasons.", "Three seasons."],
                  resolution: "Start with two and pre-register expansion.",
                },
              ],
              open_questions: ["Can Site C guarantee irrigation?"],
            },
          },
        ],
      }),
    );

    expect(view).toEqual({
      summary: "Use two seasons with a family-held-out validation set.",
      agreements: ["Hold families out together."],
      disagreements: [
        {
          topic: "Season count",
          positions: ["Two seasons.", "Three seasons."],
          resolution: "Start with two and pre-register expansion.",
        },
      ],
      openQuestions: ["Can Site C guarantee irrigation?"],
      limitations: ["The third site is not confirmed."],
      failedChecks: [
        "Leakage boundary: Families must not cross folds.",
      ],
      nextActions: ["Confirm irrigation at Site C."],
    });
  });

  it("does not manufacture a decision map from a package with no consensus", () => {
    expect(
      parseDesignConsensusPackage(
        JSON.stringify({
          results: [
            {
              capability: "experimental_design",
              summary: "A position only.",
            },
          ],
        }),
      ),
    ).toBeNull();
  });
});
