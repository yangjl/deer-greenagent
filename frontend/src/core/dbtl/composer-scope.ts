/**
 * The composer's DBTL scope selector — pure logic.
 *
 * The composer is the only input surface: a request's DBTL scope is chosen
 * beside the attachment and voice controls, and the request the user *types* is
 * what starts, continues, or stays out of a cycle. There is no separate form.
 *
 * Kept pure so the three promises the selector makes to the user are testable
 * without rendering anything:
 *
 * 1. **The selection affects the next request only.** Enforced by
 *    {@link nextContextAfterSend}, which returns ordinary unconditionally. It is
 *    not the user's job to remember to switch back, and it is not a cleanup step
 *    a component can forget.
 * 2. **The label states the request's scope truthfully.** A cycle can be
 *    completed or abandoned in another tab between the click and the send, so
 *    {@link normalizeContext} resolves the selection against the live cycle list
 *    rather than trusting the stored id.
 * 3. **Choosing "start a cycle" records nothing by itself.** It routes the
 *    request into the backend's setup branch, which proposes and asks for
 *    confirmation; only that confirmation writes a durable record.
 *
 * The payload keys here are the backend's runtime-context keys
 * (`deerflow.agents.dbtl.supervisor`). They travel in the run request's
 * `context`, never `config.configurable`, because `configurable` is checkpointed
 * and a per-request selection stored there would keep steering later turns.
 */

import { CYCLE_STATE_LABELS, type CycleRecord, isLive } from "./cycle-view";

/**
 * The council depths the backend accepts, in the order the card offers them.
 * Kept as a literal list rather than derived from the card's own options so a
 * malformed payload cannot smuggle an unknown depth into the run context.
 *
 * Every option the card offers must appear here. An omission is silent — the
 * depth is simply dropped and the backend falls back to its own
 * recommendation, so the person's answer is replaced by one they did not give.
 * `human_input` was missing, which meant declining to consult anyone convened
 * the council anyway.
 */
export const COUNCIL_DEPTHS = [
  "human_input",
  "light",
  "medium",
  "heavy",
] as const;

export type CouncilDepth = (typeof COUNCIL_DEPTHS)[number];

export const SCOPE_ORDINARY_LABEL = "Ordinary project work";
export const SCOPE_RECOMMEND_LABEL = "Ask the AI to recommend";
export const SCOPE_START_CYCLE_LABEL = "Start a new cycle";

/** Explanatory line shown in the menu; the promise the selector is making. */
export const SCOPE_NOTE =
  "Applies to your next request only, and is echoed in the run activity.";

export type RequestContextKind =
  | "ordinary"
  | "cycle"
  | "recommend"
  | "auto"
  | "start_cycle";

export interface RequestContext {
  kind: RequestContextKind;
  cycleId: string | null;
}

export type DbtlExplicitChoice = "ordinary" | "start_cycle" | "continue_cycle";

export const ORDINARY_REQUEST_CONTEXT: RequestContext = Object.freeze({
  kind: "ordinary",
  cycleId: null,
});

export const START_CYCLE_REQUEST_CONTEXT: RequestContext = Object.freeze({
  kind: "start_cycle",
  cycleId: null,
});

/**
 * The resting state: let the assistant judge.
 *
 * This is deliberately *not* `ordinary`. An explicit choice is the first rung
 * of the backend's precedence ladder and the classifier is the last, so
 * sending "ordinary" merely because a control defaulted to it settled every
 * request before the classifier was ever asked — shadow telemetry recorded
 * `route_source=explicit_choice` on every row and measured nothing. A default
 * is the *absence* of a choice, which is exactly the case the ladder reserves
 * for classification.
 */
export const AUTO_REQUEST_CONTEXT: RequestContext = Object.freeze({
  kind: "auto",
  cycleId: null,
});

export interface ScopeMenuOption {
  kind: RequestContextKind;
  cycleId: string | null;
  label: string;
  description: string;
}

