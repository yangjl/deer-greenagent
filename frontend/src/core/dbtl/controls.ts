import type { DbtlFeature } from "@/core/features/api";

/**
 * Derived state for every DBTL workflow control in the project rail.
 *
 * Phase 0 keeps the cycle projection visible but fails closed: a control is
 * interactive only when the backend explicitly reports
 * `graph_execution_enabled`. The reason travels with the state because a
 * frozen control has to explain itself — see `ariaDisabled` below.
 */
export interface DbtlControlState {
  /** True only when the backend explicitly enabled graph execution. */
  enabled: boolean;
  /**
   * Rendered with `aria-disabled` rather than the native `disabled`
   * attribute. A natively disabled button leaves the tab order, so a keyboard
   * or screen-reader user cannot reach it to find out why it is frozen —
   * which the Phase 0 accessibility requirement forbids. Handlers must call
   * `blockedByReadiness` before mutating.
   */
  ariaDisabled: boolean;
  /** Short status shown next to the section label. */
  statusLabel: string;
  /** Why the control is frozen; empty when the control is interactive. */
  reason: string;
}

const CHECKING_LABEL = "checking readiness";
const CHECKING_REASON =
  "Checking DBTL readiness. Workflow controls stay disabled until the check completes.";
const DEFAULT_FROZEN_REASON =
  "DBTL workflow controls are disabled during readiness review.";

/** Human-readable status for a mode, e.g. `audit_only` -> `audit only`. */
export function dbtlStatusLabel(mode: DbtlFeature["mode"]): string {
  return mode.replace(/_/g, " ");
}

/**
 * Fold the feature response into the state every rail control renders from.
 *
 * Unknown/absent feature data is treated as frozen, so a failed or pending
 * `/api/features` call can never present a live workflow control.
 */
export function dbtlControlState(
  feature: DbtlFeature | undefined,
  isLoading = false,
): DbtlControlState {
  if (isLoading) {
    return {
      enabled: false,
      ariaDisabled: true,
      statusLabel: CHECKING_LABEL,
      reason: CHECKING_REASON,
    };
  }
  if (!feature) {
    return {
      enabled: false,
      ariaDisabled: true,
      statusLabel: "disabled",
      reason: DEFAULT_FROZEN_REASON,
    };
  }
  if (feature.graph_execution_enabled) {
    return {
      enabled: true,
      ariaDisabled: false,
      statusLabel: dbtlStatusLabel(feature.mode),
      reason: "",
    };
  }
  return {
    enabled: false,
    ariaDisabled: true,
    statusLabel: dbtlStatusLabel(feature.mode),
    reason: feature.reason?.trim() ? feature.reason : DEFAULT_FROZEN_REASON,
  };
}

/**
 * Accessible name for a frozen control: the action plus why it is unavailable,
 * so the reason is announced even though no tooltip is.
 */
export function controlAccessibleLabel(
  action: string,
  state: DbtlControlState,
): string {
  return state.enabled ? action : `${action} (unavailable: ${state.reason})`;
}

/** Guard for click/submit handlers so a focusable frozen control is inert. */
export function blockedByReadiness(state: DbtlControlState): boolean {
  return !state.enabled;
}
