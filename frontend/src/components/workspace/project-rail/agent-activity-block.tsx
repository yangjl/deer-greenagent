"use client";

import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Loader2,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  type ActivityRow,
  type ActivityState,
  activitySentence,
  activityStateLabel,
  activityView,
  useActivityContext,
} from "@/core/activity";
import { cn } from "@/lib/utils";

import { AgentActivitySheet } from "./agent-activity-sheet";

/**
 * The one thing that moves.
 *
 * States are paired with an icon *shape* as well as a tone, so the block
 * survives greyscale and the common colour-vision deficiencies; the state word
 * is always present in text beside it, so colour is never the only signal. The
 * tone pairs are lifted from the rail's existing `STATUS_MARK` — no new hue
 * family — but the keys are activity states, not DBTL stage statuses, and the
 * two vocabularies must not be allowed to drift into each other.
 */
const STATE_MARK: Record<
  ActivityState,
  { icon: typeof Loader2; tone: string; spin: boolean }
> = {
  routing: { icon: Loader2, tone: "text-foreground", spin: true },
  preparing: { icon: Loader2, tone: "text-foreground", spin: true },
  thinking: { icon: Loader2, tone: "text-foreground", spin: true },
  computing: { icon: Loader2, tone: "text-foreground", spin: true },
  coordinating: { icon: Loader2, tone: "text-foreground", spin: true },
  recording: { icon: Loader2, tone: "text-foreground", spin: true },
  // Dispatching is a hand-off, not work in progress: a static glyph says the
  // actor is between things without competing with the leaf that is running.
  dispatching: { icon: Loader2, tone: "text-muted-foreground", spin: false },
  waiting: { icon: CircleDashed, tone: "text-muted-foreground", spin: false },
  completed: {
    icon: CheckCircle2,
    tone: "text-emerald-700 dark:text-emerald-400",
    spin: false,
  },
  failed: { icon: AlertTriangle, tone: "text-destructive", spin: false },
  cancelled: {
    icon: AlertTriangle,
    tone: "text-amber-700 dark:text-amber-400",
    spin: false,
  },
  interrupted: {
    icon: AlertTriangle,
    tone: "text-amber-700 dark:text-amber-400",
    spin: false,
  },
};

const UNKNOWN_MARK = {
  icon: CircleDashed,
  tone: "text-muted-foreground/50",
  spin: false,
} as const;

export function activityStateMark(state: string) {
  return STATE_MARK[state as ActivityState] ?? UNKNOWN_MARK;
}

/** The word in the section header: what the Agents section is right now. */
const MODE_WORDS = {
  live: "Live",
  waiting: "Waiting",
  settled: "Done",
  idle: "Idle",
  unavailable: "Unavailable",
} as const;

export interface AgentActivityBlockProps {
  /** Off during rollout, or when the run-event backend cannot persist rows. */
  available?: boolean;
  /** Resolves a cycle id to the rail's own "Cycle 01" label. */
  cycleLabel?: (cycleId: string) => string | undefined;
  /** Test seam: the sheet is a portal and is not always wanted in a unit test. */
  renderSheet?: boolean;
}

const SHEET_ID = "project-rail-activity-sheet";

/**
 * Fixed height, no internal scrolling, every line truncated.
 *
 * At ~224px of content width a wrapped actor name changes the block's height,
 * and a block whose height changes as work moves is exactly the flicker this
 * design exists to avoid. The full text lives in the accessible name and in the
 * expanded sheet, both of which have room for it.
 */
