import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const mutate = rs.fn();
const featureState = { designDeckFeedback: false, progressiveGate: false };
const timelineState = { parked: false, invalidated: false };
const detailState: {
  transitions?: Array<Record<string, unknown>>;
  transitionGate?: Record<string, unknown>;
} = {};

const emptyEntry = {
  assessedDifficulty: null,
  assessmentRationale: null,
  humanOverride: null,
  overrodeAssessment: false,
  offeredRoutes: [],
  decidedAt: null,
  decisionSurfaceId: null,
  decidedInThreadId: null,
  evidenceHash: null,
  backfilled: false,
};

rs.mock("@/core/dbtl", () => ({
  CYCLE_STATE_LABELS: { design: "Design" },
  STAGE_LABELS: { build: "Build", design: "Design" },
  STATUS_LABELS: { in_progress: "In progress" },
  activityRevision: () => null,
  deriveCycleTimeline: (transitions: unknown[]) => ({
    entries:
      transitions.length > 0
        ? [
            {
              ...emptyEntry,
              stage: "design",
              round: 1,
              status: timelineState.invalidated ? "invalidated" : "passed",
              chosenRoute: "approve",
              recordId: "transition-1",
              decidedBy: "user-1",
              decisionSurfaceId: "surface-1",
              decidedInThreadId: "thread-7",
            },
            {
              ...emptyEntry,
              stage: "build",
              round: 1,
              status: "current",
              chosenRoute: null,
              recordId: null,
              decidedBy: null,
            },
          ]
        : [],
    parked: timelineState.parked,
    parkedStage: timelineState.parked ? "design" : null,
    hasInvalidatedWork: timelineState.invalidated,
  }),
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
      ...(detailState.transitionGate
        ? { transition_gate: detailState.transitionGate }
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
  timelineState.parked = false;
  timelineState.invalidated = false;
  delete detailState.transitions;
  delete detailState.transitionGate;
});

function renderSheet(stage: "design" | "build" = "design") {
  return render(
    <I18nProvider initialLocale="en-US">
      <CycleStageSheet
        projectId="project-1"
        projectSlug="drought"
        cycleId="cycle-1"
        stage={stage}
        open
        onOpenChange={rs.fn()}
      />
    </I18nProvider>,
  );
}

describe("CycleStageSheet evidence guidance", () => {
  it("keeps the evidence inspector read-only and points decisions to chat", () => {
    renderSheet("build");

    expect(screen.queryByLabelText(/Artifact type/)).toBeNull();
    expect(screen.queryByLabelText(/Workspace file path/)).toBeNull();
    expect(screen.queryByLabelText(/SHA-256/)).toBeNull();
    expect(screen.getByText("Continue in chat")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Attach evidence" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Submit for review" }),
    ).toBeNull();
  });
});

describe("CycleStageSheet Design read-only review", () => {
  const READ_ONLY = "Continue in chat";

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
    expect(screen.queryByLabelText("Record a blocker")).toBeNull();
  });

  it("also points at the deck when the cutover flag is enabled", () => {
    featureState.designDeckFeedback = true;

    renderSheet();

    expect(screen.getByText(READ_ONLY)).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Submit for review" }),
    ).toBeNull();
  });

  it("shows the progressive assessment, legal routes, and parked state", () => {
    featureState.designDeckFeedback = true;
    featureState.progressiveGate = true;
    detailState.transitionGate = {
      stage: "design",
      assessment: {
        difficulty: "high_stakes",
        rationale: "The field intervention cannot be reversed.",
        source: "model",
      },
      routes: [
        {
          slug: "advance",
          to_stage: "build",
          label: "Continue to Build",
          value: "Open Build.",
        },
        {
          slug: "park",
          to_stage: "design",
          label: "Park — work with the lead agent",
          value: "Keep Design open.",
        },
      ],
      surface_id: "surface-1",
      deck_uri: "/mnt/user-data/outputs/design.html",
      originating_thread_id: "thread-1",
      parked: true,
    };

    renderSheet();

    expect(screen.getByText(/Agent assessment:/)).toBeTruthy();
    expect(
      screen.getByText("The field intervention cannot be reversed."),
    ).toBeTruthy();
    expect(screen.getByText("Continue to Build")).toBeTruthy();
    expect(screen.getByText("Park — work with the lead agent")).toBeTruthy();
    expect(screen.getByText(/Parked — ordinary requests/)).toBeTruthy();
  });

  it("keeps a non-design stage read-only too", () => {
    renderSheet("build");

    expect(
      screen.queryByRole("button", { name: "Submit for review" }),
    ).toBeNull();
    expect(screen.getByText(READ_ONLY)).toBeTruthy();
  });
});

describe("CycleStageSheet cycle timeline", () => {
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

  it("renders no timeline while the progressive gate flag is off", () => {
    detailState.transitions = transitions;

    renderSheet();

    expect(screen.queryByLabelText("Cycle path")).toBeNull();
    expect(screen.queryByText("Design 1")).toBeNull();
  });

  it("renders no timeline when the flag is on but the payload has no transitions", () => {
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
  });

  it("keeps the audit detail behind an explicit disclosure", () => {
    featureState.progressiveGate = true;
    detailState.transitions = transitions;

    renderSheet();

    expect(screen.queryByText("transition-1")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Show how each step was decided" }),
    );

    expect(screen.getByText("transition-1")).toBeTruthy();
    expect(screen.getByText("user-1")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Hide decision detail" }),
    ).toBeTruthy();
  });

  it("links a deck-decided edge to the conversation its deck answers", () => {
    featureState.progressiveGate = true;
    detailState.transitions = transitions;

    renderSheet();
    fireEvent.click(
      screen.getByRole("button", { name: "Show how each step was decided" }),
    );

    const link = screen.getByRole("link", {
      name: "Open the deck this was decided on",
    });
    expect(link.getAttribute("href")).toContain("thread-7");
    expect(link.getAttribute("href")).toContain("drought");
  });

  it("states a parked cycle and invalidated work in words", () => {
    featureState.progressiveGate = true;
    detailState.transitions = transitions;
    timelineState.parked = true;
    timelineState.invalidated = true;

    renderSheet();

    expect(screen.getByText("This cycle is parked.")).toBeTruthy();
    expect(
      screen.getByText(
        "Work approved before Design was reopened no longer counts.",
      ),
    ).toBeTruthy();
  });
});
