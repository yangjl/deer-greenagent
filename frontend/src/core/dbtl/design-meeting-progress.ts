import type { StageWorkerRun } from "./reconciliation-api";

export type DesignMeetingParticipantStatus =
  | "in_progress"
  | "completed"
  | "failed";

export interface DesignMeetingParticipant {
  id: string;
  role: "position" | "red_team" | "chair";
  roleLabel: string;
  agentName: string;
  viaGeneralist: boolean;
  model: string;
  status: DesignMeetingParticipantStatus;
  summary: string;
  totalTokens: number;
  /** Loaded from the authenticated worker endpoint, never trusted from chat. */
  result: Record<string, unknown> | null;
}

export interface DesignMeetingProgress {
  version: 1;
  projectId: string;
  cycleId: string;
  surfaceId: string;
  runId: string;
  round: number;
  state: "synthesizing" | "settled" | "failed";
  choiceLabel: string;
  comment: string;
  priorChairUnitId: string;
  participants: DesignMeetingParticipant[];
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown) {
  return typeof value === "string" ? value : "";
}

function participant(value: unknown): DesignMeetingParticipant | null {
  const item = record(value);
  if (!item) return null;
  const role = text(item.role);
  const status = text(item.status);
  if (
    !["position", "red_team", "chair"].includes(role) ||
    !["in_progress", "completed", "failed"].includes(status)
  ) {
    return null;
  }
  return {
    id: text(item.id),
    role: role as DesignMeetingParticipant["role"],
    roleLabel: text(item.role_label),
    agentName: text(item.agent_name),
    viaGeneralist: item.via_generalist === true,
    model: text(item.model),
    status: status as DesignMeetingParticipantStatus,
    summary: text(item.summary),
    totalTokens: typeof item.total_tokens === "number" ? item.total_tokens : 0,
    result: null,
  };
}

export function readDesignMeetingProgress(
  additionalKwargs: unknown,
): DesignMeetingProgress | null {
  const kwargs = record(additionalKwargs);
  const value = record(kwargs?.dbtl_meeting_progress);
  if (
    value?.version !== 1 ||
    !text(value.project_id) ||
    !text(value.cycle_id) ||
    !Array.isArray(value.participants)
  ) {
    return null;
  }
  const participants = value.participants
    .map(participant)
    .filter((item): item is DesignMeetingParticipant => item !== null);
  if (participants.length === 0) return null;
  return {
    version: 1,
    projectId: text(value.project_id),
    cycleId: text(value.cycle_id),
    surfaceId: text(value.surface_id),
    runId: text(value.run_id),
    round: typeof value.round === "number" ? value.round : 1,
    state: "synthesizing",
    choiceLabel: text(value.choice_label),
    comment: text(value.comment),
    priorChairUnitId: text(value.prior_chair_unit_id),
    participants,
  };
}

function workerResult(worker: StageWorkerRun) {
  return record(worker.result) ?? {};
}

export function updateMeetingFromWorkers(
  progress: DesignMeetingProgress,
  workers: StageWorkerRun[],
): DesignMeetingProgress {
  const workersByUnitId = new Map(
    workers.map((worker) => [worker.unit_id, worker]),
  );
  const reportedParticipants = progress.participants
    .filter((item) => item.role !== "chair")
    .map((item) => {
      const worker = workersByUnitId.get(item.id);
      if (!worker) return item;
      const result = workerResult(worker);
      return {
        ...item,
        status:
          worker.status === "failed"
            ? ("failed" as const)
            : ("completed" as const),
        summary: text(result.summary) || item.summary,
        result,
      };
    });
  const resumedChairs = workers
    .filter(
      (worker) =>
        worker.capability === "design_council_chair" &&
        worker.unit_id !== progress.priorChairUnitId,
    )
    .sort((left, right) => left.created_at.localeCompare(right.created_at));
  const latest = resumedChairs.at(-1);
  if (!latest) {
    return {
      ...progress,
      participants: [
        ...reportedParticipants,
        ...progress.participants.filter((item) => item.role === "chair"),
      ],
    };
  }

  const result = workerResult(latest);
  const resultStatus = text(result.status);
  const status: DesignMeetingParticipantStatus =
    latest.status === "failed" || resultStatus === "failed"
      ? "failed"
      : latest.status === "completed" && resultStatus === "completed"
        ? "completed"
        : "in_progress";
  const execution = record(result.execution);
  const usage = record(result.token_usage);
  const nextChair: DesignMeetingParticipant = {
    id: latest.unit_id,
    role: "chair",
    roleLabel: "Chair",
    agentName: latest.agent_name || "Design chair",
    viaGeneralist: latest.via_generalist,
    model: text(execution?.model),
    status,
    summary:
      text(result.summary) ||
      (status === "in_progress"
        ? "Revisiting the synthesis with the project owner’s recorded decision."
        : ""),
    totalTokens:
      typeof usage?.total_tokens === "number" ? usage.total_tokens : 0,
    result,
  };
  return {
    ...progress,
    state:
      status === "completed"
        ? "settled"
        : status === "failed"
          ? "failed"
          : "synthesizing",
    participants: [...reportedParticipants, nextChair],
  };
}
