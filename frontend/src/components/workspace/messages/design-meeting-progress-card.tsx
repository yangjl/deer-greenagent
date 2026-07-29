"use client";

import {
  CheckIcon,
  GavelIcon,
  Loader2Icon,
  SwordsIcon,
  TriangleAlertIcon,
  UserIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  type DesignMeetingParticipant,
  type DesignMeetingProgress,
  updateMeetingFromWorkers,
} from "@/core/dbtl/design-meeting-progress";
import { fetchStageWorkers } from "@/core/dbtl/reconciliation-api";
import { formatTokenCount } from "@/core/messages/usage";
import type { Subtask } from "@/core/tasks/types";
import { cn } from "@/lib/utils";

import { MeetingParticipantInspector } from "./meeting-participant-inspector";

const ROLE_ICONS = {
  position: UserIcon,
  red_team: SwordsIcon,
  chair: GavelIcon,
};

export function DesignMeetingProgressCard({
  progress: initialProgress,
}: {
  progress: DesignMeetingProgress;
}) {
  const [progress, setProgress] = useState(initialProgress);
  const [inspectedId, setInspectedId] = useState<string | null>(null);

  useEffect(() => {
    setProgress(initialProgress);
  }, [initialProgress]);

  useEffect(() => {
    if (progress.state !== "synthesizing") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;

    const refresh = async () => {
      try {
        const workers = await fetchStageWorkers(
          initialProgress.projectId,
          initialProgress.cycleId,
          "design",
        );
        if (cancelled) return;
        const next = updateMeetingFromWorkers(initialProgress, workers);
        setProgress(next);
        if (next.state !== "synthesizing") return;
      } catch {
        // The snapshot itself is durable and remains useful while a transient
        // workers read fails. Polling may recover without replacing the card.
      }
      attempts += 1;
      if (!cancelled && attempts < 200) {
        timer = setTimeout(() => void refresh(), 1_500);
      }
    };

    void refresh();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [initialProgress, progress.state]);

  const reported = progress.participants.filter(
    (item) => item.status !== "in_progress",
  ).length;
  const totalTokens = useMemo(
    () =>
      progress.participants.reduce(
        (total, item) => total + item.totalTokens,
        0,
      ),
    [progress.participants],
  );
  const stateCopy =
    progress.state === "settled"
      ? "Synthesis ready"
      : progress.state === "failed"
        ? "Chair stopped"
        : "Synthesizing";
  const inspectedParticipant =
    progress.participants.find((item) => item.id === inspectedId) ?? null;
  const inspectedTask = inspectedParticipant
    ? participantAsSubtask(inspectedParticipant, progress.round)
    : null;

  return (
    <>
      <section
        aria-label="Design meeting"
        className="border-border/60 bg-card/40 my-3 rounded-xl border backdrop-blur-sm"
        data-testid="design-meeting-progress"
      >
        <header className="border-border/60 space-y-2.5 border-b px-4 py-3">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="text-foreground text-sm font-medium tracking-tight">
                Design meeting
              </h3>
              <p className="text-muted-foreground mt-0.5 text-xs">
                Round {progress.round} · {reported} of{" "}
                {progress.participants.length} participants reported
                {totalTokens > 0
                  ? ` · ${formatTokenCount(totalTokens)} tokens used`
                  : ""}
              </p>
            </div>
            <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
              {progress.state === "settled" ? (
                <CheckIcon className="size-3.5 text-emerald-600 dark:text-emerald-400" />
              ) : progress.state === "failed" ? (
                <TriangleAlertIcon className="size-3.5 text-amber-600 dark:text-amber-500" />
              ) : (
                <Loader2Icon className="size-3.5 animate-spin" />
              )}
              {stateCopy}
            </span>
          </div>
          <div
            aria-label={`${reported} of ${progress.participants.length} meeting participants reported`}
            aria-valuemax={progress.participants.length}
            aria-valuemin={0}
            aria-valuenow={reported}
            className="bg-muted h-1 overflow-hidden rounded-full"
            role="progressbar"
          >
            <div
              className="bg-primary h-full rounded-full transition-[width] duration-500"
              style={{
                width: `${Math.round((reported / progress.participants.length) * 100)}%`,
              }}
            />
          </div>
        </header>

        <div className="divide-border/40 divide-y">
          <ul className="space-y-2 px-4 py-3">
            {progress.participants.map((participant) => (
              <ParticipantLane
                key={`${participant.role}/${participant.id}`}
                participant={participant}
                onInspect={() => setInspectedId(participant.id)}
              />
            ))}
          </ul>
          <div className="bg-muted/20 px-4 py-3">
            <p className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
              Your recorded decision
            </p>
            <p className="text-foreground mt-1 text-sm">
              {progress.choiceLabel}
            </p>
            {progress.comment ? (
              <p className="text-muted-foreground mt-1 text-xs">
                {progress.comment}
              </p>
            ) : null}
          </div>
        </div>
      </section>
      <MeetingParticipantInspector
        open={inspectedTask !== null}
        task={inspectedTask}
        onOpenChange={(open) => {
          if (!open) setInspectedId(null);
        }}
      />
    </>
  );
}

function ParticipantLane({
  participant,
  onInspect,
}: {
  participant: DesignMeetingParticipant;
  onInspect: () => void;
}) {
  const Icon = ROLE_ICONS[participant.role];
  return (
    <li
      className="hover:bg-muted/40 -mx-2 flex cursor-pointer items-start gap-3 rounded-md px-2 py-1 transition-colors"
      role="button"
      tabIndex={0}
      title="Open this participant’s recorded report"
      onClick={onInspect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onInspect();
        }
      }}
    >
      <span
        aria-hidden
        className={cn(
          "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md",
          participant.role === "chair"
            ? "bg-primary/10 text-primary"
            : "bg-muted text-muted-foreground",
        )}
      >
        <Icon className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-foreground text-sm leading-snug font-medium">
          {participant.roleLabel}
        </p>
        <p className="text-muted-foreground mt-0.5 text-[11px]">
          {participant.agentName}
          {participant.model ? ` · ${participant.model}` : ""}
          {participant.totalTokens
            ? ` · ${formatTokenCount(participant.totalTokens)} tokens`
            : ""}
          {participant.viaGeneralist ? " · stand-in" : ""}
        </p>
        {participant.summary ? (
          <p className="text-foreground/80 mt-1 line-clamp-2 text-xs leading-5">
            {participant.summary}
          </p>
        ) : null}
        <p className="text-muted-foreground mt-1 text-[11px]">
          open recorded report
        </p>
      </div>
      <ParticipantStatus status={participant.status} />
    </li>
  );
}

