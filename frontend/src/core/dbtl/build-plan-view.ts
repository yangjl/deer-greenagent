/**
 * The Build plan as the project rail renders it.
 *
 * Pure and React-free, because the rail's constraints are arithmetic and the
 * rules below are worth testing without a DOM: at ~224px of content width one
 * wrapped title changes the rail's height as work moves, and a rail whose
 * height moves is the flicker two features are already trying to avoid.
 *
 * **One source, two projections.** The transcript's workflow block and this rail
 * read the same server view. A rail that derived its own notion of progress
 * would eventually disagree with the transcript, and the person would have no
 * way to tell which was right — so nothing here recomputes a status. It only
 * decides how to *say* one.
 */

/** Bounded by the backend's own `MAX_BUILD_PHASES`. */
export const MAX_RAIL_PHASES = 8;

export type BuildStepStatus =
  | "queued"
  | "waiting"
  | "running"
  | "succeeded"
  | "needs_input"
  | "failed"
  | "invalidated"
  | "cancelled";

export interface BuildWorkflowPhaseRow {
  phase_key: string | null;
  phase_index: number | null;
  status: string;
  capability: string | null;
  agent_name: string | null;
  via_generalist: boolean;
  execution?: Record<string, unknown>;
}

export interface BuildWorkflowPlan {
  feasibility: string | null;
  degraded: boolean;
  phase_count: number;
  phases: Array<{ index: number; phase_key: string; title: string }>;
}

export interface BuildWorkflowView {
  cycle_id?: string;
  stage?: string;
  workflow_spec_key: string;
  stage_attempt_id: string;
  staleness_checked: boolean;
  plan: BuildWorkflowPlan | null;
  steps: Array<{ key: string; label: string; status: string }>;
  phases: BuildWorkflowPhaseRow[];
  next_step: string | null;
  is_complete: boolean;
}

/**
 * The state word, never colour alone.
 *
 * `waiting` is deliberately "Queued" rather than a fourth word: to a reader of
 * a four-line list, "not started" and "blocked behind something not started"
 * are the same fact, and spending a distinct word on the difference buys
 * nothing at this width.
 */
export const BUILD_PHASE_STATE_LABELS: Record<string, string> = {
  queued: "Queued",
  waiting: "Queued",
  running: "Running",
  succeeded: "Done",
  needs_input: "Waiting for you",
  failed: "Failed",
  invalidated: "Superseded",
  cancelled: "Cancelled",
};

export interface BuildPlanRow {
  phaseKey: string;
  index: number;
  title: string;
  status: BuildStepStatus;
  stateLabel: string;
  capability: string | null;
  /** Whether a generalist stood in for a capability nobody declared. */
  viaGeneralist: boolean;
  /** Only the running phase moves; see `movingRow`. */
  running: boolean;
}

export interface BuildPlanProjection {
  rows: BuildPlanRow[];
  /** `2 of 4`, or empty when there is no plan to count against. */
  progress: string;
  /** One muted line when there is no plan yet, rather than an empty heading. */
  emptyNote: string;
  /** Present when the Build stopped somewhere a person should look. */
  attention: string;
}

function normalizeStatus(value: string): BuildStepStatus {
  return (value in BUILD_PHASE_STATE_LABELS
    ? value
    : "queued") as BuildStepStatus;
}

function titleOf(
  row: BuildWorkflowPhaseRow,
  planned: Map<string, string>,
): string {
  const recorded = row.execution?.title;
  if (typeof recorded === "string" && recorded.trim()) return recorded.trim();
  const key = row.phase_key ?? "";
  return planned.get(key) ?? key ?? "Untitled phase";
}

/**
 * Fold the server's plan and its phase rows into the rows the rail renders.
 *
 * The plan supplies phases that have not started — a rail that could only show
 * rows that exist would say a four-phase Build has one phase until the fourth
 * finished. Recorded rows win over the plan wherever both describe the same
 * phase, since a row is what actually happened.
 */
export function buildPlanProjection(
  view: BuildWorkflowView | null | undefined,
): BuildPlanProjection {
  if (!view) {
    return { rows: [], progress: "", emptyNote: "No build has started.", attention: "" };
  }

  const planned = new Map(
    (view.plan?.phases ?? []).map((phase) => [phase.phase_key, phase.title]),
  );
  const order = new Map(
    (view.plan?.phases ?? []).map((phase) => [phase.phase_key, phase.index]),
  );

  // Newest attempt per phase: a retried phase has several rows, and the rail
  // reports where the work stands rather than how many tries it took.
  const newest = new Map<string, BuildWorkflowPhaseRow>();
  for (const row of view.phases ?? []) {
    const key = row.phase_key ?? "";
    if (!key) continue;
    newest.set(key, row);
  }

  const keys = [
    ...planned.keys(),
    ...[...newest.keys()].filter((key) => !planned.has(key)),
  ].slice(0, MAX_RAIL_PHASES);

  const rows: BuildPlanRow[] = keys.map((key, position) => {
    const row = newest.get(key);
    const status = normalizeStatus(row ? row.status : "queued");
    return {
      phaseKey: key,
      index: order.get(key) ?? row?.phase_index ?? position + 1,
      title: row ? titleOf(row, planned) : (planned.get(key) ?? key),
      status,
      stateLabel: BUILD_PHASE_STATE_LABELS[status] ?? status,
      capability: row?.capability ?? null,
      viaGeneralist: Boolean(row?.via_generalist),
      running: status === "running",
    };
  });

  const done = rows.filter((row) => row.status === "succeeded").length;
  const stopped = rows.find(
    (row) => row.status === "failed" || row.status === "needs_input",
  );
  return {
    rows,
    progress: rows.length ? `${done} of ${rows.length}` : "",
    emptyNote: rows.length ? "" : "No build has started.",
    attention: stopped
      ? stopped.status === "needs_input"
        ? `${stopped.title} is waiting for you.`
        : `${stopped.title} stopped.`
      : "",
  };
}

/**
 * The one row that may carry a moving indicator.
 *
 * Exactly one thing moves in the rail, and the rule is a property of the rail
 * rather than of this feature — the Agents section directly below applies the
 * same one. Returning a single key rather than a per-row boolean is what makes
 * "two spinners" unrepresentable instead of merely unlikely.
 */
export function movingRow(
  projection: BuildPlanProjection,
): string | null {
  const running = projection.rows.filter((row) => row.running);
  return running.length === 1 ? running[0]!.phaseKey : null;
}

/**
 * The row's accessible name.
 *
 * Capability has no column — there is no room for a third at this width, and
 * the transcript already names it — so it rides here, where a screen reader
 * gets it and the layout does not pay for it.
 */
export function phaseAccessibleName(row: BuildPlanRow): string {
  const parts = [`Phase ${row.index}: ${row.title}`, row.stateLabel];
  if (row.capability) {
    parts.push(
      row.viaGeneralist
        ? `${row.capability} (general-purpose stand-in)`
        : row.capability,
    );
  }
  return parts.join(" — ");
}

/**
 * How the Cycles section reports open work items now that Blockers is gone.
 *
 * Deleting the Blockers surface to make room would trade one real signal for
 * another: open work items are how a person learns Reconciliation is unsettled.
 * Folding the count onto the stage it belongs to keeps it where that stage
 * already is, and the full list stays in the stage sheet that already renders
 * it.
 */
export function blockerBadge(count: number): string {
  return count > 0 ? `${count} open` : "";
}
