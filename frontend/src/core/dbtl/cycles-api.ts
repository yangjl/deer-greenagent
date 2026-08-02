import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { BuildWorkflowView } from "./build-plan-view";
import type {
  ActivityEvent,
  CycleClass,
  CycleRecord,
  CycleWeight,
  DbtlGraphStage,
  DbtlStage,
  ReviewDecision,
} from "./cycle-view";

export interface CycleListResponse {
  project_id: string;
  stages: DbtlStage[];
  cycles: CycleRecord[];
}

export type DesignFeedbackActionKind =
  | "chair_option"
  | "chair_text"
  | "submit_for_review"
  | "approve"
  | "request_changes"
  | "reject"
  | "advance"
  | "park"
  | "convene_review_meeting"
  | "choose_route"
  | "recommend_promotion"
  | "close_without_candidate";

export type TransitionDifficulty = "routine" | "standard" | "high_stakes";

export interface TransitionGate {
  stage: DbtlGraphStage;
  assessment: {
    difficulty: TransitionDifficulty;
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
}

export interface MeetingGate {
  stage: Exclude<DbtlGraphStage, "design">;
  assessed_difficulty: TransitionDifficulty;
  effective_difficulty: TransitionDifficulty;
  requirement: "skipped" | "optional" | "required" | "complete";
  transition_routes_locked: boolean;
  can_convene: boolean;
}

export interface DesignFeedbackSurface {
  surface_id: string;
  project_id: string;
  cycle_id: string;
  stage: "design" | "build" | "test" | "learn";
  surface_revision: number;
  lifecycle_state: "open" | "consumed" | "superseded";
  originating_thread_id: string;
  mode: "chair_feedback" | "stage_review" | "read_only";
  deck_content_hash: string;
  deck_schema_version: string;
  evidence_artifact_id: string | null;
  evidence_artifact_revision: number | null;
  evidence_content_hash: string | null;
  is_current: boolean;
  newest_surface_id: string | null;
  newest_surface_uri: string | null;
  allowed_actions: DesignFeedbackActionKind[];
  interactive: boolean;
  current_db_revision: number | null;
  current_stage_status: string | null;
  receipt: {
    client_submission_id?: string;
    expected_db_revision?: number;
    status: string;
    selected_card_ids?: string[];
    human_comment?: string | null;
    receipt?: {
      message?: string;
      run_id?: string;
      db_revision?: number;
      handoff_status?: "started" | "failed" | "retrying" | "not_needed";
    };
  } | null;
  note: string;
  transition_gate: TransitionGate | null;
  meeting_gate: MeetingGate | null;
  parked: boolean;
}

async function parseError(response: Response, fallback: string) {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
  } | null;
  return body?.detail ?? `${fallback}: ${response.statusText}`;
}

export class DbtlRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "DbtlRequestError";
  }
}

function base(projectId: string) {
  return `${getBackendBaseURL()}/api/projects/${encodeURIComponent(projectId)}/dbtl`;
}

async function post<T>(
  url: string,
  body: unknown,
  fallback: string,
): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new DbtlRequestError(
      await parseError(response, fallback),
      response.status,
    );
  }
  return (await response.json()) as T;
}

export async function fetchCycles(
  projectId: string,
): Promise<CycleListResponse> {
  const response = await fetch(`${base(projectId)}/cycles`);
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load cycles"));
  }
  return (await response.json()) as CycleListResponse;
}

export async function fetchCycle(
  projectId: string,
  cycleId: string,
): Promise<CycleRecord> {
  const response = await fetch(
    `${base(projectId)}/cycles/${encodeURIComponent(cycleId)}`,
  );
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load cycle"));
  }
  return (await response.json()) as CycleRecord;
}

export async function fetchCycleActivity(
  projectId: string,
  cycleId: string,
): Promise<ActivityEvent[]> {
  const response = await fetch(
    `${base(projectId)}/cycles/${encodeURIComponent(cycleId)}/activity`,
  );
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load activity"));
  }
  return ((await response.json()) as { events: ActivityEvent[] }).events;
}

export async function fetchDesignFeedbackSurface(input: {
  projectId: string;
  surfaceId: string;
  viewerThreadId: string;
}): Promise<DesignFeedbackSurface> {
  const params = new URLSearchParams({
    viewer_thread_id: input.viewerThreadId,
  });
  const response = await fetch(
    `${base(input.projectId)}/stage-feedback/${encodeURIComponent(input.surfaceId)}?${params}`,
  );
  if (!response.ok) {
    throw new DbtlRequestError(
      await parseError(response, "Could not verify this stage feedback deck"),
      response.status,
    );
  }
  return (await response.json()) as DesignFeedbackSurface;
}

