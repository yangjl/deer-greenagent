import { fetch } from "../api/fetcher";
import { getBackendBaseURL } from "../config";
import { normalizeTokenUsage, type TokenUsage } from "../messages/usage";

import { eventsToSteps, type SubtaskStep } from "./steps";

/** Default per-request page size; matches the events endpoint's default. */
const SUBTASK_STEPS_PAGE_SIZE = 500;
/** Safety bound on pagination so a misbehaving cursor can't loop forever. */
const SUBTASK_STEPS_MAX_PAGES = 100;

export class StageWorkerFetchError extends Error {
  constructor(public readonly status: number) {
    super(`Failed to load stage workers: ${status}`);
    this.name = "StageWorkerFetchError";
  }
}

type FetchedEvent = Parameters<typeof eventsToSteps>[0][number] & {
  seq?: number;
  run_id?: string;
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
  runId: string;
  description: string;
  dbtlStage: string;
  status: "in_progress" | "completed" | "failed";
  result?: string;
  displaySummary?: string;
  error?: string;
  modelName?: string;
  usage?: TokenUsage;
}

type FoldedStageWorkerRecord = StageWorkerRecord & { lastSeq: number };

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
  pageSize: number = SUBTASK_STEPS_PAGE_SIZE,
): Promise<StageWorkerRecord[]> {
  const base = `${getBackendBaseURL()}/api/threads/${encodeURIComponent(
    threadId,
  )}/stage-worker-events`;
  const events: FetchedEvent[] = [];
  let beforeSeq: number | undefined;
  for (let page = 0; page < SUBTASK_STEPS_MAX_PAGES; page++) {
    const params = new URLSearchParams({ limit: String(pageSize) });
    if (beforeSeq !== undefined) params.set("before_seq", String(beforeSeq));
    const response = await fetch(`${base}?${params.toString()}`, {
      credentials: "include",
    });
    if (!response.ok) {
      throw new StageWorkerFetchError(response.status);
    }
    const payload = (await response.json()) as {
      events?: FetchedEvent[];
      next_before_seq?: number | null;
    };
    events.push(...(payload.events ?? []));
    if (payload.next_before_seq == null) break;
    if (payload.next_before_seq === beforeSeq) break;
    beforeSeq = payload.next_before_seq;
  }

  events.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));

  const started = new Map<string, FoldedStageWorkerRecord>();
  for (const event of events) {
    const content = (event.content ?? {}) as Record<string, unknown>;
    const taskId =
      typeof content.task_id === "string" ? content.task_id : undefined;
    if (!taskId) {
      continue;
    }
    const runId = typeof event.run_id === "string" ? event.run_id : "";
    if (!runId) continue;
    const key = `${runId}\u0000${taskId}`;
    if (event.event_type === "subagent.start") {
      const stage =
        typeof content.dbtl_stage === "string" ? content.dbtl_stage.trim() : "";
      if (!stage) {
        // An ordinary delegated subtask: it has an assistant message to render
        // from, and adopting it here would render it twice.
        continue;
      }
      // Design meeting seats share the stage marker but already have their
      // own DebatePanel. The persisted seat marker keeps reload behavior
      // identical to the live stream instead of drawing the participant twice.
      if (
        content.council_seat &&
        typeof content.council_seat === "object" &&
        !Array.isArray(content.council_seat)
      ) {
        continue;
      }
      started.set(key, {
        taskId,
        runId,
        description:
          typeof content.description === "string" && content.description.trim()
            ? content.description
            : "Stage work",
        dbtlStage: stage,
        status: "in_progress",
        lastSeq: event.seq ?? 0,
      });
    } else if (event.event_type === "subagent.end") {
      const record = started.get(key);
      if (!record) continue;
      const status = content.status === "completed" ? "completed" : "failed";
      const text = (value: unknown) =>
        typeof value === "string" && value.trim() ? value.trim() : undefined;
      const usage = normalizeTokenUsage(content.usage);
      started.set(key, {
        ...record,
        status,
        lastSeq: event.seq ?? record.lastSeq,
        ...(text(content.result) ? { result: text(content.result) } : {}),
        ...(text(content.display_summary)
          ? { displaySummary: text(content.display_summary) }
          : {}),
        ...(text(content.error) ? { error: text(content.error) } : {}),
        ...(text(content.model_name)
          ? { modelName: text(content.model_name) }
          : {}),
        ...(usage ? { usage } : {}),
      });
    }
  }

  // A re-plan/resume may reuse a logical work-unit id in a later run. The UI
  // has one card per logical worker, so retain only its newest execution and
  // let the newer run reset an older terminal state.
  const latestByTask = new Map<string, FoldedStageWorkerRecord>();
  for (const record of started.values()) {
    const previous = latestByTask.get(record.taskId);
    if (!previous || record.lastSeq > previous.lastSeq) {
      latestByTask.set(record.taskId, record);
    }
  }
  return [...latestByTask.values()]
    .sort((a, b) => a.lastSeq - b.lastSeq)
    .map(({ lastSeq: _lastSeq, ...record }) => record);
}
