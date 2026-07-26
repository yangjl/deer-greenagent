"use client";

import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Lock,
  MessageSquarePlus,
  MessagesSquare,
  Plus,
  Presentation,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { useProjectCycleSelection } from "@/components/workspace/dbtl";
import { SettingsDialog } from "@/components/workspace/settings";
import {
  CYCLE_STATE_LABELS,
  DBTL_STAGES,
  type CycleRecord,
  type DbtlStage,
  STAGE_LABELS,
  STATUS_LABELS,
  blockedByReadiness,
  controlAccessibleLabel,
  dbtlControlState,
  defaultSelectedCycle,
  isLive,
  openWorkItems,
  useCycleDetail,
  useDbtlFeature,
  useProjectCycles,
} from "@/core/dbtl";
import {
  pathOfNewProjectConversation,
  pathOfProjectThread,
  useProjectBySlug,
  useProjectConversations,
} from "@/core/workspaces";
import { cn } from "@/lib/utils";

import { CycleStageSheet } from "./cycle-stage-sheet";
import { Phase7DemoDialog } from "./phase7-demo-dialog";
import { StartCycleDialog } from "./start-cycle-dialog";

/** Agent roster placeholder until project agent assignment ships. */
const PLACEHOLDER_AGENTS = [
  { name: "Lead agent", role: "Conversation & delegation" },
] as const;

function SectionLabel({
  children,
  action,
}: {
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="mt-6 mb-1 flex items-center justify-between px-4">
      <span className="text-muted-foreground/70 text-[11px] font-semibold tracking-widest uppercase">
        {children}
      </span>
      {action}
    </div>
  );
}

const LOCKED_MARK = { icon: Lock, tone: "text-muted-foreground/50" } as const;

const STATUS_MARK: Record<string, { icon: typeof Lock; tone: string }> = {
  locked: { icon: Lock, tone: "text-muted-foreground/50" },
  in_progress: { icon: CircleDashed, tone: "text-foreground" },
  awaiting_review: {
    icon: AlertTriangle,
    tone: "text-amber-700 dark:text-amber-400",
  },
  changes_requested: {
    icon: AlertTriangle,
    tone: "text-amber-700 dark:text-amber-400",
  },
  approved: {
    icon: CheckCircle2,
    tone: "text-emerald-700 dark:text-emerald-400",
  },
  rejected: { icon: AlertTriangle, tone: "text-destructive" },
};

/** One stage row: an icon, a name, and its status **in words**, never colour alone. */
function StageRow({
  cycle,
  stage,
  onOpen,
}: {
  cycle: CycleRecord;
  stage: DbtlStage;
  onOpen: (stage: DbtlStage) => void;
}) {
  const record = cycle.stages.find((item) => item.stage === stage);
  const mark = STATUS_MARK[record?.status ?? "locked"] ?? LOCKED_MARK;
  const Icon = mark.icon;
  return (
    <button
      type="button"
      onClick={() => onOpen(stage)}
      className="hover:bg-muted/60 group flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors"
    >
      <Icon className={cn("size-3.5 shrink-0", mark.tone)} />
      <span className="min-w-0 flex-1 truncate">{STAGE_LABELS[stage]}</span>
      <span className="text-muted-foreground shrink-0 text-[11px]">
        {STATUS_LABELS[record?.status ?? "locked"]}
      </span>
    </button>
  );
}

/**
 * Second rail of the project workspace: durable DBTL cycles and their stages,
 * the selected cycle's open work items, the agents on the project, and its
 * conversations. The project tree lives only in the first rail.
 *
 * Every cycle value rendered here comes from the server. There is no
 * browser-local cycle projection any more, which is what makes the Phase 3
 * no-go — "localStorage can override durable cycle state" — unreachable
 * rather than merely unlikely.
 */
