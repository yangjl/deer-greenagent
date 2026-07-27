import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

const created = {
  id: "cyc-new",
  project_id: "proj-1",
  parent_cycle_id: null,
  title: "Genomic selection in maize",
  cycle_class: "computational",
  cycle_weight: "full",
  state: "design",
  db_revision: 1,
  research_question: "Rank candidate lines",
  objective: "Rank candidate lines",
  success_criteria: "Use held-out validation.",
  created_by: "user-1",
  created_at: "2026-07-26T00:00:00Z",
  updated_at: "2026-07-26T00:00:00Z",
  stages: [],
};

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

const createCycleMock = rs.fn(async () => created);
const evaluateMock = rs.fn(async () => evaluation);
const outcomeMock = rs.fn();

rs.mock("@/core/dbtl", () => ({
  isLive: () => true,
  outcomeForAction: (action: string) => action,
  useCreateCycle: () => ({ mutateAsync: createCycleMock, isPending: false }),
  useEvaluateRequest: () => ({ mutateAsync: evaluateMock }),
  useProjectCycles: () => ({ data: { cycles: [] } }),
  useRecordProposalOutcome: () => ({ mutate: outcomeMock }),
}));

import { useDbtlUpgradeProposal } from "@/components/workspace/dbtl/use-upgrade-proposal";
import type { CycleRecord } from "@/core/dbtl";

afterEach(() => {
  cleanup();
  createCycleMock.mockClear();
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

  it("creates from server-owned card metadata and returns the durable cycle", async () => {
    const { result } = renderHook(() => useDbtlUpgradeProposal("proj-1"));

    await act(async () => {
      await result.current.evaluate({ text: "design a GS model in maize" });
    });
    await waitFor(() => expect(evaluateMock).toHaveBeenCalled());

    let cycle: CycleRecord | null = null;
    await act(async () => {
      cycle = await result.current.createFromNativeSetup(
        {
          title: "Genomic selection in maize",
          objective: "Rank candidate lines",
          success_criteria: "Use held-out validation.",
        },
        "dbtl-setup-confirm:abc123",
      );
    });

    expect(createCycleMock).toHaveBeenCalledWith({
      title: "Genomic selection in maize",
      cycleClass: "computational",
      cycleWeight: "full",
      researchQuestion: "Rank candidate lines",
      objective: "Rank candidate lines",
      successCriteria: "Use held-out validation.",
      parentCycleId: null,
      idempotencyKey: "cycle-dbtl-setup-confirm:abc123",
    });
    expect(cycle).toEqual(created);
    expect(outcomeMock).toHaveBeenCalledWith(
      { evaluationId: "eval-1", outcome: "start_setup" },
      expect.anything(),
    );
  });

  it("does not create a cycle outside a project", async () => {
    const { result } = renderHook(() => useDbtlUpgradeProposal(null));

    let cycle: CycleRecord | null = created as CycleRecord;
    await act(async () => {
      cycle = await result.current.createFromNativeSetup(
        {
          title: "Genomic selection",
          objective: "Rank lines",
          success_criteria: "",
        },
        "request-1",
      );
    });

    expect(createCycleMock).not.toHaveBeenCalled();
    expect(cycle).toBeNull();
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
