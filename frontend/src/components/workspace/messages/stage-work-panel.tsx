"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { fetchStageWorkers, StageWorkerFetchError } from "@/core/tasks/api";
import { useReconcileSubtasks, useSubtaskContext } from "@/core/tasks/context";
import { stageWorkTasks } from "@/core/tasks/stage-work";
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
 * Rebuild governed stage work for a page that missed the stream.
 *
 * A stage worker is dispatched by the stage adapter rather than by an assistant
 * turn, so unlike an ordinary delegated subtask it has no `task` tool call in
 * the transcript to rebuild from. This fills the subtask store instead and
 * renders nothing itself: mounted once per thread, so a reload costs one read
 * rather than one per run.
 */
export function StageWorkHydrator({
  threadId,
  isLoading,
}: {
  threadId?: string;
  isLoading: boolean;
}) {
  const reconcileSubtasks = useReconcileSubtasks();
  const hydratedRef = useRef<string | null>(null);
  const retryEpochRef = useRef<string | null>(null);
  const retryAttemptRef = useRef(0);
  const [retryToken, setRetryToken] = useState(0);
  useEffect(() => {
    // Hydrate even while the thread is still loading. A mid-run reload — or a
    // tab opened onto a thread whose Build/Test/Learn phase is already running —
    // gets no SSE replay of the worker's subagent.start, so gating hydration on
    // `!isLoading` left the in-progress stage card (and its live progress
    // report) invisible until the run ended. reconcileSubtasks merges by id and
    // this hydrate omits `steps`, so it cannot clobber live-streamed steps; the
    // once-per-thread guard keeps it to a single fetch.
    if (!threadId) {
      return;
    }
    const epoch = threadId;
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
            // just the stage lane. `stageWorkTasks` filters seats out of the
            // stage cards, so nothing is drawn twice.
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
  }, [threadId, isLoading, retryToken, reconcileSubtasks]);

  return null;
}

/**
 * One run's governed stage workers, rendered where that run sits.
 *
 * The same native card an ordinary delegated subtask uses, anchored in its own
 * message group instead of collected into a labelled panel at the end of the
 * transcript. That panel had to guess which run to show, so a run that produced
 * no stage work of its own made the previous run's cards disappear, and
 * whatever survived was pinned below every later turn. Anchoring removes the
 * guess: a run renders its own workers, in transcript order, or nothing.
 */
export function StageWorkCards({
  className,
  threadId,
  runId,
}: {
  className?: string;
  threadId?: string;
  runId?: string;
}) {
  const { tasks: taskMap } = useSubtaskContext();
  const tasks = useMemo(
    () => (runId ? stageWorkTasks(Object.values(taskMap), runId) : []),
    [taskMap, runId],
  );

  if (tasks.length === 0) {
    return null;
  }

  return (
    <>
      {tasks.map((task) => (
        <SubtaskCard
          key={task.id}
          className={cn("mb-4", className)}
          taskId={task.id}
          threadId={threadId}
          runId={task.runId ?? runId}
          isLoading={task.status === "in_progress"}
          flat
          headerless
          showTerminalReport={false}
        />
      ))}
    </>
  );
}
