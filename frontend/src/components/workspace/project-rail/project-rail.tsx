"use client";

import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  CircleSlash,
  ListChecks,
  Lock,
  MessageSquarePlus,
  MessagesSquare,
  MoreHorizontal,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";
import { resetThreadChatAfterDelete } from "@/components/workspace/chats/use-thread-chat";
import { useProjectCycleSelection } from "@/components/workspace/dbtl";
import { SettingsDialog } from "@/components/workspace/settings";
import { useAgentActivityFeature } from "@/core/activity";
import {
  CYCLE_STATE_LABELS,
  DBTL_STAGES,
  type CycleRecord,
  type DbtlStage,
  STAGE_LABELS,
  STATUS_LABELS,
  dbtlControlState,
  defaultSelectedCycle,
  isLive,
  blockerBadge,
  buildPlanProjection,
  openWorkItems,
  shouldShowReadinessNotice,
  toggleCycleDisclosure,
  useAbandonCycle,
  useCycleDetail,
  useDbtlFeature,
  useProjectCycles,
  useStageWorkflow,
} from "@/core/dbtl";
import { useDeleteThread } from "@/core/threads/hooks";
import {
  type ProjectConversation,
  pathOfNewProjectConversation,
  pathOfProjectThread,
  useProjectBySlug,
  useProjectConversations,
} from "@/core/workspaces";
import { cn } from "@/lib/utils";

import {
  AgentActivityBlock,
  useActivityCollapsedItem,
  useActivityHeaderWord,
} from "./agent-activity-block";
import { BuildPlanBlock } from "./build-plan-block";
import { CycleStageSheet } from "./cycle-stage-sheet";
import { ProjectRailFrame } from "./project-rail-frame";

/** Agent roster placeholder until project agent assignment ships. */
const PLACEHOLDER_AGENTS = [
  { name: "Lead agent", role: "Conversation & delegation" },
] as const;

const RAIL_SECTION_IDS = {
  agents: "project-rail-agents",
  buildPlan: "project-rail-build-plan",
  conversations: "project-rail-conversations",
  cycles: "project-rail-cycles",
} as const;