function participantAsSubtask(
  participant: DesignMeetingParticipant,
  round: number,
): Subtask {
  const result = participant.result
    ? JSON.stringify(participant.result)
    : JSON.stringify({
        status: participant.status,
        summary: participant.summary,
      });
  return {
    id: participant.id,
    status: participant.status,
    subagent_type: participant.agentName,
    description: participant.roleLabel,
    modelName: participant.model,
    prompt: "",
    result,
    councilSeat: {
      role: participant.role,
      roleLabel: participant.roleLabel,
      focus: "",
      capability: "",
      agentName: participant.agentName,
      viaGeneralist: participant.viaGeneralist,
      model: participant.model,
      round,
      countsTowardStageOutput: participant.role === "chair",
    },
  };
}

function ParticipantStatus({
  status,
}: {
  status: DesignMeetingParticipant["status"];
}) {
  if (status === "in_progress") {
    return (
      <span className="text-muted-foreground flex shrink-0 items-center gap-1.5 text-xs">
        <Loader2Icon className="size-3 animate-spin" />
        synthesizing
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="flex shrink-0 items-center gap-1.5 text-xs text-amber-600 dark:text-amber-500">
        <TriangleAlertIcon className="size-3" />
        no result
      </span>
    );
  }
  return (
    <span className="text-muted-foreground flex shrink-0 items-center gap-1.5 text-xs">
      <CheckIcon className="size-3" />
      spoke
    </span>
  );
}
