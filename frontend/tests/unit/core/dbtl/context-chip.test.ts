import { describe, expect, it } from "@rstest/core";

import {
  CHIP_ORDINARY_LABEL,
  CHIP_RECOMMEND_LABEL,
  ORDINARY_REQUEST_CONTEXT,
  type RequestContext,
  chipLabel,
  contextMenuOptions,
  cycleShortLabel,
  isContextStillValid,
  nextContextAfterSend,
  normalizeContext,
  proposalContextPayload,
  runActivityMetadata,
  runContextPayload,
} from "@/core/dbtl/context-chip";
import type { CycleRecord, CycleState } from "@/core/dbtl/cycle-view";

function cycle(overrides: Partial<CycleRecord> = {}): CycleRecord {
  return {
    id: "cyc-1",
    project_id: "proj-1",
    parent_cycle_id: null,
    title: "Drought response",
    cycle_class: "computational",
    cycle_weight: "full",
    state: "design",
    db_revision: 1,
    research_question: "q",
    objective: "o",
    success_criteria: "s",
    created_by: "user-1",
    created_at: "2026-07-25T00:00:00Z",
    updated_at: "2026-07-25T00:00:00Z",
    stages: [],
    ...overrides,
  };
}

describe("chip label", () => {
  it("names ordinary work in words, not by absence", () => {
    expect(chipLabel(ORDINARY_REQUEST_CONTEXT, [])).toBe(CHIP_ORDINARY_LABEL);
  });

  it("shows the cycle number and its current stage", () => {
    const cycles = [cycle({ id: "cyc-1", state: "design" })];
    const label = chipLabel({ kind: "cycle", cycleId: "cyc-1" }, cycles);
    expect(label).toBe("Cycle 01 · Design");
  });

  it("uses the cycle's own stage wording rather than a generic one", () => {
    const cycles = [cycle({ id: "cyc-1", state: "reconciliation" })];
    expect(chipLabel({ kind: "cycle", cycleId: "cyc-1" }, cycles)).toBe(
      "Cycle 01 · Data reconciliation",
    );
  });

  it("falls back to ordinary when the selected cycle is gone", () => {
    // A cycle can be completed or abandoned in another tab between the click
    // and the send. Showing a stale cycle would misstate the request's scope.
    expect(chipLabel({ kind: "cycle", cycleId: "cyc-missing" }, [])).toBe(
      CHIP_ORDINARY_LABEL,
    );
  });

  it("labels the recommend option distinctly", () => {
    expect(chipLabel({ kind: "recommend", cycleId: null }, [])).toBe(
      CHIP_RECOMMEND_LABEL,
    );
  });
});

describe("cycle numbering", () => {
  it("numbers by position, zero-padded, so it is stable and scannable", () => {
    const cycles = [cycle({ id: "a" }), cycle({ id: "b" }), cycle({ id: "c" })];
    expect(cycles.map((c, i) => cycleShortLabel(c, i))).toEqual([
      "Cycle 01 · Design",
      "Cycle 02 · Design",
      "Cycle 03 · Design",
    ]);
  });

  it("does not pad past two digits", () => {
    expect(cycleShortLabel(cycle(), 11)).toBe("Cycle 12 · Design");
  });
});

describe("context menu", () => {
  it("always offers keeping the request ordinary, first", () => {
    const options = contextMenuOptions([]);
    expect(options[0]).toMatchObject({ kind: "ordinary", label: CHIP_ORDINARY_LABEL });
  });

  it("always offers asking the AI to recommend, last", () => {
    const options = contextMenuOptions([cycle()]);
    expect(options[options.length - 1]).toMatchObject({ kind: "recommend" });
  });

  it("offers each live cycle", () => {
    const options = contextMenuOptions([
      cycle({ id: "cyc-1", state: "design" }),
      cycle({ id: "cyc-2", state: "build" }),
    ]);
    expect(options.filter((o) => o.kind === "cycle").map((o) => o.cycleId)).toEqual([
      "cyc-1",
      "cyc-2",
    ]);
  });

  it.each<CycleState>(["completed", "abandoned"])(
    "never offers to continue a %s cycle",
    (state) => {
      const options = contextMenuOptions([cycle({ id: "cyc-done", state })]);
      expect(options.some((o) => o.kind === "cycle")).toBe(false);
    },
  );

  it("keeps numbering aligned with the full list, not the live subset", () => {
    // Cycle 01 completed; the second cycle must still read "Cycle 02", or the
    // chip and the project rail would disagree about which cycle is which.
    const options = contextMenuOptions([
      cycle({ id: "cyc-1", state: "completed" }),
      cycle({ id: "cyc-2", state: "build" }),
    ]);
    const live = options.filter((o) => o.kind === "cycle");
    expect(live).toHaveLength(1);
    expect(live[0]?.label).toBe("Cycle 02 · Build");
  });
});

