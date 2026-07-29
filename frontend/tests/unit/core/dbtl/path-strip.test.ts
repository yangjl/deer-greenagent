import { describe, expect, it } from "@rstest/core";

import type { DbtlStageTransition } from "@/core/dbtl/cycle-view";
import { derivePathStrip } from "@/core/dbtl/path-strip";

let sequence = 0;

function transition(
  overrides: Partial<DbtlStageTransition>,
): DbtlStageTransition {
  sequence += 1;
  return {
    id: `transition-${sequence}`,
    cycle_id: "cycle-1",
    seq: sequence,
    from_stage: "design",
    from_attempt: 1,
    stage_attempt_id: `attempt-${sequence}`,
    chosen_route: "approve",
    to_stage: "build",
    assessed_difficulty: null,
    human_override: null,
    offered_routes: null,
    decided_by: "user-1",
    decision_surface_id: null,
    evidence_hash: null,
    dataset_fingerprint: null,
    stage_spec_version: null,
    policy_version: null,
    backfilled: false,
    decided_at: "2026-07-29T00:00:00Z",
    ...overrides,
  };
}

describe("derivePathStrip", () => {
  it("derives the head from the cycle state when there are no transitions", () => {
    expect(derivePathStrip([], "design")).toEqual([
      { stage: "design", attempt: 1, status: "current", backfilled: false },
    ]);
    expect(derivePathStrip([], "ready_for_build")).toEqual([
      { stage: "build", attempt: 1, status: "current", backfilled: false },
    ]);
  });

  it("derives no head from a terminal or non-graph cycle state", () => {
    expect(derivePathStrip([], "completed")).toEqual([]);
    expect(derivePathStrip([], "abandoned")).toEqual([]);
    expect(derivePathStrip([], "reconciliation")).toEqual([]);
  });

  it("walks a linear Design → Build → Test approval path", () => {
    const strip = derivePathStrip(
      [
        transition({
          seq: 1,
          from_stage: "design",
          chosen_route: "approve",
          to_stage: "build",
        }),
        transition({
          seq: 2,
          from_stage: "build",
          chosen_route: "approve",
          to_stage: "test",
        }),
      ],
      "test",
    );
    expect(strip).toEqual([
      { stage: "design", attempt: 1, status: "passed", backfilled: false },
      { stage: "build", attempt: 1, status: "passed", backfilled: false },
      { stage: "test", attempt: 1, status: "current", backfilled: false },
    ]);
  });

  it("marks a request_changes decision as revised and counts the next attempt", () => {
    const strip = derivePathStrip(
      [
        transition({
          seq: 1,
          from_stage: "design",
          from_attempt: 1,
          chosen_route: "request_changes",
          to_stage: "design",
        }),
      ],
      "design",
    );
    expect(strip).toEqual([
      { stage: "design", attempt: 1, status: "revised", backfilled: false },
      { stage: "design", attempt: 2, status: "current", backfilled: false },
    ]);
  });

  it("invalidates earlier passed items when a later transition returns to Design", () => {
    const strip = derivePathStrip(
      [
        transition({
          seq: 1,
          from_stage: "design",
          chosen_route: "approve",
          to_stage: "build",
        }),
        transition({
          seq: 2,
          from_stage: "build",
          chosen_route: "approve",
          to_stage: "test",
        }),
        transition({
          seq: 3,
          from_stage: "test",
          chosen_route: "return_to_design",
          to_stage: "design",
        }),
      ],
      "design",
    );
    expect(strip).toEqual([
      { stage: "design", attempt: 1, status: "invalidated", backfilled: false },
      { stage: "build", attempt: 1, status: "invalidated", backfilled: false },
      { stage: "test", attempt: 1, status: "revised", backfilled: false },
      { stage: "design", attempt: 2, status: "current", backfilled: false },
    ]);
  });

  it("keeps items decided after the return to Design as passed", () => {
    const strip = derivePathStrip(
      [
        transition({
          seq: 1,
          from_stage: "design",
          chosen_route: "approve",
          to_stage: "build",
        }),
        transition({
          seq: 2,
          from_stage: "build",
          chosen_route: "return_to_design",
          to_stage: "design",
        }),
        transition({
          seq: 3,
          from_stage: "design",
          from_attempt: 2,
          chosen_route: "approve",
          to_stage: "build",
        }),
      ],
      "build",
    );
    expect(strip).toEqual([
      { stage: "design", attempt: 1, status: "invalidated", backfilled: false },
      { stage: "build", attempt: 1, status: "revised", backfilled: false },
      { stage: "design", attempt: 2, status: "passed", backfilled: false },
      { stage: "build", attempt: 2, status: "current", backfilled: false },
    ]);
  });

  it("renders close_cycle as a closed item with no current head", () => {
    const strip = derivePathStrip(
      [
        transition({
          seq: 1,
          from_stage: "design",
          chosen_route: "approve",
          to_stage: "build",
        }),
        transition({
          seq: 2,
          from_stage: "build",
          chosen_route: "close_cycle",
          to_stage: "completed",
        }),
      ],
      "completed",
    );
    expect(strip).toEqual([
      { stage: "design", attempt: 1, status: "passed", backfilled: false },
      { stage: "build", attempt: 1, status: "closed", backfilled: false },
    ]);
  });

  it("carries the backfilled flag through and defaults a null attempt to 1", () => {
    const strip = derivePathStrip(
      [
        transition({
          seq: 1,
          from_stage: "design",
          from_attempt: null,
          chosen_route: "approve",
          to_stage: "build",
          backfilled: true,
        }),
      ],
      "build",
    );
    expect(strip).toEqual([
      { stage: "design", attempt: 1, status: "passed", backfilled: true },
      { stage: "build", attempt: 1, status: "current", backfilled: false },
    ]);
  });

  it("numbers rounds from the walk, not the reused durable attempt row", () => {
    // A changes-requested stage is re-submitted on the SAME attempt row, so
    // both decisions carry `from_attempt: 1`. Printing that verbatim labelled
    // two different rounds "Design 1".
    const strip = derivePathStrip(
      [
        transition({
          seq: 1,
          from_stage: "design",
          from_attempt: 1,
          chosen_route: "request_changes",
          to_stage: "design",
          backfilled: true,
        }),
        transition({
          seq: 2,
          from_stage: "design",
          from_attempt: 1,
          chosen_route: "approve",
          to_stage: "build",
        }),
      ],
      "build",
    );
    expect(strip).toEqual([
      { stage: "design", attempt: 1, status: "revised", backfilled: true },
      { stage: "design", attempt: 2, status: "passed", backfilled: false },
      { stage: "build", attempt: 1, status: "current", backfilled: false },
    ]);
  });

  it("orders by seq regardless of array order and does not mutate its input", () => {
    const second = transition({
      seq: 2,
      from_stage: "build",
      chosen_route: "advance_to_learn",
      to_stage: "learn",
    });
    const first = transition({
      seq: 1,
      from_stage: "design",
      chosen_route: "approve",
      to_stage: "build",
    });
    const input = [second, first];
    const strip = derivePathStrip(input, "learn");
    expect(strip).toEqual([
      { stage: "design", attempt: 1, status: "passed", backfilled: false },
      { stage: "build", attempt: 1, status: "passed", backfilled: false },
      { stage: "learn", attempt: 1, status: "current", backfilled: false },
    ]);
    expect(input[0]).toBe(second);
    expect(input[1]).toBe(first);
  });
});
