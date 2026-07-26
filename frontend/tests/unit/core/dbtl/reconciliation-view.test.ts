import { describe, expect, test } from "@rstest/core";

import {
  BLOCKER_KIND_LABELS,
  GATE_OUTCOME_LABELS,
  RECONCILIATION_ROW_STATUSES,
  ROW_STATUS_LABELS,
  blockingRows,
  canDecideRow,
  canSubmitDecision,
  decidedBy,
  decisionOptions,
  gateHeadline,
  gateTone,
  invalidationNotice,
  mutableRawSources,
  orderedRows,
  reconciliationBlockReason,
  requiresBlockerKind,
  rowStatusTone,
  shortHash,
  sourceCells,
} from "@/core/dbtl/reconciliation-view";
import type {
  DatasetRecord,
  GateState,
  ReconciliationRow,
  ReconciliationView,
  RowStatus,
} from "@/core/dbtl/reconciliation-view";

function row(overrides: Partial<ReconciliationRow> = {}): ReconciliationRow {
  const base: ReconciliationRow = {
    row_id: "row-1",
    check: "units_and_encoding",
    check_label: "Units, encodings, and missing-value conventions",
    field_name: "Yield units",
    source_a_label: "Source A",
    source_a_value: "bu/ac",
    source_b_label: "Source B",
    source_b_value: "Mg/ha",
    required: true,
    status: "open",
    resolution: "",
    resolved_by_actor: null,
    resolved_by_user_id: null,
    blocker_kind: null,
    evidence_refs: [],
    needs_human_decision: false,
    blocks_gate: true,
    db_revision: 5,
  };
  return { ...base, ...overrides };
}

function gate(overrides: Partial<GateState> = {}): GateState {
  const base: GateState = {
    outcome: "changes_required",
    ready: false,
    blocking_rows: ["row-1"],
    reasons: ["Yield units: not yet reconciled."],
    dataset_fingerprint: "f".repeat(64),
    resolved_count: 1,
    total_required: 3,
  };
  return { ...base, ...overrides };
}

function dataset(overrides: Partial<DatasetRecord> = {}): DatasetRecord {
  const base: DatasetRecord = {
    id: "dataset-1",
    source_key: "yield_trial",
    uri: "/mnt/user-data/workspace/yield.csv",
    content_hash: "a".repeat(64),
    declared_immutable: true,
    role: "raw",
    recorded_by: "user-1",
    db_revision: 3,
    created_at: "2026-07-26T00:00:00Z",
    updated_at: "2026-07-26T00:00:00Z",
  };
  return { ...base, ...overrides };
}

describe("vocabulary", () => {
  test("every row status has a word, not only a colour", () => {
    for (const status of RECONCILIATION_ROW_STATUSES) {
      expect(ROW_STATUS_LABELS[status]).toBeTruthy();
    }
  });

  test("row statuses match the backend contract", () => {
    expect([...RECONCILIATION_ROW_STATUSES]).toEqual([
      "open",
      "proposed",
      "resolved",
      "blocked",
      "waived",
    ]);
  });

  test("every gate outcome and blocker kind has a label", () => {
    expect(Object.keys(GATE_OUTCOME_LABELS)).toHaveLength(5);
    expect(Object.keys(BLOCKER_KIND_LABELS)).toHaveLength(3);
  });

  test("a proposal reads as unfinished work, not as a resolution", () => {
    expect(ROW_STATUS_LABELS.proposed).toContain("awaiting");
    expect(rowStatusTone("proposed")).toBe("pending");
  });

  test("only settled statuses read as positive", () => {
    const positive = RECONCILIATION_ROW_STATUSES.filter(
      (status: RowStatus) => rowStatusTone(status) === "positive",
    );
    expect(positive).toEqual(["resolved", "waived"]);
  });
});

describe("the gate", () => {
  test("a ready gate still names the count a reviewer can check", () => {
    const headline = gateHeadline(
      gate({ ready: true, outcome: "ready_for_build", resolved_count: 3 }),
    );
    expect(headline).toBe("Ready for Build — 3 of 3 required rows settled.");
  });

  test("a blocked gate leads with what has to be fixed", () => {
    expect(gateHeadline(gate({ outcome: "blocked_conflicting_sources" }))).toContain(
      "Blocked — sources conflict",
    );
  });

  test("one required row is singular", () => {
    expect(
      gateHeadline(gate({ ready: true, resolved_count: 1, total_required: 1 })),
    ).toContain("1 of 1 required row settled");
  });

  test("changes required is pending, a hard block is critical", () => {
    expect(gateTone(gate({ outcome: "changes_required" }))).toBe("pending");
    expect(gateTone(gate({ outcome: "blocked_missing_data" }))).toBe("critical");
    expect(gateTone(gate({ ready: true, outcome: "ready_for_build" }))).toBe(
      "positive",
    );
  });
});

