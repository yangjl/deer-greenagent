"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { fetchStageWorkers, StageWorkerFetchError } from "@/core/tasks/api";
import { useReconcileSubtasks, useSubtaskContext } from "@/core/tasks/context";
import { terminalStageReportForDisplay } from "@/core/tasks/presentation";
import { stageWorkTasks } from "@/core/tasks/stage-work";

import { MarkdownContent } from "./markdown-content";
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
    if (!threadId || isLoading) {
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
  const { t } = useI18n();
  const { tasks: taskMap } = useSubtaskContext();
  const tasks = useMemo(
    () => (runId ? stageWorkTasks(Object.values(taskMap), runId) : []),
    [taskMap, runId],
  );

  if (tasks.length === 0) {
    return null;
  }

  const cappedFailureMessages = {
    token_capped: t.subtasks.stageTokenCapped,
    turn_capped: t.subtasks.stageTurnCapped,
    loop_capped: t.subtasks.stageLoopCapped,
  };

  return (
    <div className={className}>
      {tasks.map((task) => {
        const report = terminalStageReportForDisplay(
          task,
          cappedFailureMessages,
        );
        return (
          <div key={task.id} className="mb-4 flex w-full flex-col gap-3">
            <SubtaskCard
              taskId={task.id}
              threadId={threadId}
              runId={task.runId ?? runId}
              isLoading={task.status === "in_progress"}
              // Governed stage work reads as native tool-call / script-writing
              // progress, not a decorated card with a shine border.
              flat
              showTerminalReport={false}
            />
            {report ? (
              <div className="text-foreground text-sm leading-6">
                <MarkdownContent content={report} isLoading={false} />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
