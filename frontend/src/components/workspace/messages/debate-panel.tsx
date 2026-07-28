"use client";

import {
  CheckIcon,
  GavelIcon,
  Loader2Icon,
  MessageCircleQuestionIcon,
  ShieldAlertIcon,
  SwordsIcon,
  TriangleAlertIcon,
  UserIcon,
  WrenchIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { fetchSubtaskSteps } from "@/core/tasks/api";
import { useSubtaskContext, useUpdateSubtask } from "@/core/tasks/context";
import {
  consensusSnapshot,
  consensusState,
  councilSeatSummary,
  debateRounds,
  type ConsensusState,
  type ConsensusSnapshot,
} from "@/core/tasks/council-seat";
import { meetingTranscript } from "@/core/tasks/meeting-transcript";
import type { CouncilSeatIdentity, Subtask } from "@/core/tasks/types";
import { cn } from "@/lib/utils";

import { MeetingParticipantInspector } from "./meeting-participant-inspector";

/**
 * The Design council, while it is arguing.
 *
 * The seats already streamed as anonymous progress cards; what was missing was
 * *who* — a reader could not tell the red team from a position, could not see
 * that three differently-labelled experts were one stand-in generalist, and
 * could not tell which lane's answer would actually count. All three are shown
 * here, live, because finding out afterwards in a file is finding out too late
 * to intervene.
 */
export function DebatePanel({
  className,
  threadId,
  runId,
}: {
  className?: string;
  threadId?: string;
  runId?: string;
}) {
  const { tasks: taskMap } = useSubtaskContext();
  const tasks = useMemo(() => Object.values(taskMap), [taskMap]);
  const rounds = useMemo(() => debateRounds(tasks), [tasks]);
  const state = useMemo(() => consensusState(tasks), [tasks]);
  const snapshot = useMemo(() => consensusSnapshot(tasks), [tasks]);
  const [inspectedId, setInspectedId] = useState<string | null>(null);
  const inspected = inspectedId ? (taskMap[inspectedId] ?? null) : null;

  if (rounds.length === 0) {
    return null;
  }
  const seats = rounds.flatMap((round) => round.seats);
  const reported = seats.filter((seat) => seat.status !== "in_progress").length;
  const progress = Math.round((reported / seats.length) * 100);
  const currentRound = Math.max(...rounds.map((round) => round.round));

  return (
    <section
      className={cn(
        "border-border/60 bg-card/40 rounded-xl border backdrop-blur-sm",
        className,
      )}
      aria-label="Design meeting debate"
    >
      <header className="border-border/60 space-y-2.5 border-b px-4 py-3">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-foreground text-sm font-medium tracking-tight">
              Design meeting
            </h3>
            <p className="text-muted-foreground mt-0.5 text-xs">
              Round {currentRound} · {reported} of {seats.length} participants
              reported
            </p>
          </div>
          <ConsensusBadge snapshot={snapshot} state={state} />
        </div>
        <div
          aria-label={`${reported} of ${seats.length} meeting participants reported`}
          aria-valuemax={seats.length}
          aria-valuemin={0}
          aria-valuenow={reported}
          className="bg-muted h-1 overflow-hidden rounded-full"
          role="progressbar"
        >
          <div
            className="bg-primary h-full rounded-full transition-[width] duration-500 ease-out"
            style={{ width: `${progress}%` }}
          />
        </div>
      </header>

      <div className="divide-border/40 divide-y">
        {rounds.map(({ round, seats }) => (
          <div key={round} className="px-4 py-3">
            {rounds.length > 1 && (
              <p className="text-muted-foreground mb-2 text-[11px] font-medium tracking-wide uppercase">
                Round {round}
              </p>
            )}
            <ul className="space-y-2">
              {seats.map((seat) => (
                <SeatLane
                  key={seat.id}
                  runId={runId}
                  task={seat}
                  threadId={threadId}
                  onInspect={() => setInspectedId(seat.id)}
                />
              ))}
            </ul>
          </div>
        ))}
      </div>

      <MeetingParticipantInspector
        open={inspected !== null}
        task={inspected}
        onOpenChange={(next) => {
          if (!next) {
            setInspectedId(null);
          }
        }}
      />
    </section>
  );
}

const CONSENSUS_COPY: Record<ConsensusState, { label: string; hint: string }> =
  {
    debating: {
      label: "Debating",
      hint: "Independent positions are still being argued.",
    },
    synthesizing: {
      label: "Synthesizing",
      hint: "The chair is weighing the positions against each other.",
    },
    awaiting_input: {
      label: "Waiting on you",
      hint: "The chair needs one project-owner decision before it can synthesize.",
    },
    partial: {
      label: "Partial synthesis",
      hint: "The chair reported, but one or more participants returned no usable result.",
    },
    settled: {
      label: "Synthesis ready",
      hint: "The chair has reported. You review it; nothing has advanced.",
    },
    stalled: {
      label: "No synthesis",
      hint: "The debate ended without a usable synthesis.",
    },
  };

function ConsensusBadge({
  state,
  snapshot,
}: {
  state: ConsensusState;
  snapshot: ConsensusSnapshot | null;
}) {
  const copy = CONSENSUS_COPY[state];
  const settled = state === "settled";
  const stalled = state === "stalled";
  const awaitingInput = state === "awaiting_input";
  const partial = state === "partial";
  return (
    <span className="flex items-center gap-1.5" title={copy.hint}>
      {settled ? (
        <CheckIcon className="size-3.5 text-emerald-600 dark:text-emerald-400" />
      ) : awaitingInput ? (
        <MessageCircleQuestionIcon className="text-primary size-3.5" />
      ) : stalled || partial ? (
        <TriangleAlertIcon className="size-3.5 text-amber-600 dark:text-amber-500" />
      ) : (
        <Loader2Icon className="text-muted-foreground size-3.5 animate-spin" />
      )}
      {/* Never colour alone: the state has to survive a greyscale screenshot
          and a reader who cannot distinguish the two accent hues. */}
      <span className="text-muted-foreground text-right text-xs">
        <span className="block">{copy.label}</span>
        {snapshot ? (
          <span className="mt-0.5 block text-[10px] tabular-nums">
            {snapshot.agreements} agreed · {snapshot.disagreements} contested ·{" "}
            {snapshot.openQuestions} open
          </span>
        ) : null}
      </span>
    </span>
  );
}

const ROLE_ICONS: Record<string, typeof GavelIcon> = {
  position: UserIcon,
  red_team: SwordsIcon,
  chair: GavelIcon,
};

function SeatLane({
  task,
  threadId,
  runId,
  onInspect,
}: {
  task: Subtask;
  threadId?: string;
  runId?: string;
  onInspect: () => void;
}) {
  const seat = task.councilSeat;
  const updateSubtask = useUpdateSubtask();
  const stepCount = task.steps?.length ?? 0;
  const entries = useMemo(() => meetingTranscript(task.steps), [task.steps]);
  const toolCalls = entries.filter((entry) => entry.kind === "tool").length;
  const latest = entries[entries.length - 1];

  // A reloaded run has no live SSE steps, so the timeline would be empty for
  // exactly the meetings a reader most wants to inspect — the ones that
  // already finished badly. Backfill once from the events endpoint; a failure
  // leaves the lane readable and retries on the next mount.
  const backfilledRef = useRef(false);
  useEffect(() => {
    if (backfilledRef.current || stepCount > 0 || !threadId || !runId) {
      return;
    }
    backfilledRef.current = true;
    fetchSubtaskSteps(threadId, runId, task.id)
      .then((steps) => {
        if (steps.length > 0) {
          updateSubtask({ id: task.id, steps });
        }
      })
      .catch(() => {
        backfilledRef.current = false;
      });
  }, [stepCount, threadId, runId, task.id, updateSubtask]);

  if (!seat) {
    return null;
  }
  const Icon = ROLE_ICONS[seat.role] ?? UserIcon;
  const summary = councilSeatSummary(task);

  return (
    <li
      className="hover:bg-muted/40 -mx-2 flex cursor-pointer items-start gap-3 rounded-md px-2 py-1 transition-colors"
      role="button"
      tabIndex={0}
      title="Open this participant's steps"
      onClick={onInspect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onInspect();
        }
      }}
    >
      <span
        className={cn(
          "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md",
          seat.countsTowardStageOutput
            ? "bg-primary/10 text-primary"
            : "bg-muted text-muted-foreground",
        )}
        aria-hidden
      >
        <Icon className="size-3.5" />
      </span>

      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-foreground text-sm leading-snug font-medium">
            {seat.roleLabel}
          </span>
          {seat.focus && (
            <span className="text-muted-foreground truncate text-xs">
              {seat.focus}
            </span>
          )}
        </p>
        <p className="text-muted-foreground mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
          <span>{seat.agentName}</span>
          {seat.model && <span>· {seat.model}</span>}
          {seat.viaGeneralist && (
            <span
              className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-500"
              title="No specialist is registered for this seat, so a generalist is standing in. Its position is not independent expertise."
            >
              <ShieldAlertIcon className="size-3" />
              stand-in
            </span>
          )}
        </p>
        {/* The live process report: what this participant is doing right now,
            not merely that it is busy. A spinner labelled "arguing" over three
            minutes of silence is what sent a reader to the logs. */}
        {latest ? (
          <p className="text-muted-foreground mt-1 flex items-center gap-1.5 font-mono text-[11px]">
            {task.status === "in_progress" ? (
              <Loader2Icon className="size-2.5 shrink-0 animate-spin" />
            ) : (
              <WrenchIcon className="size-2.5 shrink-0" />
            )}
            <span className="truncate">{latest.title}</span>
          </p>
        ) : null}
        {summary ? (
          <p className="text-foreground/80 mt-1 line-clamp-2 text-xs leading-5">
            {summary}
          </p>
        ) : null}
        <p className="text-muted-foreground mt-1 text-[11px]">
          {toolCalls > 0
            ? `${toolCalls} tool call${toolCalls === 1 ? "" : "s"} · open to inspect`
            : "open to inspect the steps"}
        </p>
      </div>

      <SeatStatus status={task.status} stopReason={task.stopReason} />
    </li>
  );
}

function SeatStatus({
  status,
  stopReason,
}: {
  status: Subtask["status"];
  stopReason?: string;
}) {
  if (status === "in_progress") {
    return (
      <span className="text-muted-foreground flex shrink-0 items-center gap-1.5 text-xs">
        <Loader2Icon className="size-3 animate-spin" />
        arguing
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="flex shrink-0 items-center gap-1.5 text-xs text-amber-600 dark:text-amber-500">
        <TriangleAlertIcon className="size-3" />
        {/* A cap and a crash need opposite fixes, so they are not both "failed". */}
        {stopReason ? stopReason.replace(/_/g, " ") : "no result"}
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

export type { CouncilSeatIdentity };
