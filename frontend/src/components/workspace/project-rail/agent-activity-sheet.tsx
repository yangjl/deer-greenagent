"use client";

import { useMemo } from "react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  type ActivityProjection,
  type ActivityRow,
  activityView,
  activityStateLabel,
  dispatcherChain,
  isTerminalActivityState,
  useActivityContext,
} from "@/core/activity";
import { cn } from "@/lib/utils";

import { activityStateMark } from "./agent-activity-block";

/**
 * Indentation stops here.
 *
 * A fourth-level DBTL chain would run off the edge of any sane width, so it
 * renders flat with an explicit "Dispatched by …" line instead. Depth is a
 * reading aid; the lineage sentence is the actual record.
 */
const MAX_DEPTH = 3;

export interface AgentActivitySheetProps {
  id: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projection: ActivityProjection;
  cycleLabel?: (cycleId: string) => string | undefined;
}

interface TreeNode {
  row: ActivityRow;
  depth: number;
  /** Immediate dispatcher, stated even when its settled row is filtered out. */
  dispatchedBy?: string;
}

/** Roots first, then children in the order they opened. */
function buildTree(projection: ActivityProjection): TreeNode[] {
  const children = new Map<string, ActivityRow[]>();
  const roots: ActivityRow[] = [];
  for (const id of projection.order) {
    const row = projection.rows[id];
    if (!row) continue;
    const parentId = row.parentActivityId;
    if (parentId && projection.rows[parentId]) {
      children.set(parentId, [...(children.get(parentId) ?? []), row]);
    } else {
      roots.push(row);
    }
  }

  const nodes: TreeNode[] = [];
  const seen = new Set<string>();
  const walk = (row: ActivityRow, depth: number) => {
    if (seen.has(row.activityId)) return;
    seen.add(row.activityId);
    const capped = depth >= MAX_DEPTH;
    const parent = projection.rows[row.parentActivityId ?? ""];
    nodes.push({
      row,
      depth: capped ? MAX_DEPTH : depth,
      // Current actors excludes settled ancestors. Indentation alone therefore
      // cannot communicate lineage: an active worker would otherwise sit under
      // blank space with no indication who dispatched it. Naming the immediate
      // parent on every child also keeps capped and uncapped depths consistent.
      dispatchedBy: parent?.displayName,
    });
    for (const child of children.get(row.activityId) ?? []) {
      walk(child, depth + 1);
    }
  };
  for (const root of roots) walk(root, 0);
  return nodes;
}

function ActorRow({
  node,
  cycleLabel,
  animate,
}: {
  node: TreeNode;
  cycleLabel?: (cycleId: string) => string | undefined;
  animate: boolean;
}) {
  const mark = activityStateMark(node.row.state);
  const Icon = mark.icon;
  const cycle = node.row.cycleId ? cycleLabel?.(node.row.cycleId) : undefined;
  return (
    <li
      className="flex items-start gap-2 py-1 text-sm"
      style={{ paddingLeft: `${node.depth * 14}px` }}
    >
      <Icon
        className={cn(
          "mt-0.5 size-3.5 shrink-0",
          mark.tone,
          // Ancestors of a spinning leaf are static: exactly one thing moves.
          mark.spin && animate && "animate-spin motion-reduce:animate-none",
        )}
        aria-hidden
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate">{node.row.displayName}</span>
        {(node.dispatchedBy ?? cycle) && (
          <span className="text-muted-foreground/70 block truncate text-[11px]">
            {[
              node.dispatchedBy ? `Dispatched by ${node.dispatchedBy}` : null,
              cycle ?? null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        )}
      </span>
      <span className="text-muted-foreground shrink-0 text-[11px]">
        {activityStateLabel(node.row.state)}
      </span>
    </li>
  );
}

/**
 * The expanded surface: the dispatch tree, then the ordered history.
 *
 * A Sheet rather than an in-rail disclosure, following `cycle-stage-sheet`.
 * Growing in place would push Cycles, Blockers, and Conversations out of view
 * and nest a second scroll region inside the rail's own — both worse than a
 * surface with room to be honest.
 */
export function AgentActivitySheet({
  id,
  open,
  onOpenChange,
  projection,
  cycleLabel,
}: AgentActivitySheetProps) {
  const { hasOlder, isLoadingOlder, loadOlder } = useActivityContext();
  const nodes = useMemo(() => buildTree(projection), [projection]);
  const activeLeafId = useMemo(
    () => activityView(projection).leaf?.activityId,
    [projection],
  );
  const active = nodes.filter(
    (node) => !isTerminalActivityState(node.row.state),
  );
  const timeline = useMemo(
    () => [...projection.timeline].reverse(),
    [projection.timeline],
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        id={id}
        className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-lg"
        onScroll={(event) => {
          const target = event.currentTarget;
          if (
            target.scrollHeight - target.scrollTop - target.clientHeight <=
              80 &&
            hasOlder
          ) {
            void loadOlder();
          }
        }}
      >
        <SheetHeader>
          <SheetTitle>Activity</SheetTitle>
          <SheetDescription>
            What the runtime is doing in this conversation, and which actor
            dispatched it. Read-only.
          </SheetDescription>
        </SheetHeader>

        <section className="px-4 pb-4">
          <h3 className="text-muted-foreground/70 mb-1 text-[11px] font-semibold tracking-widest uppercase">
            Current actors
          </h3>
          {active.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              Nothing is running right now.
            </p>
          ) : (
            <ul>
              {active.map((node) => (
                <ActorRow
                  key={node.row.activityId}
                  node={node}
                  cycleLabel={cycleLabel}
                  animate={node.row.activityId === activeLeafId}
                />
              ))}
            </ul>
          )}
        </section>

        <section className="px-4 pb-6">
          <h3 className="text-muted-foreground/70 mb-1 text-[11px] font-semibold tracking-widest uppercase">
            Earlier
          </h3>
          {timeline.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No activity has been recorded yet.
            </p>
          ) : (
            // `role="log"` with `aria-live="off"`, matching the conversation
            // element: a reader who opened this is reading it, not being read
            // to.
            <ol role="log" aria-live="off" className="space-y-1">
              {timeline.map((entry) => (
                <li
                  key={`${entry.activityId}-${entry.seq}`}
                  className="flex items-start gap-2 text-sm"
                >
                  <span className="text-muted-foreground/60 w-16 shrink-0 text-[11px] tabular-nums">
                    {new Date(entry.receivedAt).toLocaleTimeString()}
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {entry.displayName}
                  </span>
                  <span className="text-muted-foreground shrink-0 text-[11px]">
                    {activityStateLabel(entry.state)}
                  </span>
                </li>
              ))}
            </ol>
          )}
          {hasOlder && (
            <button
              type="button"
              onClick={() => void loadOlder()}
              disabled={isLoadingOlder}
              className="text-muted-foreground hover:text-foreground mt-3 text-xs underline-offset-4 hover:underline disabled:opacity-60"
            >
              {isLoadingOlder
                ? "Loading earlier activity…"
                : "Load earlier activity"}
            </button>
          )}
        </section>
      </SheetContent>
    </Sheet>
  );
}

/** Exported for tests: the chain a row's lineage line is built from. */
export function chainFor(projection: ActivityProjection, row: ActivityRow) {
  return dispatcherChain(projection, row);
}