describe("run context payload", () => {
  it("maps ordinary to the explicit choice the backend understands", () => {
    expect(runContextPayload(ORDINARY_REQUEST_CONTEXT)).toEqual({
      dbtl_supervisor_enabled: true,
      dbtl_explicit_choice: "ordinary",
    });
  });

  it("maps a cycle to a continuation plus the cycle id", () => {
    expect(runContextPayload({ kind: "cycle", cycleId: "cyc-9" })).toEqual({
      dbtl_supervisor_enabled: true,
      dbtl_explicit_choice: "continue_cycle",
      dbtl_selected_cycle_id: "cyc-9",
    });
  });

  it("selects the supervisor but sends no explicit choice for recommend", () => {
    // "Ask the AI to recommend" omits a routing choice while retaining the
    // one-run supervisor opt-in.
    expect(runContextPayload({ kind: "recommend", cycleId: null })).toEqual({
      dbtl_supervisor_enabled: true,
    });
  });

  it("never claims a cycle continuation without a cycle id", () => {
    expect(runContextPayload({ kind: "cycle", cycleId: null })).toEqual({
      dbtl_supervisor_enabled: true,
      dbtl_explicit_choice: "ordinary",
    });
  });
});

describe("shared routing context", () => {
  it("keeps ordinary work explicit for both the graph and proposal evaluator", () => {
    expect(proposalContextPayload(ORDINARY_REQUEST_CONTEXT)).toEqual({
      selectedCycleId: null,
      explicitChoice: "ordinary",
    });
  });

  it("sends the same cycle continuation to both routing paths", () => {
    expect(
      proposalContextPayload({ kind: "cycle", cycleId: "cyc-9" }),
    ).toEqual({
      selectedCycleId: "cyc-9",
      explicitChoice: "continue_cycle",
    });
  });

  it("leaves recommendation unforced so the classifier can decide", () => {
    expect(
      proposalContextPayload({ kind: "recommend", cycleId: null }),
    ).toEqual({
      selectedCycleId: null,
      explicitChoice: null,
    });
  });

  it("persists an inspectable run-scope echo without steering execution", () => {
    expect(
      runActivityMetadata({ kind: "cycle", cycleId: "cyc-9" }),
    ).toEqual({
      dbtl_request_context: {
        kind: "cycle",
        cycle_id: "cyc-9",
      },
    });
  });
});

describe("per-request scope", () => {
  it("resets to ordinary after a send", () => {
    // The chip promises the selection affects the next request only. That has
    // to be enforced here, not remembered by the user.
    expect(nextContextAfterSend({ kind: "cycle", cycleId: "cyc-1" })).toEqual(
      ORDINARY_REQUEST_CONTEXT,
    );
    expect(nextContextAfterSend({ kind: "recommend", cycleId: null })).toEqual(
      ORDINARY_REQUEST_CONTEXT,
    );
  });

  it("treats a selection whose cycle went terminal as no longer valid", () => {
    const cycles = [cycle({ id: "cyc-1", state: "completed" })];
    expect(isContextStillValid({ kind: "cycle", cycleId: "cyc-1" }, cycles)).toBe(false);
  });

  it("treats ordinary and recommend as always valid", () => {
    expect(isContextStillValid(ORDINARY_REQUEST_CONTEXT, [])).toBe(true);
    expect(isContextStillValid({ kind: "recommend", cycleId: null }, [])).toBe(true);
  });

  it("normalizes an invalid selection back to ordinary", () => {
    const cycles = [cycle({ id: "cyc-1", state: "abandoned" })];
    const context: RequestContext = { kind: "cycle", cycleId: "cyc-1" };
    expect(normalizeContext(context, cycles)).toEqual(ORDINARY_REQUEST_CONTEXT);
  });

  it("leaves a valid selection untouched", () => {
    const cycles = [cycle({ id: "cyc-1", state: "build" })];
    const context: RequestContext = { kind: "cycle", cycleId: "cyc-1" };
    expect(normalizeContext(context, cycles)).toBe(context);
  });
});
