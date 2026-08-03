import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, renderHook } from "@testing-library/react";

const evaluation = {
  evaluation_id: "eval-1",
  route: "cycle_setup",
  proposals_visible: true,
  proposal: {
    evaluation_id: "eval-1",
    kind: "cycle_setup",
    proposed_objective: "Rank candidate lines",
    missing_fields: [],
    band: "low",
    confidence: 0,
    project_name: "test1",
    cycle_id: null,
    creates_record: true,
    requires_confirmation: true,
    notice: "",
  },
};

const evaluateMock = rs.fn(async () => evaluation);
const outcomeMock = rs.fn();

rs.mock("@/core/dbtl", () => ({
  outcomeForAction: (action: string) => action,
  useEvaluateRequest: () => ({ mutateAsync: evaluateMock }),
  useRecordProposalOutcome: () => ({ mutate: outcomeMock }),
}));

import { useDbtlUpgradeProposal } from "@/components/workspace/dbtl/use-upgrade-proposal";

afterEach(() => {
  cleanup();
  evaluateMock.mockClear();
  outcomeMock.mockClear();
});

describe("useDbtlUpgradeProposal native confirmation", () => {
  it("forwards the pre-send first-turn state to shadow telemetry", async () => {
    const { result } = renderHook(() => useDbtlUpgradeProposal("proj-1"));

    await act(async () => {
      await result.current.evaluate({
        text: "evaluate the trial",
        threadId: "thread-1",
        isNewConversation: true,
      });
    });

    expect(evaluateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        text: "evaluate the trial",
        threadId: "thread-1",
        isNewConversation: true,
      }),
    );
  });

  it("records dismissal choices without rendering a custom proposal card", async () => {
    const { result } = renderHook(() => useDbtlUpgradeProposal("proj-1"));
    await act(async () => {
      await result.current.evaluate({ text: "design a GS model in maize" });
    });

    act(() => result.current.recordOutcome("keep_ordinary"));

    expect(outcomeMock).toHaveBeenCalledWith(
      { evaluationId: "eval-1", outcome: "keep_ordinary" },
      expect.anything(),
    );
  });
});
