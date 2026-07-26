/**
 * Pure logic for the Memory scope migration review.
 *
 * Kept free of React and of network access so the privacy rules that matter —
 * counts before content, no bulk sharing, an explicit decision per fact — can
 * be asserted directly rather than inferred from a rendered tree.
 */

export type MigrationDecision =
  | "keep_private"
  | "share"
  | "edit_then_share"
  | "quarantine";

export interface MigrationCounts {
  private_legacy: number;
  suggested_for_project: number;
  already_project_scoped: number;
  needs_classification: number;
}

export interface MigrationSuggestion {
  fact_id: string;
  bucket_id: string;
  agent_name: string;
  owner_user_id: string;
  sha256: string;
  category: string;
  suggested_decision: MigrationDecision;
  reason: string;
  title: string;
  content: string;
}

export const DECISION_LABELS: Record<MigrationDecision, string> = {
  keep_private: "Keep private",
  share: "Share with this project",
  edit_then_share: "Edit then share",
  quarantine: "Quarantine for later",
};

/**
 * The four actions, in the order the review drawer offers them.
 *
 * Deliberately a fixed list rather than a derived one: adding a bulk action
 * would have to be a conscious edit here, not an emergent consequence.
 */
export const DECISION_ORDER: readonly MigrationDecision[] = [
  "keep_private",
  "share",
  "edit_then_share",
  "quarantine",
];

export const EMPTY_COUNTS: MigrationCounts = {
  private_legacy: 0,
  suggested_for_project: 0,
  already_project_scoped: 0,
  needs_classification: 0,
};

export interface CountRow {
  key: keyof MigrationCounts;
  label: string;
  value: number;
  /** Whether this row is an action the user can take, not just a number. */
  actionable: boolean;
}

const COUNT_LABELS: Record<keyof MigrationCounts, string> = {
  private_legacy: "Private legacy facts",
  suggested_for_project: "Suggested for project",
  already_project_scoped: "Already project-scoped",
  needs_classification: "Needs classification",
};

/** Build the landing view's rows. Order is fixed so the panel never reflows. */
export function countRows(counts: MigrationCounts): CountRow[] {
  return (Object.keys(COUNT_LABELS) as Array<keyof MigrationCounts>).map(
    (key) => ({
      key,
      label: COUNT_LABELS[key],
      value: counts[key] ?? 0,
      actionable: key === "private_legacy" && (counts[key] ?? 0) > 0,
    }),
  );
}

/** Label for the button that opens the caller's own review queue. */
export function reviewCallToAction(counts: MigrationCounts): string {
  const pending = counts.private_legacy ?? 0;
  if (pending === 0) {
    return "Review my memory";
  }
  return `Review my ${pending} fact${pending === 1 ? "" : "s"}`;
}

/** True when there is nothing at all to migrate for this project. */
export function isMigrationEmpty(counts: MigrationCounts): boolean {
  return (
    (counts.private_legacy ?? 0) === 0 &&
    (counts.already_project_scoped ?? 0) === 0 &&
    (counts.needs_classification ?? 0) === 0
  );
}

/**
 * Which fact the drawer shows next after *decidedFactId* is resolved.
 *
 * Reviewing is one fact at a time, so the queue advances rather than
 * re-rendering a list the user has to re-find their place in.
 */
export function suggestionKey(suggestion: MigrationSuggestion): string {
  return JSON.stringify([
    suggestion.bucket_id,
    suggestion.agent_name,
    suggestion.fact_id,
    suggestion.sha256,
  ]);
}

export function nextSuggestionKey(
  suggestions: readonly MigrationSuggestion[],
  decidedKey: string,
): string | null {
  const index = suggestions.findIndex(
    (item) => suggestionKey(item) === decidedKey,
  );
  if (index === -1) {
    return suggestions[0] ? suggestionKey(suggestions[0]) : null;
  }
  const remaining = suggestions.filter(
    (item) => suggestionKey(item) !== decidedKey,
  );
  const next = remaining[index] ?? remaining[remaining.length - 1];
  return next ? suggestionKey(next) : null;
}

/** A short, non-secret provenance line for one fact. */
export function provenanceLabel(suggestion: MigrationSuggestion): string {
  const agent =
    suggestion.agent_name === "__default__"
      ? "default agent"
      : suggestion.agent_name;
  return `${agent} · ${suggestion.category} · ${suggestion.sha256.slice(0, 12)}`;
}

/** Whether a decision can be submitted given the current draft edit. */
export function canSubmitDecision(
  decision: MigrationDecision,
  draft: string,
): boolean {
  if (decision === "edit_then_share") {
    return draft.trim().length > 0;
  }
  return true;
}

/** Human-readable consequence, shown before the user commits. */
export function decisionConsequence(decision: MigrationDecision): string {
  switch (decision) {
    case "share":
      return "Every authorized member of this project will be able to retrieve this fact.";
    case "edit_then_share":
      return "Only your edited text becomes visible to the project. The original stays private.";
    case "quarantine":
      return "Nothing moves. The fact is set aside and will not be suggested again.";
    default:
      return "Nothing moves. This fact stays visible only to you.";
  }
}
