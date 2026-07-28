import type { CouncilSeatIdentity, Subtask } from "./types";

/**
 * Reading the council seat off a task event.
 *
 * The backend sends this on `task_started`, `task_completed`, and `task_failed`
 * so a live view can say "the red team is arguing" while it happens. Parsing is
 * defensive in one direction only: a malformed or absent seat means "this is an
 * ordinary subtask", never a seat with guessed fields. A debate panel that
 * invented a role would be worse than one that showed a plain progress card.
 */
export function readCouncilSeat(value: unknown): CouncilSeatIdentity | null {
  if (!isRecord(value)) {
    return null;
  }
  const role = str(value.role);
  const agentName = str(value.agent_name);
  if (!role || !agentName) {
    return null;
  }
  return {
    role,
    roleLabel: str(value.role_label) || "Meeting participant",
    focus: str(value.focus),
    capability: str(value.capability),
    agentName,
    viaGeneralist: value.via_generalist === true,
    model: str(value.model),
    round: Number.isFinite(value.round) ? Number(value.round) : 1,
    countsTowardStageOutput: value.counts_toward_stage_output === true,
  };
}

/** Debate order, which is not the order the seats happen to finish in. */
const ROLE_ORDER: Record<string, number> = {
  position: 0,
  red_team: 1,
  chair: 2,
};

/**
 * Council seats grouped into rounds, each round in debate order.
 *
 * Sorting by role rather than by arrival keeps the chair last even when it
 * finishes first, because the reader is following an argument rather than a
 * race. An unknown role sorts after the known ones instead of being dropped.
 */
export function debateRounds(
  subtasks: readonly Subtask[],
): { round: number; seats: Subtask[] }[] {
  const byRound = new Map<number, Subtask[]>();
  for (const task of subtasks) {
    if (!task.councilSeat) {
      continue;
    }
    const round = task.councilSeat.round;
    const seats = byRound.get(round) ?? [];
    seats.push(task);
    byRound.set(round, seats);
  }
  return [...byRound.entries()]
    .sort(([a], [b]) => a - b)
    .map(([round, seats]) => ({
      round,
      seats: seats.sort(
        (a, b) =>
          (ROLE_ORDER[a.councilSeat!.role] ?? 99) -
          (ROLE_ORDER[b.councilSeat!.role] ?? 99),
      ),
    }));
}

export type ConsensusState =
  | "debating"
  | "synthesizing"
  | "awaiting_input"
  | "settled"
  | "stalled";

export type ConsensusSnapshot = {
  agreements: number;
  disagreements: number;
  openQuestions: number;
  unresolved: number;
};

export function consensusSnapshot(
  seats: readonly Subtask[],
): ConsensusSnapshot | null {
  const chair = seats.find(
    (task) =>
      task.councilSeat?.role === "chair" &&
      task.status === "completed" &&
      task.result,
  );
  if (!chair?.result) {
    return null;
  }
  const payload = parseObject(chair.result);
  const consensus = isRecord(payload?.consensus) ? payload.consensus : null;
  if (!consensus) {
    return null;
  }
  const agreements = arrayLength(consensus.agreements);
  const disagreements = Array.isArray(consensus.disagreements)
    ? consensus.disagreements.filter(isRecord).length
    : 0;
  const openQuestions = arrayLength(consensus.open_questions);
  const unresolvedDisagreements = Array.isArray(consensus.disagreements)
    ? consensus.disagreements.filter(
        (item) => isRecord(item) && !str(item.resolution),
      ).length
    : 0;
  return {
    agreements,
    disagreements,
    openQuestions,
    unresolved: unresolvedDisagreements + openQuestions,
  };
}

export function councilSeatSummary(task: Subtask): string {
  if (task.result) {
    const summary = str(parseObject(task.result)?.summary);
    if (summary) {
      return summary;
    }
  }
  for (const step of [...(task.steps ?? [])].reverse()) {
    if (step.kind === "ai" && step.text.trim()) {
      return step.text.trim();
    }
  }
  if (task.status === "failed" && task.error) {
    return task.error;
  }
  return "";
}

/**
 * How far the debate has got, from the seats alone.
 *
 * Deliberately derived rather than reported: a separate "consensus" field would
 * be a second source of truth about a thing the seats already say, and the two
 * would eventually disagree in front of a user. `stalled` is its own state
 * because a council whose chair failed has not reached consensus and has not
 * merely finished — those need different words and different next actions.
 */
export function consensusState(seats: readonly Subtask[]): ConsensusState {
  const council = seats.filter((task) => task.councilSeat);
  if (council.length === 0) {
    return "debating";
  }
  const chair = council.find((task) => task.councilSeat?.role === "chair");
  const positions = council.filter((task) => task.councilSeat?.role !== "chair");

  if (chair?.status === "completed") {
    if (str(parseObject(chair.result ?? "")?.status) === "needs_input") {
      return "awaiting_input";
    }
    return "settled";
  }
  if (chair?.status === "failed") {
    return "stalled";
  }
  if (chair) {
    return "synthesizing";
  }
  // No chair yet. If every position has already failed there is nothing left
  // to synthesize, and calling that "debating" would leave a spinner running
  // over a council that is already over.
  if (positions.length > 0 && positions.every((task) => task.status === "failed")) {
    return "stalled";
  }
  return "debating";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function parseObject(value: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(value);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function arrayLength(value: unknown): number {
  return Array.isArray(value)
    ? value.filter((item) => typeof item === "string" && item.trim()).length
    : 0;
}
