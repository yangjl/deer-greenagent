/**
 * Pure view logic for the DBTL Upgrade Proposal (Phase 4).
 *
 * The card offers to turn ordinary work into a research cycle. Nothing here
 * decides that a proposal exists — that judgement is the server's, and the
 * client only renders what it was sent. `hasProposalToShow` is the shape of
 * that rule: a route kind of "proposal" with no payload shows nothing, so a
 * client-side heuristic has no way to manufacture a card.
 *
 * Wording that the human exit review signs off on (the notice, the record
 * effect, the required gates) arrives from the server rather than being
 * duplicated here, so approving the backend copy approves what users read.
 */

export const NO_RECORD_NOTICE = "No cycle has been created yet.";

export type RouteKind =
  | "ordinary"
  | "cycle_setup"
  | "cycle_continuation"
  | "proposal";

export type RouteSource =
  | "explicit_choice"
  | "explicit_request"
  | "selected_cycle"
  | "no_project"
  | "classifier";

export type ConfidenceBand = "low" | "medium" | "high";

export type ProposalOutcome =
  | "start_setup"
  | "keep_ordinary"
  | "not_sure"
  | "dismissed"
  | "continue_cycle";

/** What the user can do on the card. `dismiss` is closing it, not a button. */
export type ProposalAction =
  | "start_setup"
  | "keep_ordinary"
  | "not_sure"
  | "continue_cycle"
  | "dismiss";

export interface ConfirmationPayload {
  project_name: string;
  required_gates: string[];
  record_effect: string;
  notice: string;
}

export interface ProposalPayload {
  kind: RouteKind;
  proposed_objective: string;
  missing_fields: string[];
  band: ConfidenceBand;
  confidence: number;
  project_name: string;
  cycle_id: string | null;
  creates_record: boolean;
  requires_confirmation: boolean;
  notice: string;
  confirmation: ConfirmationPayload;
}

export interface EvaluationResponse {
  evaluation_id: string;
  route_kind: RouteKind;
  route_source: RouteSource;
  proposals_visible: boolean;
  proposal: ProposalPayload | null;
}

export interface EvaluationRow {
  evaluation_id: string;
  thread_id: string | null;
  user_id: string;
  route_kind: RouteKind;
  route_source: RouteSource;
  band: ConfidenceBand;
  confidence: number;
  rule_hits: { rule_id: string; weight: number; evidence: string }[];
  missing_fields: string[];
  proposed_objective: string;
  human_choice: ProposalOutcome | null;
  decided_at: string | null;
  created_at: string;
}

export interface EvaluationStats {
  total: number;
  proposed: number;
  classifier_ordinary: number;
  decided: number;
  false_upgrades: number;
  missed_cycles: number;
}

export interface ActionDescriptor {
  id: Exclude<ProposalAction, "dismiss">;
  label: string;
  consequence: string;
}

/**
 * The three buttons, in the reviewed order.
 *
 * A fixed list rather than a derived one: adding a fourth action — or a
 * "start it for me" shortcut that skips confirmation — has to be a conscious
 * edit here, which is where a reviewer will see it.
 */
export const PROPOSAL_ACTIONS: readonly ActionDescriptor[] = [
  {
    id: "start_setup",
    label: "Start DBTL setup",
    consequence:
      "Opens setup in this card. A cycle is still only created after you confirm.",
  },
  {
    id: "keep_ordinary",
    label: "Keep as ordinary chat",
    consequence:
      "Nothing is recorded and the conversation continues unchanged.",
  },
  {
    id: "not_sure",
    label: "Not sure",
    consequence:
      "Leaves the work ordinary and notes that the suggestion was unclear.",
  },
] as const;

export function outcomeForAction(action: ProposalAction): ProposalOutcome {
  return action === "dismiss" ? "dismissed" : action;
}

/**
 * Whether there is a card to render.
 *
 * Deliberately keyed on the payload, not the route kind: the server withholds
 * the payload when proposals are not visible to users yet, and the client
 * must have no second opinion about that.
 */
export function hasProposalToShow(
  evaluation: EvaluationResponse | null | undefined,
): boolean {
  return Boolean(evaluation?.proposal);
}

