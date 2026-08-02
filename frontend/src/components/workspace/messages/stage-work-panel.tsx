"use client";

import { Loader2Icon } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import { fetchStageWorkers } from "@/core/tasks/api";
import { useSubtaskContext, useUpdateSubtask } from "@/core/tasks/context";
import {
  stageLabel,
  stageWorkGroups,
  stageWorkIsRunning,
} from "@/core/tasks/stage-work";
import { cn } from "@/lib/utils";

import { SubtaskCard } from "./subtask-card";

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
 * backfill on reload. Design meeting seats are excluded here because
 * `DebatePanel` already renders them with their role and round; the two
 * surfaces must never show one participant twice.
 */
export function StageWorkPanel({
  className,
  threadId,
  runId,
}: {
  className?: string;
  threadId?: string;
  runId?: string;
}) {
  const { tasks: taskMap } = useSubtaskContext();
  const updateSubtask = useUpdateSubtask();
  const groups = useMemo(
    () => stageWorkGroups(Object.values(taskMap)),
    [taskMap],
  );

  // Rebuild stage work for a page that missed the stream. Live runs already
  // have their tasks, so the `groups.length` guard skips the fetch; a reloaded
  // one has nothing in the transcript to rebuild from, because a stage worker
  // has no `task` tool call to hang from.
  const hydratedRef = useRef<string | null>(null);
  const hasStageWork = groups.length > 0;
  useEffect(() => {
    if (!threadId || !runId || hasStageWork || hydratedRef.current === runId) {
      return;
    }
    hydratedRef.current = runId;
    fetchStageWorkers(threadId, runId)
      .then((workers) => {
        for (const worker of workers) {
          updateSubtask({
            id: worker.taskId,
            status: worker.status,
            description: worker.description,
            dbtlStage: worker.dbtlStage,
            subagent_type: "subagent",
            prompt: "",
            runId,
          });
        }
      })
      .catch(() => {
        // Allow a retry on the next render pass rather than leaving the
        // conversation permanently missing work that did happen.
        hydratedRef.current = null;
      });
  }, [threadId, runId, hasStageWork, updateSubtask]);

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
