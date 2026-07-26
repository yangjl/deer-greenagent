/**
 * The composer's request-context chip (Phase 5).
 *
 *     [ Ordinary project work ▾ ]   or   [ Cycle 01 · Design ▾ ]
 *
 * Pure, so the two claims the chip makes to the user are testable without
 * rendering anything:
 *
 * 1. **The selection affects the next request only.** Enforced by
 *    {@link nextContextAfterSend}, which returns ordinary unconditionally. It is
 *    not the user's job to remember to switch back, and it is not a cleanup step
 *    a component can forget.
 * 2. **The chip states the request's scope truthfully.** A cycle can be
 *    completed or abandoned in another tab between the click and the send, so
 *    {@link normalizeContext} resolves the selection against the live cycle list
 *    rather than trusting the stored id.
 *
 * The payload keys here are the backend's runtime-context keys
 * (`deerflow.agents.dbtl.supervisor`). They travel in the run request's
 * `context`, never `config.configurable`, because `configurable` is checkpointed
 * and a per-request selection stored there would keep steering later turns.
 */

import {
  CYCLE_STATE_LABELS,
  type CycleRecord,
  isLive,
} from "./cycle-view";

export const CHIP_ORDINARY_LABEL = "Ordinary project work";
export const CHIP_RECOMMEND_LABEL = "Ask the AI to recommend";

/** Explanatory line shown in the menu; the promise the chip is making. */
export const CHIP_SCOPE_NOTE =
  "Applies to your next request only, and is echoed in the run activity.";

export type RequestContextKind = "ordinary" | "cycle" | "recommend";

export interface RequestContext {
  kind: RequestContextKind;
  cycleId: string | null;
}

export type DbtlExplicitChoice =
  | "ordinary"
  | "start_cycle"
  | "continue_cycle";

export const ORDINARY_REQUEST_CONTEXT: RequestContext = Object.freeze({
  kind: "ordinary",
  cycleId: null,
});

export interface ContextMenuOption {
  kind: RequestContextKind;
  cycleId: string | null;
  label: string;
  description: string;
}

/**
 * "Cycle 03 · Data reconciliation".
 *
 * The number comes from the cycle's position in the project's ordered list so
 * it matches the project rail. Zero-padded to two digits for scannability, and
 * not truncated beyond that.
 */
export function cycleShortLabel(cycle: CycleRecord, index: number): string {
  const ordinal = String(index + 1).padStart(2, "0");
  return `Cycle ${ordinal} · ${CYCLE_STATE_LABELS[cycle.state]}`;
}

function findCycle(
  cycleId: string | null,
  cycles: readonly CycleRecord[],
): { cycle: CycleRecord; index: number } | null {
  const index = cycles.findIndex((candidate) => candidate.id === cycleId);
  if (index < 0) return null;
  const cycle = cycles[index];
  if (!cycle || !isLive(cycle)) return null;
  return { cycle, index };
}

/** Whether a stored selection still describes something the user can continue. */
export function isContextStillValid(
  context: RequestContext,
  cycles: readonly CycleRecord[],
): boolean {
  if (context.kind !== "cycle") return true;
  return findCycle(context.cycleId, cycles) !== null;
}

/** Resolve a selection against the live cycle list, degrading to ordinary. */
export function normalizeContext(
  context: RequestContext,
  cycles: readonly CycleRecord[],
): RequestContext {
  return isContextStillValid(context, cycles) ? context : ORDINARY_REQUEST_CONTEXT;
}

export function chipLabel(
  context: RequestContext,
  cycles: readonly CycleRecord[],
): string {
  if (context.kind === "recommend") return CHIP_RECOMMEND_LABEL;
  if (context.kind === "cycle") {
    const found = findCycle(context.cycleId, cycles);
    if (found) return cycleShortLabel(found.cycle, found.index);
  }
  return CHIP_ORDINARY_LABEL;
}

/**
 * The menu: keep it ordinary, continue one visible cycle, or ask for a
 * recommendation. Ordinary is first because it is the default and the safe
 * choice; recommendation is last because it is the only one that hands the
 * decision to the classifier.
 *
 * Terminal cycles are omitted — a completed cycle cannot be continued, and
 * offering it would produce a refusal instead of an action. Numbering is still
 * taken from the full list so the chip and the project rail agree.
 */
export function contextMenuOptions(
  cycles: readonly CycleRecord[],
): ContextMenuOption[] {
  const options: ContextMenuOption[] = [
    {
      kind: "ordinary",
      cycleId: null,
      label: CHIP_ORDINARY_LABEL,
      description: "Nothing is recorded against a cycle.",
    },
  ];

  cycles.forEach((cycle, index) => {
    if (!isLive(cycle)) return;
    options.push({
      kind: "cycle",
      cycleId: cycle.id,
      label: cycleShortLabel(cycle, index),
      description: cycle.title,
    });
  });

  options.push({
    kind: "recommend",
    cycleId: null,
    label: CHIP_RECOMMEND_LABEL,
    description: "Let the assistant suggest whether this belongs in a cycle.",
  });

  return options;
}

/**
 * The run request's `context` fields for this selection.
 *
 * Recommendation sends the one-run supervisor flag but deliberately omits an
 * explicit routing choice. That absence is the one case where the backend's
 * precedence ladder consults the classifier.
 */
export function runContextPayload(
  context: RequestContext,
): Record<string, string | boolean> {
  // Selects the supervisor for this run without changing the thread's durable
  // assistant_id. That keeps checkpoint/state access compatible with rollback.
  const supervisor = { dbtl_supervisor_enabled: true };
  if (context.kind === "recommend") return supervisor;
  if (context.kind === "cycle" && context.cycleId) {
    return {
      ...supervisor,
      dbtl_explicit_choice: "continue_cycle",
      dbtl_selected_cycle_id: context.cycleId,
    };
  }
  // Includes a "cycle" selection with no id: never claim a continuation
  // without naming what is being continued.
  return { ...supervisor, dbtl_explicit_choice: "ordinary" };
}

/**
 * Keep Phase 4's visible proposal and Phase 5's graph route on the same
 * precedence inputs. A disagreement here would let the transcript say
 * "ordinary" while a proposal card recommends starting a cycle (or vice
 * versa).
 */
export function proposalContextPayload(context: RequestContext): {
  selectedCycleId: string | null;
  explicitChoice: DbtlExplicitChoice | null;
} {
  if (context.kind === "recommend") {
    return { selectedCycleId: null, explicitChoice: null };
  }
  if (context.kind === "cycle" && context.cycleId) {
    return {
      selectedCycleId: context.cycleId,
      explicitChoice: "continue_cycle",
    };
  }
  return { selectedCycleId: null, explicitChoice: "ordinary" };
}

/** Metadata persisted on the run so its one-shot scope is auditable. */
export function runActivityMetadata(
  context: RequestContext,
): Record<string, unknown> {
  return {
    dbtl_request_context: {
      kind: context.kind,
      cycle_id: context.kind === "cycle" ? context.cycleId : null,
    },
  };
}

/**
 * The context for the request *after* this one.
 *
 * Always ordinary. This is the mechanism behind "affects the next request
 * only" — a sticky selection would silently scope later turns to a cycle the
 * user believes they have left.
 */
export function nextContextAfterSend(_sent: RequestContext): RequestContext {
  return ORDINARY_REQUEST_CONTEXT;
}
