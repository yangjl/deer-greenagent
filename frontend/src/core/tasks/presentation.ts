import { formatTokenCount, type TokenUsage } from "@/core/messages/usage";
import type { Model } from "@/core/models/types";

import type { Subtask } from "./types";

/** Return the user-facing label for a configured subagent model. */
export function resolveSubtaskModelLabel(
  modelName: string | undefined,
  models: Model[],
): string | undefined {
  if (!modelName) {
    return undefined;
  }
  return (
    models.find((model) => model.name === modelName)?.display_name ?? modelName
  );
}

export function formatSubtaskTokenUsage(
  usage: TokenUsage | undefined,
): string | undefined {
  return usage ? formatTokenCount(usage.totalTokens) : undefined;
}

/** Never expose a governed worker's raw JSON contract as its answer. */
export function subtaskResultForDisplay(task: Subtask): string | undefined {
  if (!task.dbtlStage) return task.result;
  if (task.displaySummary) return task.displaySummary;
  if (!task.result) return undefined;
  try {
    const parsed = JSON.parse(task.result) as Record<string, unknown>;
    if (typeof parsed.summary === "string" && parsed.summary.trim()) {
      return parsed.summary.trim();
    }
    if (Array.isArray(parsed.phases)) {
      const titles = parsed.phases
        .map((phase) =>
          phase && typeof phase === "object"
            ? (phase as Record<string, unknown>).title
            : undefined,
        )
        .filter(
          (title): title is string =>
            typeof title === "string" && title.trim().length > 0,
        );
      if (titles.length) {
        return `Build plan ready · ${titles.length} ${titles.length === 1 ? "phase" : "phases"}\n${titles
          .map((title, index) => `${index + 1}. ${title}`)
          .join("\n")}`;
      }
    }
  } catch {
    // An old malformed structured result is audit data, not display prose.
  }
  return undefined;
}

/** Bounded terminal prose that a governed card may show while collapsed. */
export function terminalStageReportForDisplay(
  task: Subtask,
  cappedFailureMessages: Readonly<Record<string, string>> = {},
): string | undefined {
  if (!task.dbtlStage) return undefined;
  if (task.status === "completed") return subtaskResultForDisplay(task);
  if (task.status === "failed") {
    return (
      (task.stopReason ? cappedFailureMessages[task.stopReason] : undefined) ??
      task.error
    );
  }
  return undefined;
}

/** Is this a typed DBTL terminal contract rather than display prose? */
export function isDbtlStructuredResult(value: string | undefined): boolean {
  if (!value?.trim()) return false;
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return false;
    }
    // Build planners and ordinary stage workers intentionally have different
    // contracts. Keep this allowlist narrow so a legitimate JSON progress
    // note is not silently hidden.
    const isBuildPlan =
      typeof parsed.feasibility === "string" && Array.isArray(parsed.phases);
    const isStageWorker =
      typeof parsed.status === "string" &&
      typeof parsed.summary === "string" &&
      (Array.isArray(parsed.artifacts) || Array.isArray(parsed.limitations));
    return isBuildPlan || isStageWorker;
  } catch {
    return false;
  }
}

/** Hide only the final typed answer from a governed task's step timeline. */
export function shouldHideTrailingDbtlContract(
  dbtlStage: string | undefined,
  steps: Subtask["steps"],
): boolean {
  if (!dbtlStage) return false;
  const trailingStep = steps?.[steps.length - 1];
  return Boolean(
    trailingStep?.kind === "ai" &&
    !trailingStep.tool_calls?.length &&
    isDbtlStructuredResult(trailingStep.text),
  );
}
