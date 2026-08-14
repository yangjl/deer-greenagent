import { describe, expect, it } from "@rstest/core";

import {
  consensusSnapshot,
  councilSeatSummary,
  consensusState,
  debateRounds,
  readCouncilSeat,
} from "@/core/tasks/council-seat";
import type { Subtask } from "@/core/tasks/types";

function seatEvent(overrides: Record<string, unknown> = {}) {
  return {
    role: "position",
    role_label: "Independent position",
    focus: "quantitative genetics",
    capability: "quantitative_genetics",
    agent_name: "quant-geneticist",
    via_generalist: false,
    model: "gpt-5.6-sol",
    round: 1,
    counts_toward_stage_output: false,
    ...overrides,
  };
}

function task(
  id: string,
  status: Subtask["status"],
  seat: Record<string, unknown> | null,
): Subtask {
  return {
    id,
    status,
    subagent_type: "general-purpose",
    description: "",
    prompt: "",
    ...(seat ? { councilSeat: readCouncilSeat(seat)! } : {}),
  };
}

describe("readCouncilSeat", () => {
  it("reads a seat the backend sent", () => {
    const seat = readCouncilSeat(seatEvent({ role: "red_team" }));

    expect(seat?.role).toBe("red_team");
    expect(seat?.stage).toBe("design");
    expect(seat?.agentName).toBe("quant-geneticist");
    expect(seat?.model).toBe("gpt-5.6-sol");
  });

  it("carries the stage for a non-Design review meeting", () => {
    expect(readCouncilSeat(seatEvent({ stage: "build" }))?.stage).toBe("build");
  });

  it("keeps a stand-in generalist visible", () => {
    const seat = readCouncilSeat(
      seatEvent({ agent_name: "general-purpose", via_generalist: true }),
    );

    expect(seat?.viaGeneralist).toBe(true);
  });

  it("treats an absent or malformed seat as an ordinary subtask", () => {
    // Never a seat with guessed fields: a panel that invented a role would be
    // worse than one that showed a plain progress card.
    expect(readCouncilSeat(undefined)).toBeNull();
    expect(readCouncilSeat({})).toBeNull();
    expect(readCouncilSeat({ role: "chair" })).toBeNull();
    expect(readCouncilSeat("chair")).toBeNull();
  });
});

describe("debateRounds", () => {
  it("orders seats by debate role, not by arrival", () => {
    const rounds = debateRounds([
      task("c", "completed", seatEvent({ role: "chair" })),
      task("p", "completed", seatEvent({ role: "position" })),
      task("r", "completed", seatEvent({ role: "red_team" })),
    ]);

    expect(rounds).toHaveLength(1);
    expect(rounds[0]!.seats.map((s) => s.councilSeat!.role)).toEqual([
      "position",
      "red_team",
      "chair",
    ]);
  });

  it("keeps rounds separate and in order", () => {
    const rounds = debateRounds([
      task("b", "in_progress", seatEvent({ round: 2 })),
      task("a", "completed", seatEvent({ round: 1 })),
    ]);

    expect(rounds.map((r) => r.round)).toEqual([1, 2]);
  });

  it("ignores subtasks that are not council seats", () => {
    const rounds = debateRounds([
      task("plain", "completed", null),
      task("seat", "completed", seatEvent()),
    ]);

    expect(rounds[0]!.seats.map((s) => s.id)).toEqual(["seat"]);
  });
});

describe("consensusState", () => {
  it("is debating while positions are still arguing", () => {
    expect(
      consensusState([
        task("p1", "in_progress", seatEvent()),
        task("p2", "completed", seatEvent()),
      ]),
    ).toBe("debating");
  });

  it("is synthesizing once the chair is seated", () => {
    expect(
      consensusState([
        task("p1", "completed", seatEvent()),
        task("c", "in_progress", seatEvent({ role: "chair" })),
      ]),
    ).toBe("synthesizing");
  });

  it("is settled when the chair has spoken", () => {
    expect(
      consensusState([
        task("p1", "completed", seatEvent()),
        task("c", "completed", seatEvent({ role: "chair" })),
      ]),
    ).toBe("settled");
  });

  it("waits on the human when the chair asks for one decision", () => {
    const chair = task("c", "completed", seatEvent({ role: "chair" }));
    chair.result = JSON.stringify({
      status: "needs_input",
      summary: "The site choice remains open.",
      clarification_question: "Which site can guarantee irrigation?",
    });

    expect(consensusState([task("p1", "completed", seatEvent()), chair])).toBe(
      "awaiting_input",
    );
  });

  it("labels a chair summary as partial when another participant failed", () => {
    const chair = task("c", "completed", seatEvent({ role: "chair" }));
    chair.result = JSON.stringify({
      status: "needs_input",
      summary: "The chair could only inspect the workspace.",
      clarification_question: "Which benchmark should govern the cycle?",
    });

    expect(
      consensusState([
        task("p1", "failed", seatEvent()),
        task("red", "failed", seatEvent({ role: "red_team" })),
        chair,
      ]),
    ).toBe("partial");
  });

  it("is stalled when the chair failed", () => {
    // Not "settled" and not "still going": a failed chair needs a different
    // word and a different next action from either.
    expect(
      consensusState([
        task("p1", "completed", seatEvent()),
        task("c", "failed", seatEvent({ role: "chair" })),
      ]),
    ).toBe("stalled");
  });

  it("is stalled when every position failed before a chair was seated", () => {
    // Leaving a spinner running over a council that is already over is the
    // exact experience the whole redesign is meant to remove.
    expect(
      consensusState([
        task("p1", "failed", seatEvent()),
        task("p2", "failed", seatEvent()),
      ]),
    ).toBe("stalled");
  });
});

describe("consensusSnapshot", () => {
  it("counts the chair's recorded agreements, disagreements, and open questions", () => {
    const chair = task("chair", "completed", seatEvent({ role: "chair" }));
    chair.result = JSON.stringify({
      status: "completed",
      summary: "Use two seasons and preserve the holdout.",
      consensus: {
        agreements: ["Preserve a holdout.", "Model genotype by environment."],
        disagreements: [
          {
            topic: "Season count",
            positions: ["Two.", "Three."],
            resolution: "",
          },
        ],
        open_questions: ["Which sites have irrigation control?"],
      },
    });

    expect(consensusSnapshot([chair])).toEqual({
      agreements: 2,
      disagreements: 1,
      openQuestions: 1,
      unresolved: 2,
    });
    expect(councilSeatSummary(chair)).toBe(
      "Use two seasons and preserve the holdout.",
    );
  });

  it("uses the latest bounded argument step while a seat is running", () => {
    const position = task("position", "in_progress", seatEvent());
    position.steps = [
      {
        message_index: 1,
        kind: "ai",
        text: "The holdout must be separated by family.",
        truncated: false,
      },
    ];

    expect(councilSeatSummary(position)).toBe(
      "The holdout must be separated by family.",
    );
  });

  it("uses the server-authored display summary instead of a raw terminal contract", () => {
    const chair = task("chair", "completed", seatEvent({ role: "chair" }));
    chair.displaySummary = "The input source still needs an owner decision.";
    chair.result = '{"status":"needs_input"';
    chair.steps = [
      {
        message_index: 4,
        kind: "ai",
        text: '{"status":"needs_input","summary":"raw contract"}',
        truncated: false,
      },
    ];

    expect(councilSeatSummary(chair)).toBe(
      "The input source still needs an owner decision.",
    );
  });
});
