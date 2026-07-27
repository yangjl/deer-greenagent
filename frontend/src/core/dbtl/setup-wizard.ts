import type {
  SetupQuestion,
  SetupQuestionOption,
} from "@/core/messages/human-input";

/**
 * The stepped DBTL setup card: one question at a time, each already answered.
 *
 * Pure and React-free, because the interesting rules are all decidable without
 * a DOM: which step is next, whether the reader may advance, and what the
 * assembled answer says. A wizard whose progression logic lives in component
 * state can only be checked by driving the UI.
 *
 * Two design rules the tests pin:
 *
 * 1. **A suggestion is pre-selected, never pre-committed.** Every step opens on
 *    the model's recommendation so the fast path is "next, next, done", but the
 *    assembled answer records what the human left standing — accepting a
 *    default is a decision, and the record cannot tell the difference later, so
 *    the card must make it cheap to look before agreeing.
 * 2. **"Other" is always offered and never pre-filled.** The options are the
 *    model's guesses at the shape of the answer; the reader has to be able to
 *    say something it did not think of without leaving the card.
 */

/** The synthetic option id for a free-text answer. Never sent by the model. */
export const OTHER_OPTION_ID = "__other__";

export const OTHER_OPTION_LABEL = "Other";
export const OTHER_OPTION_DESCRIPTION = "Answer in your own words";

export interface SetupAnswer {
  /** The chosen option, or `OTHER_OPTION_ID` for a typed answer. */
  optionId: string;
  /** What the human typed, when `optionId` is `OTHER_OPTION_ID`. */
  text: string;
}

export type SetupAnswers = Readonly<Record<string, SetupAnswer>>;

/**
 * The options a step renders, with "Other" appended.
 *
 * Questions that arrived without options become pure free-text steps: the
 * reader gets the recommendation as editable starting text rather than a
 * single-item menu, which would be a choice with nothing to choose.
 */
export function stepOptions(question: SetupQuestion): SetupQuestionOption[] {
  const offered = question.options ?? [];
  if (offered.length === 0) {
    return [];
  }
  return [
    ...offered,
    {
      id: OTHER_OPTION_ID,
      label: OTHER_OPTION_LABEL,
      description: OTHER_OPTION_DESCRIPTION,
    },
  ];
}

/** Where a step starts: the model's pick, or free text when it offered none. */
export function initialAnswer(question: SetupQuestion): SetupAnswer {
  const offered = question.options ?? [];
  const recommended = question.recommended_option_id;
  if (
    recommended &&
    offered.some((option: SetupQuestionOption) => option.id === recommended)
  ) {
    return { optionId: recommended, text: "" };
  }
  if (offered.length > 0) {
    // No recommendation is a real answer too — leaving it unselected makes the
    // reader choose rather than nudging them onto an arbitrary first option.
    return { optionId: "", text: "" };
  }
  return { optionId: OTHER_OPTION_ID, text: question.recommendation ?? "" };
}

export function initialAnswers(questions: SetupQuestion[]): SetupAnswers {
  return Object.fromEntries(
    questions.map((question) => [question.id, initialAnswer(question)]),
  );
}

/** Whether the current step has enough to move on. */
export function isAnswered(
  question: SetupQuestion,
  answer: SetupAnswer | undefined,
): boolean {
  if (!answer) {
    return false;
  }
  if (answer.optionId === OTHER_OPTION_ID) {
    return answer.text.trim().length > 0;
  }
  return answer.optionId.length > 0;
}

/**
 * The next unanswered step, or `null` when the wizard is done.
 *
 * "Adaptive" in the sense that matters here: it advances past steps that are
 * already settled instead of walking the reader through a fixed list.
 */
export function nextStepIndex(
  questions: SetupQuestion[],
  answers: SetupAnswers,
  from: number,
): number | null {
  for (let index = from; index < questions.length; index += 1) {
    const question = questions[index];
    if (question && !isAnswered(question, answers[question.id])) {
      return index;
    }
  }
  return null;
}

export function isComplete(
  questions: SetupQuestion[],
  answers: SetupAnswers,
): boolean {
  return questions.every((question) =>
    isAnswered(question, answers[question.id]),
  );
}

function answerLabel(
  question: SetupQuestion,
  answer: SetupAnswer | undefined,
): string {
  if (!answer) {
    return "";
  }
  if (answer.optionId === OTHER_OPTION_ID) {
    return answer.text.trim();
  }
  const chosen = (question.options ?? []).find(
    (option: SetupQuestionOption) => option.id === answer.optionId,
  );
  return chosen?.label ?? "";
}

/**
 * The text sent back as the card's answer.
 *
 * Question and answer are kept together rather than sent as a bare list: this
 * text is what the Design council is grounded in, and an answer detached from
 * its question is a value with no claim attached.
 */
export function composeAnswerText(
  questions: SetupQuestion[],
  answers: SetupAnswers,
): string {
  return questions
    .map((question) => {
      const label = answerLabel(question, answers[question.id]);
      return `${question.question}\n${label || "(not answered)"}`;
    })
    .join("\n\n");
}