function scrollToRailSection(sectionId: string) {
  window.setTimeout(() => {
    document
      .getElementById(sectionId)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 0);
}

function SectionLabel({
  children,
  action,
  id,
}: {
  children: React.ReactNode;
  action?: React.ReactNode;
  id?: string;
}) {
  return (
    <div id={id} className="mt-6 mb-1 flex items-center justify-between px-4">
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
  // Muted rather than green: the stage was dealt with, but nothing about it
  // was validated, and a success tone here would say otherwise.
  skipped: { icon: CircleSlash, tone: "text-muted-foreground" },
};

/** One stage row: an icon, a name, and its status **in words**, never colour alone. */
function StageRow({
  cycle,
  stage,
  onOpen,
  designDeckFeedback,
  openWorkCount = 0,
  attentionLabel = "",
}: {
  cycle: CycleRecord;
  stage: DbtlStage;
  onOpen: (stage: DbtlStage) => void;
  designDeckFeedback: boolean;
  openWorkCount?: number;
  attentionLabel?: string;
}) {
  const record = cycle.stages.find((item) => item.stage === stage);
  const mark = attentionLabel
    ? { icon: AlertTriangle, tone: "text-amber-700 dark:text-amber-400" }
    : (STATUS_MARK[record?.status ?? "locked"] ?? LOCKED_MARK);
  const Icon = mark.icon;
  const badge = blockerBadge(openWorkCount);
  const label =
    stage === "design" && designDeckFeedback
      ? "Open feedback deck"
      : STAGE_LABELS[stage];
  return (
    <button
      type="button"
      onClick={() => onOpen(stage)}
      title={badge ? `${label} · ${badge}` : label}
      aria-label={badge ? `${label}, ${badge} work items` : undefined}
      className="hover:bg-muted/60 group flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors"
    >
      <Icon className={cn("size-3.5 shrink-0", mark.tone)} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {/* The open-work count sits on the stage it belongs to, in words rather
          than colour alone, so the status word beside it still reads. */}
      {badge && (
        <span className="shrink-0 text-[11px] text-amber-700 dark:text-amber-400">
          {badge}
        </span>
      )}
      <span className="text-muted-foreground shrink-0 text-[11px]">
        {attentionLabel
          ? attentionLabel
          : STATUS_LABELS[record?.status ?? "locked"]}
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
  const router = useRouter();
  const { project } = useProjectBySlug(projectSlug);
  const conversations = useProjectConversations(project?.id);
  const dbtl = useDbtlFeature();
  const controls = dbtlControlState(dbtl.feature, dbtl.isLoading);
  const cycleQuery = useProjectCycles(project?.id);
  const activityFeature = useAgentActivityFeature();
  const activityHeaderWord = useActivityHeaderWord(activityFeature.durable);
  const activityCollapsed = useActivityCollapsedItem(activityFeature.durable);
  // Numbered from the *full* ordered list so the activity block and the Cycles
  // section agree about which cycle is "Cycle 02".
  const cycleLabelFor = useCallback(
    (cycleId: string) => {
      const cycles = cycleQuery.data?.cycles ?? [];
      const index = cycles.findIndex((candidate) => candidate.id === cycleId);
      return index < 0
        ? undefined
        : `Cycle ${String(index + 1).padStart(2, "0")}`;
    },
    [cycleQuery.data],
  );

  const { selectedCycleId, selectCycle } = useProjectCycleSelection();
  const [cyclesOverride, setCyclesOverride] = useState<boolean | null>(null);
  const [readinessOpen, setReadinessOpen] = useState(false);
  const [openStage, setOpenStage] = useState<DbtlStage | null>(null);
  // `undefined` means the user has not made a disclosure choice yet, so the
  // existing default cycle opens initially. `null` is an explicit fold-all.
  const [expandedCycleId, setExpandedCycleId] = useState<
    string | null | undefined
  >(undefined);
  const [cycleToRemove, setCycleToRemove] = useState<CycleRecord | null>(null);
  const [cycleRemovalReason, setCycleRemovalReason] = useState("");
  const [conversationToRemove, setConversationToRemove] =
    useState<ProjectConversation | null>(null);
  const abandonCycle = useAbandonCycle(project?.id);
  const deleteConversation = useDeleteThread();

  const cycles = (cycleQuery.data?.cycles ?? []).filter(
    (cycle) => cycle.state !== "abandoned",
  );
  const selected =
    cycles.find((item) => item.id === selectedCycleId) ??
    defaultSelectedCycle(cycles);
  const disclosedCycleId =
    expandedCycleId === undefined ? (selected?.id ?? null) : expandedCycleId;
  const detail = useCycleDetail(project?.id, selected?.id);
  // Open work items keep their signal, folded onto the stage they belong to
  // rather than deleted to make room. The full list stays in the stage sheet
  // that already renders it.
  const blockers = openWorkItems(detail.data);
  // Only reconciliation rows name the stage they belong to, so only they can
  // ride as a badge on it. The rest keep a collapsed line beneath the Build
  // plan rather than being dropped: a work item nobody can see is a signal
  // traded away, which is the one thing replacing this section must not do.
  const reconciliationBlockers = blockers.filter(
    (item) => item.payload?.kind === "reconciliation",
  ).length;
  const unattributedBlockers = blockers.length - reconciliationBlockers;
  // The same query the block below reads, shared through the cache rather than
  // fetched twice — a header count derived separately is a second source of
  // truth about the same plan.
  const buildWorkflow = useStageWorkflow(project?.id, selected?.id, "build", {
    live: Boolean(selected && isLive(selected)),
  });
  const buildProjection = buildPlanProjection(buildWorkflow.data);
  const buildProgress = buildProjection.progress;
  const buildStageStatusLabel = buildProjection.stageStatusLabel;

  // Cycles minimize themselves once nothing is running; an explicit click
  // always wins over that default.
  const cyclesOpen = cyclesOverride ?? cycles.some(isLive);

  async function removeCycle() {
    if (!cycleToRemove || !cycleRemovalReason.trim()) return;
    try {
      await abandonCycle.mutateAsync({
        cycleId: cycleToRemove.id,
        rationale: cycleRemovalReason.trim(),
        expectedDbRevision: cycleToRemove.db_revision,
        idempotencyKey: crypto.randomUUID(),
      });
      selectCycle(null);
      setExpandedCycleId(null);
      setCycleToRemove(null);
      setCycleRemovalReason("");
      toast.success("Cycle removed");
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Failed to remove cycle",
      );
    }
  }

  async function removeConversation() {
    if (!conversationToRemove) return;
    const href = pathOfProjectThread(
      projectSlug,
      conversationToRemove.threadId,
    );
    const isCurrent = pathname === href;
    const nextPath = pathOfNewProjectConversation(projectSlug);
    try {
      await deleteConversation.mutateAsync({
        threadId: conversationToRemove.threadId,
        onRemoteDeleted: isCurrent
          ? () =>
              resetThreadChatAfterDelete({
                deletedThreadId: conversationToRemove.threadId,
                nextPath,
                force: true,
              })
          : undefined,
      });
      setConversationToRemove(null);
      toast.success("Conversation deleted");
      if (isCurrent) {
        router.replace(nextPath);
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to delete conversation",
      );
    }
  }

  return (
    <ProjectRailFrame
      collapsedItems={[
        {
          label: "Cycles",
          icon: CircleDashed,
          onSelect: () => scrollToRailSection(RAIL_SECTION_IDS.cycles),
        },
        {
          label: "Build plan",
          icon: ListChecks,
          onSelect: () => scrollToRailSection(RAIL_SECTION_IDS.buildPlan),
        },
        {
          label: activityFeature.enabled ? activityCollapsed.label : "Agents",
          icon: activityFeature.enabled ? activityCollapsed.icon : Bot,
          onSelect: () => scrollToRailSection(RAIL_SECTION_IDS.agents),
        },
        {
          label: "Conversations",
          icon: MessagesSquare,
          onSelect: () => scrollToRailSection(RAIL_SECTION_IDS.conversations),
        },
      ]}
    >
      <SettingsDialog
        open={readinessOpen}
        onOpenChange={setReadinessOpen}
        defaultSection="dbtl"
      />
      {project && (
        <CycleStageSheet
          projectId={project.id}
          projectSlug={projectSlug}
          cycleId={selected?.id ?? null}
          stage={openStage}
          open={openStage !== null}
          onOpenChange={(next) => !next && setOpenStage(null)}
        />
      )}
      <SectionLabel id={RAIL_SECTION_IDS.cycles}>
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
      {shouldShowReadinessNotice(controls, dbtl.isLoading) && (
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
              const active = disclosedCycleId === entry.id;
              const originConversation = entry.originating_thread_id
                ? conversations.data?.find(
                    (conversation) =>
                      conversation.threadId === entry.originating_thread_id,
                  )
                : undefined;
              return (
                <div key={entry.id} className="mb-1">
                  <div className="group/cycle flex items-center">
                    <button
                      type="button"
                      aria-expanded={active}
                      onClick={() => {
                        const next = toggleCycleDisclosure(
                          disclosedCycleId,
                          entry.id,
                        );
                        setExpandedCycleId(next);
                        selectCycle(next);
                      }}
                      className={cn(
                        "hover:bg-muted/60 flex min-w-0 flex-1 items-center gap-1.5 rounded px-2 py-2 text-left text-sm transition-colors",
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
                    {isLive(entry) && (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            type="button"
                            aria-label={`Cycle actions for ${entry.title}`}
                            title={`Cycle actions for ${entry.title}`}
                            className="text-muted-foreground hover:text-foreground flex size-7 shrink-0 items-center justify-center"
                          >
                            <MoreHorizontal className="size-3.5" />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent side="right" align="start">
                          <DropdownMenuItem
                            className="text-destructive focus:text-destructive"
                            onSelect={() => {
                              setCycleRemovalReason("");
                              setCycleToRemove(entry);
                            }}
                          >
                            <Trash2 />
                            Remove cycle
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </div>
                  {active && (
                    <div className="border-border/70 ml-3 border-l pl-1.5">
                      {DBTL_STAGES.map((stage) => (
                        <StageRow
                          key={stage}
                          cycle={entry}
                          stage={stage}
                          onOpen={setOpenStage}
                          designDeckFeedback={
                            dbtl.feature?.design_deck_feedback === true
                          }
                          openWorkCount={
                            entry.id === selected?.id &&
                            stage === "reconciliation"
                              ? reconciliationBlockers
                              : 0
                          }
                          attentionLabel={
                            entry.id === selected?.id && stage === "build"
                              ? buildStageStatusLabel
                              : ""
                          }
                        />
                      ))}
                      {entry.originating_thread_id &&
                        (originConversation ? (
                          <Link
                            href={pathOfProjectThread(
                              projectSlug,
                              originConversation.threadId,
                            )}
                            onClick={() => selectCycle(null)}
                            className="text-muted-foreground hover:text-foreground flex items-center gap-2 rounded px-2 py-1.5 text-[11px] transition-colors"
                          >
                            <MessagesSquare className="size-3.5 shrink-0" />
                            <span className="min-w-0 truncate">
                              Origin ·{" "}
                              {originConversation.title ??
                                "Untitled conversation"}
                            </span>
                          </Link>
                        ) : (
                          <div className="text-muted-foreground px-2 py-1.5 text-[11px]">
                            {conversations.isLoading
                              ? "Loading originating conversation…"
                              : "Originating conversation unavailable"}
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}

      <SectionLabel
        id={RAIL_SECTION_IDS.buildPlan}
        action={
          buildProgress ? (
            <span className="text-muted-foreground/70 text-[11px]">
              {buildProgress}
            </span>
          ) : undefined
        }
      >
        Build plan{selected ? ` · ${selected.title}` : ""}
      </SectionLabel>
      <div className="px-2">
        <BuildPlanBlock
          projectId={project?.id}
          cycleId={selected?.id ?? null}
          live={Boolean(selected && isLive(selected))}
          onOpenPhase={() => setOpenStage("build")}
        />
        {unattributedBlockers > 0 && (
          <button
            type="button"
            onClick={() => setOpenStage(DBTL_STAGES[0])}
            className="hover:bg-muted/60 flex w-full items-center gap-2 rounded px-2 py-1.5 text-left transition-colors"
          >
            <AlertTriangle className="size-3.5 shrink-0 text-amber-700 dark:text-amber-400" />
            <span className="text-muted-foreground min-w-0 flex-1 truncate text-xs">
              {unattributedBlockers} open work item
              {unattributedBlockers === 1 ? "" : "s"}
            </span>
          </button>
        )}
      </div>

      <SectionLabel
        id={RAIL_SECTION_IDS.agents}
        action={
          activityFeature.enabled ? (
            <span className="text-muted-foreground/70 text-[11px]">
              {activityHeaderWord}
            </span>
          ) : undefined
        }
      >
        Agents
      </SectionLabel>
      {activityFeature.enabled ? (
        <AgentActivityBlock
          available={activityFeature.durable}
          cycleLabel={cycleLabelFor}
        />
      ) : (
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
      )}

      <SectionLabel
        id={RAIL_SECTION_IDS.conversations}
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
              <div
                key={conversation.threadId}
                className="group/conversation flex items-center"
              >
                <Link
                  href={href}
                  className={cn(
                    "hover:bg-muted/60 flex min-w-0 flex-1 items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors",
                    active && "bg-muted/80 font-medium",
                  )}
                >
                  <MessagesSquare className="text-muted-foreground size-3.5 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">
                    {conversation.title ?? "Untitled conversation"}
                  </span>
                </Link>
                <button
                  type="button"
                  aria-label={`Delete ${conversation.title ?? "Untitled conversation"}`}
                  title="Delete conversation"
                  className="text-muted-foreground hover:text-destructive flex size-7 shrink-0 items-center justify-center"
                  onClick={() => setConversationToRemove(conversation)}
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            );
          })
        ) : (
          <div className="text-muted-foreground px-2 py-1.5 text-xs">
            No conversations yet.
          </div>
        )}
      </div>
      <Dialog
        open={cycleToRemove !== null}
        onOpenChange={(open) => !open && setCycleToRemove(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {cycleToRemove?.title}?</DialogTitle>
            <DialogDescription>
              The cycle will leave the active rail and be recorded as abandoned.
              Its evidence and activity history remain available for audit.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={cycleRemovalReason}
            onChange={(event) => setCycleRemovalReason(event.target.value)}
            placeholder="Reason for removing this cycle"
            rows={3}
          />
          <DialogFooter>
            <Button
              variant="outline"
              disabled={abandonCycle.isPending}
              onClick={() => setCycleToRemove(null)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={abandonCycle.isPending || !cycleRemovalReason.trim()}
              onClick={() => void removeCycle()}
            >
              {abandonCycle.isPending ? "Removing…" : "Remove cycle"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={conversationToRemove !== null}
        onOpenChange={(open) => !open && setConversationToRemove(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this conversation?</DialogTitle>
            <DialogDescription>
              This permanently deletes the conversation and its local thread
              data. Files in the project folder are not deleted.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={deleteConversation.isPending}
              onClick={() => setConversationToRemove(null)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteConversation.isPending}
              onClick={() => void removeConversation()}
            >
              {deleteConversation.isPending
                ? "Deleting…"
                : "Delete conversation"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ProjectRailFrame>
  );
}
