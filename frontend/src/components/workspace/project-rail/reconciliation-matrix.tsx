"use client";

/**
 * The data readiness and reconciliation matrix (Phase 6).
 *
 * The panel's job is to make a disagreement between two sources impossible to
 * approve past by accident, so three things are deliberate:
 *
 * - **Blocking rows sort to the top.** A matrix in creation order buries the one
 *   blocked row under twelve resolved ones.
 * - **Every status carries a word.** Tone is a second signal, never the only one.
 * - **The decision control states its consequence.** Settling a contradiction is
 *   a scientific call, and "Resolve" alone does not say Build will proceed on it.
 *
 * All of the judgement lives in `@/core/dbtl/reconciliation-view`; this file
 * renders it.
 */

import { AlertTriangle, Database, GitCompare, ShieldAlert } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  BLOCKER_KIND_LABELS,
  type BlockerKind,
  DATASET_ROLE_LABELS,
  type HumanDecision,
  ROW_STATUS_LABELS,
  type ReconciliationRow,
  type StatusTone,
  canDecideRow,
  canSubmitDecision,
  decidedBy,
  decisionOptions,
  gateHeadline,
  gateTone,
  invalidationNotice,
  mutableRawSources,
  orderedRows,
  reconciliationBlockReason,
  requiresBlockerKind,
  rowStatusTone,
  shortHash,
  sourceCells,
  useDecideReconciliationRow,
  useReconciliation,
} from "@/core/dbtl";
import { uuid } from "@/core/utils/uuid";
import { cn } from "@/lib/utils";

const TONE_CLASSES: Record<StatusTone, string> = {
  neutral: "border-border text-muted-foreground",
  pending: "border-amber-500/40 text-amber-700 dark:text-amber-400",
  positive: "border-emerald-500/40 text-emerald-700 dark:text-emerald-400",
  critical: "border-red-500/50 text-red-700 dark:text-red-400",
};

const BLOCKER_KINDS: BlockerKind[] = [
  "missing_data",
  "conflicting_sources",
  "indeterminate",
];

function ToneBadge({ tone, children }: { tone: StatusTone; children: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("shrink-0 text-[10px] font-medium", TONE_CLASSES[tone])}
    >
      {children}
    </Badge>
  );
}

function RowDecisionForm({
  row,
  projectId,
  cycleId,
  dbRevision,
  workItemRevision,
}: {
  row: ReconciliationRow;
  projectId: string;
  cycleId: string;
  dbRevision: number;
  workItemRevision: number;
}) {
  const [decision, setDecision] = useState<HumanDecision>("resolved");
  const [rationale, setRationale] = useState("");
  const [blockerKind, setBlockerKind] = useState<BlockerKind | null>(null);
  const decide = useDecideReconciliationRow(projectId, cycleId);

  const options = decisionOptions(row);
  const active = options.find((item) => item.id === decision)!;
  const ready = canSubmitDecision(decision, rationale, blockerKind);

  return (
    <form
      className="mt-3 space-y-2 border-t pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready) return;
        decide.mutate(
          {
            rowId: row.row_id,
            status: decision,
            resolution: rationale.trim(),
            blockerKind: requiresBlockerKind(decision) ? blockerKind : null,
            expectedDbRevision: dbRevision,
            expectedWorkItemRevision: workItemRevision,
            idempotencyKey: `decide-${uuid()}`,
          },
          { onSuccess: () => setRationale("") },
        );
      }}
    >
      <div className="flex flex-wrap gap-1.5">
        {options.map((option) => (
          <Button
            key={option.id}
            type="button"
            size="sm"
            variant={option.id === decision ? "secondary" : "ghost"}
            className="h-7 text-xs"
            onClick={() => {
              setDecision(option.id);
              if (option.id !== "blocked") setBlockerKind(null);
            }}
          >
            {option.label}
          </Button>
        ))}
      </div>

      <p className="text-muted-foreground text-xs">{active.consequence}</p>

      {requiresBlockerKind(decision) && (
        <div className="flex flex-wrap gap-1.5">
          {BLOCKER_KINDS.map((kind) => (
            <Button
              key={kind}
              type="button"
              size="sm"
              variant={kind === blockerKind ? "secondary" : "outline"}
              className="h-7 text-xs"
              onClick={() => setBlockerKind(kind)}
            >
              {BLOCKER_KIND_LABELS[kind]}
            </Button>
          ))}
        </div>
      )}

      <Textarea
        value={rationale}
        onChange={(event) => setRationale(event.target.value)}
        placeholder="Why this decision? This goes on the record with your name."
        className="min-h-16 text-xs"
      />

      {decide.error && (
        <p className="text-xs text-red-600 dark:text-red-400">
          {decide.error.message}
        </p>
      )}

      <Button
        type="submit"
        size="sm"
        className="h-7 text-xs"
        disabled={!ready || decide.isPending}
      >
        {decide.isPending ? "Recording…" : "Record decision"}
      </Button>
    </form>
  );
}