export function proposalHeadline(proposal: ProposalPayload): string {
  switch (proposal.kind) {
    case "cycle_continuation":
      return "This looks like a continuation of the selected cycle.";
    case "cycle_setup":
      return "You asked to set up a DBTL cycle.";
    default:
      return "This looks like multi-step research work.";
  }
}

export interface ClarificationPrompt {
  field: string;
  question: string;
  placeholder: string;
}

const CLARIFICATION_COPY: Record<string, Omit<ClarificationPrompt, "field">> = {
  "research objective": {
    question: "What research question should this cycle answer?",
    placeholder: "Compare genomic-selection strategies across environments",
  },
  "target trait": {
    question: "Which trait is this about?",
    placeholder: "grain yield",
  },
  "season range": {
    question: "Which seasons or years does it cover?",
    placeholder: "2023–2024",
  },
  "validation expectation": {
    question: "What would count as validated?",
    placeholder: "held-out environments, preregistered metric",
  },
  "population scope": {
    question: "Which population or panel?",
    placeholder: "1,204 hybrids across 14 environments",
  },
};

export function clarificationPrompts(
  proposal: ProposalPayload,
): ClarificationPrompt[] {
  return proposal.missing_fields.map((field) => ({
    field,
    question: CLARIFICATION_COPY[field]?.question ?? `What is the ${field}?`,
    placeholder: CLARIFICATION_COPY[field]?.placeholder ?? "",
  }));
}

export type ClarificationState = Readonly<Record<string, string>>;

export function initialClarification(
  proposal: ProposalPayload,
): ClarificationState {
  return Object.fromEntries(
    proposal.missing_fields.map((field) => [field, ""]),
  );
}

/** Returns a new state; the previous one is never mutated. */
export function advanceClarification(
  state: ClarificationState,
  field: string,
  value: string,
): ClarificationState {
  return { ...state, [field]: value };
}

export function isClarificationComplete(
  proposal: ProposalPayload,
  state: ClarificationState,
): boolean {
  return proposal.missing_fields.every(
    (field) => (state[field] ?? "").trim().length > 0,
  );
}

export function canConfirmSetup(
  proposal: ProposalPayload,
  state: ClarificationState,
  title: string,
): boolean {
  return title.trim().length > 0 && isClarificationComplete(proposal, state);
}

export function resolvedObjective(
  proposal: ProposalPayload,
  state: ClarificationState,
  title: string,
): string {
  return (
    proposal.proposed_objective.trim() ||
    (state["research objective"] ?? "").trim() ||
    title.trim()
  );
}

export interface ConfirmationLine {
  label: string;
  value: string;
}

/** The last screen before a durable record exists. */
export function confirmationLines(
  proposal: ProposalPayload,
  cycleClass: string,
  objective = proposal.proposed_objective,
  parentCycleTitle?: string | null,
): ConfirmationLine[] {
  const lines = [
    { label: "Project", value: proposal.confirmation.project_name },
    { label: "Cycle class", value: cycleClass },
    { label: "Objective", value: objective },
    {
      label: "Required human gates",
      value: proposal.confirmation.required_gates.join(" · "),
    },
    { label: "Effect", value: proposal.confirmation.record_effect },
  ];
  if (parentCycleTitle) {
    lines.splice(2, 0, { label: "Parent cycle", value: parentCycleTitle });
  }
  return lines;
}

export interface EvaluationSummary {
  falseUpgradeRate: number;
  missedCycleRate: number;
  decidedLabel: string;
}

/**
 * The two rates the human exit review approves thresholds against.
 *
 * A false upgrade is measured against what was proposed; a missed cycle
 * against what was not. Using one denominator for both would make the pair
 * move together and hide the trade-off between them.
 */
export function summarizeEvaluations(
  stats: EvaluationStats,
): EvaluationSummary {
  return {
    falseUpgradeRate:
      stats.proposed > 0 ? stats.false_upgrades / stats.proposed : 0,
    missedCycleRate:
      stats.classifier_ordinary > 0
        ? stats.missed_cycles / stats.classifier_ordinary
        : 0,
    decidedLabel: `${stats.decided} of ${stats.total} decided`,
  };
}
