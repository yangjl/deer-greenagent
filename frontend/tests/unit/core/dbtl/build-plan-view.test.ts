import { describe, expect, it } from "@rstest/core";

import {
  type BuildWorkflowView,
  blockerBadge,
  buildPlanProjection,
  movingRow,
  phaseAccessibleName,
} from "@/core/dbtl/build-plan-view";

function view(overrides: Partial<BuildWorkflowView> = {}): BuildWorkflowView {
  return {
    workflow_spec_key: "generic:build-workflow:v1",
    stage_attempt_id: "sa-1",
    staleness_checked: true,
    plan: {
      feasibility: "planned",
      degraded: false,
      phase_count: 3,
      phases: [
        { index: 1, phase_key: "simulate", title: "Simulate founder population" },
        { index: 2, phase_key: "markers", title: "Derive marker matrix" },
        { index: 3, phase_key: "fit", title: "Fit and evaluate model" },
      ],
    },
    steps: [],
    phases: [],
    next_step: null,
    is_complete: false,
    ...overrides,
  };
}

function phase(
  key: string,
  status: string,
  extra: Record<string, unknown> = {},
) {
  return {
    phase_key: key,
    phase_index: 1,
    status,
    capability: "software_and_workflow_engineering",
    agent_name: "builder",
    via_generalist: false,
    ...extra,
  };
}

describe("the rail names every planned phase, not only the ones that ran", () => {
  it("shows a phase that has not started yet", () => {
    const projection = buildPlanProjection(
      view({ phases: [phase("simulate", "succeeded")] }),
    );

    expect(projection.rows.map((row) => row.title)).toEqual([
      "Simulate founder population",
      "Derive marker matrix",
      "Fit and evaluate model",
    ]);
    expect(projection.rows[2]!.stateLabel).toBe("Queued");
  });

  it("counts progress against the whole plan", () => {
    const projection = buildPlanProjection(
      view({
        phases: [phase("simulate", "succeeded"), phase("markers", "running")],
      }),
    );

    expect(projection.progress).toBe("1 of 3");
  });

  it("prefers the recorded row's own title over the plan's", () => {
    const projection = buildPlanProjection(
      view({
        phases: [
          phase("simulate", "succeeded", {
            execution: { title: "Simulate founders (revised)" },
          }),
        ],
      }),
    );

    expect(projection.rows[0]!.title).toBe("Simulate founders (revised)");
  });

  it("reports the newest attempt of a retried phase", () => {
    const projection = buildPlanProjection(
      view({
        phases: [phase("simulate", "failed"), phase("simulate", "succeeded")],
      }),
    );

    expect(projection.rows[0]!.stateLabel).toBe("Done");
  });

  it("keeps a phase the plan no longer names, because it really ran", () => {
    const projection = buildPlanProjection(
      view({ phases: [phase("legacy", "succeeded")] }),
    );

    expect(projection.rows.map((row) => row.phaseKey)).toContain("legacy");
  });

  it("stays inside the rail's bound", () => {
    const projection = buildPlanProjection(
      view({
        plan: {
          feasibility: "planned",
          degraded: false,
          phase_count: 12,
          phases: Array.from({ length: 12 }, (_, index) => ({
            index: index + 1,
            phase_key: `p${index}`,
            title: `Phase ${index}`,
          })),
        },
      }),
    );

    expect(projection.rows).toHaveLength(8);
  });
});

describe("a build with nothing to show says so", () => {
  it("renders one muted line rather than an empty heading", () => {
    const projection = buildPlanProjection(view({ plan: null }));

    expect(projection.rows).toEqual([]);
    expect(projection.emptyNote).toBe("No build has started.");
  });

  it("says the same for a missing view", () => {
    expect(buildPlanProjection(undefined).emptyNote).toBe(
      "No build has started.",
    );
  });
});

describe("exactly one thing moves", () => {
  it("names the single running phase", () => {
    const projection = buildPlanProjection(
      view({ phases: [phase("markers", "running")] }),
    );

    expect(movingRow(projection)).toBe("markers");
  });

  it("names nothing when two phases claim to be running", () => {
    const projection = buildPlanProjection(
      view({
        phases: [phase("simulate", "running"), phase("markers", "running")],
      }),
    );

    expect(movingRow(projection)).toBeNull();
  });

  it("names nothing when the build is finished", () => {
    const projection = buildPlanProjection(
      view({ phases: [phase("simulate", "succeeded")] }),
    );

    expect(movingRow(projection)).toBeNull();
  });
});

describe("state is in words, and stopping is called out", () => {
  it("gives every status a word", () => {
    for (const status of [
      "queued",
      "waiting",
      "running",
      "succeeded",
      "needs_input",
      "failed",
      "invalidated",
      "cancelled",
    ]) {
      const projection = buildPlanProjection(
        view({ phases: [phase("simulate", status)] }),
      );
      expect(projection.rows[0]!.stateLabel).not.toBe("");
    }
  });

  it("says when a phase is waiting for the reader", () => {
    const projection = buildPlanProjection(
      view({ phases: [phase("markers", "needs_input")] }),
    );

    expect(projection.attention).toBe("Derive marker matrix is waiting for you.");
  });

  it("says when a phase stopped", () => {
    const projection = buildPlanProjection(
      view({ phases: [phase("markers", "failed")] }),
    );

    expect(projection.attention).toBe("Derive marker matrix stopped.");
  });

  it("says nothing when the plan is simply in progress", () => {
    const projection = buildPlanProjection(
      view({ phases: [phase("markers", "running")] }),
    );

    expect(projection.attention).toBe("");
  });
});

describe("capability rides in the accessible name, not a third column", () => {
  it("names the phase, its state, and what it asked for", () => {
    const projection = buildPlanProjection(
      view({ phases: [phase("simulate", "running")] }),
    );

    expect(phaseAccessibleName(projection.rows[0]!)).toBe(
      "Phase 1: Simulate founder population — Running — software_and_workflow_engineering",
    );
  });

  it("says when a generalist stood in", () => {
    const projection = buildPlanProjection(
      view({
        phases: [phase("simulate", "running", { via_generalist: true })],
      }),
    );

    expect(phaseAccessibleName(projection.rows[0]!)).toContain(
      "(general-purpose stand-in)",
    );
  });
});

describe("open work items keep their signal", () => {
  it("renders a count as words", () => {
    expect(blockerBadge(2)).toBe("2 open");
  });

  it("renders nothing when there is nothing open", () => {
    expect(blockerBadge(0)).toBe("");
  });
});


describe("a malformed status cannot unmount the rail", () => {
  it("treats a prototype member as an unknown status", () => {
    const projection = buildPlanProjection(
      view({ phases: [phase("simulate", "constructor")] }),
    );

    // `in` would accept this and every lookup keyed on it would resolve to a
    // function, which React renders as an invalid element type.
    expect(projection.rows[0]!.status).toBe("queued");
    expect(projection.rows[0]!.stateLabel).toBe("Queued");
  });

  it("treats an unrecognized status as queued rather than passing it through", () => {
    const projection = buildPlanProjection(
      view({ phases: [phase("simulate", "exploded")] }),
    );

    expect(projection.rows[0]!.stateLabel).toBe("Queued");
  });

  it("falls back to the phase key when the recorded title is empty", () => {
    const projection = buildPlanProjection(
      view({
        plan: {
          feasibility: "planned",
          degraded: false,
          phase_count: 1,
          phases: [{ index: 1, phase_key: "simulate", title: "   " }],
        },
      }),
    );

    expect(projection.rows[0]!.title).toBe("simulate");
  });
});