describe("row ordering", () => {
  test("blocking rows come first so the worst is not buried", () => {
    const rows = [
      row({ row_id: "settled", field_name: "A", status: "resolved", blocks_gate: false }),
      row({ row_id: "open", field_name: "Z", status: "open", blocks_gate: true }),
    ];
    expect(orderedRows(rows).map((item) => item.row_id)).toEqual(["open", "settled"]);
  });

  test("blocking rows are sorted blocked, then proposed, then open", () => {
    const rows = [
      row({ row_id: "open", status: "open" }),
      row({ row_id: "blocked", status: "blocked" }),
      row({ row_id: "proposed", status: "proposed" }),
    ];
    expect(blockingRows(rows).map((item) => item.row_id)).toEqual([
      "blocked",
      "proposed",
      "open",
    ]);
  });

  test("ordering does not mutate the input", () => {
    const rows = [row({ row_id: "a", blocks_gate: false }), row({ row_id: "b" })];
    const before = rows.map((item) => item.row_id);
    orderedRows(rows);
    blockingRows(rows);
    expect(rows.map((item) => item.row_id)).toEqual(before);
  });
});

describe("decisions", () => {
  test("a settled row offers no further decision", () => {
    expect(canDecideRow(row({ status: "resolved" }))).toBe(false);
    expect(canDecideRow(row({ status: "waived" }))).toBe(false);
  });

  test("an agent's proposal is still decidable by a person", () => {
    expect(canDecideRow(row({ status: "proposed" }))).toBe(true);
  });

  test("blocking requires naming why", () => {
    expect(requiresBlockerKind("blocked")).toBe(true);
    expect(canSubmitDecision("blocked", "cannot match IDs", null)).toBe(false);
    expect(canSubmitDecision("blocked", "cannot match IDs", "missing_data")).toBe(
      true,
    );
  });

  test("every decision requires a rationale", () => {
    expect(canSubmitDecision("resolved", "   ", null)).toBe(false);
    expect(canSubmitDecision("waived", "out of scope", null)).toBe(true);
  });

  test("resolving a contradiction says it is your call", () => {
    const options = decisionOptions(row({ needs_human_decision: true }));
    expect(options[0]!.consequence).toContain("your call");
  });

  test("blocking says Build stays locked", () => {
    const blocked = decisionOptions(row()).find((item) => item.id === "blocked");
    expect(blocked!.consequence).toContain("Build locked");
  });
});

describe("attribution", () => {
  test("an agent proposal says a reviewer is still needed", () => {
    expect(
      decidedBy(row({ status: "proposed", resolved_by_actor: "agent" })),
    ).toContain("needs a reviewer");
  });

  test("a human decision names the reviewer", () => {
    expect(
      decidedBy(
        row({
          status: "resolved",
          resolved_by_actor: "human",
          resolved_by_user_id: "user-7",
        }),
      ),
    ).toBe("Decided by user-7");
  });

  test("an undecided row says so", () => {
    expect(decidedBy(row())).toBe("Not yet decided");
  });
});

describe("sources", () => {
  test("a single-source row renders one cell", () => {
    const cells = sourceCells(row({ source_b_label: "", source_b_value: "" }));
    expect(cells).toHaveLength(1);
    expect(cells[0]!.value).toBe("bu/ac");
  });

  test("a comparison row renders both cells", () => {
    expect(sourceCells(row())).toHaveLength(2);
  });
});

describe("invalidation and datasets", () => {
  test("nothing is rendered when the approval still holds", () => {
    expect(
      invalidationNotice({
        approval_invalidation: { invalidated: false, reasons: [] },
      }),
    ).toBeNull();
    expect(invalidationNotice({ approval_invalidation: null })).toBeNull();
  });

  test("a changed dataset produces a notice carrying the reasons", () => {
    const notice = invalidationNotice({
      approval_invalidation: {
        invalidated: true,
        reasons: ["A declared dataset changed since this reconciliation was approved."],
      },
    });
    expect(notice!.headline).toContain("no longer matches");
    expect(notice!.reasons).toHaveLength(1);
  });

  test("only writable raw sources are flagged", () => {
    const datasets = [
      dataset({ id: "ok" }),
      dataset({ id: "writable-raw", declared_immutable: false }),
      dataset({ id: "derived", role: "derived", declared_immutable: false }),
    ];
    expect(mutableRawSources(datasets).map((item) => item.id)).toEqual([
      "writable-raw",
    ]);
  });

  test("hashes are shortened for display without losing the full value", () => {
    expect(shortHash("a".repeat(64))).toBe(`${"a".repeat(12)}…`);
    expect(shortHash("short")).toBe("short");
  });
});

describe("block reasons", () => {
  function view(overrides: Partial<ReconciliationView> = {}): ReconciliationView {
    const base: ReconciliationView = {
      cycle_id: "cycle-1",
      design_approved: true,
      rows: [],
      unreadable_row_ids: [],
      datasets: [dataset()],
      gate: gate(),
      summary: { open: 0, proposed: 0, resolved: 0, blocked: 0, waived: 0 },
      approval_binding: null,
      approval_invalidation: null,
      db_revision: 4,
    };
    return { ...base, ...overrides };
  }

  test("an unapproved design is named rather than shown as an empty matrix", () => {
    expect(reconciliationBlockReason(view({ design_approved: false }))).toContain(
      "Design has not been approved",
    );
  });

  test("no declared sources is named too", () => {
    expect(reconciliationBlockReason(view({ datasets: [] }))).toContain(
      "No data sources have been declared",
    );
  });

  test("a workable matrix has no block reason", () => {
    expect(reconciliationBlockReason(view())).toBe("");
  });

  test("no view yields no reason rather than a crash", () => {
    expect(reconciliationBlockReason(null)).toBe("");
  });
});
