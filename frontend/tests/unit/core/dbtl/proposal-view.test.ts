import { describe, expect, it } from "@rstest/core";

import {
  NO_RECORD_NOTICE,
  PROPOSAL_ACTIONS,
  type EvaluationResponse,
  type ProposalPayload,
  advanceClarification,
  canConfirmSetup,
  clarificationPrompts,
  confirmationLines,
  hasProposalToShow,
  initialClarification,
  isClarificationComplete,
  outcomeForAction,
  proposalHeadline,
  resolvedObjective,
  summarizeEvaluations,
} from "@/core/dbtl/proposal-view";

function payload(overrides: Partial<ProposalPayload> = {}): ProposalPayload {
  return {
    kind: "proposal",
    proposed_objective:
      "Compare drought-response models across G2F environments",
    missing_fields: ["target trait", "season range", "validation expectation"],
    band: "high",
    confidence: 0.84,
    project_name: "G2F",
    cycle_id: null,
    creates_record: false,
    requires_confirmation: true,
    notice: NO_RECORD_NOTICE,
    confirmation: {
      project_name: "G2F",
      required_gates: ["Design", "Data reconciliation"],
      record_effect: "Creates a durable research record in this project.",
      notice: "Starting this cycle creates a durable research record.",
    },
    ...overrides,
  };
}

function evaluation(
  overrides: Partial<EvaluationResponse> = {},
): EvaluationResponse {
  return {
    evaluation_id: "eval-1",
    route_kind: "proposal",
    route_source: "classifier",
    proposals_visible: true,
    proposal: payload(),
    ...overrides,
  };
}

describe("showing a proposal", () => {
  it("states that no cycle has been created yet", () => {
    expect(NO_RECORD_NOTICE).toBe("No cycle has been created yet.");
    expect(payload().notice).toBe(NO_RECORD_NOTICE);
  });

  it("shows nothing when the server withheld the proposal", () => {
    expect(
      hasProposalToShow(
        evaluation({ proposal: null, proposals_visible: false }),
      ),
    ).toBe(false);
  });

  it("shows nothing for ordinary routing", () => {
    expect(
      hasProposalToShow(evaluation({ route_kind: "ordinary", proposal: null })),
    ).toBe(false);
  });

  it("shows the card when the server sent one", () => {
    expect(hasProposalToShow(evaluation())).toBe(true);
  });

  it("never shows a card the client built for itself", () => {
    // The client has no classifier. If the server did not send a proposal
    // there is nothing to render, whatever the route kind says.
    expect(
      hasProposalToShow(evaluation({ route_kind: "proposal", proposal: null })),
    ).toBe(false);
  });

  it("names a continuation differently from a new cycle", () => {
    expect(proposalHeadline(payload())).toMatch(/multi-step research/i);
    expect(
      proposalHeadline(
        payload({ kind: "cycle_continuation", cycle_id: "c-1" }),
      ),
    ).toMatch(/continu/i);
    expect(proposalHeadline(payload({ kind: "cycle_setup" }))).toMatch(
      /set up/i,
    );
  });
});

describe("the three card actions", () => {
  it("offers exactly the reviewed set", () => {
    expect(PROPOSAL_ACTIONS.map((action) => action.id)).toEqual([
      "start_setup",
      "keep_ordinary",
      "not_sure",
    ]);
  });

  it("maps each action to the outcome recorded for it", () => {
    expect(outcomeForAction("start_setup")).toBe("start_setup");
    expect(outcomeForAction("keep_ordinary")).toBe("keep_ordinary");
    expect(outcomeForAction("not_sure")).toBe("not_sure");
    expect(outcomeForAction("continue_cycle")).toBe("continue_cycle");
  });

  it("records a dismissal as its own outcome", () => {
    // A closed card is the false-upgrade signal; it must not be silence.
    expect(outcomeForAction("dismiss")).toBe("dismissed");
  });

  it("states a consequence for every action", () => {
    for (const action of PROPOSAL_ACTIONS) {
      expect(action.label.length).toBeGreaterThan(0);
      expect(action.consequence.length).toBeGreaterThan(0);
    }
  });

  it("promises that keeping it ordinary changes nothing", () => {
    const keep = PROPOSAL_ACTIONS.find((a) => a.id === "keep_ordinary")!;
    expect(keep.consequence.toLowerCase()).toContain("nothing");
  });
});

