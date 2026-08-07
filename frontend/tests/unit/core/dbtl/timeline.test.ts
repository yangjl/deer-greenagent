/**
 * Phase 4: the full non-linear timeline that replaces the Phase 0 path strip.
 *
 * The strip could say a cycle looped. It could not say *why* any edge was
 * taken, what the agent assessed it as, whether a person overrode that, what
 * else they were offered, or which document their decision bound. All of that
 * is already on the durable transition row — the strip simply dropped it.
 *
 * Two rules here are easy to get wrong and expensive when wrong. Park is a
 * routing choice, not the conclusion of an attempt, so it must be *visible*
 * without inventing a stage round nobody worked. And a backward edge into
 * Design invalidates the approvals recorded forward of it, because they were
 * granted against a design that has since been reopened.
 */

import { describe, expect, it } from "@rstest/core";

import type { DbtlStageTransition } from "@/core/dbtl/cycle-view";
import { deriveCycleTimeline } from "@/core/dbtl/timeline";

let seq = 0;

function transition(
  overrides: Partial<DbtlStageTransition> & {
    from_stage: DbtlStageTransition["from_stage"];
    to_stage: string;
    chosen_route: string;
  },
): DbtlStageTransition {
  seq += 1;
  return {
    id: `dst-${seq}`,
    cycle_id: "cycle-1",
    seq,
    from_attempt: 1,
    stage_attempt_id: `attempt-${overrides.from_stage}`,
    assessed_difficulty: null,
    assessment_rationale: null,
    human_override: null,
    offered_routes: null,
    decided_by: "user-1",
    decision_surface_id: null,
    decided_in_thread_id: null,
    evidence_hash: null,
    dataset_fingerprint: null,
    stage_spec_version: null,
    policy_version: null,
    backfilled: false,
    decided_at: "2026-07-31T00:00:00Z",
    ...overrides,
  };
}

function walk(
  ...routes: Array<[string, string, string]>
): DbtlStageTransition[] {
  seq = 0;
  return routes.map(([from, to, route]) =>
    transition({
      from_stage: from as DbtlStageTransition["from_stage"],
      to_stage: to,
      chosen_route: route,
    }),
  );
}

describe("a loop reads as one cycle", () => {
  it("keeps exception advancement visibly distinct from a pass", () => {
    const timeline = deriveCycleTimeline(
      walk(["build", "test", "advanced_with_exception"]),
      "test",
    );

    expect(timeline.entries[0]?.status).toBe("exception");
    expect(timeline.entries[0]?.chosenRoute).toBe("advanced_with_exception");
  });

  it("keeps every attempt of a repeated stage as its own numbered entry", () => {
    const timeline = deriveCycleTimeline(
      walk(
        ["design", "build", "approve"],
        ["build", "test", "approve"],
        ["test", "build", "return_to_build"],
        ["build", "test", "approve"],
        ["test", "learn", "advance_to_learn"],
      ),
      "learn",
    );

    expect(
      timeline.entries.map((entry) => `${entry.stage}${entry.round}`),
    ).toEqual(["design1", "build1", "test1", "build2", "test2", "learn1"]);
  });

  it("counts rounds from the walk, not the durable attempt number", () => {
    /* A changes-requested stage is re-submitted on the same attempt row, so
     * `attempt_number` stays at 1 and two distinct rounds would print as one. */
    const transitions = walk(
      ["design", "design", "request_changes"],
      ["design", "build", "approve"],
    );

    const timeline = deriveCycleTimeline(transitions, "build");

    expect(timeline.entries.map((entry) => entry.round)).toEqual([1, 2, 1]);
  });

  it("marks the head the cycle is currently working", () => {
    const timeline = deriveCycleTimeline(
      walk(["design", "build", "approve"]),
      "build",
    );

    const head = timeline.entries.at(-1);
    expect(head?.stage).toBe("build");
    expect(head?.status).toBe("current");
  });

  it("adds no head once the cycle is over", () => {
    const timeline = deriveCycleTimeline(
      walk(["learn", "completed", "close_cycle"]),
      "completed",
    );

    expect(timeline.entries.every((entry) => entry.status !== "current")).toBe(
      true,
    );
  });
});

describe("reopening Design invalidates what was approved after it", () => {
  it("marks earlier passed entries invalidated", () => {
    const timeline = deriveCycleTimeline(
      walk(
        ["design", "build", "approve"],
        ["build", "test", "approve"],
        ["test", "design", "return_to_design"],
      ),
      "design",
    );

    const statuses = timeline.entries
      .filter((entry) => entry.status !== "current")
      .map((entry) => entry.status);
    expect(statuses).toEqual(["invalidated", "invalidated", "revised"]);
  });

  it("reports that something on the path is invalidated", () => {
    const timeline = deriveCycleTimeline(
      walk(
        ["design", "build", "approve"],
        ["build", "design", "return_to_design"],
      ),
      "design",
    );

    expect(timeline.hasInvalidatedWork).toBe(true);
  });

  it("says nothing is invalidated on a straight walk", () => {
    const timeline = deriveCycleTimeline(
      walk(["design", "build", "approve"]),
      "build",
    );

    expect(timeline.hasInvalidatedWork).toBe(false);
  });
});

