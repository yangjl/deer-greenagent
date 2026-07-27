import { describe, expect, it } from "@rstest/core";

import {
  OTHER_OPTION_ID,
  composeAnswerText,
  initialAnswer,
  initialAnswers,
  isAnswered,
  isComplete,
  nextStepIndex,
  stepOptions,
} from "@/core/dbtl/setup-wizard";
import type { SetupQuestion } from "@/core/messages/human-input";

const TRAIT: SetupQuestion = {
  id: "trait",
  question: "Which trait should this cycle target?",
  options: [
    { id: "height", label: "Plant height", description: "Highly heritable" },
    { id: "yield", label: "Grain yield", description: "The breeding target" },
  ],
  recommended_option_id: "yield",
};

const SCALE: SetupQuestion = {
  id: "scale",
  question: "How many individuals?",
  recommendation: "100 inbreds",
};

describe("stepOptions", () => {
  it("always offers Other, so the reader is never boxed in by the guesses", () => {
    const ids = stepOptions(TRAIT).map((option) => option.id);
    expect(ids).toEqual(["height", "yield", OTHER_OPTION_ID]);
  });

  it("offers no menu at all when the model proposed no choices", () => {
    // A single-item menu is a choice with nothing to choose; that step is
    // free text instead.
    expect(stepOptions(SCALE)).toEqual([]);
  });
});

describe("initialAnswer", () => {
  it("opens on the recommendation so accepting everything is fast", () => {
    expect(initialAnswer(TRAIT)).toEqual({ optionId: "yield", text: "" });
  });

  it("seeds free-text steps with the recommendation as editable text", () => {
    expect(initialAnswer(SCALE)).toEqual({
      optionId: OTHER_OPTION_ID,
      text: "100 inbreds",
    });
  });

  it("leaves the step unselected when the model declined to recommend", () => {
    // Nudging onto an arbitrary first option would manufacture a decision
    // nobody made, and this answer becomes a durable research record.
    const undecided: SetupQuestion = { ...TRAIT, recommended_option_id: "" };
    expect(initialAnswer(undecided)).toEqual({ optionId: "", text: "" });
  });

  it("ignores a recommendation naming an option that is not offered", () => {
    const stale: SetupQuestion = { ...TRAIT, recommended_option_id: "gone" };
    expect(initialAnswer(stale).optionId).toBe("");
  });
});

describe("isAnswered", () => {
  it("requires text when the reader chose Other", () => {
    expect(isAnswered(TRAIT, { optionId: OTHER_OPTION_ID, text: "  " })).toBe(
      false,
    );
    expect(isAnswered(TRAIT, { optionId: OTHER_OPTION_ID, text: "ear leaf" })).toBe(
      true,
    );
  });

  it("treats an unselected step as unanswered", () => {
    expect(isAnswered(TRAIT, { optionId: "", text: "" })).toBe(false);
    expect(isAnswered(TRAIT, undefined)).toBe(false);
  });
});

describe("nextStepIndex", () => {
  const questions = [TRAIT, SCALE];

  it("skips steps that are already settled", () => {
    const answers = initialAnswers(questions);
    // Both open pre-answered, so there is nothing left to stop on.
    expect(nextStepIndex(questions, answers, 0)).toBeNull();
    expect(isComplete(questions, answers)).toBe(true);
  });

  it("stops on the first step still needing the reader", () => {
    const answers = {
      trait: { optionId: "", text: "" },
      scale: { optionId: OTHER_OPTION_ID, text: "100" },
    };
    expect(nextStepIndex(questions, answers, 0)).toBe(0);
  });

  it("does not walk backwards past the step it was given", () => {
    const answers = {
      trait: { optionId: "", text: "" },
      scale: { optionId: OTHER_OPTION_ID, text: "" },
    };
    expect(nextStepIndex(questions, answers, 1)).toBe(1);
  });
});

describe("composeAnswerText", () => {
  it("keeps each answer attached to the question it answers", () => {
    const text = composeAnswerText([TRAIT, SCALE], {
      trait: { optionId: "height", text: "" },
      scale: { optionId: OTHER_OPTION_ID, text: "250 lines" },
    });

    expect(text).toContain("Which trait should this cycle target?\nPlant height");
    expect(text).toContain("How many individuals?\n250 lines");
  });

  it("sends the reader's words when they chose Other", () => {
    const text = composeAnswerText([TRAIT], {
      trait: { optionId: OTHER_OPTION_ID, text: "ear leaf angle" },
    });
    expect(text).toContain("ear leaf angle");
    expect(text).not.toContain("Grain yield");
  });

  it("says so rather than silently dropping an unanswered step", () => {
    const text = composeAnswerText([TRAIT], {
      trait: { optionId: "", text: "" },
    });
    expect(text).toContain("(not answered)");
  });
});
