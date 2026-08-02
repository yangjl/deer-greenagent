/**
 * Pure view logic for durable DBTL cycles.
 *
 * Nothing here reads storage or the network. That matters for the Phase 3
 * no-go — "localStorage can override durable cycle state" — because it makes
 * the source of every rendered status a function argument rather than an
 * ambient read, so a stray browser-local value has nowhere to enter.
 */

export const DBTL_STAGES = [
  "design",
  "reconciliation",
  "build",
  "test",
  "learn",
] as const;

export type DbtlStage = (typeof DBTL_STAGES)[number];

export type StageStatus =
  | "locked"
  | "in_progress"
  | "awaiting_review"
  | "changes_requested"
  | "approved"
  | "rejected";

export type CycleState =
  | "design"
  | "reconciliation"
  | "ready_for_build"
  | "build"
  | "test"
  | "learn"
  | "completed"
  | "abandoned";

export type CycleClass = "season/program" | "computational" | "other";
export type CycleWeight = "full" | "light" | "retroactive";

export type ReviewDecision = "approve" | "request_changes" | "reject";

export interface StageRecord {
  id: string;
  stage: DbtlStage;
  status: StageStatus;
  attempt_number: number;
  db_revision: number;
  updated_at: string;
}

export interface CycleArtifact {
  id: string;
  stage_attempt_id: string;
  artifact_type: string;
  revision: number;
  content_hash: string;
  uri: string;
  created_by: string;
  created_at: string;
}

export interface CycleWorkItem {
  id: string;
  title: string;
  status: "open" | "resolved";
  payload: { kind?: string; resolution?: string };
  db_revision: number;
  created_at: string;
}

/** The four stages the progressive gate's transition graph walks. */
export type DbtlGraphStage = "design" | "build" | "test" | "learn";

/**
 * One recorded routing decision on a cycle's stage graph.
 *
 * Served on the cycle detail payload only when the backend's
 * `dbtl.progressive_gate` flag is on. Rows are an ordered walk (`seq` is
 * 1-based); `backfilled` marks a synthetic row reconstructed by migration
 * rather than clicked by a person.
 */
export interface DbtlStageTransition {
  id: string;
  cycle_id: string;
  seq: number;
  from_stage: DbtlGraphStage;
  from_attempt: number | null;
  stage_attempt_id: string | null;
  chosen_route: string;
  to_stage: string;
  assessed_difficulty: string | null;
  assessment_rationale: string | null;
  human_override: string | null;
  offered_routes: string[] | null;
  decided_by: string;
  decision_surface_id: string | null;
  /** The conversation the deciding deck answers, server-joined from the
   * registered surface; null when the decision was taken off-deck. */
  decided_in_thread_id?: string | null;
  evidence_hash: string | null;
  dataset_fingerprint: string | null;
  stage_spec_version: string | null;
  policy_version: string | null;
  backfilled: boolean;
  decided_at: string;
}

export interface CycleTransitionGate {
  stage: DbtlGraphStage;
  assessment: {
    difficulty: "routine" | "standard" | "high_stakes";
    rationale: string;
    source: string;
  };
  routes: Array<{
    slug: string;
    to_stage: string;
    label: string;
    value: string;
    blocked?: boolean;
    blocked_reason?: string;
  }>;
  surface_id: string;
  deck_uri: string;
  originating_thread_id: string;
  parked: boolean;
}

export interface CycleRecord {
  id: string;
  project_id: string;
  parent_cycle_id: string | null;
  originating_thread_id?: string | null;
  title: string;
  cycle_class: CycleClass;
  cycle_weight: CycleWeight;
  state: CycleState;
  db_revision: number;
  research_question: string;
  objective: string;
  success_criteria: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  stages: StageRecord[];
  artifacts?: CycleArtifact[];
  work_items?: CycleWorkItem[];
  /** Present only when the backend's `dbtl.progressive_gate` flag is on. */
  transitions?: DbtlStageTransition[];
  transition_gate?: CycleTransitionGate;
  parked?: boolean;
  parked_stage?: string | null;
}

