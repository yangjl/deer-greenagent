import { describe, expect, test } from "@rstest/core";

import {
  buildReadinessExport,
  groupReadinessItems,
  type DbtlReadinessReport,
} from "@/core/dbtl";

const report: DbtlReadinessReport = {
  mode: "audit_only",
  mutations_enabled: false,
  graph_execution_enabled: false,
  reason: "Inspection only",
  policy_version: "v2-draft",
  test_outcomes: ["supported", "not_supported", "inconclusive", "invalidated"],
  knowledge_candidate_statuses: [
    "candidate",
    "promoted",
    "rejected",
    "superseded",
  ],
  counts: {
    compatible: 1,
    repairable: 1,
    invalid_or_ambiguous: 0,
    safe_to_supersede: 0,
  },
  checks: [],
  items: [
    {
      path: ".greenagent/dbtl-cycles/repair.json",
      record_type: "cycle",
      classification: "repairable",
      reason: "Needs mapping",
      record_id: "repair",
    },
    {
      path: ".greenagent/handoffs/ok.json",
      record_type: "handoff",
      classification: "compatible",
      reason: "Readable",
      record_id: "ok",
    },
  ],
};

describe("DBTL readiness helpers", () => {
  test("groups all four review classifications in stable order", () => {
    const groups = groupReadinessItems(report.items);

    expect(groups.map((group) => group.classification)).toEqual([
      "compatible",
      "repairable",
      "invalid_or_ambiguous",
      "safe_to_supersede",
    ]);
    expect(groups[0]?.items[0]?.record_id).toBe("ok");
    expect(groups[1]?.items[0]?.record_id).toBe("repair");
  });

  test("builds a human-readable JSON export without changing the report", () => {
    const before = structuredClone(report);
    const exported = buildReadinessExport(report);

    expect(exported.filename).toMatch(/^greenagent-dbtl-readiness-/);
    expect(JSON.parse(exported.contents)).toEqual(report);
    expect(report).toEqual(before);
  });
});
