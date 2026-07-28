"use client";

import {
  CheckIcon,
  GavelIcon,
  Loader2Icon,
  ShieldAlertIcon,
  SwordsIcon,
  TriangleAlertIcon,
  UserIcon,
} from "lucide-react";
import { useMemo } from "react";

import { useSubtaskContext } from "@/core/tasks/context";
import {
  consensusState,
  debateRounds,
  type ConsensusState,
} from "@/core/tasks/council-seat";
import type { CouncilSeatIdentity, Subtask } from "@/core/tasks/types";
import { cn } from "@/lib/utils";

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
export function DebatePanel({ className }: { className?: string }) {
  const { tasks: taskMap } = useSubtaskContext();
  const tasks = useMemo(() => Object.values(taskMap), [taskMap]);
  const rounds = useMemo(() => debateRounds(tasks), [tasks]);
  const state = useMemo(() => consensusState(tasks), [tasks]);

  if (rounds.length === 0) {
    return null;
  }

  return (
    <section
      className={cn(
        "border-border/60 bg-card/40 rounded-xl border backdrop-blur-sm",
        className,
      )}
      aria-label="Design council debate"
    >
      <header className="border-border/60 flex items-baseline justify-between gap-4 border-b px-4 py-3">
        <h3 className="text-foreground text-sm font-medium tracking-tight">
          Design council
        </h3>
        <ConsensusBadge state={state} />
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
                <SeatLane key={seat.id} task={seat} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

const CONSENSUS_COPY: Record<ConsensusState, { label: string; hint: string }> = {
  debating: {
    label: "Debating",
    hint: "Independent positions are still being argued.",
  },
  synthesizing: {
    label: "Synthesizing",
    hint: "The chair is weighing the positions against each other.",
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

function ConsensusBadge({ state }: { state: ConsensusState }) {
  const copy = CONSENSUS_COPY[state];
  const settled = state === "settled";
  const stalled = state === "stalled";
  return (
    <span className="flex items-center gap-1.5" title={copy.hint}>
      {settled ? (
        <CheckIcon className="size-3.5 text-emerald-600 dark:text-emerald-400" />
      ) : stalled ? (
        <TriangleAlertIcon className="size-3.5 text-amber-600 dark:text-amber-500" />
      ) : (
        <Loader2Icon className="text-muted-foreground size-3.5 animate-spin" />
      )}
      {/* Never colour alone: the state has to survive a greyscale screenshot
          and a reader who cannot distinguish the two accent hues. */}
      <span className="text-muted-foreground text-xs">{copy.label}</span>
    </span>
  );
}

const ROLE_ICONS: Record<string, typeof GavelIcon> = {
  position: UserIcon,
  red_team: SwordsIcon,
  chair: GavelIcon,
};

function SeatLane({ task }: { task: Subtask }) {
  const seat = task.councilSeat;
  if (!seat) {
    return null;
  }
  const Icon = ROLE_ICONS[seat.role] ?? UserIcon;

  return (
    <li className="flex items-start gap-3">
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