/**
 * A prepared replay cannot faithfully restore a DBTL request's one-shot scope.
 * Keep Regenerate/Edit unavailable while governed work is live instead of
 * silently replaying a stage turn as ordinary lead-agent chat.
 */
export function canReplayConversation(cycles: readonly CycleRecord[]): boolean {
  return !cycles.some(isLive);
}

/** Resolve the one server-created discovery cycle after a start-card reply. */
export function findNewDiscoveryCycle(
  cycles: readonly CycleRecord[],
  cycleIdsBeforeSend: ReadonlySet<string>,
  threadId: string,
): CycleRecord | null {
  return (
    cycles.find(
      (cycle) =>
        !cycleIdsBeforeSend.has(cycle.id) &&
        cycle.originating_thread_id === threadId &&
        Boolean(cycle.discovery_package_hash),
    ) ?? null
  );
}

/** Resolve the one server-created legacy-setup cycle after its card reply. */
export function findNewSetupCycle(
  cycles: readonly CycleRecord[],
  cycleIdsBeforeSend: ReadonlySet<string>,
  threadId: string,
): CycleRecord | null {
  return (
    cycles.find(
      (cycle) =>
        !cycleIdsBeforeSend.has(cycle.id) &&
        cycle.originating_thread_id === threadId &&
        !cycle.discovery_package_hash,
    ) ?? null
  );
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
  return isContextStillValid(context, cycles)
    ? context
    : ORDINARY_REQUEST_CONTEXT;
}

export function scopeLabel(
  context: RequestContext,
  cycles: readonly CycleRecord[],
): string {
  if (context.kind === "recommend") return SCOPE_RECOMMEND_LABEL;
  if (context.kind === "start_cycle") return SCOPE_START_CYCLE_LABEL;
  if (context.kind === "cycle") {
    const found = findCycle(context.cycleId, cycles);
    if (found) return cycleShortLabel(found.cycle, found.index);
  }
  return SCOPE_ORDINARY_LABEL;
}

/**
 * The menu: keep it ordinary, continue one visible cycle, start a new one, or
 * ask for a recommendation.
 *
 * Ordinary is first because it is the default and the safe choice. Starting a
 * cycle follows the continuable ones so every cycle-related choice reads as one
 * group. Recommendation is last because it is the only one that hands the
 * decision to the classifier.
 *
 * Terminal cycles are omitted — a completed cycle cannot be continued, and
 * offering it would produce a refusal instead of an action. Numbering is still
 * taken from the full list so the menu and the project rail agree.
 */
