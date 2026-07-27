"use client";

import {
  ArrowLeft,
  Download,
  Lock,
  PencilLine,
  RotateCcw,
  ShieldQuestion,
  Users,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  DECISION_LABELS,
  DECISION_ORDER,
  EMPTY_COUNTS,
  canSubmitDecision,
  countRows,
  decisionConsequence,
  isMigrationEmpty,
  nextSuggestionKey,
  provenanceLabel,
  reviewCallToAction,
  suggestionKey,
  useMigrationManifest,
  useMigrationOverview,
  useMigrationQueue,
  useRollbackMigration,
  useSubmitMigrationDecision,
} from "@/core/memory-scope";
import type { MigrationDecision } from "@/core/memory-scope";
import { useActiveWorkspaceProjects } from "@/core/workspaces";
import type { Project } from "@/core/workspaces";
import { cn } from "@/lib/utils";

const DECISION_ICONS: Record<MigrationDecision, typeof Lock> = {
  keep_private: Lock,
  share: Users,
  edit_then_share: PencilLine,
  quarantine: ShieldQuestion,
};

function downloadJson(payload: unknown, filename: string) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function ProjectPicker({
  projects,
  selectedId,
  onSelect,
}: {
  projects: Project[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {projects.map((project) => {
        const active = project.id === selectedId;
        return (
          <button
            key={project.id}
            type="button"
            onClick={() => onSelect(project.id)}
            aria-pressed={active}
            className={cn(
              "rounded-full border px-3 py-1 text-sm transition-colors",
              "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none",
              active
                ? "border-foreground/20 bg-foreground text-background"
                : "border-border hover:border-foreground/30 hover:bg-muted",
            )}
          >
            {project.name}
          </button>
        );
      })}
    </div>
  );
}

function ReviewDrawer({
  projectId,
  onClose,
}: {
  projectId: string;
  onClose: () => void;
}) {
  const queue = useMigrationQueue(projectId, true);
  const submit = useSubmitMigrationDecision(projectId);
  const [activeSuggestionKey, setActiveSuggestionKey] = useState<string | null>(
    null,
  );
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<MigrationDecision | null>(null);

  const suggestions = useMemo(
    () => queue.data?.suggestions ?? [],
    [queue.data],
  );
  const current =
    suggestions.find((item) => suggestionKey(item) === activeSuggestionKey) ??
    suggestions[0];

  if (queue.isPending) {
    return (
      <p className="text-muted-foreground py-8 text-center text-sm">
        Loading your review queue…
      </p>
    );
  }
  if (queue.error) {
    return (
      <p className="text-destructive py-8 text-center text-sm">
        {queue.error.message}
      </p>
    );
  }
  if (!current) {
    return (
      <div className="py-10 text-center">
        <p className="text-sm font-medium">Nothing left to review</p>
        <p className="text-muted-foreground mt-1 text-sm">
          Every legacy fact in this project has a decision.
        </p>
        <Button variant="ghost" size="sm" className="mt-4" onClick={onClose}>
          <ArrowLeft className="size-4" /> Back to summary
        </Button>
      </div>
    );
  }

  const decide = (decision: MigrationDecision) => {
    if (
      pending !== null ||
      submit.isPending ||
      !canSubmitDecision(decision, draft)
    ) {
      return;
    }
    setPending(decision);
    submit.mutate(
      {
        factId: current.fact_id,
        agentName: current.agent_name,
        sourceSha256: current.sha256,
        decision,
        editedContent: decision === "edit_then_share" ? draft : undefined,
      },
      {
        onSettled: () => setPending(null),
        onSuccess: () => {
          setActiveSuggestionKey(
            nextSuggestionKey(suggestions, suggestionKey(current)),
          );
          setDraft("");
        },
      },
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={onClose}>
          <ArrowLeft className="size-4" /> Summary
        </Button>
        <span className="text-muted-foreground text-xs tabular-nums">
          {suggestions.length} awaiting your decision
        </span>
      </div>

      <article className="bg-card ring-border rounded-xl p-5 shadow-sm ring-1">
        <div className="flex items-start justify-between gap-4">
          <h3 className="text-base leading-snug font-medium">
            {current.title || "Memory fact"}
          </h3>
          <Badge variant="outline" className="shrink-0 capitalize">
            {current.category}
          </Badge>
        </div>
        <p className="mt-3 text-sm leading-relaxed whitespace-pre-wrap">
          {current.content}
        </p>
        <p className="text-muted-foreground mt-4 font-mono text-[11px]">
          {provenanceLabel(current)}
        </p>
        <p className="text-muted-foreground mt-2 text-xs">{current.reason}</p>
      </article>

      <div>
        <label
          htmlFor="memory-scope-edit"
          className="text-muted-foreground text-xs font-medium"
        >
          Edited text (required only for “Edit then share”)
        </label>
        <Textarea
          id="memory-scope-edit"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={3}
          placeholder="Rewrite this fact before the project sees it…"
          className="mt-1.5"
        />
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {DECISION_ORDER.map((decision) => {
          const Icon = DECISION_ICONS[decision];
          const enabled = canSubmitDecision(decision, draft);
          const isSuggested = decision === current.suggested_decision;
          return (
            <button
              key={decision}
              type="button"
              onClick={() => decide(decision)}
              disabled={!enabled || pending !== null || submit.isPending}
              aria-disabled={!enabled || pending !== null || submit.isPending}
              className={cn(
                "group rounded-lg border p-3 text-left transition-all",
                "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                enabled && pending === null
                  ? "hover:border-foreground/30 hover:shadow-sm"
                  : "cursor-not-allowed opacity-50",
                isSuggested
                  ? "border-emerald-600/40 bg-emerald-50/60 dark:bg-emerald-950/20"
                  : "border-border",
              )}
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                <Icon className="size-4" />
                {DECISION_LABELS[decision]}
                {isSuggested && (
                  <Badge variant="secondary" className="ml-auto text-[10px]">
                    suggested
                  </Badge>
                )}
              </span>
              <span className="text-muted-foreground mt-1 block text-xs leading-snug">
                {decisionConsequence(decision)}
              </span>
            </button>
          );
        })}
      </div>

      {submit.error && (
        <p className="text-destructive text-sm">{submit.error.message}</p>
      )}
    </div>
  );
}

export function MemoryScopeSettingsPage() {
  const { projects } = useActiveWorkspaceProjects();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [confirmingRollback, setConfirmingRollback] = useState(false);

  const projectList = projects.data ?? [];
  const activeId = selectedId ?? projectList[0]?.id ?? null;
  const active = projectList.find((project) => project.id === activeId) ?? null;

  const overview = useMigrationOverview(activeId);
  const manifest = useMigrationManifest(activeId);
  const rollback = useRollbackMigration(activeId);

  const counts = overview.data?.counts ?? EMPTY_COUNTS;
  const rows = countRows(counts);

  if (projects.isPending) {
    return (
      <div className="text-muted-foreground py-10 text-center text-sm">
        Loading projects…
      </div>
    );
  }
  if (!active) {
    return (
      <div className="border-border rounded-lg border border-dashed p-6 text-center">
        <p className="text-sm font-medium">No projects yet</p>
        <p className="text-muted-foreground mt-1 text-sm">
          Memory scope migration applies to a project’s memory, so create a
          project first.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-lg font-semibold">
          Memory migration{" "}
          <span className="text-muted-foreground font-normal">
            · {active.name}
          </span>
        </h2>
        <p className="text-muted-foreground mt-2 max-w-2xl text-sm">
          Legacy memory was stored per person. This moves it onto explicit
          scopes. Nothing becomes visible to the rest of the project until you
          decide, one fact at a time — there is no “share everything”.
        </p>
      </header>

      {projectList.length > 1 && (
        <ProjectPicker
          projects={projectList}
          selectedId={activeId}
          onSelect={(id) => {
            setSelectedId(id);
            setReviewing(false);
            setConfirmingRollback(false);
          }}
        />
      )}

      {overview.error ? (
        <p className="text-destructive text-sm">{overview.error.message}</p>
      ) : reviewing ? (
        <ReviewDrawer
          projectId={active.id}
          onClose={() => setReviewing(false)}
        />
      ) : (
        <>
          <dl className="divide-border border-border divide-y rounded-xl border">
            {rows.map((row) => (
              <div
                key={row.key}
                className="flex items-baseline justify-between px-4 py-3"
              >
                <dt
                  className={cn(
                    "text-sm",
                    row.actionable ? "font-medium" : "text-muted-foreground",
                  )}
                >
                  {row.label}
                </dt>
                <dd
                  className={cn(
                    "tabular-nums",
                    row.actionable
                      ? "text-2xl font-semibold"
                      : "text-muted-foreground text-base",
                  )}
                >
                  {row.value}
                </dd>
              </div>
            ))}
          </dl>

          {isMigrationEmpty(counts) && (
            <p className="text-muted-foreground text-sm">
              This project has no legacy memory to migrate.
            </p>
          )}

          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => setReviewing(true)}
              disabled={counts.private_legacy === 0}
              className={cn(counts.private_legacy === 0 && "opacity-50")}
            >
              {reviewCallToAction(counts)}
            </Button>
            <Button
              variant="outline"
              disabled={manifest.isPending}
              onClick={() =>
                manifest.mutate(undefined, {
                  onSuccess: (data) =>
                    downloadJson(data, `memory-manifest-${active.slug}.json`),
                })
              }
            >
              <Download className="size-4" /> Download manifest
            </Button>
            <Button
              variant="ghost"
              disabled={rollback.isPending}
              onClick={() => {
                if (!confirmingRollback) {
                  setConfirmingRollback(true);
                  return;
                }
                rollback.mutate(undefined, {
                  onSettled: () => setConfirmingRollback(false),
                });
              }}
              title="Remove the shared copies created from your private memory"
            >
              <RotateCcw className="size-4" />{" "}
              {confirmingRollback ? "Confirm rollback" : "Roll back my sharing"}
            </Button>
            {confirmingRollback && (
              <Button
                variant="ghost"
                disabled={rollback.isPending}
                onClick={() => setConfirmingRollback(false)}
              >
                Cancel
              </Button>
            )}
          </div>

          {rollback.data && (
            <p className="text-muted-foreground text-sm">
              Removed {rollback.data.reverted} shared{" "}
              {rollback.data.reverted === 1 ? "copy" : "copies"}. Private
              originals were never modified.
            </p>
          )}
          {(manifest.error ?? rollback.error) && (
            <p className="text-destructive text-sm">
              {(manifest.error ?? rollback.error)!.message}
            </p>
          )}
        </>
      )}
    </div>
  );
}
