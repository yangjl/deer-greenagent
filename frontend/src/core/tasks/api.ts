import { fetch } from "../api/fetcher";
import { getBackendBaseURL } from "../config";

import { eventsToSteps, type SubtaskStep } from "./steps";

/** Default per-request page size; matches the events endpoint's default. */
const SUBTASK_STEPS_PAGE_SIZE = 500;
/** Safety bound on pagination so a misbehaving cursor can't loop forever. */
const SUBTASK_STEPS_MAX_PAGES = 100;

type FetchedEvent = Parameters<typeof eventsToSteps>[0][number] & {
  seq?: number;
};

/**
 * Fetch a subtask's persisted step history for a historical run (#3779).
 *
 * Scoped server-side to this `taskId` (and to `subagent.step` events) and paged
 * forward with an `after_seq` cursor until a short page, so the run-wide event
 * limit can never truncate a subagent's step timeline — even for long runs or
 * runs with several subagents. Used by the subtask card to backfill steps on
 * expand when the live SSE steps are gone (e.g. after a page reload).
 */
export async function fetchSubtaskSteps(
  threadId: string,
  runId: string,
  taskId: string,
  pageSize: number = SUBTASK_STEPS_PAGE_SIZE,
): Promise<SubtaskStep[]> {
  const base = `${getBackendBaseURL()}/api/threads/${encodeURIComponent(
    threadId,
  )}/runs/${encodeURIComponent(runId)}/events`;

  const events: FetchedEvent[] = [];
  let afterSeq: number | undefined;

  for (let page = 0; page < SUBTASK_STEPS_MAX_PAGES; page++) {
    const params = new URLSearchParams({
      event_types: "subagent.step",
      task_id: taskId,
      limit: String(pageSize),
    });
    if (afterSeq !== undefined) {
      params.set("after_seq", String(afterSeq));
    }

    const res = await fetch(`${base}?${params.toString()}`);
    if (!res.ok) {
      throw new Error(`Failed to fetch subtask steps: ${res.status}`);
    }
    const batch = (await res.json()) as FetchedEvent[];
    events.push(...batch);

    if (batch.length < pageSize) {
      break;
    }
    const lastSeq = batch[batch.length - 1]?.seq;
    if (lastSeq === undefined) {
      break; // can't advance the cursor; stop rather than refetch page 0 forever
    }
    afterSeq = lastSeq;
  }

  return eventsToSteps(events, taskId);
}

export interface StageWorkerRecord {
  taskId: string;
  description: string;
  dbtlStage: string;
  status: "in_progress" | "completed" | "failed";
}

/**
 * Governed stage workers recorded in one run, for a page that missed the stream.
 *
 * A DBTL stage worker has no `task` tool call to render from, so on reload
 * there is nothing in the transcript to rebuild it from — the whole block
 * simply vanished, and a Build that ran for ten minutes left no trace in the
 * conversation it ran in. `subagent.start` carries `dbtl_stage` for exactly
 * this: it is the only durable signal that separates a stage worker from an
 * ordinary delegated subtask.
 *
 * Terminal state comes from the matching `subagent.end`; a worker with no end
 * event is still running (or its run died), and is reported as in progress
 * rather than being invented as complete.
 */
export async function fetchStageWorkers(
  threadId: string,
  runId: string,
): Promise<StageWorkerRecord[]> {
  const base = `${getBackendBaseURL()}/api/threads/${encodeURIComponent(
    threadId,
  )}/runs/${encodeURIComponent(runId)}/events`;
  const params = new URLSearchParams({
    event_types: "subagent.start,subagent.end",
    limit: String(SUBTASK_STEPS_PAGE_SIZE),
  });

  const response = await fetch(`${base}?${params.toString()}`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(`Failed to load stage workers: ${response.status}`);
  }
  // The endpoint returns a bare array, matching `fetchSubtaskSteps` above.
  const events = (await response.json()) as FetchedEvent[];

  const started = new Map<string, StageWorkerRecord>();
  const ended = new Map<string, "completed" | "failed">();
  for (const event of events) {
    const content = (event.content ?? {}) as Record<string, unknown>;
    const taskId =
      typeof content.task_id === "string" ? content.task_id : undefined;
    if (!taskId) {
      continue;
    }
    if (event.event_type === "subagent.start") {
      const stage =
        typeof content.dbtl_stage === "string" ? content.dbtl_stage.trim() : "";
      if (!stage) {
        // An ordinary delegated subtask: it has an assistant message to render
        // from, and adopting it here would render it twice.
        continue;
      }
      started.set(taskId, {
        taskId,
        description:
          typeof content.description === "string" && content.description.trim()
            ? content.description
            : "Stage work",
        dbtlStage: stage,
        status: "in_progress",
      });
    } else if (event.event_type === "subagent.end") {
      const status = content.status;
      ended.set(taskId, status === "completed" ? "completed" : "failed");
    }
  }

  return [...started.values()].map((record) => ({
    ...record,
    status: ended.get(record.taskId) ?? record.status,
  }));
}
