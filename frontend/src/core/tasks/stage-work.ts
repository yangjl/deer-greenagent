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

/** DBTL stage workers with no transcript anchor of their own, oldest first. */
export function stageWorkTasks(
  subtasks: readonly Subtask[],
  runId?: string,
): Subtask[] {
  return subtasks.filter(
    (task) =>
      Boolean(task.dbtlStage) &&
      !task.councilSeat &&
      // Each run renders its own workers in its own message group, so a
      // historical worker must not reappear under a later Lead Agent reply.
      (!runId || task.runId === runId),
  );
}