export function ProjectRail({ projectSlug }: { projectSlug: string }) {
  const pathname = usePathname();
  const { project } = useProjectBySlug(projectSlug);
  const conversations = useProjectConversations(project?.id);
  const dbtl = useDbtlFeature();
  const controls = dbtlControlState(dbtl.feature, dbtl.isLoading);
  const cycleQuery = useProjectCycles(project?.id);

  const {
    selectedCycleId,
    selectCycle,
    requestDesignKickoff,
    requestComposerScope,
  } = useProjectCycleSelection();
  // Cycle setup is a conversation, so it needs the supervisor graph. Where the
  // graph is off (audit_only / manual) chat cannot run setup at all, and the
  // form remains the only way to open a record — see `startCycle` below.
  const setupInChat = Boolean(dbtl.feature?.graph_execution_enabled);
  const [cyclesOverride, setCyclesOverride] = useState<boolean | null>(null);
  const [readinessOpen, setReadinessOpen] = useState(false);
  const [startOpen, setStartOpen] = useState(false);
  const [phase7DemoOpen, setPhase7DemoOpen] = useState(false);
  const [openStage, setOpenStage] = useState<DbtlStage | null>(null);

  const cycles = cycleQuery.data?.cycles ?? [];
  const selected =
    cycles.find((item) => item.id === selectedCycleId) ??
    defaultSelectedCycle(cycles);
  const detail = useCycleDetail(project?.id, selected?.id);
  const blockers = openWorkItems(detail.data);

  // Cycles minimize themselves once nothing is running; an explicit click
  // always wins over that default.
  const cyclesOpen = cyclesOverride ?? cycles.some(isLive);

  /**
   * Starting a cycle arms the composer rather than opening a form: the human
   * describes the cycle in the chatbox and the setup branch proposes the rest.
   * The rail's job is to put them in the right scope, not to collect fields.
   */
  function startCycle() {
    if (blockedByReadiness(controls)) return;
    setCyclesOverride(true);
    if (setupInChat) {
      requestComposerScope("start_cycle");
      return;
    }
    setStartOpen(true);
  }

  return (
    <aside className="border-border bg-muted/20 hidden w-64 shrink-0 flex-col overflow-y-auto border-r md:flex">
      <SettingsDialog
        open={readinessOpen}
        onOpenChange={setReadinessOpen}
        defaultSection="dbtl"
      />
      {project && (
        <>
          <StartCycleDialog
            projectId={project.id}
            projectName={project.name}
            cycles={cycles}
            open={startOpen}
            onOpenChange={setStartOpen}
            onCreated={(cycle) => {
              selectCycle(cycle.id);
              requestDesignKickoff(cycle.id, cycle.title);
            }}
          />
          <Phase7DemoDialog
            open={phase7DemoOpen}
            onOpenChange={setPhase7DemoOpen}
          />
          <CycleStageSheet
            projectId={project.id}
            cycleId={selected?.id ?? null}
            stage={openStage}
            open={openStage !== null}
            onOpenChange={(next) => !next && setOpenStage(null)}
          />
        </>
      )}
      <SectionLabel
        action={
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Open one-click Phase 7 human demo"
              title="Preview Phase 7 validity outcomes"
              className="text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => setPhase7DemoOpen(true)}
            >
              <Presentation className="size-3.5" />
            </button>
            <button
              type="button"
              aria-label={controlAccessibleLabel(
                setupInChat
                  ? "Start a new cycle in the chatbox"
                  : "Start a new cycle",
                controls,
              )}
              aria-disabled={controls.ariaDisabled}
              title={
                controls.enabled
                  ? setupInChat
                    ? "Start a new cycle — describe it in the chatbox"
                    : "Start a new cycle"
                  : controls.reason
              }
              className={cn(
                "text-muted-foreground transition-colors aria-disabled:cursor-not-allowed aria-disabled:opacity-40",
                controls.enabled && "hover:text-foreground",
              )}
              onClick={startCycle}
            >
              <Plus className="size-3.5" />
            </button>
          </div>
        }
      >
        <button
          type="button"
          onClick={() => setCyclesOverride(!cyclesOpen)}
          aria-expanded={cyclesOpen}
          className="hover:text-foreground flex items-center gap-1 tracking-widest uppercase transition-colors"
        >
          <ChevronRight
            className={cn(
              "size-3 transition-transform",
              cyclesOpen && "rotate-90",
            )}
          />
          Cycles
          {!cyclesOpen && cycles.length ? ` · ${cycles.length}` : ""}
        </button>
      </SectionLabel>
      {!controls.enabled && (
        <div className="mx-3 mb-2 rounded-lg border border-amber-700/20 bg-amber-500/5 px-3 py-2">
          <p className="text-foreground text-xs font-medium">
            DBTL is {controls.statusLabel}
          </p>
          <p className="text-muted-foreground mt-1 text-[11px] leading-4">
            {controls.reason} Existing cycles stay visible, but workflow edits
            and graph runs are locked.
          </p>
          <button
            type="button"
            onClick={() => setReadinessOpen(true)}
            className="mt-1.5 text-xs font-medium text-emerald-700 hover:underline dark:text-emerald-400"
          >
            View readiness
          </button>
        </div>
      )}
      {cyclesOpen && (
        <div className="px-2">
          {cycleQuery.isPending ? (
            <div className="text-muted-foreground px-2 py-2 text-xs">
              Loading…
            </div>
          ) : cycleQuery.error ? (
            <div className="text-destructive px-2 py-2 text-xs">
              {cycleQuery.error.message}
            </div>
          ) : cycles.length === 0 ? (
            <div className="text-muted-foreground px-2 py-2 text-xs">
              No cycles yet. Starting one creates a durable research record.
            </div>
          ) : (
            cycles.map((entry) => {
              const active = selected?.id === entry.id;
              return (
                <div key={entry.id} className="mb-1">
                  <button
                    type="button"
                    onClick={() => selectCycle(entry.id)}
                    className={cn(
                      "hover:bg-muted/60 flex w-full items-center gap-1.5 rounded px-2 py-2 text-left text-sm transition-colors",
                      active && "bg-muted/80 font-medium",
                    )}
                  >
                    <span
                      className={cn(
                        "size-1.5 shrink-0 rounded-full",
                        isLive(entry)
                          ? "bg-emerald-600 dark:bg-emerald-400"
                          : "bg-muted-foreground/40",
                      )}
                    />
                    <span className="min-w-0 flex-1 truncate">
                      {entry.title}
                    </span>
                    <span className="text-muted-foreground shrink-0 text-[11px]">
                      {CYCLE_STATE_LABELS[entry.state]}
                    </span>
                  </button>
                  {active && (
                    <div className="border-border/70 ml-3 border-l pl-1.5">
                      {DBTL_STAGES.map((stage) => (
                        <StageRow
                          key={stage}
                          cycle={entry}
                          stage={stage}
                          onOpen={setOpenStage}
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}

      <SectionLabel>
        Blockers{selected ? ` · ${selected.title}` : ""}
      </SectionLabel>
      <div className="px-2">
        {blockers.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setOpenStage(DBTL_STAGES[0])}
            className="hover:bg-muted/60 flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors"
          >
            <AlertTriangle className="size-3.5 shrink-0 text-amber-700 dark:text-amber-400" />
            <span className="min-w-0 flex-1 truncate">{item.title}</span>
          </button>
        ))}
        {selected && blockers.length === 0 && (
          <div className="text-muted-foreground px-2 py-1.5 text-xs">
            Nothing is blocking this cycle.
          </div>
        )}
      </div>

      <SectionLabel>Agents</SectionLabel>
      <div className="px-2">
        {PLACEHOLDER_AGENTS.map((agent) => (
          <div
            key={agent.name}
            className="flex items-center gap-2 rounded px-2 py-1.5 text-sm"
          >
            <Bot className="text-muted-foreground size-3.5 shrink-0" />
            <span className="min-w-0 flex-1 truncate">{agent.name}</span>
            <span
              className="size-1.5 shrink-0 rounded-full bg-emerald-600 dark:bg-emerald-400"
              title={agent.role}
            />
          </div>
        ))}
        <div className="text-muted-foreground/70 px-2 py-1 text-xs">
          Breeding agents are assigned per project in a later cycle.
        </div>
      </div>

      <SectionLabel
        action={
          <Link
            href={pathOfNewProjectConversation(projectSlug)}
            aria-label="New conversation"
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <MessageSquarePlus className="size-3.5" />
          </Link>
        }
      >
        Conversations
      </SectionLabel>
      <div className="px-2 pb-4">
        {conversations.isLoading ? (
          <div className="text-muted-foreground px-2 py-1.5 text-xs">
            Loading…
          </div>
        ) : conversations.data?.length ? (
          conversations.data.map((conversation) => {
            const href = pathOfProjectThread(
              projectSlug,
              conversation.threadId,
            );
            const active = pathname === href;
            return (
              <Link
                key={conversation.threadId}
                href={href}
                className={cn(
                  "hover:bg-muted/60 flex items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors",
                  active && "bg-muted/80 font-medium",
                )}
              >
                <MessagesSquare className="text-muted-foreground size-3.5 shrink-0" />
                <span className="min-w-0 flex-1 truncate">
                  {conversation.title ?? "Untitled conversation"}
                </span>
              </Link>
            );
          })
        ) : (
          <div className="text-muted-foreground px-2 py-1.5 text-xs">
            No conversations yet.
          </div>
        )}
      </div>
    </aside>
  );
}
