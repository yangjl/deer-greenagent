import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const mutate = rs.fn();
const featureState = { designDeckFeedback: false, progressiveGate: false };
const detailState: { transitions?: Array<Record<string, unknown>> } = {};

rs.mock("@/core/dbtl", () => ({
  CYCLE_STATE_LABELS: { design: "Design" },
  STAGE_LABELS: { build: "Build", design: "Design" },
  STATUS_LABELS: { in_progress: "In progress" },
  activityRevision: () => null,
  derivePathStrip: (transitions: unknown[]) =>
    transitions.length > 0
      ? [
          { stage: "design", attempt: 1, status: "passed", backfilled: false },
          { stage: "build", attempt: 1, status: "current", backfilled: false },
        ]
      : [],
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
  useCreateWorkItem: () => ({
    error: null,
    isPending: false,
    mutate: rs.fn(),
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
      ...(detailState.transitions
        ? { transitions: detailState.transitions }
        : {}),
    },
    error: null,
    isPending: false,
  }),
  useDbtlFeature: () => ({
    feature: {
      design_deck_feedback: featureState.designDeckFeedback,
      progressive_gate: featureState.progressiveGate,
    },
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
import { I18nProvider } from "@/core/i18n/context";

afterEach(() => {
  cleanup();
  mutate.mockReset();
  featureState.designDeckFeedback = false;
  featureState.progressiveGate = false;
  delete detailState.transitions;
});

function renderSheet(stage: "design" | "build" = "design") {
  return render(
    <I18nProvider initialLocale="en-US">
      <CycleStageSheet
        projectId="project-1"
        cycleId="cycle-1"
        stage={stage}
        open
        onOpenChange={rs.fn()}
      />
    </I18nProvider>,
  );
}

describe("CycleStageSheet evidence guidance", () => {
  it("labels required inputs and explains the disabled attach action", () => {
    renderSheet("build");

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
    renderSheet();

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

describe("CycleStageSheet Design read-only review", () => {
  const READ_ONLY =
    "This sheet is for inspection only. Design is submitted and decided in the meeting's registered slide deck.";

  it("offers no submit or verdict control on Design", () => {
    renderSheet();

    expect(screen.getByText(READ_ONLY)).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Submit for review" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Request changes" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
  });

  it("keeps Design inspectable: evidence, blockers, and activity remain", () => {
    renderSheet();

    expect(screen.getByText("Evidence")).toBeTruthy();
    expect(screen.getByText("Open blockers")).toBeTruthy();
    expect(screen.getByText("Activity")).toBeTruthy();
    expect(screen.getByLabelText("Record a blocker")).toBeTruthy();
  });

  it("also points at the deck when the cutover flag is enabled", () => {
    featureState.designDeckFeedback = true;

    renderSheet();

    expect(screen.getByText("Open the feedback deck")).toBeTruthy();
    expect(screen.getByText(READ_ONLY)).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Submit for review" }),
    ).toBeNull();
  });

  it("leaves a non-design stage's submit control in place", () => {
    renderSheet("build");

    expect(
      screen.getByRole("button", { name: "Submit for review" }),
    ).toBeTruthy();
    expect(screen.queryByText(READ_ONLY)).toBeNull();
  });
});

describe("CycleStageSheet path strip", () => {
  const transitions = [
    {
      id: "transition-1",
      seq: 1,
      from_stage: "design",
      chosen_route: "approve",
      to_stage: "build",
      backfilled: false,
    },
  ];

  it("renders no strip while the progressive gate flag is off", () => {
    detailState.transitions = transitions;

    renderSheet();

    expect(screen.queryByLabelText("Cycle path")).toBeNull();
    expect(screen.queryByText("Design 1")).toBeNull();
  });

  it("renders no strip when the flag is on but the payload has no transitions", () => {
    featureState.progressiveGate = true;

    renderSheet();

    expect(screen.queryByLabelText("Cycle path")).toBeNull();
  });

  it("renders the walk and a distinct current head when the flag is on", () => {
    featureState.progressiveGate = true;
    detailState.transitions = transitions;

    renderSheet();

    expect(screen.getByLabelText("Cycle path")).toBeTruthy();
    expect(screen.getByText("Design 1")).toBeTruthy();
    const head = screen.getByText("Build 1");
    expect(head.className).toContain("font-semibold");
    expect(screen.getByText("Path record: transition-1")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Design 1" })).toBeNull();
  });
});
