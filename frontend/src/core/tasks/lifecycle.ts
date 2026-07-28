import { normalizeTokenUsage } from "../messages/usage";

import { readCouncilSeat } from "./council-seat";
import type { Subtask } from "./types";

type TaskStartedEvent = {
  type: "task_started";
  task_id: string;
  description?: unknown;
  model_name?: unknown;
};

type TaskRunningEvent = {
  type: "task_running";
  task_id: string;
  model_name?: unknown;
  usage?: unknown;
};

type TaskTerminalEvent = {
  type: "task_completed" | "task_failed";
  task_id: string;
  result?: unknown;
  error?: unknown;
  stop_reason?: unknown;
  model_name?: unknown;
};

/** Convert an additive task lifecycle event into a task-state update. */
export function taskEventToSubtaskUpdate(
  event: unknown,
): (Partial<Subtask> & { id: string }) | null {
  if (!isRecord(event)) {
    return null;
  }

  const taskId = event.task_id;
  if (typeof taskId !== "string" || !taskId.trim()) {
    return null;
  }

  if (event.type === "task_started") {
    const started = event as TaskStartedEvent;
    const modelName =
      typeof started.model_name === "string" && started.model_name.trim()
        ? started.model_name.trim()
        : undefined;
    const councilSeat = readCouncilSeat(event.council_seat);
    const description =
      typeof started.description === "string" && started.description.trim()
        ? started.description.trim()
        : "Subtask";
    return {
      id: taskId,
      status: "in_progress",
      description,
      prompt: "",
      subagent_type: councilSeat?.agentName ?? "subagent",
      ...(modelName ? { modelName } : {}),
      ...(councilSeat ? { councilSeat } : {}),
    };
  }

  if (event.type === "task_running") {
    const running = event as TaskRunningEvent;
    const usage = normalizeTokenUsage(running.usage);
    const modelName = normalizeModelName(running.model_name);
    return usage || modelName
      ? {
          id: taskId,
          ...(modelName ? { modelName } : {}),
          ...(usage ? { usage } : {}),
        }
      : null;
  }

  if (event.type === "task_completed" || event.type === "task_failed") {
    const terminal = event as TaskTerminalEvent;
    const councilSeat = readCouncilSeat(event.council_seat);
    const modelName = normalizeModelName(terminal.model_name);
    const stopReason = normalizeText(terminal.stop_reason);
    const result = normalizeText(terminal.result);
    const error = normalizeText(terminal.error);
    return {
      id: taskId,
      status: event.type === "task_completed" ? "completed" : "failed",
      ...(result ? { result } : {}),
      ...(error ? { error } : {}),
      ...(stopReason ? { stopReason } : {}),
      ...(modelName ? { modelName } : {}),
      ...(councilSeat ? { councilSeat } : {}),
    };
  }

  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizeModelName(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function normalizeText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
