import { describe, expect, test } from "@rstest/core";

import {
  blockedByReadiness,
  controlAccessibleLabel,
  dbtlControlState,
  dbtlStatusLabel,
  shouldShowReadinessNotice,
} from "@/core/dbtl/controls";
import type { DbtlFeature } from "@/core/features/api";

function feature(overrides: Partial<DbtlFeature> = {}): DbtlFeature {
  return {
    mode: "audit_only",
    mutations_enabled: false,
    graph_execution_enabled: false,
    reason: "DBTL is in audit_only mode.",
    ...overrides,
  };
}

describe("DBTL rail control state", () => {
  test("only graph_enabled makes a control interactive", () => {
    const modes: DbtlFeature["mode"][] = [
      "disabled",
      "audit_only",
      "manual",
      "graph_enabled",
    ];
    const enabled = modes.filter(
      (mode) =>
        dbtlControlState(
          feature({ mode, graph_execution_enabled: mode === "graph_enabled" }),
        ).enabled,
    );
    expect(enabled).toEqual(["graph_enabled"]);
  });

  test("fails closed while readiness is still loading", () => {
    const state = dbtlControlState(
      feature({ mode: "graph_enabled", graph_execution_enabled: true }),
      true,
    );
    expect(state.enabled).toBe(false);
    expect(state.statusLabel).toBe("checking readiness");
    expect(state.reason).not.toBe("");
  });

  test("fails closed when the feature response is missing", () => {
    const state = dbtlControlState(undefined);
    expect(state.enabled).toBe(false);
    expect(state.ariaDisabled).toBe(true);
    expect(state.statusLabel).toBe("disabled");
    expect(state.reason).not.toBe("");
  });

  test("a frozen control is aria-disabled, never natively disabled", () => {
    // Native `disabled` removes the control from the tab order, so the reason
    // becomes undiscoverable. Phase 0 requires the opposite.
    const state = dbtlControlState(feature());
    expect(state.ariaDisabled).toBe(true);
    expect(state.enabled).toBe(false);
  });

  test("surfaces the backend reason verbatim when present", () => {
    const state = dbtlControlState(
      feature({ reason: "Operator froze DBTL for migration." }),
    );
    expect(state.reason).toBe("Operator froze DBTL for migration.");
  });

  test("falls back to a reason when the backend sends a blank one", () => {
    const state = dbtlControlState(feature({ reason: "   " }));
    expect(state.reason.trim().length).toBeGreaterThan(0);
  });

  test("an enabled control carries no frozen reason", () => {
    const state = dbtlControlState(
      feature({ mode: "graph_enabled", graph_execution_enabled: true }),
    );
    expect(state.reason).toBe("");
  });

  test("status label renders modes readably", () => {
    expect(dbtlStatusLabel("audit_only")).toBe("audit only");
    expect(dbtlStatusLabel("graph_enabled")).toBe("graph enabled");
    expect(dbtlStatusLabel("disabled")).toBe("disabled");
  });
});

describe("DBTL control accessibility helpers", () => {
  test("a frozen control announces its action and its reason", () => {
    const state = dbtlControlState(feature({ reason: "Readiness review." }));
    const label = controlAccessibleLabel("Start a new cycle", state);
    expect(label).toContain("Start a new cycle");
    expect(label).toContain("Readiness review.");
  });

  test("an interactive control announces only its action", () => {
    const state = dbtlControlState(
      feature({ mode: "graph_enabled", graph_execution_enabled: true }),
    );
    expect(controlAccessibleLabel("Start a new cycle", state)).toBe(
      "Start a new cycle",
    );
  });

  test("handlers are blocked in every non-executing mode", () => {
    for (const mode of ["disabled", "audit_only", "manual"] as const) {
      expect(blockedByReadiness(dbtlControlState(feature({ mode })))).toBe(true);
    }
    expect(
      blockedByReadiness(
        dbtlControlState(
          feature({ mode: "graph_enabled", graph_execution_enabled: true }),
        ),
      ),
    ).toBe(false);
  });

  test("hides the readiness notice while loading but shows settled frozen state", () => {
    const loading = dbtlControlState(undefined, true);
    expect(shouldShowReadinessNotice(loading, true)).toBe(false);

    const frozen = dbtlControlState(feature());
    expect(shouldShowReadinessNotice(frozen, false)).toBe(true);

    const enabled = dbtlControlState(
      feature({ mode: "graph_enabled", graph_execution_enabled: true }),
    );
    expect(shouldShowReadinessNotice(enabled, false)).toBe(false);
  });
});
