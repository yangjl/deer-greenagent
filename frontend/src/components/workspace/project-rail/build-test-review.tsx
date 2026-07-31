"use client";

import { AlertTriangle } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  CHECK_STATUS_LABELS,
  OUTCOME_LABELS,
  VALIDITY_CHECK_LABELS,
  assessmentHeadline,
  outcomeTone,
  projectedValidity,
  recommendationOptions,
  type BuildTestView,
  type CheckStatus,
  type HeadlineMetric,
  type ValidityCheck,
  type WorkflowRecommendation,
  useRecordValidityAssessment,
} from "@/core/dbtl";
import { uuid } from "@/core/utils/uuid";
import { cn } from "@/lib/utils";

const CHECK_STATUSES: CheckStatus[] = [
  "passed",
  "failed",
  "missing",
  "not_applicable",
];

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

function AssessmentForm({
  projectId,
  cycleId,
  dbRevision,
  packKey,
  requiredChecks,
}: {
  projectId: string;
  cycleId: string;
  dbRevision: number;
  packKey: string;
  requiredChecks: string[];
}) {
  const mutation = useRecordValidityAssessment(projectId, cycleId);
  const [metricName, setMetricName] = useState("accuracy");
  const [metricValue, setMetricValue] = useState("");
  const [metricThreshold, setMetricThreshold] = useState("");
  const [plausibleMax, setPlausibleMax] = useState("");
  const [checks, setChecks] = useState<Record<string, ValidityCheck>>(() =>
    Object.fromEntries(
      requiredChecks.map((check) => [
        check,
        { check, status: "missing", detail: "", evidence_refs: [] },
      ]),
    ),
  );
  const [rationale, setRationale] = useState("");
  const [limitations, setLimitations] = useState("");
  const [route, setRoute] = useState<WorkflowRecommendation>("repeat_test");

  const metric = useMemo<HeadlineMetric | null>(() => {
    const value = Number(metricValue);
    const threshold = Number(metricThreshold);
    const ceiling = plausibleMax.trim() ? Number(plausibleMax) : null;
    if (
      !metricName.trim() ||
      !metricValue.trim() ||
      !metricThreshold.trim() ||
      !Number.isFinite(value) ||
      !Number.isFinite(threshold) ||
      (ceiling !== null && !Number.isFinite(ceiling))
    )
      return null;
    return {
      name: metricName.trim(),
      value,
      threshold,
      criterion: "gte",
      plausible_max: ceiling,
      unit: "",
    };
  }, [metricName, metricValue, metricThreshold, plausibleMax]);
  const checkList = Object.values(checks);
  const projected = projectedValidity(
    metric ? [metric] : [],
    checkList,
    requiredChecks,
  );
  const routes = recommendationOptions(projected);
  const effectiveRoute = routes.some((item) => item.id === route)
    ? route
    : routes[0]!.id;
  const checksComplete = checkList.every(
    (check) =>
      (check.status !== "passed" ||
        Boolean(check.detail.trim() && check.evidence_refs[0])) &&
      (check.status !== "failed" || Boolean(check.detail.trim())),
  );
  const ready = Boolean(metric && rationale.trim() && checksComplete);

  function updateCheck(check: string, patch: Partial<ValidityCheck>) {
    setChecks((current) => ({
      ...current,
      [check]: { ...current[check]!, ...patch },
    }));
  }

  return (
    <form
      className="space-y-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready || !metric) return;
        mutation.mutate({
          metrics: [metric],
          checks: checkList,
          recommendation: effectiveRoute,
          limitations: limitations
            .split("\n")
            .map((item) => item.trim())
            .filter(Boolean),
          rationale: rationale.trim(),
          expectedDbRevision: dbRevision,
          idempotencyKey: `validity-${uuid()}`,
        });
      }}
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold">Human validity review</p>
          <p className="text-muted-foreground text-xs">
            {packKey} · outcome is computed from the evidence below
          </p>
        </div>
        <Badge variant="outline">Human decision</Badge>
      </div>

      <div>
        <p className="text-[11px] font-semibold tracking-widest uppercase">
          Headline result
        </p>
        <div className="mt-2 grid gap-2 sm:grid-cols-3">
          <Input
            aria-label="Metric name"
            value={metricName}
            onChange={(event) => setMetricName(event.target.value)}
            placeholder="accuracy"
          />
          <Input
            aria-label="Metric value"
            value={metricValue}
            onChange={(event) => setMetricValue(event.target.value)}
            inputMode="decimal"
            placeholder="0.94"
          />
          <Input
            aria-label="Success threshold"
            value={metricThreshold}
            onChange={(event) => setMetricThreshold(event.target.value)}
            inputMode="decimal"
            placeholder="≥ threshold"
          />
        </div>
        <Input
          className="mt-2"
          aria-label="Plausible maximum"
          value={plausibleMax}
          onChange={(event) => setPlausibleMax(event.target.value)}
          inputMode="decimal"
          placeholder="Plausible maximum (optional)"
        />
      </div>

      <div>
        <p className="text-[11px] font-semibold tracking-widest uppercase">
          Validity assessment
        </p>
        <div className="divide-border border-border mt-2 divide-y border-y">
          {checkList.map((check) => (
            <div
              key={check.check}
              className="grid gap-2 py-3 sm:grid-cols-[1fr_9rem]"
            >
              <label className="text-sm font-medium">
                {VALIDITY_CHECK_LABELS[check.check]}
              </label>
              <select
                aria-label={`${VALIDITY_CHECK_LABELS[check.check]} status`}
                className="border-input bg-background h-9 rounded-md border px-2 text-sm"
                value={check.status}
                onChange={(event) =>
                  updateCheck(check.check, {
                    status: event.target.value as CheckStatus,
                  })
                }
              >
                {CHECK_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {CHECK_STATUS_LABELS[status]}
                  </option>
                ))}
              </select>
              <Input
                className="sm:col-span-2"
                aria-label={`${VALIDITY_CHECK_LABELS[check.check]} detail`}
                value={check.detail}
                onChange={(event) =>
                  updateCheck(check.check, { detail: event.target.value })
                }
                placeholder="Evidence detail"
              />
              {check.status === "passed" && (
                <Input
                  className="sm:col-span-2"
                  aria-label={`${VALIDITY_CHECK_LABELS[check.check]} evidence`}
                  value={check.evidence_refs[0] ?? ""}
                  onChange={(event) =>
                    updateCheck(check.check, {
                      evidence_refs: event.target.value
                        ? [event.target.value]
                        : [],
                    })
                  }
                  placeholder="Evidence reference"
                />
              )}
            </div>
          ))}
        </div>
      </div>

      <Outcome
        outcome={projected.outcome}
        headline={assessmentHeadline(projected)}
      />

      <label className="block space-y-1.5">
        <span className="text-xs font-medium">Workflow recommendation</span>
        <select
          className="border-input bg-background h-9 w-full rounded-md border px-2 text-sm"
          value={effectiveRoute}
          onChange={(event) =>
            setRoute(event.target.value as WorkflowRecommendation)
          }
        >
          {routes.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <Textarea
        value={rationale}
        onChange={(event) => setRationale(event.target.value)}
        rows={3}
        placeholder="Reviewer rationale (required)"
        aria-label="Validity rationale"
      />
      <Textarea
        value={limitations}
        onChange={(event) => setLimitations(event.target.value)}
        rows={2}
        placeholder="Limitations, one per line"
        aria-label="Validity limitations"
      />
      <Button type="submit" size="sm" disabled={!ready || mutation.isPending}>
        {mutation.isPending ? "Recording…" : "Record outcome and route"}
      </Button>
      {!ready && (
        <p className="text-muted-foreground text-xs">
          Add a metric, rationale, and evidence for every passed check. Missing
          evidence may be recorded explicitly as Missing.
        </p>
      )}
      {mutation.error && (
        <p className="text-destructive text-sm" role="alert">
          {mutation.error.message}
        </p>
      )}
    </form>
  );
}

export function BuildTestReview({
  projectId,
  cycleId,
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
    <AssessmentForm
      projectId={projectId}
      cycleId={cycleId}
      dbRevision={view.db_revision}
      packKey={view.validity_pack.pack_key}
      requiredChecks={view.validity_pack.required_checks}
    />
  );
}