export async function applyDesignFeedbackAction(input: {
  projectId: string;
  surface: DesignFeedbackSurface;
  viewerThreadId: string;
  action: {
    kind: DesignFeedbackActionKind;
    optionIds: string[];
    difficultyOverride?: TransitionDifficulty | null;
  };
  comment: string;
  clientSubmissionId: string;
}) {
  const retryingRecordedHandoff =
    input.surface.receipt?.status === "handoff_failed";
  return post<{
    status: string;
    run_id?: string | null;
    receipt?: { message?: string; run_id?: string; db_revision?: number };
    replayed: boolean;
  }>(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.surface.cycle_id)}/stage-feedback/${encodeURIComponent(input.surface.surface_id)}/actions`,
    {
      version: 1,
      action: {
        kind: input.action.kind,
        option_ids: input.action.optionIds,
        difficulty_override: input.action.difficultyOverride ?? null,
      },
      comment: input.comment,
      client_submission_id: input.clientSubmissionId,
      originating_thread_id: input.viewerThreadId,
      expected_db_revision: retryingRecordedHandoff
        ? (input.surface.receipt?.expected_db_revision ??
          input.surface.current_db_revision)
        : input.surface.current_db_revision,
      expected_evidence: input.surface.evidence_artifact_id
        ? {
            artifact_id: input.surface.evidence_artifact_id,
            revision: input.surface.evidence_artifact_revision,
            content_hash: input.surface.evidence_content_hash,
          }
        : null,
      expected_deck_hash: input.surface.deck_content_hash,
    },
    "Could not record the stage feedback",
  );
}

export interface CreateCycleInput {
  projectId: string;
  title: string;
  cycleClass: CycleClass;
  cycleWeight: CycleWeight;
  researchQuestion: string;
  objective?: string;
  successCriteria?: string;
  parentCycleId?: string | null;
  /** Makes a retried submit resolve to the same durable record. */
  idempotencyKey: string;
}

export async function createCycle(
  input: CreateCycleInput,
): Promise<CycleRecord> {
  return post<CycleRecord>(
    `${base(input.projectId)}/cycles`,
    {
      title: input.title,
      cycle_class: input.cycleClass,
      cycle_weight: input.cycleWeight,
      research_question: input.researchQuestion,
      objective: input.objective ?? "",
      success_criteria: input.successCriteria ?? "",
      parent_cycle_id: input.parentCycleId ?? null,
      idempotency_key: input.idempotencyKey,
    },
    "Could not start the cycle",
  );
}

export async function abandonCycle(input: {
  projectId: string;
  cycleId: string;
  rationale: string;
  expectedDbRevision: number;
  idempotencyKey: string;
}): Promise<CycleRecord> {
  return post<CycleRecord>(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/abandon`,
    {
      rationale: input.rationale,
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not remove the cycle",
  );
}

export async function submitStage(input: {
  projectId: string;
  cycleId: string;
  stage: DbtlStage;
  expectedDbRevision: number;
  idempotencyKey: string;
}): Promise<CycleRecord> {
  return post<CycleRecord>(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/stages/${input.stage}/submit`,
    {
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not submit the stage",
  );
}

export async function reviewStage(input: {
  projectId: string;
  cycleId: string;
  stage: DbtlStage;
  decision: ReviewDecision;
  /**
   * Optional server-side, except for `reject`, which is refused without one.
   * A blank rationale is filled by a labelled server projection rather than
   * being invented, so a caller may honestly leave it empty.
   */
  rationale?: string;
  expectedDbRevision: number;
  idempotencyKey: string;
}): Promise<CycleRecord> {
  return post<CycleRecord>(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/stages/${input.stage}/review`,
    {
      decision: input.decision,
      rationale: input.rationale ?? "",
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not record the review",
  );
}

export async function attachArtifact(input: {
  projectId: string;
  cycleId: string;
  stage: DbtlStage;
  artifactType: string;
  uri: string;
  contentHash: string;
  expectedDbRevision: number;
  idempotencyKey: string;
}) {
  return post(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/artifacts`,
    {
      stage: input.stage,
      artifact_type: input.artifactType,
      uri: input.uri,
      content_hash: input.contentHash,
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not attach the artifact",
  );
}

export async function createWorkItem(input: {
  projectId: string;
  cycleId: string;
  title: string;
  kind: "blocker" | "task" | "question";
  expectedDbRevision: number;
  idempotencyKey: string;
}) {
  return post(
    `${base(input.projectId)}/cycles/${encodeURIComponent(input.cycleId)}/work-items`,
    {
      title: input.title,
      kind: input.kind,
      expected_db_revision: input.expectedDbRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not record the blocker",
  );
}

export async function resolveWorkItem(input: {
  projectId: string;
  workItemId: string;
  resolution: string;
  expectedDbRevision: number;
  expectedWorkItemRevision: number;
  idempotencyKey: string;
}) {
  return post(
    `${base(input.projectId)}/work-items/${encodeURIComponent(input.workItemId)}/resolve`,
    {
      resolution: input.resolution,
      expected_db_revision: input.expectedDbRevision,
      expected_work_item_revision: input.expectedWorkItemRevision,
      idempotency_key: input.idempotencyKey,
    },
    "Could not resolve the blocker",
  );
}

/**
 * The Build workflow's ordered steps, its recorded plan, and what is still
 * valid. Served in every mode, including with the rollout switch off — the
 * flag governs whether the workflow drives execution, and a read model that
 * disappeared with it could not tell an owner why their Build looks the way it
 * does.
 */
export async function fetchStageWorkflow(
  projectId: string,
  cycleId: string,
  stage: string,
): Promise<BuildWorkflowView> {
  const response = await fetch(
    `${base(projectId)}/cycles/${encodeURIComponent(cycleId)}/stages/${encodeURIComponent(stage)}/workflow`,
  );
  if (!response.ok) {
    throw new Error(await parseError(response, "Failed to load the build plan"));
  }
  return (await response.json()) as BuildWorkflowView;
}
