import { describe, expect, it } from "@rstest/core";

import {
  ASSUMPTION_NOTE,
  DRAFTED_NOTE,
  applySetupDraft,
  assumedFieldLabel,
  isDraftUseful,
} from "@/core/dbtl/setup-draft-merge";

const DRAFT = {
  enabled: true,
  title: "Genomic selection in maize",
  fields: {
    "research objective": "Compare genomic-selection strategies",
    "target trait": "grain yield",
  },
  assumed_fields: ["target trait"],
};

describe("applying a draft", () => {
  it("fills empty fields and the title", () => {
    const next = applySetupDraft({
      draft: DRAFT,
      title: "",
      clarification: { "research objective": "", "target trait": "" },
    });
    expect(next.title).toBe("Genomic selection in maize");
    expect(next.clarification["research objective"]).toBe(
      "Compare genomic-selection strategies",
    );
    expect(next.clarification["target trait"]).toBe("grain yield");
  });

  it("never overwrites what the scientist already typed", () => {
    // The draft can arrive while they are mid-sentence. Replacing their words
    // with the model's would be the worst possible moment to be helpful.
    const next = applySetupDraft({
      draft: DRAFT,
      title: "gen",
      clarification: {
        "research objective": "my own wording",
        "target trait": "",
      },
    });
    expect(next.title).toBe("gen");
    expect(next.clarification["research objective"]).toBe("my own wording");
    expect(next.clarification["target trait"]).toBe("grain yield");
  });

  it("treats whitespace as empty rather than as an edit", () => {
    const next = applySetupDraft({
      draft: DRAFT,
      title: "   ",
      clarification: { "target trait": "  " },
    });
    expect(next.title).toBe("Genomic selection in maize");
    expect(next.clarification["target trait"]).toBe("grain yield");
  });

  it("does not add fields the form is not asking about", () => {
    const next = applySetupDraft({
      draft: DRAFT,
      title: "",
      clarification: { "target trait": "" },
    });
    expect(Object.keys(next.clarification)).toEqual(["target trait"]);
  });

  it("reports only the assumptions it actually filled", () => {
    const next = applySetupDraft({
      draft: DRAFT,
      // The scientist already answered the assumed field, so it is their value
      // now and must not be flagged as the model's assumption.
      title: "",
      clarification: { "target trait": "ear height" },
    });
    expect(next.assumed).toEqual([]);
  });

  it("flags an assumption it did fill", () => {
    const next = applySetupDraft({
      draft: DRAFT,
      title: "",
      clarification: { "target trait": "", "research objective": "" },
    });
    expect(next.assumed).toEqual(["target trait"]);
  });

  it("returns a new state and leaves the input untouched", () => {
    const clarification = { "target trait": "" };
    const next = applySetupDraft({ draft: DRAFT, title: "", clarification });
    expect(clarification["target trait"]).toBe("");
    expect(next.clarification).not.toBe(clarification);
  });

  it("changes nothing when drafting is disabled", () => {
    const next = applySetupDraft({
      draft: { enabled: false, title: "", fields: {}, assumed_fields: [] },
      title: "",
      clarification: { "target trait": "" },
    });
    expect(next.title).toBe("");
    expect(next.clarification["target trait"]).toBe("");
    expect(next.assumed).toEqual([]);
  });

  it("changes nothing for a null draft", () => {
    const next = applySetupDraft({
      draft: null,
      title: "keep",
      clarification: { "target trait": "mine" },
    });
    expect(next.title).toBe("keep");
    expect(next.clarification["target trait"]).toBe("mine");
  });
});

describe("draft usefulness", () => {
  it("is useful when it filled a value", () => {
    expect(isDraftUseful(DRAFT)).toBe(true);
  });

  it("is not useful when disabled, empty, or absent", () => {
    expect(isDraftUseful(null)).toBe(false);
    expect(
      isDraftUseful({
        enabled: false,
        title: "",
        fields: {},
        assumed_fields: [],
      }),
    ).toBe(false);
    expect(
      isDraftUseful({
        enabled: true,
        title: "",
        fields: {},
        assumed_fields: [],
      }),
    ).toBe(false);
  });

  it("counts a title-only draft as useful", () => {
    expect(
      isDraftUseful({
        enabled: true,
        title: "t",
        fields: {},
        assumed_fields: [],
      }),
    ).toBe(true);
  });
});

describe("provenance wording", () => {
  it("says the draft came from the user's own request", () => {
    expect(DRAFTED_NOTE).toMatch(/your request/i);
  });

  it("says assumptions were not stated, so review is required", () => {
    // The scientist has to be able to tell the model's guess from their own
    // words before a confirmation makes it a durable record.
    expect(ASSUMPTION_NOTE).toMatch(/did not say|not stated/i);
  });

  it("labels an assumed field in words rather than by colour", () => {
    expect(assumedFieldLabel()).toMatch(/assumption|assumed/i);
  });
});