describe("park is visible without inventing an attempt", () => {
  it("does not become a stage round of its own", () => {
    /* Nobody worked a Design round by parking one. Rendering park as an
     * attempt is how the old strip would have shown a phantom revised round. */
    const timeline = deriveCycleTimeline(
      walk(["design", "design", "park"]),
      "design",
    );

    expect(
      timeline.entries.filter((entry) => entry.status !== "current"),
    ).toEqual([]);
  });

  it("is still reported, because a parked cycle looks idle otherwise", () => {
    const timeline = deriveCycleTimeline(
      walk(["design", "design", "park"]),
      "design",
    );

    expect(timeline.parked).toBe(true);
    expect(timeline.parkedStage).toBe("design");
  });

  it("stops being parked once a later decision is recorded", () => {
    const timeline = deriveCycleTimeline(
      walk(["design", "design", "park"], ["design", "build", "approve"]),
      "build",
    );

    expect(timeline.parked).toBe(false);
  });
});

describe("every human-decided edge carries its audit detail", () => {
  it("keeps the assessment, the override, and what else was offered", () => {
    seq = 0;
    const timeline = deriveCycleTimeline(
      [
        transition({
          from_stage: "design",
          to_stage: "build",
          chosen_route: "approve",
          assessed_difficulty: "routine",
          assessment_rationale: "A parameter sweep on a settled design.",
          human_override: "high_stakes",
          offered_routes: ["approve", "request_changes", "park"],
        }),
      ],
      "build",
    );

    const entry = timeline.entries[0]!;
    expect(entry.assessedDifficulty).toBe("routine");
    expect(entry.assessmentRationale).toBe(
      "A parameter sweep on a settled design.",
    );
    expect(entry.humanOverride).toBe("high_stakes");
    expect(entry.offeredRoutes).toEqual(["approve", "request_changes", "park"]);
  });

  it("reports an override only when it disagrees with the assessment", () => {
    /* Recording the same value is not a reviewer disputing the agent, and
     * labelling it "overridden" would inflate the one telemetry number Phase 4
     * gates the default-on decision behind. */
    seq = 0;
    const timeline = deriveCycleTimeline(
      [
        transition({
          from_stage: "design",
          to_stage: "build",
          chosen_route: "approve",
          assessed_difficulty: "standard",
          human_override: "standard",
        }),
      ],
      "build",
    );

    expect(timeline.entries[0]!.overrodeAssessment).toBe(false);
  });

  it("flags a genuine disagreement", () => {
    seq = 0;
    const timeline = deriveCycleTimeline(
      [
        transition({
          from_stage: "design",
          to_stage: "build",
          chosen_route: "approve",
          assessed_difficulty: "routine",
          human_override: "high_stakes",
        }),
      ],
      "build",
    );

    expect(timeline.entries[0]!.overrodeAssessment).toBe(true);
  });

  it("links the deck and the evidence the decision bound", () => {
    seq = 0;
    const timeline = deriveCycleTimeline(
      [
        transition({
          from_stage: "design",
          to_stage: "build",
          chosen_route: "approve",
          decision_surface_id: "dfs-abc",
          decided_in_thread_id: "thread-9",
          evidence_hash: "f".repeat(64),
        }),
      ],
      "build",
    );

    expect(timeline.entries[0]!.decisionSurfaceId).toBe("dfs-abc");
    expect(timeline.entries[0]!.decidedInThreadId).toBe("thread-9");
    expect(timeline.entries[0]!.evidenceHash).toBe("f".repeat(64));
  });

  it("keeps a backfilled edge distinguishable from one a person took", () => {
    /* Migration 0024 synthesized rows for decisions taken before the graph
     * existed. They are real history, but nobody chose a route on a deck, so an
     * audit view must not present them as if somebody had. */
    seq = 0;
    const timeline = deriveCycleTimeline(
      [
        transition({
          from_stage: "design",
          to_stage: "build",
          chosen_route: "approve",
          backfilled: true,
        }),
      ],
      "build",
    );

    expect(timeline.entries[0]!.backfilled).toBe(true);
  });

  it("carries the record id so a person can cite one edge", () => {
    seq = 0;
    const timeline = deriveCycleTimeline(
      [
        transition({
          from_stage: "design",
          to_stage: "build",
          chosen_route: "approve",
        }),
      ],
      "build",
    );

    expect(timeline.entries[0]!.recordId).toBe("dst-1");
  });

  it("gives the current head no record id, because nothing decided it yet", () => {
    const timeline = deriveCycleTimeline(
      walk(["design", "build", "approve"]),
      "build",
    );

    expect(timeline.entries.at(-1)!.recordId).toBeNull();
  });
});

describe("it is deterministic and order-independent", () => {
  it("sorts by seq rather than trusting arrival order", () => {
    seq = 0;
    const ordered = walk(
      ["design", "build", "approve"],
      ["build", "test", "approve"],
    );

    const forward = deriveCycleTimeline(ordered, "test");
    const shuffled = deriveCycleTimeline([...ordered].reverse(), "test");

    expect(shuffled).toEqual(forward);
  });

  it("does not mutate the transitions it is given", () => {
    seq = 0;
    const transitions = walk(
      ["build", "test", "approve"],
      ["design", "build", "approve"],
    );
    const before = transitions.map((item) => item.id);

    deriveCycleTimeline(transitions, "test");

    expect(transitions.map((item) => item.id)).toEqual(before);
  });

  it("shows the stage a cycle sits at before any decision exists", () => {
    const timeline = deriveCycleTimeline([], "design");

    expect(timeline.entries).toHaveLength(1);
    expect(timeline.entries[0]!.status).toBe("current");
    expect(timeline.entries[0]!.stage).toBe("design");
  });
});
