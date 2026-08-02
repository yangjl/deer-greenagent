"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  CircleDashed,
  Loader2,
} from "lucide-react";

import {
  type BuildPlanRow,
  buildPlanProjection,
  movingRow,
  phaseAccessibleName,
  useStageWorkflow,
} from "@/core/dbtl";
import { cn } from "@/lib/utils";

/**
 * Reused from `STATUS_MARK`'s tone pairs deliberately: no new hue family enters
 * a 256px column that already carries stage statuses and agent activity. The
 * *keys* differ because these are step states, not DBTL stage statuses — the
 * two vocabularies happen to overlap in three places and mean different things.
 */
const PHASE_MARK: Record<string, { icon: typeof Circle; tone: string }> = {
  queued: { icon: Circle, tone: "text-muted-foreground/50" },
  waiting: { icon: Circle, tone: "text-muted-foreground/50" },
  running: { icon: CircleDashed, tone: "text-foreground" },
  succeeded: {
    icon: CheckCircle2,
    tone: "text-emerald-700 dark:text-emerald-400",
  },
  needs_input: {
    icon: AlertTriangle,
    tone: "text-amber-700 dark:text-amber-400",
  },
  failed: { icon: AlertTriangle, tone: "text-destructive" },
  invalidated: { icon: Circle, tone: "text-muted-foreground/50" },
  cancelled: { icon: Circle, tone: "text-muted-foreground/50" },
};

function PhaseRow({
  row,
  moving,
  onOpen,
}: {
  row: BuildPlanRow;
  moving: boolean;
  onOpen: () => void;
}) {
  const mark = PHASE_MARK[row.status] ?? PHASE_MARK.queued!;
  const Icon = moving ? Loader2 : mark.icon;
  const name = phaseAccessibleName(row);
  return (
    <button
      type="button"
      onClick={onOpen}
      title={name}
      aria-label={name}
      className="hover:bg-muted/60 flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors"
    >
      <Icon
        // Keyed by the phase alone so a state change on the same row updates it
        // without remounting the icon: a remounted `animate-spin` restarts its
        // rotation, which reads as a stutter rather than as progress.
        key={row.phaseKey}
        className={cn(
          "size-3.5 shrink-0",
          mark.tone,
          moving && "animate-spin motion-reduce:animate-none",
        )}
      />
      {/* Truncate, never wrap: a wrapping title changes the rail's height as
          work moves. The full title is in `title`/`aria-label` and in the
          transcript. */}
      <span className="min-w-0 flex-1 truncate">{row.title}</span>
      <span className="text-muted-foreground shrink-0 text-[11px]">
        {row.stateLabel}
      </span>
    </button>
  );
}

/**
 * The selected cycle's Build plan, rendered from the same server view the
 * transcript's workflow block uses.
 *
 * **Read-only.** No retry, no hold, no answer, no confirm — selecting a phase
 * navigates to the conversation that ran it. That is the whole interaction, and
 * it is what keeps mutation in the chat card where the decision becomes durable.
 */
export function BuildPlanBlock({
  projectId,
  cycleId,
  live,
  onOpenPhase,
}: {
  projectId: string | null | undefined;
  cycleId: string | null | undefined;
  live: boolean;
  onOpenPhase: () => void;
}) {
  const workflow = useStageWorkflow(projectId, cycleId, "build", { live });
  const projection = buildPlanProjection(workflow.data);
  const moving = movingRow(projection);

  if (!cycleId) {
    return (
      <div className="text-muted-foreground px-2 py-1.5 text-xs">
        Select a cycle to see its build plan.
      </div>
    );
  }
  if (workflow.isPending) {
    return <div className="text-muted-foreground px-2 py-1.5 text-xs">Loading…</div>;
  }
  if (workflow.error) {
    return (
      <div className="text-muted-foreground px-2 py-1.5 text-xs">
        The build plan is unavailable.
      </div>
    );
  }
  if (!projection.rows.length) {
    return (
      <div className="text-muted-foreground px-2 py-1.5 text-xs">
        {projection.emptyNote}
      </div>
    );
  }

  return (
    <div>
      {projection.rows.map((row) => (
        <PhaseRow
          key={row.phaseKey}
          row={row}
          moving={moving === row.phaseKey}
          onOpen={onOpenPhase}
        />
      ))}
      {projection.attention && (
        <p className="text-muted-foreground px-2 py-1 text-[11px] leading-4">
          {projection.attention}
        </p>
      )}
    </div>
  );
}
