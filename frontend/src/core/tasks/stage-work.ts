/**
 * Which running work belongs to a governed DBTL stage rather than to a chat turn.
 *
 * A `SubtaskCard` is mounted from an assistant message's `task` tool call. A
 * DBTL stage worker has no such message — the adapter dispatches it directly,
 * and its task id is a work-unit id — so its events reached the task provider
 * and then rendered nowhere at all. This selects the ones that need an anchor
 * of their own.
 *
 * Two exclusions, both deliberate:
 *
 * **A meeting seat is not stage work here.** It already renders in
 * `DebatePanel`, with its role, its round, and its standing in the argument.
 * Rendering it twice would make one participant look like two.
 *
 * **The stage comes from the server.** `dbtlStage` is set from the event the
 * backend emitted; nothing infers it from a task id or a description, because
 * a view that parses identifiers to decide what something is breaks silently on
 * the first rename.
 */

import type { Subtask } from "./types";

export interface StageWorkGroup {
  stage: string;
  tasks: Subtask[];
}

/** DBTL stage workers with no transcript anchor of their own, oldest first. */
export function stageWorkTasks(
  subtasks: readonly Subtask[],
  runId?: string,
): Subtask[] {
  return subtasks.filter(
    (task) =>
      Boolean(task.dbtlStage) &&
      !task.councilSeat &&
      // The panel is mounted under the latest conversation turn. Historical
      // workers belong to their own run and must not reappear beneath a later
      // Lead Agent reply, which made unrelated chat look like an active Build.
      (!runId || task.runId === runId),
  );
}

/**
 * Stage work grouped by its stage, in the order each stage first appeared.
 *
 * One run can legitimately touch more than one stage — a Build that finished
 * and a Test that began — and a flat list would present them as one stretch of
 * work.
 */
export function stageWorkGroups(
  subtasks: readonly Subtask[],
  runId?: string,
): StageWorkGroup[] {
  const byStage = new Map<string, Subtask[]>();
  for (const task of stageWorkTasks(subtasks, runId)) {
    const stage = task.dbtlStage!;
    const group = byStage.get(stage) ?? [];
    group.push(task);
    byStage.set(stage, group);
  }
  return [...byStage.entries()].map(([stage, tasks]) => ({ stage, tasks }));
}

/** How the stage reads in a heading: `build` → `Build`. */
export function stageLabel(stage: string): string {
  const trimmed = (stage ?? "").trim();
  if (!trimmed) {
    return "Stage";
  }
  return trimmed
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/** True while any of this group's workers is still running. */
export function stageWorkIsRunning(tasks: readonly Subtask[]): boolean {
  return tasks.some((task) => task.status === "in_progress");
}
