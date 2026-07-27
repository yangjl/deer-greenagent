/**
 * The data readiness and reconciliation matrix, as a pure view model (Phase 6).
 *
 * Nothing here reads storage or the network, so the source of every rendered
 * status is a function argument — the same rule `cycle-view.ts` follows, and the
 * reason both are testable without a DOM.
 *
 * Two product rules are expressed as code rather than as component conditionals,
 * because a component is where they would quietly drift:
 *
 * - **Status is never conveyed by colour alone.** Every status, outcome, and
 *   blocker kind has a word here, and `rowStatusTone` is offered only alongside
 *   that word.
 * - **A proposed resolution is not a decision.** `blocksGate` treats an agent's
 *   unconfirmed proposal as outstanding work, matching the backend gate exactly.
 *   If these two disagreed, the matrix would show a settled row the gate still
 *   refuses, and the reviewer would have no way to find out why.
 */

export const RECONCILIATION_ROW_STATUSES = [
  "open",
  "proposed",
  "resolved",
  "blocked",
  "waived",
] as const;
export type RowStatus = (typeof RECONCILIATION_ROW_STATUSES)[number];

export type ActorType = "human" | "agent";
export type BlockerKind =
  | "missing_data"
  | "conflicting_sources"
  | "indeterminate";
export type GateOutcome =
  | "ready_for_build"
  | "changes_required"
  | "blocked_missing_data"
  | "blocked_conflicting_sources"
  | "inconclusive_data";
export type DatasetRole = "raw" | "derived" | "reference";

/** A decision a person may record. `proposed` is agent-only, so it is absent. */
export const HUMAN_DECISIONS = ["resolved", "blocked", "waived"] as const;
export type HumanDecision = (typeof HUMAN_DECISIONS)[number];

export interface ReconciliationRow {
  row_id: string;
  check: string;
  check_label: string;
  field_name: string;
  source_a_label: string;
  source_a_value: string;
  source_b_label: string;
  source_b_value: string;
  required: boolean;
  status: RowStatus;
  resolution: string;
  resolved_by_actor: ActorType | null;
  resolved_by_user_id: string | null;
  blocker_kind: BlockerKind | null;
  evidence_refs: string[];
  needs_human_decision: boolean;
  blocks_gate: boolean;
  /** The row's own revision, not the cycle's — a decision sends both. */
  db_revision: number;
}

export interface DatasetRecord {
  id: string;
  source_key: string;
  uri: string;
  content_hash: string;
  declared_immutable: boolean;
  role: DatasetRole;
  recorded_by: string;
  db_revision: number;
  created_at: string;
  updated_at: string;
}

export interface GateState {
  outcome: GateOutcome;
  ready: boolean;
  blocking_rows: string[];
  reasons: string[];
  dataset_fingerprint: string;
  resolved_count: number;
  total_required: number;
}

export interface ApprovalInvalidation {
  invalidated: boolean;
  reasons: string[];
}

export interface ReconciliationView {
  cycle_id: string;
  design_approved: boolean;
  rows: ReconciliationRow[];
  unreadable_row_ids: string[];
  datasets: DatasetRecord[];
  gate: GateState;
  summary: Record<RowStatus, number>;
  approval_binding: {
    dataset_fingerprint: string;
    stage_spec_key: string;
    policy_version: string;
  } | null;
  approval_invalidation: ApprovalInvalidation | null;
  db_revision: number;
}

export const ROW_STATUS_LABELS: Record<RowStatus, string> = {
  open: "Not reconciled",
  proposed: "Proposed — awaiting a reviewer",
  resolved: "Resolved",
  blocked: "Blocked",
  waived: "Waived",
};

export const GATE_OUTCOME_LABELS: Record<GateOutcome, string> = {
  ready_for_build: "Ready for Build",
  changes_required: "Changes required",
  blocked_missing_data: "Blocked — missing data",
  blocked_conflicting_sources: "Blocked — sources conflict",
  inconclusive_data: "Inconclusive data",
};

export const BLOCKER_KIND_LABELS: Record<BlockerKind, string> = {
  missing_data: "Missing data",
  conflicting_sources: "Sources conflict",
  indeterminate: "Cannot be determined from these inputs",
};

export const DATASET_ROLE_LABELS: Record<DatasetRole, string> = {
  raw: "Raw",
  derived: "Derived",
  reference: "Reference",
};

/** A semantic tone, always offered *beside* a label — never instead of one. */
export type StatusTone = "neutral" | "pending" | "positive" | "critical";

export function rowStatusTone(status: RowStatus): StatusTone {
  switch (status) {
    case "resolved":
    case "waived":
      return "positive";
    case "blocked":
      return "critical";
    case "proposed":
      return "pending";
    default:
      return "neutral";
  }
}

export function gateTone(gate: GateState): StatusTone {
  if (gate.ready) return "positive";
  return gate.outcome === "changes_required" ? "pending" : "critical";
}

/** Whether a person may record a decision on this row right now. */
export function canDecideRow(row: ReconciliationRow): boolean {
  return row.status !== "resolved" && row.status !== "waived";
}

/** A blocked row must say *why*, so the outcome code can name the real problem. */
export function requiresBlockerKind(decision: HumanDecision): boolean {
  return decision === "blocked";
}