export function scopeMenuOptions(
  cycles: readonly CycleRecord[],
): ScopeMenuOption[] {
  const options: ScopeMenuOption[] = [
    {
      kind: "ordinary",
      cycleId: null,
      label: SCOPE_ORDINARY_LABEL,
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
    kind: "start_cycle",
    cycleId: null,
    label: SCOPE_START_CYCLE_LABEL,
    // States what the choice does *not* do. The backend proposes an objective
    // and names the missing fields first; only a confirmation writes a record.
    description:
      "Describe it in your message. Nothing is recorded until you confirm.",
  });

  options.push({
    kind: "recommend",
    cycleId: null,
    label: SCOPE_RECOMMEND_LABEL,
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
  if (context.kind === "auto") {
    // No explicit choice, so the classifier decides. A cycle the human picked
    // in the rail is still named: that click is a deliberate act, and without
    // it continuing a cycle would be unreachable now that the menu is gone.
    return context.cycleId
      ? { ...supervisor, dbtl_selected_cycle_id: context.cycleId }
      : supervisor;
  }
  if (context.kind === "start_cycle") {
    // No cycle id: setup has nothing to continue yet, and sending a stale one
    // would let the backend read this as a continuation.
    return { ...supervisor, dbtl_explicit_choice: "start_cycle" };
  }
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
 * The run context for answering a Human Input Card.
 *
 * Answering a card is a reply, not a scope choice — but a scope still has to be
 * sent, so the fallback decides where the reply lands. Defaulting to ordinary
 * strands it: the user's answer reaches the lead agent instead of the branch
 * that asked the question. Each clarification therefore names the scope that
 * continues it, keyed off the request's own `clarification_type`.
 */
export function humanInputRunContext(
  request: { source: string; clarification_type?: string },
  selectedCycleId: string | null,
  answer?: string,
): Record<string, string | boolean> {
  if (request.source === "ask_clarification") {
    // The preflight answer is the council's depth, and it has to travel with
    // the request that convenes it: the backend reads the depth from this same
    // per-request context, and a reply that carried only the cycle scope would
    // re-raise the card it just answered.
    if (request.clarification_type === "council_preflight") {
      const depth = COUNCIL_DEPTHS.find((item) => item === answer?.trim());
      return {
        ...(selectedCycleId
          ? runContextPayload({ kind: "cycle", cycleId: selectedCycleId })
          : runContextPayload(AUTO_REQUEST_CONTEXT)),
        // An unrecognized answer is left off rather than guessed at; the
        // backend then falls back to its own recommendation.
        ...(depth ? { dbtl_council_depth: depth } : {}),
      };
    }
    // A design question belongs to the cycle whose council raised it, and so
    // does a design the owner wrote in place of one. Notably the authoring
    // reply carries no depth: the server put it on the card it emitted and
    // reads it back from there, so a client that has forgotten the choice
    // cannot accidentally convene the council the owner declined.
    // A roster adjustment belongs to the cycle whose preflight raised it, and
    // like the authoring reply it carries no depth: the person has not chosen
    // one yet, and the redrawn roster is shown again before they do.
    if (
      request.clarification_type === "design_decision" ||
      request.clarification_type === "design_authoring" ||
      request.clarification_type === "council_adjustment"
    ) {
      return selectedCycleId
        ? runContextPayload({ kind: "cycle", cycleId: selectedCycleId })
        : runContextPayload(ORDINARY_REQUEST_CONTEXT);
    }
    // Start/Hold answers the question "does this cycle's next stage begin now",
    // so it belongs to that cycle. The card carries its own cycle id and the
    // backend recovers it, but sending the scope keeps the two agreeing and
    // means a Start does not depend on recovery to route correctly.
    if (
      request.clarification_type === "dbtl_stage_handoff" ||
      request.clarification_type === "dbtl_build_control"
    ) {
      return selectedCycleId
        ? runContextPayload({ kind: "cycle", cycleId: selectedCycleId })
        : runContextPayload(AUTO_REQUEST_CONTEXT);
    }
    // Setup is not yet a cycle, so there is nothing to continue — the answer
    // returns to the setup branch that asked for the missing fields.
    if (
      request.clarification_type === "cycle_setup" ||
      request.clarification_type === "cycle_setup_confirmation" ||
      request.clarification_type === "dbtl_discovery_start"
    ) {
      return runContextPayload(START_CYCLE_REQUEST_CONTEXT);
    }
    // A generic clarification may have been raised just before the lead agent
    // learned that the work is DBTL-shaped. Preserve the supervisor decision
    // instead of turning the absence of a subtype into an explicit opt-out.
    return runContextPayload(AUTO_REQUEST_CONTEXT);
  }
  return runContextPayload(ORDINARY_REQUEST_CONTEXT);
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
  if (context.kind === "auto") {
    // Must mirror `runContextPayload`'s auto branch exactly: no explicit
    // choice, but still name a rail-selected cycle. Omitting this branch let
    // `auto` fall through to "ordinary" below, so the graph consulted the
    // classifier while the card was withheld and every telemetry row recorded
    // an explicit choice that nobody made.
    return { selectedCycleId: context.cycleId, explicitChoice: null };
  }
  if (context.kind === "start_cycle") {
    return { selectedCycleId: null, explicitChoice: "start_cycle" };
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
 * user believes they have left. It matters most for `start_cycle`: setup
 * continues through the assistant's own follow-up questions, so re-entering the
 * setup branch on every subsequent message would trap the conversation.
 */
export function nextContextAfterSend(_sent: RequestContext): RequestContext {
  return AUTO_REQUEST_CONTEXT;
}