function MatrixRow({
  row,
  projectId,
  cycleId,
  dbRevision,
}: {
  row: ReconciliationRow;
  projectId: string;
  cycleId: string;
  dbRevision: number;
}) {
  const [open, setOpen] = useState(false);
  const cells = sourceCells(row);

  return (
    <li
      className={cn(
        "rounded-md border px-3 py-2 transition-colors",
        row.blocks_gate ? "bg-muted/40" : "bg-transparent",
        row.status === "blocked" && "border-red-500/40",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{row.field_name}</p>
          <p className="text-muted-foreground truncate text-[11px]">
            {row.check_label}
            {!row.required && " · optional"}
          </p>
        </div>
        <ToneBadge tone={rowStatusTone(row.status)}>
          {ROW_STATUS_LABELS[row.status]}
        </ToneBadge>
      </div>

      {cells.length > 0 && (
        <dl className="mt-2 grid grid-cols-2 gap-2">
          {cells.map((cell, index) => (
            <div key={`${cell.label}-${index}`} className="min-w-0">
              <dt className="text-muted-foreground text-[10px] tracking-wide uppercase">
                {cell.label || `Source ${index + 1}`}
              </dt>
              <dd className="truncate font-mono text-xs">{cell.value || "—"}</dd>
            </div>
          ))}
        </dl>
      )}

      {row.resolution && (
        <p className="mt-2 text-xs">
          <span className="text-muted-foreground">{decidedBy(row)}: </span>
          {row.resolution}
        </p>
      )}

      {row.evidence_refs.length > 0 && (
        <div className="mt-2">
          <p className="text-muted-foreground text-[10px] tracking-wide uppercase">
            Evidence
          </p>
          <ul className="mt-1 space-y-0.5">
            {row.evidence_refs.map((reference) => (
              <li
                key={reference}
                className="text-muted-foreground break-all font-mono text-[11px]"
              >
                {reference}
              </li>
            ))}
          </ul>
        </div>
      )}

      {canDecideRow(row) &&
        (open ? (
          <RowDecisionForm
            row={row}
            projectId={projectId}
            cycleId={cycleId}
            dbRevision={dbRevision}
            workItemRevision={row.db_revision}
          />
        ) : (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="mt-2 h-7 px-2 text-xs"
            onClick={() => setOpen(true)}
          >
            {row.status === "proposed" ? "Review the proposal" : "Decide"}
          </Button>
        ))}
    </li>
  );
}

export function ReconciliationMatrix({
  projectId,
  cycleId,
}: {
  projectId: string;
  cycleId: string | null;
}) {
  const query = useReconciliation(projectId, cycleId);
  const view = query.data;

  if (query.isPending) {
    return <p className="text-muted-foreground text-xs">Loading the matrix…</p>;
  }
  if (query.error) {
    return (
      <p className="text-xs text-red-600 dark:text-red-400">
        {query.error.message}
      </p>
    );
  }
  if (!view) return null;

  const blocked = reconciliationBlockReason(view);
  const notice = invalidationNotice(view);
  const writableRaw = mutableRawSources(view.datasets);
  const rows = orderedRows(view.rows);

  return (
    <div className="space-y-3">
      <div
        className={cn(
          "rounded-md border px-3 py-2",
          TONE_CLASSES[gateTone(view.gate)],
        )}
      >
        <p className="text-sm font-medium">{gateHeadline(view.gate)}</p>
        {view.gate.reasons.length > 0 && (
          <ul className="text-muted-foreground mt-1 space-y-0.5 text-xs">
            {view.gate.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        )}
      </div>

      {notice && (
        <div className="flex gap-2 rounded-md border border-amber-500/40 px-3 py-2">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-600" />
          <div>
            <p className="text-xs font-medium">{notice.headline}</p>
            <ul className="text-muted-foreground mt-0.5 space-y-0.5 text-xs">
              {notice.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {writableRaw.length > 0 && (
        <div className="flex gap-2 rounded-md border border-red-500/40 px-3 py-2">
          <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-red-600" />
          <p className="text-xs">
            Declared raw, but not marked immutable:{" "}
            {writableRaw.map((item) => item.source_key).join(", ")}. Build must
            not be able to write to raw data.
          </p>
        </div>
      )}

      <section className="space-y-1.5">
        <h4 className="text-muted-foreground flex items-center gap-1.5 text-[11px] font-semibold tracking-widest uppercase">
          <Database className="size-3.5" />
          Declared inputs ({view.datasets.length})
        </h4>
        {view.datasets.length === 0 ? (
          <p className="text-muted-foreground text-xs">
            No data sources have been declared for this cycle yet.
          </p>
        ) : (
          <ul className="space-y-1">
            {view.datasets.map((item) => (
              <li
                key={item.id}
                className="flex items-baseline justify-between gap-2 text-xs"
              >
                <span className="min-w-0 truncate">
                  <span className="font-medium">{item.source_key}</span>{" "}
                  <span className="text-muted-foreground">{item.uri}</span>
                </span>
                <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
                  {DATASET_ROLE_LABELS[item.role]} · {shortHash(item.content_hash)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-1.5">
        <h4 className="text-muted-foreground flex items-center gap-1.5 text-[11px] font-semibold tracking-widest uppercase">
          <GitCompare className="size-3.5" />
          Reconciliation ({rows.length})
        </h4>
        {blocked && <p className="text-muted-foreground text-xs">{blocked}</p>}
        {rows.length === 0 ? (
          <p className="text-muted-foreground text-xs">
            No reconciliation rows have been opened yet.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {rows.map((row) => (
              <MatrixRow
                key={row.row_id}
                row={row}
                projectId={projectId}
                cycleId={view.cycle_id}
                dbRevision={view.db_revision}
              />
            ))}
          </ul>
        )}
      </section>

      {view.unreadable_row_ids.length > 0 && (
        <p className="text-xs text-red-600 dark:text-red-400">
          {view.unreadable_row_ids.length} reconciliation row(s) could not be read
          and block the gate. This needs an operator.
        </p>
      )}
    </div>
  );
}
