import { describe, expect, test } from "@rstest/core";

import {
  DECISION_ORDER,
  EMPTY_COUNTS,
  canSubmitDecision,
  countRows,
  decisionConsequence,
  isMigrationEmpty,
  nextSuggestionKey,
  provenanceLabel,
  reviewCallToAction,
  suggestionKey,
} from "@/core/memory-scope/review";
import type {
  MigrationCounts,
  MigrationSuggestion,
} from "@/core/memory-scope/review";

function counts(overrides: Partial<MigrationCounts> = {}): MigrationCounts {
  return { ...EMPTY_COUNTS, ...overrides };
}

function suggestion(
  overrides: Partial<MigrationSuggestion> = {},
): MigrationSuggestion {
  return {
    fact_id: "fact_1",
    bucket_id: "user-1--project--abc",
    agent_name: "__default__",
    owner_user_id: "user-1",
    sha256: "a".repeat(64),
    category: "context",
    suggested_decision: "share",
    reason: "Category 'context' describes the work.",
    title: "Tropical lines",
    content: "Tropical lines flower late here.",
    ...overrides,
  };
}

describe("landing view", () => {
  test("shows all four counts in a fixed order", () => {
    const rows = countRows(counts({ private_legacy: 24 }));

    expect(rows.map((row) => row.key)).toEqual([
      "private_legacy",
      "suggested_for_project",
      "already_project_scoped",
      "needs_classification",
    ]);
    expect(rows[0]?.value).toBe(24);
  });

  test("only the suggestion count is an actionable row", () => {
    const rows = countRows(
      counts({ private_legacy: 24, suggested_for_project: 7 }),
    );

    expect(rows.filter((row) => row.actionable).map((row) => row.key)).toEqual([
      "private_legacy",
    ]);
  });

  test("nothing is actionable when there is nothing left to review", () => {
    const rows = countRows(counts({ suggested_for_project: 3 }));

    expect(rows.some((row) => row.actionable)).toBe(false);
  });

  test("missing count fields degrade to zero rather than NaN", () => {
    const rows = countRows({} as MigrationCounts);

    expect(rows.every((row) => row.value === 0)).toBe(true);
  });

  test("call to action counts the caller's own suggestions", () => {
    expect(reviewCallToAction(counts({ private_legacy: 7 }))).toBe(
      "Review my 7 facts",
    );
    expect(reviewCallToAction(counts({ private_legacy: 1 }))).toBe(
      "Review my 1 fact",
    );
    expect(reviewCallToAction(counts())).toBe("Review my memory");
  });

  test("an untouched project reports as empty", () => {
    expect(isMigrationEmpty(counts())).toBe(true);
    expect(isMigrationEmpty(counts({ needs_classification: 1 }))).toBe(false);
    expect(isMigrationEmpty(counts({ already_project_scoped: 2 }))).toBe(false);
  });
});

describe("decisions", () => {
  test("offers exactly the four per-fact actions and no bulk action", () => {
    expect([...DECISION_ORDER]).toEqual([
      "keep_private",
      "share",
      "edit_then_share",
      "quarantine",
    ]);
    expect(DECISION_ORDER.some((decision) => decision.includes("all"))).toBe(
      false,
    );
  });

  test("edit-then-share cannot be submitted without text", () => {
    expect(canSubmitDecision("edit_then_share", "")).toBe(false);
    expect(canSubmitDecision("edit_then_share", "   ")).toBe(false);
    expect(canSubmitDecision("edit_then_share", "Redacted.")).toBe(true);
  });

  test("the other decisions never depend on the draft", () => {
    for (const decision of ["keep_private", "share", "quarantine"] as const) {
      expect(canSubmitDecision(decision, "")).toBe(true);
    }
  });

  test("each decision states its consequence before it is taken", () => {
    expect(decisionConsequence("share")).toContain("authorized member");
    expect(decisionConsequence("edit_then_share")).toContain("edited text");
    expect(decisionConsequence("keep_private")).toContain("only to you");
    expect(decisionConsequence("quarantine")).toContain("Nothing moves");
  });
});

describe("queue advance", () => {
  const queue = [
    suggestion({ fact_id: "a" }),
    suggestion({ fact_id: "b" }),
    suggestion({ fact_id: "c" }),
  ];

  test("advances to the fact that takes the decided one's place", () => {
    expect(nextSuggestionKey(queue, suggestionKey(queue[0]!))).toBe(
      suggestionKey(queue[1]!),
    );
    expect(nextSuggestionKey(queue, suggestionKey(queue[1]!))).toBe(
      suggestionKey(queue[2]!),
    );
  });

  test("falls back to the new last fact when the tail is decided", () => {
    expect(nextSuggestionKey(queue, suggestionKey(queue[2]!))).toBe(
      suggestionKey(queue[1]!),
    );
  });

  test("returns null when the queue empties", () => {
    const only = suggestion({ fact_id: "only" });
    expect(nextSuggestionKey([only], suggestionKey(only))).toBeNull();
  });

  test("an unknown id restarts at the head instead of stranding the drawer", () => {
    expect(nextSuggestionKey(queue, "missing")).toBe(suggestionKey(queue[0]!));
    expect(nextSuggestionKey([], "missing")).toBeNull();
  });

  test("the same fact id in two agent buckets has two distinct keys", () => {
    const first = suggestion({ fact_id: "same", agent_name: "__default__" });
    const second = suggestion({ fact_id: "same", agent_name: "breeding-bot" });

    expect(suggestionKey(first)).not.toBe(suggestionKey(second));
  });
});

describe("provenance", () => {
  test("shows source, category, and a truncated checksum", () => {
    expect(provenanceLabel(suggestion())).toBe(
      `default agent · context · ${"a".repeat(12)}`,
    );
  });

  test("names a custom agent instead of the default sentinel", () => {
    expect(
      provenanceLabel(suggestion({ agent_name: "breeding-bot" })),
    ).toContain("breeding-bot");
  });

  test("never leaks the full checksum or the fact body", () => {
    const label = provenanceLabel(suggestion());

    expect(label).not.toContain("a".repeat(64));
    expect(label).not.toContain("Tropical lines flower late");
  });
});
