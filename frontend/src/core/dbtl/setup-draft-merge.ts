/**
 * Merging an LLM-drafted setup form into what the scientist has typed.
 *
 * Pure, because the two rules that matter are safety rules and should be
 * testable without rendering a form:
 *
 * 1. **A draft never overwrites a human's words.** It can arrive while they are
 *    mid-sentence, so replacing their text with the model's would be the worst
 *    possible moment to be helpful. Only blank fields are filled.
 * 2. **Assumptions stay distinguishable from statements.** Confirming this form
 *    creates a durable research record, so a value the model proposed must not
 *    become indistinguishable from one the scientist stated. `assumed` reports
 *    only the fields the draft actually filled — a field the human answered is
 *    theirs, and flagging it would be a lie in the other direction.
 */

import type { ClarificationState } from "./proposal-view";
import type { SetupDraftResponse } from "./proposals-api";

/** Shown once above a drafted form. */
export const DRAFTED_NOTE =
  "Drafted from your request. Review each line, edit anything, then confirm.";

/** Shown when at least one field is the model's proposal rather than yours. */
export const ASSUMPTION_NOTE =
  "Marked lines are suggestions you did not say — check them before confirming.";

/** The per-field marker. A word, never colour alone. */
export function assumedFieldLabel(): string {
  return "assumption";
}

export interface AppliedSetupDraft {
  title: string;
  clarification: ClarificationState;
  /** Fields this merge filled with an unstated value, in the draft's order. */
  assumed: string[];
}

function isBlank(value: string | undefined): boolean {
  return !(value ?? "").trim();
}

/** Whether a draft has anything worth showing the user. */
export function isDraftUseful(
  draft: SetupDraftResponse | null | undefined,
): boolean {
  if (!draft?.enabled) return false;
  return Boolean(draft.title.trim()) || Object.keys(draft.fields).length > 0;
}

/**
 * Fill the blanks, and report which filled values were assumptions.
 *
 * Returns new objects; the inputs are never mutated.
 */
export function applySetupDraft(input: {
  draft: SetupDraftResponse | null | undefined;
  title: string;
  clarification: ClarificationState;
}): AppliedSetupDraft {
  const { draft, title, clarification } = input;
  if (!draft?.enabled) {
    return { title, clarification: { ...clarification }, assumed: [] };
  }

  const assumedInDraft = new Set(draft.assumed_fields);
  const next: Record<string, string> = { ...clarification };
  const assumed: string[] = [];

  // Iterate the form's own fields, not the draft's: the server already drops
  // unknown keys, and this keeps the form's shape authoritative regardless.
  for (const field of Object.keys(clarification)) {
    const drafted = draft.fields[field];
    if (!drafted || !isBlank(next[field])) continue;
    next[field] = drafted;
    if (assumedInDraft.has(field)) {
      assumed.push(field);
    }
  }

  return {
    title: isBlank(title) && draft.title.trim() ? draft.title : title,
    clarification: next,
    assumed,
  };
}
