"use client";

import { AlertTriangle } from "lucide-react";

import {
  CHECK_STATUS_LABELS,
  OUTCOME_LABELS,
  VALIDITY_CHECK_LABELS,
  assessmentHeadline,
  outcomeTone,
  projectedValidity,
  type BuildTestView,
} from "@/core/dbtl";
import { cn } from "@/lib/utils";

function shortHash(value: string) {
  return value.length > 16 ? `${value.slice(0, 12)}…` : value;
}

function Outcome({
  outcome,
  headline,
}: {
  outcome: keyof typeof OUTCOME_LABELS;
  headline: string;
}) {
  const tone = outcomeTone(outcome);
  return (
    <div
      className={cn(
        "border-l-4 px-4 py-3",
        tone === "critical" && "border-l-destructive bg-destructive/5",
        tone === "pending" && "border-l-amber-500 bg-amber-500/5",
        tone === "positive" && "border-l-emerald-600 bg-emerald-500/5",
        tone === "neutral" && "border-l-foreground/30 bg-muted/40",
      )}
    >
      <p className="text-[11px] font-semibold tracking-widest uppercase">
        Scientific outcome
      </p>
      <p className="mt-1 text-lg font-semibold">{OUTCOME_LABELS[outcome]}</p>
      <p className="text-muted-foreground mt-1 text-sm leading-relaxed">
        {headline}
      </p>
    </div>
  );
}

function BuildLineagePanel({ view }: { view: BuildTestView }) {
  const lineage = view.build_lineage;
  if (!lineage) {
    return (
      <div className="border-border border-l-2 pl-4">
        <p className="text-sm font-medium">Reproducibility record required</p>
        <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
          Run Build in this cycle context. Submission stays blocked until the
          system records server-bound inputs, code and config revisions,
          environment, outputs, deviations, and logs.
        </p>
      </div>
    );
  }
  return (
    <div className="divide-border border-border divide-y border-y">
      <div className="grid gap-4 py-3 sm:grid-cols-2">
        <div>
          <p className="text-muted-foreground text-[11px] uppercase">
            Dataset binding
          </p>
          <p
            className="mt-1 font-mono text-xs"
            title={lineage.dataset_fingerprint}
          >
            {shortHash(lineage.dataset_fingerprint)}
          </p>
        </div>
        <div>
          <p className="text-muted-foreground text-[11px] uppercase">
            Stage contract
          </p>
          <p className="mt-1 font-mono text-xs">{lineage.stage_spec_key}</p>
        </div>
      </div>
      <div className="grid gap-4 py-3 sm:grid-cols-2">
        <div>
          <p className="text-muted-foreground text-[11px] uppercase">
            Code revision
          </p>
          <p className="mt-1 font-mono text-xs break-all">
            {lineage.code_revision}
          </p>
        </div>
        <div>
          <p className="text-muted-foreground text-[11px] uppercase">
            Config revision
          </p>
          <p className="mt-1 font-mono text-xs break-all">
            {lineage.config_revision}
          </p>
        </div>
      </div>
      <div className="py-3">
        <p className="text-muted-foreground text-[11px] uppercase">
          Versioned outputs
        </p>
        <ul className="mt-1.5 space-y-1">
          {lineage.output_artifacts.map((item) => (
            <li key={`${item.uri}:${item.revision}`} className="text-xs">
              <span className="font-medium">rev {item.revision}</span>{" "}
              <span className="text-muted-foreground break-all">
                {item.uri}
              </span>
              <span className="text-muted-foreground ml-2 font-mono">
                {shortHash(item.content_hash)}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="py-3">
        <p className="text-muted-foreground text-[11px] uppercase">
          Environment
        </p>
        <p className="mt-1 font-mono text-xs leading-relaxed">
          {Object.entries(lineage.environment)
            .map(([key, value]) => `${key}=${String(value)}`)
            .join(" · ")}
        </p>
        {lineage.deviations.length > 0 && (
          <p className="mt-2 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            {lineage.deviations.join(" ")}
          </p>
        )}
      </div>
    </div>
  );
}

function RecordedAssessment({ view }: { view: BuildTestView }) {
  const assessment = view.validity_assessment;
  if (!assessment) return null;
  const projected = projectedValidity(
    assessment.headline_metrics,
    assessment.checks,
    assessment.checks.map((check) => check.check),
  );
  return (
    <div className="space-y-4">
      <Outcome
        outcome={assessment.outcome}
        headline={assessmentHeadline(projected)}
      />
      <div className="grid gap-6 sm:grid-cols-2">
        <div>
          <p className="text-sm font-semibold">Headline result</p>
          <div className="divide-border mt-2 divide-y">
            {assessment.headline_metrics.map((metric) => (
              <div
                key={metric.name}
                className="flex justify-between gap-3 py-2 text-sm"
              >
                <span>{metric.name}</span>
                <span className="font-mono">
                  {metric.value}
                  {metric.unit}
                </span>
              </div>
            ))}
          </div>
        </div>
        <div>
          <p className="text-sm font-semibold">Validity assessment</p>
          <div className="divide-border mt-2 divide-y">
            {assessment.checks.map((check) => (
              <div
                key={check.check}
                className="flex justify-between gap-3 py-2 text-sm"
              >
                <span>{VALIDITY_CHECK_LABELS[check.check] ?? check.check}</span>
                <span className="font-medium">
                  {CHECK_STATUS_LABELS[check.status]}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <p className="text-muted-foreground text-xs">
        {assessment.validity_pack_key} · reviewed by{" "}
        {assessment.reviewer_user_id} ·{" "}
        {assessment.recommendation.replaceAll("_", " ")}
      </p>
    </div>
  );
}

export function BuildTestReview({
  projectId: _projectId,
  cycleId: _cycleId,
  stage,
  stageStatus,
  view,
  isPending,
  error,
}: {
  projectId: string;
  cycleId: string;
  stage: "build" | "test";
  stageStatus: string;
  view: BuildTestView | null;
  isPending: boolean;
  error: Error | null;
}) {
  if (isPending) {
    return <p className="text-muted-foreground text-sm">Loading provenance…</p>;
  }
  if (!view) {
    return (
      <p className="text-destructive text-sm" role="alert">
        {error?.message ?? "Build and Test records are unavailable."}
      </p>
    );
  }
  if (stage === "build") return <BuildLineagePanel view={view} />;
  if (view.validity_assessment) return <RecordedAssessment view={view} />;
  if (stageStatus !== "awaiting_review") {
    return (
      <p className="text-muted-foreground text-sm">
        Submit Test evidence before a human records the validity outcome.
      </p>
    );
  }
  return (
    <div className="border-border border-l-2 pl-4">
      <p className="text-sm font-medium">Decision required in chat</p>
      <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
        Return to the originating conversation. The Test evidence card lets you
        convene a review meeting or continue to the outcome decision; this
        inspector does not record human input.
      </p>
    </div>
  );
}
