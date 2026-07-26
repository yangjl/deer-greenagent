import { describe, expect, it } from "@rstest/core";

import {
  ORDINARY_REQUEST_CONTEXT,
  type RequestContext,
  SCOPE_ORDINARY_LABEL,
  SCOPE_RECOMMEND_LABEL,
  SCOPE_START_CYCLE_LABEL,
  START_CYCLE_REQUEST_CONTEXT,
  cycleShortLabel,
  isContextStillValid,
  nextContextAfterSend,
  normalizeContext,
  proposalContextPayload,
  runActivityMetadata,
  humanInputRunContext,
  runContextPayload,
  scopeLabel,
  scopeMenuOptions,
} from "@/core/dbtl/composer-scope";
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

describe("scope label", () => {
  it("names ordinary work in words, not by absence", () => {
    expect(scopeLabel(ORDINARY_REQUEST_CONTEXT, [])).toBe(SCOPE_ORDINARY_LABEL);
  });

  it("shows the cycle number and its current stage", () => {
    const cycles = [cycle({ id: "cyc-1", state: "design" })];
    const label = scopeLabel({ kind: "cycle", cycleId: "cyc-1" }, cycles);
    expect(label).toBe("Cycle 01 · Design");
  });

  it("uses the cycle's own stage wording rather than a generic one", () => {
    const cycles = [cycle({ id: "cyc-1", state: "reconciliation" })];
    expect(scopeLabel({ kind: "cycle", cycleId: "cyc-1" }, cycles)).toBe(
      "Cycle 01 · Data reconciliation",
    );
  });

  it("falls back to ordinary when the selected cycle is gone", () => {
    // A cycle can be completed or abandoned in another tab between the click
    // and the send. Showing a stale cycle would misstate the request's scope.
    expect(scopeLabel({ kind: "cycle", cycleId: "cyc-missing" }, [])).toBe(
      SCOPE_ORDINARY_LABEL,
    );
  });

  it("labels the recommend option distinctly", () => {
    expect(scopeLabel({ kind: "recommend", cycleId: null }, [])).toBe(
      SCOPE_RECOMMEND_LABEL,
    );
  });

  it("labels the start-a-cycle scope distinctly", () => {
    expect(scopeLabel(START_CYCLE_REQUEST_CONTEXT, [])).toBe(
      SCOPE_START_CYCLE_LABEL,
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

describe("scope menu", () => {
  it("always offers keeping the request ordinary, first", () => {
    const options = scopeMenuOptions([]);
    expect(options[0]).toMatchObject({
      kind: "ordinary",
      label: SCOPE_ORDINARY_LABEL,
    });
  });

  it("always offers asking the AI to recommend, last", () => {
    const options = scopeMenuOptions([cycle()]);
    expect(options[options.length - 1]).toMatchObject({ kind: "recommend" });
  });

  it("offers each live cycle", () => {
    const options = scopeMenuOptions([
      cycle({ id: "cyc-1", state: "design" }),
      cycle({ id: "cyc-2", state: "build" }),
    ]);
    expect(
      options.filter((o) => o.kind === "cycle").map((o) => o.cycleId),
    ).toEqual(["cyc-1", "cyc-2"]);
  });

  it.each<CycleState>(["completed", "abandoned"])(
    "never offers to continue a %s cycle",
    (state) => {
      const options = scopeMenuOptions([cycle({ id: "cyc-done", state })]);
      expect(options.some((o) => o.kind === "cycle")).toBe(false);
    },
  );

  it("keeps numbering aligned with the full list, not the live subset", () => {
    // Cycle 01 completed; the second cycle must still read "Cycle 02", or the
    // menu and the project rail would disagree about which cycle is which.
    const options = scopeMenuOptions([
      cycle({ id: "cyc-1", state: "completed" }),
      cycle({ id: "cyc-2", state: "build" }),
    ]);
    const live = options.filter((o) => o.kind === "cycle");
    expect(live).toHaveLength(1);
    expect(live[0]?.label).toBe("Cycle 02 · Build");
  });

  it("offers starting a cycle from the composer, after the continuable ones", () => {
    // Starting a cycle is a composer scope, not a form: the request the user
    // types becomes the setup conversation. It sits after the cycles so the
    // cycle-related choices read as one group.
    const options = scopeMenuOptions([cycle({ id: "cyc-1", state: "design" })]);
    expect(options.map((option) => option.kind)).toEqual([
      "ordinary",
      "cycle",
      "start_cycle",
      "recommend",
    ]);
  });

  it("still offers starting a cycle when the project has none", () => {
    const options = scopeMenuOptions([]);
    expect(options.some((option) => option.kind === "start_cycle")).toBe(true);
  });

  it("says that starting a cycle creates no record yet", () => {
    // The backend's setup branch proposes and asks for confirmation without
    // writing. The menu must not imply the click itself creates the record.
    const option = scopeMenuOptions([]).find((o) => o.kind === "start_cycle");
    expect(option?.description).toMatch(
      /nothing is recorded until you confirm/i,
    );
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

  it("routes the start-a-cycle scope into the backend's setup branch", () => {
    expect(runContextPayload(START_CYCLE_REQUEST_CONTEXT)).toEqual({
      dbtl_supervisor_enabled: true,
      dbtl_explicit_choice: "start_cycle",
    });
  });

  it("never sends a cycle id alongside a start request", () => {
    // Setup has no cycle yet. Sending a stale id would let the backend read the
    // request as a continuation of something the user is not continuing.
    expect(
      runContextPayload({ kind: "start_cycle", cycleId: "cyc-9" }),
    ).toEqual({
      dbtl_supervisor_enabled: true,
      dbtl_explicit_choice: "start_cycle",
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
    expect(proposalContextPayload({ kind: "cycle", cycleId: "cyc-9" })).toEqual(
      {
        selectedCycleId: "cyc-9",
        explicitChoice: "continue_cycle",
      },
    );
  });

  it("sends the same start-a-cycle intent to both routing paths", () => {
    // Phase 4's visible proposal and Phase 5's graph route must agree, or a
    // card could recommend starting a cycle while the run reads as ordinary.
    expect(proposalContextPayload(START_CYCLE_REQUEST_CONTEXT)).toEqual({
      selectedCycleId: null,
      explicitChoice: "start_cycle",
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
    expect(runActivityMetadata({ kind: "cycle", cycleId: "cyc-9" })).toEqual({
      dbtl_request_context: {
        kind: "cycle",
        cycle_id: "cyc-9",
      },
    });
  });

  it("echoes a start request without inventing a cycle id", () => {
    expect(runActivityMetadata(START_CYCLE_REQUEST_CONTEXT)).toEqual({
      dbtl_request_context: {
        kind: "start_cycle",
        cycle_id: null,
      },
    });
  });
});

describe("per-request scope", () => {
  it("resets to ordinary after a send", () => {
    // The menu promises the selection affects the next request only. That has
    // to be enforced here, not remembered by the user.
    expect(nextContextAfterSend({ kind: "cycle", cycleId: "cyc-1" })).toEqual(
      ORDINARY_REQUEST_CONTEXT,
    );
    expect(nextContextAfterSend({ kind: "recommend", cycleId: null })).toEqual(
      ORDINARY_REQUEST_CONTEXT,
    );
  });

  it("resets after a start request too, so setup cannot capture later turns", () => {
    // The setup conversation continues through the assistant's own follow-up
    // questions; a sticky "start" scope would re-enter setup on every message.
    expect(nextContextAfterSend(START_CYCLE_REQUEST_CONTEXT)).toEqual(
      ORDINARY_REQUEST_CONTEXT,
    );
  });

  it("treats a selection whose cycle went terminal as no longer valid", () => {
    const cycles = [cycle({ id: "cyc-1", state: "completed" })];
    expect(
      isContextStillValid({ kind: "cycle", cycleId: "cyc-1" }, cycles),
    ).toBe(false);
  });

  it("treats ordinary, recommend, and start as always valid", () => {
    expect(isContextStillValid(ORDINARY_REQUEST_CONTEXT, [])).toBe(true);
    expect(isContextStillValid({ kind: "recommend", cycleId: null }, [])).toBe(
      true,
    );
    // Starting a cycle depends on no existing record, so no cycle list can
    // invalidate it.
    expect(isContextStillValid(START_CYCLE_REQUEST_CONTEXT, [])).toBe(true);
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

describe("humanInputRunContext", () => {
  it("returns a design question to the cycle whose council raised it", () => {
    expect(
      humanInputRunContext(
        { source: "ask_clarification", clarification_type: "design_decision" },
        "cyc-9",
      ),
    ).toEqual({
      dbtl_supervisor_enabled: true,
      dbtl_explicit_choice: "continue_cycle",
      dbtl_selected_cycle_id: "cyc-9",
    });
  });

  it("returns a setup question to the setup branch, naming no cycle", () => {
    // Setup has not created a record yet, so there is nothing to continue.
    // Answering must not read as a continuation of some other cycle.
    expect(
      humanInputRunContext(
        { source: "ask_clarification", clarification_type: "cycle_setup" },
        "cyc-9",
      ),
    ).toEqual({
      dbtl_supervisor_enabled: true,
      dbtl_explicit_choice: "start_cycle",
    });
  });

  it("falls back to ordinary for clarifications it does not know", () => {
    expect(
      humanInputRunContext({ source: "ask_clarification" }, null),
    ).toEqual({
      dbtl_supervisor_enabled: true,
      dbtl_explicit_choice: "ordinary",
    });
    expect(humanInputRunContext({ source: "some_other_tool" }, "cyc-9")).toEqual(
      {
        dbtl_supervisor_enabled: true,
        dbtl_explicit_choice: "ordinary",
      },
    );
  });

  it("does not claim a design continuation with no cycle selected", () => {
    expect(
      humanInputRunContext(
        { source: "ask_clarification", clarification_type: "design_decision" },
        null,
      ),
    ).toEqual({
      dbtl_supervisor_enabled: true,
      dbtl_explicit_choice: "ordinary",
    });
  });
});
