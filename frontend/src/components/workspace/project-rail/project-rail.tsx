"use client";

import {
  Bot,
  CheckCircle2,
  ChevronRight,
  Circle,
  MessageSquarePlus,
  MessagesSquare,
  Plus,
  RefreshCcw,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type FormEvent, useState } from "react";

import { uuid } from "@/core/utils/uuid";
import {
  DBTL_PHASES,
  type DbtlCycle,
  addCycle,
  addTodo,
  hasActiveCycle,
  pathOfNewProjectConversation,
  pathOfProjectThread,
  removeTodo,
  selectCycle,
  selectedCycle,
  setCyclePhase,
  toggleCycleStatus,
  toggleTodo,
  useProjectBySlug,
  useProjectConversations,
} from "@/core/workspaces";
import { cn } from "@/lib/utils";

import { useCyclePlan } from "./use-cycle-plan";

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

function PhaseDots({
  cycle,
  onSetPhase,
}: {
  cycle: DbtlCycle;
  onSetPhase: (phase: (typeof DBTL_PHASES)[number]) => void;
}) {
  const activeIndex = DBTL_PHASES.indexOf(cycle.phase);
  const done = cycle.status === "done";
  return (
    <span className="flex items-center gap-1.5">
      {DBTL_PHASES.map((phase, index) => (
        <button
          key={phase}
          type="button"
          title={`${cycle.name}: mark phase ${phase}`}
          aria-label={`Mark ${cycle.name} phase as ${phase}`}
          onClick={() => onSetPhase(phase)}
          className={cn(
            "size-2 rounded-full transition-transform hover:scale-150",
            index < activeIndex && "bg-emerald-700 dark:bg-emerald-500",
            index === activeIndex &&
              !done &&
              "scale-125 bg-emerald-600 motion-safe:animate-pulse dark:bg-emerald-400",
            index === activeIndex && done && "bg-muted-foreground/50",
            index > activeIndex && "bg-muted-foreground/25",
          )}
        />
      ))}
    </span>
  );
}

/**
 * Second rail of the project workspace: DBTL cycles (the "Tasks" analog), the
 * selected cycle's to-do list (the "Drive" analog), the agents on the project,
 * and its conversations. The project tree lives only in the first rail.
 */
export function ProjectRail({ projectSlug }: { projectSlug: string }) {
  const pathname = usePathname();
  const { project } = useProjectBySlug(projectSlug);
  const conversations = useProjectConversations(project?.id);
  const { plan, update } = useCyclePlan(project?.id, project?.dbtl_phase);
  const [draft, setDraft] = useState("");
  const [cyclesOverride, setCyclesOverride] = useState<boolean | null>(null);
  const cycle = plan ? selectedCycle(plan) : null;
  // Cycles minimize themselves once nothing is running; an explicit click
  // always wins over that default.
  const cyclesOpen = cyclesOverride ?? hasActiveCycle(plan);
  function submitTodo(event: FormEvent) {
    event.preventDefault();
    if (!cycle) {
      return;
    }
    update((current) => addTodo(current, cycle.id, uuid(), draft));
    setDraft("");
  }

  return (
    <aside className="border-border bg-muted/20 hidden w-64 shrink-0 flex-col overflow-y-auto border-r md:flex">
      <SectionLabel
        action={
          <button
            type="button"
            aria-label="Start a new cycle"
            className="text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => {
              setCyclesOverride(true);
              update((current) => addCycle(current, uuid()));
            }}
          >
            <Plus className="size-3.5" />
          </button>
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
          {!cyclesOpen && plan?.cycles.length ? ` · ${plan.cycles.length}` : ""}
        </button>
      </SectionLabel>
      {cyclesOpen && (
        <div className="px-2">
          {/* The phase and completion controls sit beside the select button,
              never inside it: a button cannot nest in a button. */}
          {plan?.cycles.map((entry) => {
            const done = entry.status === "done";
            return (
              <div
                key={entry.id}
                className={cn(
                  "hover:bg-muted/60 flex w-full items-center gap-1.5 rounded px-2 py-2 text-sm transition-colors",
                  cycle?.id === entry.id && "bg-muted/80 font-medium",
                )}
              >
                <button
                  type="button"
                  aria-label={
                    done
                      ? `Reopen ${entry.name}`
                      : `Mark ${entry.name} complete`
                  }
                  title={done ? "Reopen cycle" : "Mark cycle complete"}
                  onClick={() =>
                    update((current) => toggleCycleStatus(current, entry.id))
                  }
                  className="text-muted-foreground shrink-0 transition-colors hover:text-emerald-700 dark:hover:text-emerald-400"
                >
                  {done ? (
                    <CheckCircle2 className="size-3.5 text-emerald-700 dark:text-emerald-400" />
                  ) : (
                    <RefreshCcw className="size-3.5" />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    update((current) => selectCycle(current, entry.id))
                  }
                  className="min-w-0 flex-1 text-left"
                >
                  <span
                    className={cn(
                      "truncate",
                      done && "text-muted-foreground line-through",
                    )}
                  >
                    {entry.name}
                  </span>
                </button>
                <PhaseDots
                  cycle={entry}
                  onSetPhase={(phase) =>
                    update((current) => setCyclePhase(current, entry.id, phase))
                  }
                />
              </div>
            );
          }) ?? (
            <div className="text-muted-foreground px-2 py-2 text-xs">
              Loading…
            </div>
          )}
        </div>
      )}

      <SectionLabel>To-Do{cycle ? ` · ${cycle.name}` : ""}</SectionLabel>
      <div className="px-2">
        {cycle?.todos.map((todo) => (
          <div
            key={todo.id}
            className="group/todo hover:bg-muted/60 flex items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors"
          >
            <button
              type="button"
              aria-label={todo.done ? "Mark as open" : "Mark as done"}
              onClick={() =>
                update((current) => toggleTodo(current, cycle.id, todo.id))
              }
              className="text-muted-foreground shrink-0 transition-colors hover:text-emerald-700 dark:hover:text-emerald-400"
            >
              {todo.done ? (
                <CheckCircle2 className="size-4 text-emerald-700 dark:text-emerald-400" />
              ) : (
                <Circle className="size-4" />
              )}
            </button>
            <span
              className={cn(
                "min-w-0 flex-1 truncate",
                todo.done && "text-muted-foreground line-through",
              )}
            >
              {todo.text}
            </span>
            <button
              type="button"
              aria-label={`Remove to-do: ${todo.text}`}
              onClick={() =>
                update((current) => removeTodo(current, cycle.id, todo.id))
              }
              className="text-muted-foreground/60 hover:text-destructive shrink-0 opacity-0 transition-opacity group-hover/todo:opacity-100"
            >
              <X className="size-3.5" />
            </button>
          </div>
        ))}
        {cycle?.todos.length === 0 && (
          <div className="text-muted-foreground px-2 py-1.5 text-xs">
            Nothing planned for this cycle yet.
          </div>
        )}
        {cycle && (
          <form onSubmit={submitTodo} className="px-2 pt-1">
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Add a to-do…"
              aria-label={`Add a to-do to ${cycle.name}`}
              className="border-border placeholder:text-muted-foreground/60 w-full border-b bg-transparent py-1 text-sm outline-none focus:border-emerald-600"
            />
          </form>
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
