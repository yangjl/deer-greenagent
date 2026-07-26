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
  research_question: "q",
  objective: "Rank candidate lines",
  success_criteria: "",
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

const draftSetupMock = rs.fn(async () => ({
  enabled: true,
  title: "Genomic selection in maize",
  fields: { "target trait": "grain yield" },
  assumed_fields: ["target trait"],
}));

rs.mock("@/core/dbtl", () => ({
  isLive: () => true,
  outcomeForAction: (action: string) => action,
  useCreateCycle: () => ({ mutateAsync: createCycleMock, isPending: false }),
  useDraftCycleSetup: () => ({ mutateAsync: draftSetupMock }),
  useEvaluateRequest: () => ({ mutateAsync: evaluateMock }),
  useProjectCycles: () => ({ data: { cycles: [] } }),
  useRecordProposalOutcome: () => ({ mutate: rs.fn() }),
}));

import { useDbtlUpgradeProposal } from "@/components/workspace/dbtl/use-upgrade-proposal";

afterEach(cleanup);

describe("useDbtlUpgradeProposal confirm", () => {
  it("returns the durable cycle it created so the caller can kick off Design", async () => {
    // The Design council debate is a cycle-scoped request, so it cannot be sent
    // until a cycle id exists. Discarding the created record here is what left
    // the conversational path unable to start the debate at all.
    const { result } = renderHook(() => useDbtlUpgradeProposal("proj-1"));

    await act(async () => {
      await result.current.evaluate({ text: "design a GS model in maize" });
    });
    await waitFor(() => expect(result.current.evaluation).toBeTruthy());

    const box: { value: { id: string; title: string } | null } = {
      value: null,
    };
    await act(async () => {
      box.value = await result.current.confirm({
        title: "Genomic selection in maize",
        objective: "Rank candidate lines",
        clarification: {},
      });
    });

    expect(createCycleMock).toHaveBeenCalled();
    expect(box.value).toBeTruthy();
    expect(box.value?.id).toBe("cyc-new");
    expect(box.value?.title).toBe("Genomic selection in maize");
  });

  it("drafts from the same request text that raised the proposal", async () => {
    const { result } = renderHook(() => useDbtlUpgradeProposal("proj-1"));

    await act(async () => {
      await result.current.evaluate({ text: "design a GS model in maize" });
    });

    const box: { value: unknown } = { value: null };
    await act(async () => {
      box.value = await result.current.draftSetup(["target trait"]);
    });

    expect(draftSetupMock).toHaveBeenCalledWith({
      text: "design a GS model in maize",
      fields: ["target trait"],
    });
    expect(box.value).toBeTruthy();
  });

  it("does not call the model before a request has been evaluated", async () => {
    // Nothing to ground a draft in yet; asking anyway would invent the record.
    draftSetupMock.mockClear();
    const { result } = renderHook(() => useDbtlUpgradeProposal("proj-1"));

    const box: { value: unknown } = { value: "unset" };
    await act(async () => {
      box.value = await result.current.draftSetup(["target trait"]);
    });

    expect(draftSetupMock).not.toHaveBeenCalled();
    expect(box.value).toBe(null);
  });

  it("returns null when there is no proposal to confirm", async () => {
    const { result } = renderHook(() => useDbtlUpgradeProposal("proj-1"));

    const box: { value: unknown } = { value: created };
    await act(async () => {
      box.value = await result.current.confirm({
        title: "x",
        objective: "y",
        clarification: {},
      });
    });

    // No kickoff may be attempted for a cycle that was never created.
    expect(box.value).toBe(null);
  });
});