export interface ActivityEvent {
  id: string;
  sequence: number;
  event_type: string;
  actor_user_id: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export const STAGE_LABELS: Record<DbtlStage, string> = {
  design: "Design",
  reconciliation: "Data reconciliation",
  build: "Build",
  test: "Test",
  learn: "Learn",
};

/**
 * Status wording. Never conveyed by colour alone (shared interaction rule),
 * so every state has a word a screen reader can announce.
 */
export const STATUS_LABELS: Record<StageStatus, string> = {
  locked: "Locked",
  in_progress: "In progress",
  awaiting_review: "Awaiting review",
  changes_requested: "Changes requested",
  approved: "Approved",
  rejected: "Rejected",
};

export const CYCLE_STATE_LABELS: Record<CycleState, string> = {
  design: "Design",
  reconciliation: "Data reconciliation",
  ready_for_build: "Ready for build",
  build: "Build",
  test: "Test",
  learn: "Learn",
  completed: "Completed",
  abandoned: "Abandoned",
};

export const CYCLE_CLASS_LABELS: Record<CycleClass, string> = {
  "season/program": "Season / program",
  computational: "Computational",
  other: "Other",
};

export const CYCLE_WEIGHT_LABELS: Record<CycleWeight, string> = {
  full: "Full",
  light: "Light",
  retroactive: "Retroactive",
};

const TERMINAL_STATES: ReadonlySet<CycleState> = new Set([
  "completed",
  "abandoned",
]);

export function isTerminal(state: CycleState): boolean {
  return TERMINAL_STATES.has(state);
}

/** A cycle counts as live while it can still move. */
export function isLive(cycle: CycleRecord): boolean {
  return !isTerminal(cycle.state);
}

/** One-open-at-a-time disclosure behavior for cycle rows in the project rail. */
export function toggleCycleDisclosure(
  expandedCycleId: string | null,
  clickedCycleId: string,
): string | null {
  return expandedCycleId === clickedCycleId ? null : clickedCycleId;
}

export function stageOf(
  cycle: CycleRecord | null | undefined,
  stage: DbtlStage,
): StageRecord | null {
  return cycle?.stages.find((item) => item.stage === stage) ?? null;
}

/**
 * Whether the reviewer can act on this stage right now.
 *
 * Derived from the server's status alone — the client never decides that a
 * gate is open, it only renders what the durable record already says.
 */
export function canSubmitStage(status: StageStatus | undefined): boolean {
  return status === "in_progress" || status === "changes_requested";
}

export function canReviewStage(status: StageStatus | undefined): boolean {
  return status === "awaiting_review";
}

/** A review needs a rationale, whichever way it goes. */
export function canSubmitReview(rationale: string): boolean {
  return rationale.trim().length > 0;
}

export interface ActionReadiness {
  ready: boolean;
  message: string;
}

function readableList(items: string[]): string {
  if (items.length < 2) return items[0] ?? "";
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items.at(-1)}`;
}

/**
 * Names the next evidence action without weakening native disabled controls.
 * A non-empty but malformed hash is called out separately from blank fields.
 */
export function artifactAttachmentReadiness(
  artifactType: string,
  artifactUri: string,
  artifactHash: string,
): ActionReadiness {
  const missing = [
    !artifactType.trim() && "artifact type",
    !artifactUri.trim() && "artifact URI",
    !artifactHash && "SHA-256",
  ].filter((item): item is string => Boolean(item));

  if (missing.length > 0) {
    return {
      ready: false,
      message: `Add ${readableList(missing)} to continue.`,
    };
  }
  if (!/^[0-9a-f]{64}$/.test(artifactHash)) {
    return {
      ready: false,
      message: "Enter a valid 64-character SHA-256 to continue.",
    };
  }
  return {
    ready: true,
    message: "Ready to attach this evidence.",
  };
}

export function reviewSubmissionReadiness(
  artifactCount: number,
): ActionReadiness {
  return artifactCount > 0
    ? {
        ready: true,
        message: "Evidence attached. Ready to submit this stage for review.",
      }
    : {
        ready: false,
        message:
          "Attach at least one evidence file above to enable review submission.",
      };
}

export interface StageBlockReason {
  blocked: boolean;
  reason: string;
}

/** Why a locked stage is locked, in the reviewer's own vocabulary. */
export function stageBlockReason(
  cycle: CycleRecord | null | undefined,
  stage: DbtlStage,
  /** Whether this deployment gates Build on a settled reconciliation matrix. */
  reconciliationRequired = true,
): StageBlockReason {
  const record = stageOf(cycle, stage);
  if (!cycle || !record) {
    return { blocked: true, reason: "This cycle has no such stage." };
  }
  if (record.status !== "locked") {
    return { blocked: false, reason: "" };
  }
  if (stage === "build") {
    // Which stages Build waits for is a deployment rule, not a constant. A
    // project that does not gate Build on Data reconciliation leaves that
    // stage locked forever, and naming it here told people to go and approve
    // a stage nothing was waiting for. The rule has to be passed in: stage
    // statuses alone cannot tell "locked because skipped" from "locked
    // because not reached yet", since both look identical here.
    const priors = reconciliationRequired
      ? (["design", "reconciliation"] as const)
      : (["design"] as const);
    const outstanding = priors
      .filter((prior) => stageOf(cycle, prior)?.status !== "approved")
      .map((prior) => STAGE_LABELS[prior]);
    return {
      blocked: true,
      reason: outstanding.length
        ? `Build opens once ${outstanding.join(" and ")} ${outstanding.length === 1 ? "is" : "are"} approved.`
        : "Build is not open yet.",
    };
  }
  const index = DBTL_STAGES.indexOf(stage);
  const previous = DBTL_STAGES[index - 1];
  return {
    blocked: true,
    reason: previous
      ? `${STAGE_LABELS[stage]} opens once ${STAGE_LABELS[previous]} is approved.`
      : `${STAGE_LABELS[stage]} is not open yet.`,
  };
}

/** Consequence text shown next to each verdict before it is committed. */
export function decisionConsequence(decision: ReviewDecision): string {
  switch (decision) {
    case "approve":
      return "Records an approval against this evidence revision and opens the next stage.";
    case "request_changes":
      return "Returns the stage to work. Nothing is lost; a new revision can be submitted.";
    default:
      return "Rejects this stage. The cycle stays where it is and the record keeps the rationale.";
  }
}

/** Human summary of one activity event for the timeline. */
export function describeActivity(event: ActivityEvent): string {
  const payload = event.payload as {
    stage?: string;
    decision?: string;
    title?: string;
    artifact_type?: string;
    revision?: number;
  };
  switch (event.event_type) {
    case "cycle.created":
      return "Opened the cycle";
    case "artifact.attached":
      return `Attached ${payload.artifact_type ?? "an artifact"} revision ${payload.revision ?? 1}`;
    case "stage.submitted":
      return `Submitted ${payload.stage ?? "a stage"} for review`;
    case "stage.reviewed":
      return `Reviewed ${payload.stage ?? "a stage"}: ${payload.decision ?? "decided"}`;
    case "work_item.opened":
      return `Opened a blocker: ${payload.title ?? ""}`.trim();
    case "work_item.resolved":
      return "Resolved a blocker";
    default:
      return event.event_type;
  }
}

/**
 * The revision an event committed at, as display text.
 *
 * The payload is untyped JSON from the server, so this narrows rather than
 * stringifying blindly — an unexpected shape reads as "—", not
 * "[object Object]".
 */
export function activityRevision(event: ActivityEvent): string {
  const value = event.payload.db_revision;
  return typeof value === "number" || typeof value === "string"
    ? String(value)
    : "—";
}

/** Open blockers gate nothing automatically, but the reviewer must see them. */
export function openWorkItems(cycle: CycleRecord | null | undefined) {
  return (cycle?.work_items ?? []).filter((item) => item.status === "open");
}

/** The newest artifact revision per type, which is what a review binds to. */
export function latestArtifacts(
  cycle: CycleRecord | null | undefined,
): CycleArtifact[] {
  const newest = new Map<string, CycleArtifact>();
  for (const artifact of cycle?.artifacts ?? []) {
    const current = newest.get(artifact.artifact_type);
    if (!current || artifact.revision > current.revision) {
      newest.set(artifact.artifact_type, artifact);
    }
  }
  return [...newest.values()].sort((left, right) =>
    left.artifact_type.localeCompare(right.artifact_type),
  );
}

/** The selected stage's newest evidence only; other stages are not review input. */
export function latestArtifactsForStage(
  cycle: CycleRecord | null | undefined,
  stage: DbtlStage,
): CycleArtifact[] {
  const stageAttemptId = stageOf(cycle, stage)?.id;
  if (!stageAttemptId) {
    return [];
  }
  return latestArtifacts({
    ...cycle!,
    artifacts: (cycle?.artifacts ?? []).filter(
      (artifact) => artifact.stage_attempt_id === stageAttemptId,
    ),
  });
}

/** The cycle the rail should select by default: the live one, else the newest. */
export function defaultSelectedCycle(
  cycles: readonly CycleRecord[],
): CycleRecord | null {
  return cycles.find(isLive) ?? cycles[cycles.length - 1] ?? null;
}