export function canSubmitDecision(
  decision: HumanDecision,
  rationale: string,
  blockerKind: BlockerKind | null,
): boolean {
  if (!rationale.trim()) return false;
  return requiresBlockerKind(decision) ? blockerKind !== null : true;
}

export interface DecisionDescriptor {
  id: HumanDecision;
  label: string;
  consequence: string;
}

/**
 * What each decision does, in the reviewer's terms. The consequence text is
 * shown on the control itself: a reviewer settling a contradiction is making a
 * scientific call, and "Resolve" alone does not say that Build will proceed on
 * it.
 */
export function decisionOptions(row: ReconciliationRow): DecisionDescriptor[] {
  return [
    {
      id: "resolved",
      label: "Resolve",
      consequence: row.needs_human_decision
        ? "Records your call on this contradiction. Build will use the reconciled input."
        : "Marks this field reconciled. Build will use the reconciled input.",
    },
    {
      id: "blocked",
      label: "Block",
      consequence:
        "Keeps Build locked and names what has to be fixed before this cycle can continue.",
    },
    {
      id: "waived",
      label: "Waive",
      consequence:
        "Accepts this field as out of scope for this cycle, with your rationale on the record.",
    },
  ];
}

/**
 * A one-line answer to "can Build start?".
 *
 * The ready case still names the count, because "Ready for Build" with no
 * denominator gives a reviewer nothing to sanity-check against.
 */
export function gateHeadline(gate: GateState): string {
  const progress = `${gate.resolved_count} of ${gate.total_required} required ${
    gate.total_required === 1 ? "row" : "rows"
  } settled`;
  if (gate.ready) return `Ready for Build — ${progress}.`;
  return `${GATE_OUTCOME_LABELS[gate.outcome]} — ${progress}.`;
}

/** Rows that are holding the gate, blocked first so the worst is at the top. */
export function blockingRows(rows: ReconciliationRow[]): ReconciliationRow[] {
  const order: Record<RowStatus, number> = {
    blocked: 0,
    proposed: 1,
    open: 2,
    resolved: 3,
    waived: 4,
  };
  return rows
    .filter((row) => row.blocks_gate)
    .slice()
    .sort((a, b) => order[a.status] - order[b.status]);
}

/**
 * Matrix order: unsettled work first, settled rows after.
 *
 * A matrix sorted by creation order buries the one blocked row under twelve
 * resolved ones, which is the opposite of what the reviewer opened it for.
 */
export function orderedRows(rows: ReconciliationRow[]): ReconciliationRow[] {
  return rows
    .slice()
    .sort(
      (a, b) =>
        Number(b.blocks_gate) - Number(a.blocks_gate) ||
        a.field_name.localeCompare(b.field_name),
    );
}

export interface SourceCell {
  label: string;
  value: string;
}

/** The two source columns, omitting a source the row does not compare. */
export function sourceCells(row: ReconciliationRow): SourceCell[] {
  const cells: SourceCell[] = [];
  if (row.source_a_label || row.source_a_value) {
    cells.push({ label: row.source_a_label, value: row.source_a_value });
  }
  if (row.source_b_label || row.source_b_value) {
    cells.push({ label: row.source_b_label, value: row.source_b_value });
  }
  return cells;
}

/** Who settled a row, in words — the matrix has to show this. */
export function decidedBy(row: ReconciliationRow): string {
  if (!row.resolved_by_actor) return "Not yet decided";
  if (row.resolved_by_actor === "agent") {
    return row.status === "proposed"
      ? "Proposed by an agent — needs a reviewer"
      : "Recorded by an agent";
  }
  return row.resolved_by_user_id
    ? `Decided by ${row.resolved_by_user_id}`
    : "Decided by a reviewer";
}

/**
 * The banner shown when an approval no longer describes the current inputs.
 *
 * Returns `null` when nothing is wrong, so the caller renders nothing rather
 * than an empty reassurance box.
 */
export function invalidationNotice(
  view: Pick<ReconciliationView, "approval_invalidation">,
): { headline: string; reasons: string[] } | null {
  const check = view.approval_invalidation;
  if (!check?.invalidated) return null;
  return {
    headline:
      "This reconciliation approval no longer matches the declared inputs.",
    reasons: check.reasons,
  };
}

/** Raw sources someone declared as writable — a gate condition, not a nit. */
export function mutableRawSources(datasets: DatasetRecord[]): DatasetRecord[] {
  return datasets.filter(
    (item) => item.role === "raw" && !item.declared_immutable,
  );
}

export function shortHash(value: string, length = 12): string {
  return value.length <= length ? value : `${value.slice(0, length)}…`;
}

/**
 * Whether the matrix can be opened for review at all.
 *
 * Reconciliation without an approved Design has nothing to reconcile *against*,
 * so the panel says that instead of showing an empty matrix that looks finished.
 */
export function reconciliationBlockReason(
  view: ReconciliationView | null | undefined,
): string {
  if (!view) return "";
  if (!view.design_approved) {
    return "Design has not been approved yet, so there is nothing to reconcile against.";
  }
  if (view.datasets.length === 0) {
    return "No data sources have been declared for this cycle yet.";
  }
  return "";
}
