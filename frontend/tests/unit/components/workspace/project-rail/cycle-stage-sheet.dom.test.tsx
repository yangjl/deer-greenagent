import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const mutate = rs.fn();

rs.mock("@/core/dbtl", () => ({
  CYCLE_STATE_LABELS: { design: "Design" },
  STAGE_LABELS: { design: "Design" },
  STATUS_LABELS: { in_progress: "In progress" },
  activityRevision: () => null,
  artifactAttachmentReadiness: (
    artifactType: string,
    artifactUri: string,
    artifactHash: string,
  ) => {
    if (!artifactType.trim() || !artifactUri.trim() || !artifactHash) {
      return {
        ready: false,
        message: "Add artifact URI and SHA-256 to continue.",
      };
    }
    if (!/^[0-9a-f]{64}$/.test(artifactHash)) {
      return {
        ready: false,
        message: "Enter a valid 64-character SHA-256 to continue.",
      };
    }
    return { ready: true, message: "Ready to attach this evidence." };
  },
  canReviewStage: () => false,
  canSubmitReview: () => false,
  canSubmitStage: () => true,
  decisionConsequence: () => "",
  describeActivity: () => "",
  latestArtifactsForStage: () => [],
  openWorkItems: () => [],
  reviewSubmissionReadiness: () => ({
    ready: false,
    message:
      "Attach at least one evidence file above to enable review submission.",
  }),
  stageBlockReason: () => ({ blocked: false, reason: "" }),
  stageOf: (cycle: { stages: unknown[] }) => cycle.stages[0],
  useAttachArtifact: () => ({
    error: null,
    isPending: false,
    mutate,
  }),
  useBuildTest: () => ({
    data: null,
    error: null,
    isPending: false,
  }),
  useCycleActivity: () => ({ data: [] }),
  useCycleDetail: () => ({
    data: {
      id: "cycle-1",
      title: "Data check",
      state: "design",
      db_revision: 1,
      research_question: "Check the infrastructure.",
      stages: [{ stage: "design", status: "in_progress" }],
    },
    error: null,
    isPending: false,
  }),
  useResolveWorkItem: () => ({
    error: null,
    isPending: false,
    mutate: rs.fn(),
  }),
  useReviewStage: () => ({
    error: null,
    isPending: false,
    mutate: rs.fn(),
  }),
  useSubmitStage: () => ({
    error: null,
    isPending: false,
    mutate: rs.fn(),
  }),
}));

import { CycleStageSheet } from "@/components/workspace/project-rail/cycle-stage-sheet";

afterEach(() => {
  cleanup();
  mutate.mockReset();
});

describe("CycleStageSheet evidence guidance", () => {
  it("labels required inputs and explains both disabled actions", () => {
    render(
      <CycleStageSheet
        projectId="project-1"
        cycleId="cycle-1"
        stage="design"
        open
        onOpenChange={rs.fn()}
      />,
    );

    expect(screen.getByLabelText(/Artifact type/)).toBeTruthy();
    expect(screen.getByLabelText(/Workspace file path/)).toBeTruthy();
    expect(screen.getByLabelText(/SHA-256/)).toBeTruthy();

    expect(
      screen.getByText("Add artifact URI and SHA-256 to continue."),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Attach at least one evidence file above to enable review submission.",
      ),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "Attach evidence" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(
      screen
        .getByRole("button", { name: "Submit for review" })
        .hasAttribute("disabled"),
    ).toBe(true);
  });

  it("enables evidence attachment after a valid path and hash are entered", () => {
    render(
      <CycleStageSheet
        projectId="project-1"
        cycleId="cycle-1"
        stage="design"
        open
        onOpenChange={rs.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Workspace file path/), {
      target: { value: "/mnt/user-data/workspace/design.json" },
    });
    fireEvent.change(screen.getByLabelText(/SHA-256/), {
      target: { value: "a".repeat(64) },
    });

    expect(screen.getByText("Ready to attach this evidence.")).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "Attach evidence" })
        .hasAttribute("disabled"),
    ).toBe(false);
  });
});
