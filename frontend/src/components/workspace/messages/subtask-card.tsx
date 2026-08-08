import {
  CheckCircleIcon,
  ChevronUp,
  ClipboardListIcon,
  Loader2Icon,
  SparklesIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Button } from "@/components/ui/button";
import { ShineBorder } from "@/components/ui/shine-border";
import { useI18n } from "@/core/i18n/hooks";
import { formatTokenCount } from "@/core/messages/usage";
import { hasToolCalls } from "@/core/messages/utils";
import { useModels } from "@/core/models/hooks";
import {
  streamdownPluginsWithoutRawHtml,
  streamdownWordAnimation,
} from "@/core/streamdown";
import {
  SafeStreamdown,
  toStreamdownComponents,
} from "@/core/streamdown/components";
import { fetchSubtaskSteps } from "@/core/tasks/api";
import { useSubtask, useUpdateSubtask } from "@/core/tasks/context";
import {
  formatSubtaskTokenUsage,
  resolveSubtaskModelLabel,
  shouldHideTrailingDbtlContract,
  subtaskResultForDisplay,
  terminalStageReportForDisplay,
} from "@/core/tasks/presentation";
import { taskTranscript } from "@/core/tasks/tool-transcript";
import { explainLastToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";
import { FlipDisplay } from "../flip-display";

import { MarkdownContent } from "./markdown-content";
import { ToolCall } from "./message-group";
import { SubtaskToolStep } from "./subtask-tool-step";

export function SubtaskCard({
  className,
  taskId,
  threadId,
  runId,
  isLoading,
  flat = false,
  showTerminalReport = true,
}: {
  className?: string;
  taskId: string;
  threadId?: string;
  runId?: string;
  isLoading: boolean;
  // Render as plain native progress (no ambilight glow, shine border, or
  // description shimmer). Used for governed DBTL stage work so it reads like an
  // ordinary tool-call / script-writing progress report rather than a
  // decorated card. Ordinary chat subagents keep the default look.
  flat?: boolean;
  showTerminalReport?: boolean;
}) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(true);
  // Flat stage work collapses its earlier steps under a native "N more steps"
  // toggle, keeping only the current step visible until expanded — matching
  // the Lead Agent's progress card.
  const [showStageSteps, setShowStageSteps] = useState(false);
  const task = useSubtask(taskId)!;
  const { models, tokenUsageEnabled } = useModels();
  const updateSubtask = useUpdateSubtask();
  const modelLabel = resolveSubtaskModelLabel(task.modelName, models);
  const tokenLabel = tokenUsageEnabled
    ? formatSubtaskTokenUsage(task.usage)
    : undefined;
  const runtimeUsageLabel = tokenUsageEnabled
    ? tokenLabel
      ? `${tokenLabel} ${t.tokenUsage.label}`
      : task.status === "in_progress"
        ? t.tokenUsage.collecting
        : t.tokenUsage.unavailableShort
    : undefined;
  const displayResult = subtaskResultForDisplay(task);
  const cappedFailureMessages = {
    token_capped: t.subtasks.stageTokenCapped,
    turn_capped: t.subtasks.stageTurnCapped,
    loop_capped: t.subtasks.stageLoopCapped,
  };
  const terminalStageReport = terminalStageReportForDisplay(
    task,
    cappedFailureMessages,
  );
  const displayError =
    task.status === "failed" ? (terminalStageReport ?? task.error) : task.error;

  // The card shows the subagent's step timeline (#3779): its reasoning turns
  // interleaved with the tools it ran. Each tool call is paired with the output
  // it produced (`taskTranscript`) so the row can open to show what was asked
  // and what came back, rather than naming the tool and stopping there.
  const entries = useMemo(() => {
    return taskTranscript(task.steps, {
      dropTrailingAnswer:
        task.status === "completed" ||
        shouldHideTrailingDbtlContract(task.dbtlStage, task.steps),
    });
  }, [task.dbtlStage, task.steps, task.status]);

  // While a governed stage phase is still running, surface its latest streamed
  // prose as an inline progress report so a collapsed flat card is not blank
  // during "Working…" — previously an inline report rendered only for terminal
  // (completed/failed) states.
  const liveProgressReport =
    task.dbtlStage && task.status === "in_progress"
      ? [...entries].reverse().find((entry) => entry.kind !== "tool")?.text
      : undefined;

  // Backfill step history on expand for historical runs (#3779). Live runs
  // already have steps from SSE, so the `steps.length` guard skips the fetch.
  const stepsCount = task.steps?.length ?? 0;
  const backfilledRef = useRef<string | null>(null);
  useEffect(() => {
    const backfillKey = `${runId ?? ""}\u0000${taskId}`;
    if (collapsed || backfilledRef.current === backfillKey || stepsCount > 0) {
      return;
    }
    if (!threadId || !runId) {
      return;
    }
    backfilledRef.current = backfillKey;
    let cancelled = false;
    fetchSubtaskSteps(threadId, runId, taskId)
      .then((steps) => {
        if (cancelled || backfilledRef.current !== backfillKey) return;
        if (steps.length > 0) {
          updateSubtask({ id: taskId, runId, steps });
        }
      })
      .catch(() => {
        if (cancelled) return;
        // Allow a retry on the next expand if the fetch failed.
        if (backfilledRef.current === backfillKey) {
          backfilledRef.current = null;
        }
      });
    return () => {
      cancelled = true;
    };
  }, [collapsed, stepsCount, threadId, runId, taskId, updateSubtask]);
  const icon = useMemo(() => {
    if (task.status === "completed") {
      return <CheckCircleIcon className="size-3" />;
    } else if (task.status === "failed") {
      return <XCircleIcon className="size-3 text-red-500" />;
    } else if (task.status === "in_progress") {
      return <Loader2Icon className="size-3 animate-spin" />;
    }
  }, [task.status]);
  return (
    <ChainOfThought
      className={cn(
        "relative w-full gap-2 py-0",
        // Flat governed stage work reads as plain inline progress, so it drops
        // the decorated card's outer border/rounded wrapper; ordinary chat
        // subagents keep it.
        !flat && "rounded-lg border",
        className,
      )}
      open={!collapsed}
    >
      {!flat && (
        <div
          className={cn(
            "ambilight z-[-1]",
            task.status === "in_progress" ? "enabled" : "",
          )}
        ></div>
      )}
      {!flat && task.status === "in_progress" && (
        <ShineBorder
          borderWidth={1.5}
          shineColor={["#A07CFE", "#FE8FB5", "#FFBE7B"]}
        />
      )}
      <div
        className={cn(
          "flex w-full flex-col",
          // The filled rounded surface is part of the decorated card. Flat
          // governed stage work renders on the page background with no box at
          // all — removing only the outer border still left this inner fill
          // reading as a wrapper.
          !flat && "bg-background/95 rounded-lg",
        )}
      >
        <div className="flex w-full items-center justify-between p-0.5">
          <Button
            className="w-full items-start justify-start text-left"
            variant="ghost"
            onClick={() => setCollapsed(!collapsed)}
          >
            <div className="flex w-full items-center justify-between">
              <ChainOfThoughtStep
                className="font-normal"
                label={
                  task.status === "in_progress" && !flat ? (
                    <Shimmer duration={3} spread={3}>
                      {task.description}
                    </Shimmer>
                  ) : (
                    task.description
                  )
                }
                icon={<ClipboardListIcon />}
              ></ChainOfThoughtStep>
              <div className="flex items-center gap-1">
                {collapsed && (
                  <div
                    className={cn(
                      "text-muted-foreground flex items-center gap-1 text-xs font-normal",
                      task.status === "failed" ? "text-red-500 opacity-67" : "",
                    )}
                  >
                    {modelLabel && (
                      <span className="max-w-32 truncate" title={modelLabel}>
                        {modelLabel}
                      </span>
                    )}
                    {runtimeUsageLabel && (
                      <span
                        className="max-w-28 truncate"
                        title={runtimeUsageLabel}
                      >
                        {runtimeUsageLabel}
                      </span>
                    )}
                    {icon}
                    <FlipDisplay
                      className="max-w-[420px] truncate pb-1"
                      uniqueKey={task.latestMessage?.id ?? ""}
                    >
                      {task.status === "in_progress" &&
                      task.latestMessage &&
                      hasToolCalls(task.latestMessage)
                        ? explainLastToolCall(task.latestMessage, t)
                        : t.subtasks[task.status]}
                    </FlipDisplay>
                  </div>
                )}
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4",
                    !collapsed ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
        </div>
        {collapsed &&
        (liveProgressReport || (showTerminalReport && terminalStageReport)) ? (
          <div className="border-border/60 border-t px-4 py-3">
            <div className="text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase">
              {task.status === "failed"
                ? t.subtasks.failureReport
                : t.subtasks.progressReport}
            </div>
            <div
              className={cn(
                "text-foreground/80 text-sm",
                task.status === "failed" && "text-red-600 dark:text-red-400",
              )}
            >
              <MarkdownContent
                content={
                  (showTerminalReport ? terminalStageReport : undefined) ??
                  liveProgressReport ??
                  ""
                }
                isLoading={task.status === "in_progress"}
              />
            </div>
          </div>
        ) : null}
        <ChainOfThoughtContent className="px-4 pb-4">
          {task.prompt && (
            <ChainOfThoughtStep
              label={
                <SafeStreamdown
                  {...streamdownPluginsWithoutRawHtml}
                  animated={streamdownWordAnimation}
                  components={toStreamdownComponents({ a: CitationLink })}
                  isAnimating={isLoading}
                >
                  {task.prompt}
                </SafeStreamdown>
              }
            ></ChainOfThoughtStep>
          )}
          {flat && entries.length > 1 && (
            <Button
              className="w-full items-start justify-start text-left"
              variant="ghost"
              onClick={() => setShowStageSteps(!showStageSteps)}
            >
              <ChainOfThoughtStep
                label={
                  <span className="opacity-60">
                    {showStageSteps
                      ? t.toolCalls.lessSteps
                      : t.toolCalls.moreSteps(entries.length - 1)}
                  </span>
                }
                icon={
                  <ChevronUp
                    className={cn(
                      "size-4 opacity-60 transition-transform duration-200",
                      showStageSteps ? "rotate-180" : "",
                    )}
                  />
                }
              ></ChainOfThoughtStep>
            </Button>
          )}
          {entries.map((entry, i) => {
            const isLastWhileRunning =
              task.status === "in_progress" && i === entries.length - 1;
            // In flat stage work, keep only the current step visible and
            // collapse everything above it under the native "N more steps"
            // toggle above, matching how the Lead Agent's card collapses steps
            // above its last tool call.
            if (
              flat &&
              entries.length > 1 &&
              !showStageSteps &&
              i !== entries.length - 1
            ) {
              return null;
            }
            // Governed stage work renders its tool calls through the same
            // native ToolCall component the Lead Agent uses, so a Build step
            // reads like an ordinary tool-call progress row (friendly label,
            // native icon, path/artifact chip) rather than the raw
            // "read_file · path" transcript row.
            if (flat && entry.kind === "tool") {
              return (
                <ToolCall
                  key={entry.id}
                  id={entry.id}
                  name={entry.toolName ?? "tool"}
                  args={
                    (entry.args as Record<string, unknown> | undefined) ?? {}
                  }
                  result={entry.text}
                  threadId={threadId}
                  isLoading={isLastWhileRunning}
                />
              );
            }
            const icon = isLastWhileRunning ? (
              <Loader2Icon className="size-4 animate-spin motion-reduce:animate-none" />
            ) : entry.kind === "tool" ? (
              <WrenchIcon className="size-4" />
            ) : (
              <SparklesIcon className="size-4" />
            );
            return (
              <ChainOfThoughtStep
                key={entry.id}
                label={
                  entry.kind === "tool" ? (
                    <SubtaskToolStep entry={entry} />
                  ) : (
                    <div className="min-w-0">
                      <div className="text-muted-foreground line-clamp-3 text-sm">
                        <MarkdownContent
                          content={entry.text}
                          isLoading={false}
                        />
                      </div>
                      {entry.usage ? (
                        <div
                          className="text-muted-foreground mt-0.5 text-[10px] tabular-nums"
                          title={`${entry.usage.totalTokens.toLocaleString()} total tokens`}
                        >
                          {formatTokenCount(entry.usage.inputTokens)} input ·{" "}
                          {formatTokenCount(entry.usage.outputTokens)} output
                        </div>
                      ) : null}
                    </div>
                  )
                }
                icon={icon}
              />
            );
          })}
          {showTerminalReport && task.status === "completed" && (
            <>
              <ChainOfThoughtStep
                label={t.subtasks.completed}
                icon={<CheckCircleIcon className="size-4" />}
              ></ChainOfThoughtStep>
              <ChainOfThoughtStep
                label={
                  displayResult ? (
                    <MarkdownContent
                      content={displayResult}
                      isLoading={false}
                    />
                  ) : null
                }
              ></ChainOfThoughtStep>
            </>
          )}
          {showTerminalReport && task.status === "failed" && (
            <ChainOfThoughtStep
              label={<div className="text-red-500">{displayError}</div>}
              icon={<XCircleIcon className="size-4 text-red-500" />}
            ></ChainOfThoughtStep>
          )}
        </ChainOfThoughtContent>
      </div>
    </ChainOfThought>
  );
}