describe("clarification", () => {
  it("asks only for what the request did not already answer", () => {
    const prompts = clarificationPrompts(
      payload({ missing_fields: ["target trait"] }),
    );
    expect(prompts.map((p) => p.field)).toEqual(["target trait"]);
    expect(prompts[0]!.question.length).toBeGreaterThan(0);
    expect(prompts[0]!.placeholder.length).toBeGreaterThan(0);
  });

  it("starts with an empty answer per field", () => {
    const state = initialClarification(payload());
    expect(Object.keys(state)).toEqual([
      "target trait",
      "season range",
      "validation expectation",
    ]);
    expect(Object.values(state).every((v) => v === "")).toBe(true);
  });

  it("does not mutate the previous answers when one changes", () => {
    const before = initialClarification(payload());
    const after = advanceClarification(before, "target trait", "grain yield");
    expect(before["target trait"]).toBe("");
    expect(after["target trait"]).toBe("grain yield");
    expect(after).not.toBe(before);
  });

  it("is incomplete until every field has text", () => {
    let state = initialClarification(payload());
    expect(isClarificationComplete(payload(), state)).toBe(false);
    state = advanceClarification(state, "target trait", "grain yield");
    state = advanceClarification(state, "season range", "2023-2024");
    expect(isClarificationComplete(payload(), state)).toBe(false);
    state = advanceClarification(
      state,
      "validation expectation",
      "held-out sites",
    );
    expect(isClarificationComplete(payload(), state)).toBe(true);
  });

  it("treats whitespace as unanswered", () => {
    const state = advanceClarification(
      initialClarification(payload({ missing_fields: ["target trait"] })),
      "target trait",
      "   ",
    );
    expect(
      isClarificationComplete(
        payload({ missing_fields: ["target trait"] }),
        state,
      ),
    ).toBe(false);
  });

  it("is complete immediately when nothing was missing", () => {
    const complete = payload({ missing_fields: [] });
    expect(
      isClarificationComplete(complete, initialClarification(complete)),
    ).toBe(true);
  });
});

describe("confirmation", () => {
  it("cannot be confirmed until clarification is answered", () => {
    const proposal = payload();
    expect(
      canConfirmSetup(proposal, initialClarification(proposal), "Title"),
    ).toBe(false);
  });

  it("cannot be confirmed without a title", () => {
    const proposal = payload({ missing_fields: [] });
    expect(canConfirmSetup(proposal, {}, "  ")).toBe(false);
    expect(canConfirmSetup(proposal, {}, "Drought screen")).toBe(true);
  });

  it("lists project, class, objective, gates, and the record effect", () => {
    const lines = confirmationLines(payload(), "computational");
    const labels = lines.map((line) => line.label);
    expect(labels).toContain("Project");
    expect(labels).toContain("Cycle class");
    expect(labels).toContain("Objective");
    expect(labels).toContain("Required human gates");
    expect(labels).toContain("Effect");

    const gates = lines.find((line) => line.label === "Required human gates")!;
    expect(gates.value).toContain("Design");
    expect(gates.value).toContain("Data reconciliation");

    const effect = lines.find((line) => line.label === "Effect")!;
    expect(effect.value.toLowerCase()).toContain("durable");
  });

  it("uses the server's wording rather than a client paraphrase", () => {
    const proposal = payload();
    const effect = confirmationLines(proposal, "computational").find(
      (line) => line.label === "Effect",
    )!;
    expect(effect.value).toBe(proposal.confirmation.record_effect);
  });

  it("uses a clarified research objective when an explicit start had none", () => {
    const proposal = payload({
      kind: "cycle_setup",
      proposed_objective: "",
      missing_fields: ["research objective"],
    });
    expect(
      resolvedObjective(
        proposal,
        { "research objective": "Validate prediction across held-out sites" },
        "Genomic selection",
      ),
    ).toBe("Validate prediction across held-out sites");
  });

  it("shows the durable parent cycle in the confirmation", () => {
    const lines = confirmationLines(
      payload(),
      "computational",
      "Compare models",
      "2026 field program",
    );
    expect(lines).toContainEqual({
      label: "Parent cycle",
      value: "2026 field program",
    });
  });
});

describe("the evaluation drawer", () => {
  it("reports both rates the exit review has to approve", () => {
    // 1 of the 4 proposals was rejected; 2 of the 8 non-proposals became
    // cycles anyway. The two rates have different denominators on purpose.
    const summary = summarizeEvaluations({
      total: 12,
      proposed: 4,
      classifier_ordinary: 8,
      decided: 8,
      false_upgrades: 1,
      missed_cycles: 2,
    });
    expect(summary.falseUpgradeRate).toBeCloseTo(0.25);
    expect(summary.missedCycleRate).toBeCloseTo(0.25);
    expect(summary.decidedLabel).toBe("8 of 12 decided");
  });

  it("does not divide by zero on an empty project", () => {
    const summary = summarizeEvaluations({
      total: 0,
      proposed: 0,
      classifier_ordinary: 0,
      decided: 0,
      false_upgrades: 0,
      missed_cycles: 0,
    });
    expect(summary.falseUpgradeRate).toBe(0);
    expect(summary.missedCycleRate).toBe(0);
  });
});
