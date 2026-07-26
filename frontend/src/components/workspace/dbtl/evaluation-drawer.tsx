"use client";

import { FlaskConical } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/core/auth/AuthProvider";
import {
  type EvaluationRow,
  type EvaluationStats,
  summarizeEvaluations,
  useProposalEvaluations,
  useRecordProposalOutcome,
} from "@/core/dbtl";
import { cn } from "@/lib/utils";

/**
 * The internal classifier evaluation drawer.
 *
 * Available only to authorized testers — the endpoint behind it is
 * administrator-only, so a non-admin sees the unavailable state rather than
 * an empty list that would read as "nothing was ever evaluated".
 *
 * It shows the shadow decision, its confidence band, the rule hits behind it,
 * and what the human chose, because those four together are what the exit
 * review needs to argue about a threshold.
 */
export function DbtlEvaluationDrawer({
  projectId,
  enabled,
  className,
}: {
  projectId: string | null;
  enabled: boolean;
  className?: string;
}) {
  const query = useProposalEvaluations(projectId, { enabled });
  const outcome = useRecordProposalOutcome(projectId);
  const { user } = useAuth();
  const [labelingId, setLabelingId] = useState<string | null>(null);

  function label(
    evaluationId: string,
    humanChoice: "start_setup" | "keep_ordinary",
  ) {
    setLabelingId(evaluationId);
    outcome.mutate(
      { evaluationId, outcome: humanChoice },
      { onSettled: () => setLabelingId(null) },
    );
  }

  if (!enabled || !projectId) {
    return null;
  }

  if (query.isPending) {
    return (
      <p className="text-muted-foreground py-6 text-center text-sm">
        Loading shadow evaluations…
      </p>
    );
  }

  if (query.error || !query.data) {
    return (
      <div className="border-border rounded-lg border border-dashed p-4">
        <p className="text-sm font-medium">Evaluations unavailable</p>
        <p className="text-muted-foreground mt-1 text-sm">
          {query.error?.message ??
            "Classifier evaluations are restricted to administrators."}
        </p>
      </div>
    );
  }

  const { evaluations, stats } = query.data;

  return (
    <section className={cn("space-y-4", className)}>
      <header className="flex items-center gap-2">
        <FlaskConical className="text-muted-foreground size-4" />
        <h3 className="text-sm font-semibold">Classifier shadow evaluations</h3>
        <Badge variant="outline">internal</Badge>
      </header>

      <RateSummary stats={stats} />

      {evaluations.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No requests have been evaluated in this project yet.
        </p>
      ) : (
        <ul className="divide-border border-border divide-y rounded-lg border">
          {evaluations.map((row) => (
            <EvaluationItem
              key={row.evaluation_id}
              row={row}
              canLabel={row.user_id === user?.id}
              labeling={labelingId === row.evaluation_id}
              onLabel={(choice) => label(row.evaluation_id, choice)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function RateSummary({ stats }: { stats: EvaluationStats }) {
  const summary = summarizeEvaluations(stats);
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Metric label="Evaluated" value={String(stats.total)} />
      <Metric label="Proposed" value={String(stats.proposed)} />
      <Metric
        label="False upgrades"
        value={formatRate(summary.falseUpgradeRate)}
        detail={`${stats.false_upgrades} of ${stats.proposed} proposed`}
      />
      <Metric
        label="Missed cycles"
        value={formatRate(summary.missedCycleRate)}
        detail={`${stats.missed_cycles} of ${stats.classifier_ordinary} classifier-ordinary`}
      />
    </dl>
  );
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="border-border rounded-md border px-3 py-2">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold tabular-nums">{value}</dd>
      {detail && <p className="text-muted-foreground text-xs">{detail}</p>}
    </div>
  );
}

function EvaluationItem({
  row,
  canLabel,
  labeling,
  onLabel,
}: {
  row: EvaluationRow;
  canLabel: boolean;
  labeling: boolean;
  onLabel: (choice: "start_setup" | "keep_ordinary") => void;
}) {
  return (
    <li className="space-y-1.5 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{row.route_kind.replace("_", " ")}</Badge>
        <span className="text-muted-foreground text-xs">
          via {row.route_source.replace("_", " ")}
        </span>
        <span className="text-muted-foreground text-xs">
          · {row.band} ({row.confidence.toFixed(2)})
        </span>
        <span className="ml-auto text-xs">{outcomeLabel(row)}</span>
      </div>

      {row.proposed_objective && (
        <p className="text-sm">{row.proposed_objective}</p>
      )}

      {row.rule_hits.length > 0 && (
        <p className="text-muted-foreground text-xs">
          {row.rule_hits
            .map((hit) => `${hit.rule_id} (“${hit.evidence}”)`)
            .join(" · ")}
        </p>
      )}

      {row.human_choice === null &&
        row.route_source === "classifier" &&
        row.route_kind === "ordinary" &&
        canLabel && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <span className="text-muted-foreground text-xs">
            Calibrate this ordinary decision:
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={labeling}
            onClick={() => onLabel("keep_ordinary")}
          >
            Correctly ordinary
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={labeling}
            onClick={() => onLabel("start_setup")}
          >
            Should start a cycle
          </Button>
        </div>
      )}
    </li>
  );
}

/**
 * Whether this row was a false upgrade or a missed cycle, stated in words.
 *
 * The same judgement the stats aggregate, spelled out per row so a reviewer
 * can find the specific request behind a rate they disagree with.
 */
function outcomeLabel(row: EvaluationRow): string {
  if (row.human_choice === null) {
    return "awaiting choice";
  }
  const proposed = row.route_kind === "proposal";
  if (proposed && (row.human_choice === "keep_ordinary" || row.human_choice === "dismissed")) {
    return `${row.human_choice.replace("_", " ")} — false upgrade`;
  }
  if (
    row.route_kind === "ordinary" &&
    row.route_source === "classifier" &&
    row.human_choice === "start_setup"
  ) {
    return "start setup — missed cycle";
  }
  return row.human_choice.replace("_", " ");
}

function formatRate(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}
