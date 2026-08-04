"use client";

import { Loader2Icon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { fetchStageWorkers, StageWorkerFetchError } from "@/core/tasks/api";
import { useReconcileSubtasks, useSubtaskContext } from "@/core/tasks/context";
import {
  runningStageWorkRunId,
  stageLabel,
  stageWorkGroups,
  stageWorkIsRunning,
} from "@/core/tasks/stage-work";
import { cn } from "@/lib/utils";

import { SubtaskCard } from "./subtask-card";

const MAX_HYDRATION_RETRIES = 3;

function shouldRetryHydration(error: unknown): boolean {
  const permanentClientError =
    error instanceof StageWorkerFetchError &&
    error.status >= 400 &&
    error.status < 500 &&
    error.status !== 408 &&
    error.status !== 429;
  return !permanentClientError;
}

/**
 * Governed stage work, in the conversation where it was started.
 *
 * A Build worker is dispatched by the stage adapter rather than by an assistant
 * turn, so it has no `task` tool call to hang from and — until this existed —
 * ran entirely invisibly: the run appeared idle, and the only evidence it had
 * happened was a package appearing at the end, or a stage failing with nothing
 * to look at.
 *
 * Deliberately composition around the existing `SubtaskCard` rather than a
 * second step renderer: the same expansion, the same tool disclosure, the same
 * backfill on reload. Design meeting seats are excluded here because the
 * run-scoped Meeting card renders them in transcript order; activity remains
 * the deeper audit surface.
 */
export function StageWorkPanel({
  className,
  threadId,
  runId,
  isLoading,
}: {
  className?: string;
  threadId?: string;
  runId?: string;
  isLoading: boolean;
}) {
  const { tasks: taskMap } = useSubtaskContext();
  const reconcileSubtasks = useReconcileSubtasks();
  const tasks = useMemo(() => Object.values(taskMap), [taskMap]);
  const runningRunId = useMemo(() => runningStageWorkRunId(tasks), [tasks]);
  const [streamRunId, setStreamRunId] = useState<string>();
  useEffect(() => {
    if (!isLoading) {
      setStreamRunId(undefined);
    } else if (runningRunId) {
      // Keep the streamed run selected after its last worker becomes terminal
      // but before the run's first transcript message has landed.
      setStreamRunId(runningRunId);
    }
  }, [isLoading, runningRunId]);
  const visibleRunId = isLoading
    ? (runningRunId ?? streamRunId ?? runId)
    : runId;
  const groups = useMemo(
    () => stageWorkGroups(tasks, visibleRunId),
    [tasks, visibleRunId],
  );

  // Rebuild stage work for a page that missed the stream and reconcile a
  // partial live task against its durable terminal event. A stage worker has
  // no `task` tool call in the transcript to rebuild from.
  const hydratedRef = useRef<string | null>(null);
  const retryEpochRef = useRef<string | null>(null);
  const retryAttemptRef = useRef(0);
  const [retryToken, setRetryToken] = useState(0);
  useEffect(() => {
    if (!threadId || isLoading) {
      return;
    }
    const epoch = `${threadId}:${runId ?? "history"}`;
    if (retryEpochRef.current !== epoch) {
      retryEpochRef.current = epoch;
      retryAttemptRef.current = 0;
    }
    if (hydratedRef.current === epoch) return;
    hydratedRef.current = epoch;
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    fetchStageWorkers(threadId)
      .then((workers) => {
        if (cancelled || hydratedRef.current !== epoch) return;
        retryAttemptRef.current = 0;
        reconcileSubtasks(
          workers.map((worker) => ({
            id: worker.taskId,
            status: worker.status,
            description: worker.description,
            dbtlStage: worker.dbtlStage,
            // Carried so a reloaded page can rebuild the *meeting* too, not
            // just the stage lane. `stageWorkGroups` filters seats out of this
            // panel, so nothing is drawn twice.
            councilSeat: worker.councilSeat,
            subagent_type: "subagent",
            prompt: "",
            runId: worker.runId,
            result: worker.result,
            displaySummary: worker.displaySummary,
            error: worker.error,
            stopReason: worker.stopReason,
            modelName: worker.modelName,
            usage: worker.usage,
          })),
        );
      })
      .catch((error: unknown) => {
        if (cancelled || hydratedRef.current !== epoch) return;
        if (
          !shouldRetryHydration(error) ||
          retryAttemptRef.current >= MAX_HYDRATION_RETRIES
        ) {
          return;
        }
        hydratedRef.current = null;
        const delay = 500 * 2 ** retryAttemptRef.current;
        retryAttemptRef.current += 1;
        // A failed request does not otherwise change any dependency, so an
        // explicit capped backoff signal is required to make the retry real.
        retryTimer = setTimeout(
          () => setRetryToken((value) => value + 1),
          delay,
        );
      });
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (hydratedRef.current === epoch) hydratedRef.current = null;
    };
  }, [threadId, runId, isLoading, retryToken, reconcileSubtasks]);

  if (groups.length === 0) {
    return null;
  }

  return (
    <div className={cn("flex w-full flex-col gap-4", className)}>
      {groups.map((group) => {
        const running = stageWorkIsRunning(group.tasks);
        const label = stageLabel(group.stage);
        return (
          <section
            key={group.stage}
            aria-label={`${label} stage work`}
            className="flex w-full flex-col gap-2"
          >
            <div className="text-muted-foreground flex items-center gap-2 text-[11px] font-medium tracking-wide uppercase">
              <span>{label} stage</span>
              {running ? (
                <Loader2Icon
                  aria-hidden
                  className="size-3 animate-spin motion-reduce:animate-none"
                />
              ) : null}
              <span className="sr-only">
                {running
                  ? `${label} stage work in progress`
                  : `${label} stage work finished`}
              </span>
            </div>
            {group.tasks.map((task) => (
              <SubtaskCard
                key={task.id}
                taskId={task.id}
                threadId={threadId}
                // The run this task was actually observed in. Passing the
                // thread's latest run would backfill every older task from the
                // wrong run's events, and silently get nothing back.
                runId={task.runId ?? runId}
                isLoading={task.status === "in_progress"}
              />
            ))}
          </section>
        );
      })}
    </div>
  );
}