export function AgentActivityBlock({
  available = true,
  cycleLabel,
  renderSheet = true,
}: AgentActivityBlockProps) {
  const { projection, announcement, dataAvailable, hasOlder } =
    useActivityContext();
  const [open, setOpen] = useState(false);
  const effectiveAvailable = available && dataAvailable;
  const view = useMemo(
    () => activityView(projection, { available: effectiveAvailable }),
    [projection, effectiveAvailable],
  );

  const dispatcher = view.chain[0];
  const mark = activityStateMark(view.leaf?.state ?? "unknown");
  const Icon = view.mode === "unavailable" ? CircleDashed : mark.icon;
  const sentence = activitySentence(view.leaf, dispatcher);

  return (
    <>
      {/* The live region sits outside the block's own subtree on purpose: the
          block's DOM churns on every transition, and an ancestor live region
          would announce all of it. The provider writes only actor-level
          changes here, coalesced. */}
      <div className="sr-only" role="status" aria-live="polite">
        {announcement}
      </div>

      <div className="px-2" data-testid="agent-activity-block">
        <div className="flex items-center gap-2 rounded px-2 py-1.5 text-sm">
          <Icon
            // Keyed by activity id alone so a state change on the same actor
            // updates the row without remounting the icon — a remounted
            // `animate-spin` restarts its rotation, and the one moving thing on
            // screen would stutter.
            key={view.leaf?.activityId ?? "none"}
            className={cn(
              "size-3.5 shrink-0",
              view.mode === "unavailable" ? UNKNOWN_MARK.tone : mark.tone,
              mark.spin && "animate-spin motion-reduce:animate-none",
            )}
            aria-hidden
          />
          <span className="min-w-0 flex-1 truncate" title={sentence}>
            {view.mode === "unavailable"
              ? "Status unavailable"
              : (view.leaf?.displayName ?? "Lead agent")}
          </span>
          <span className="text-muted-foreground shrink-0 text-[11px]">
            {view.mode === "unavailable"
              ? "—"
              : view.leaf
                ? activityStateLabel(view.leaf.state)
                : "Idle"}
          </span>
        </div>

        {view.leaf && (dispatcher ?? view.leaf.cycleId) && (
          <div className="text-muted-foreground/70 truncate px-2 text-[11px]">
            {[
              dispatcher ? `via ${dispatcher.displayName}` : null,
              view.leaf.cycleId
                ? (cycleLabel?.(view.leaf.cycleId) ?? null)
                : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>
        )}

        {view.siblings.length > 0 && (
          <div className="text-muted-foreground/70 truncate px-2 text-[11px]">
            {view.siblings.length === 1
              ? `Also running: ${view.siblings[0]!.displayName}`
              : `+ ${view.siblings.length} more running`}
          </div>
        )}

        <button
          type="button"
          onClick={() => setOpen(true)}
          disabled={view.stepCount === 0 && !hasOlder}
          aria-expanded={open}
          aria-controls={SHEET_ID}
          className="text-muted-foreground hover:text-foreground hover:bg-muted/60 mt-0.5 flex w-full items-center gap-2 rounded px-2 py-1 text-left text-[11px] transition-colors disabled:pointer-events-none disabled:opacity-60"
        >
          <span className="min-w-0 flex-1 truncate">
            {view.stepCount === 0
              ? hasOlder
                ? "Earlier activity"
                : "No activity yet"
              : `Activity · ${view.stepCount} step${view.stepCount === 1 ? "" : "s"}`}
          </span>
          <ChevronRight className="size-3.5 shrink-0" aria-hidden />
        </button>

        <div className="text-muted-foreground/70 px-2 py-1 text-xs">
          Breeding agents are assigned per project in a later cycle.
        </div>
      </div>

      {renderSheet && (
        <AgentActivitySheet
          id={SHEET_ID}
          open={open}
          onOpenChange={setOpen}
          projection={projection}
          cycleLabel={cycleLabel}
        />
      )}
    </>
  );
}

/** The word shown at the right of the section header. */
export function useActivityHeaderWord(available = true): string {
  const { projection, dataAvailable } = useActivityContext();
  const view = useMemo(
    () => activityView(projection, { available: available && dataAvailable }),
    [projection, available, dataAvailable],
  );
  return MODE_WORDS[view.mode];
}

/** The single item activity contributes to the 48px collapsed rail. */
export function useActivityCollapsedItem(available = true): {
  icon: typeof Loader2;
  label: string;
} {
  const { projection, dataAvailable } = useActivityContext();
  const view = useMemo(
    () => activityView(projection, { available: available && dataAvailable }),
    [projection, available, dataAvailable],
  );
  const leaf: ActivityRow | undefined = view.leaf;
  return {
    icon:
      view.mode === "unavailable"
        ? CircleDashed
        : activityStateMark(leaf?.state ?? "unknown").icon,
    label:
      view.mode === "unavailable"
        ? "Agent activity status unavailable"
        : activitySentence(leaf, view.chain[0]),
  };
}
