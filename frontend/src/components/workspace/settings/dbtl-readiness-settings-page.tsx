"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Database,
  Download,
  FileJson2,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DbtlEvaluationDrawer } from "@/components/workspace/dbtl";
import {
  type DbtlGovernanceReport,
  buildReadinessExport,
  groupReadinessItems,
  isValidationStale,
  useApproveDbtlCutover,
  useDbtlGovernance,
  useDbtlReadiness,
  useValidateDbtlGovernance,
} from "@/core/dbtl";
import { useActiveWorkspaceProjects } from "@/core/workspaces";
import { cn } from "@/lib/utils";

const GROUP_LABELS = {
  compatible: "Compatible",
  repairable: "Repairable",
  invalid_or_ambiguous: "Invalid or ambiguous",
  safe_to_supersede: "Safe to supersede",
} as const;

function downloadReport(contents: string, filename: string) {
  const url = URL.createObjectURL(
    new Blob([contents], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function discoveryRolloutSummary(
  discovery: DbtlGovernanceReport["conversational_discovery"],
) {
  if (!discovery.enabled) {
    return "Discovery is disabled. The rollback path is the server-owned immediate setup card.";
  }
  if (discovery.automatic_offers) {
    return "Ready discoveries may be offered automatically; explicit starts use the same durable path.";
  }
  return "Explicit cycle starts use durable discovery; automatic offers remain disabled.";
}

export function DbtlReadinessSettingsPage() {
  const readiness = useDbtlReadiness();
  const governance = useDbtlGovernance();
  const validation = useValidateDbtlGovernance();
  const cutover = useApproveDbtlCutover();
  const { projects } = useActiveWorkspaceProjects();
  const [showEvaluations, setShowEvaluations] = useState(false);
  const [evaluationProjectId, setEvaluationProjectId] = useState<string | null>(
    null,
  );
  const evaluationProjects = projects.data ?? [];
  const evaluationProject =
    evaluationProjects.find((project) => project.id === evaluationProjectId) ??
    evaluationProjects[0] ??
    null;

  if (readiness.isPending) {
    return (
      <div className="text-muted-foreground py-10 text-center text-sm">
        Inspecting DBTL records…
      </div>
    );
  }
  if (readiness.error || !readiness.data) {
    return (
      <div className="border-destructive/30 bg-destructive/5 rounded-lg border p-4">
        <p className="font-medium">Readiness report unavailable</p>
        <p className="text-muted-foreground mt-1 text-sm">
          Workflow controls remain disabled because the safety contract could
          not be verified.
        </p>
      </div>
    );
  }

  const report = readiness.data;
  const groups = groupReadinessItems(report.items);
  const exported = buildReadinessExport(report);

  return (
    <div className="space-y-7">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <ShieldCheck className="size-5 text-emerald-700 dark:text-emerald-400" />
            <h2 className="text-lg font-semibold">DBTL readiness</h2>
            <Badge variant="outline" className="capitalize">
              {report.mode.replace("_", " ")}
            </Badge>
          </div>
          <p className="text-muted-foreground mt-2 max-w-2xl text-sm">
            A read-only preflight for GreenAgent’s Design–Build–Test–Learn
            workflow. Phase 0 inventories existing records before any migration
            or graph execution is permitted.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => downloadReport(exported.contents, exported.filename)}
        >
          <Download />
          Export report
        </Button>
      </header>

      <section className="border-border bg-muted/35 rounded-xl border p-4">
        <div className="flex items-start gap-3">
          <LockKeyhole className="mt-0.5 size-4 shrink-0 text-amber-700 dark:text-amber-400" />
          <div>
            <p className="text-sm font-medium">Safety boundary</p>
            <p className="text-muted-foreground mt-1 text-sm">
              {report.reason}
            </p>
            <p className="text-muted-foreground mt-2 text-xs">
              Policy contract: {report.policy_version}
            </p>
          </div>
        </div>
      </section>

      <section>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              <Database className="size-4" />
              Phase 1 · durable governance
            </h3>
            <p className="text-muted-foreground mt-1 max-w-2xl text-xs">
              Operator checks for the PostgreSQL authority, strict human review
              gates, revision binding, projection integrity, and legacy-data
              disposition. Passing these checks does not enable the DBTL graph.
            </p>
          </div>
          <div className="flex gap-2">
            {governance.data && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() =>
                  downloadReport(
                    `${JSON.stringify(governance.data, null, 2)}\n`,
                    `greenagent-dbtl-governance-${new Date().toISOString().slice(0, 10)}.json`,
                  )
                }
              >
                <Download />
                Download evidence
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              disabled={validation.isPending || Boolean(governance.error)}
              onClick={() => validation.mutate()}
            >
              <RefreshCw
                className={cn(validation.isPending && "animate-spin")}
              />
              Run validation
            </Button>
          </div>
        </div>

        {governance.isPending ? (
          <div className="text-muted-foreground mt-3 rounded-lg border px-4 py-5 text-sm">
            Checking the durable governance foundation…
          </div>
        ) : governance.error || !governance.data ? (
          <div className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50/60 p-4 dark:bg-amber-950/20">
            <p className="text-sm font-medium">Operator controls unavailable</p>
            <p className="text-muted-foreground mt-1 text-xs">
              {governance.error instanceof Error
                ? governance.error.message
                : "Sign in as an administrator to inspect or validate the Phase 1 foundation."}
            </p>
          </div>
        ) : (
          <div className="mt-3 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3">
              <div>
                <p className="text-sm font-medium">
                  {governance.data.passed_checks} of{" "}
                  {governance.data.total_checks} foundation checks passed
                </p>
                <p className="text-muted-foreground mt-0.5 text-xs">
                  Database: {governance.data.database_backend} · Schema:{" "}
                  {governance.data.schema_revision ?? "create-all"}
                </p>
              </div>
              <Badge
                variant={
                  governance.data.technical_ready ? "default" : "secondary"
                }
              >
                {governance.data.technical_ready
                  ? "Technically ready"
                  : "Cutover blocked"}
              </Badge>
            </div>

            <div className="rounded-lg border px-4 py-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">
                    Conversational discovery rollout
                  </p>
                  <p className="text-muted-foreground mt-0.5 max-w-2xl text-xs">
                    {discoveryRolloutSummary(
                      governance.data.conversational_discovery,
                    )}
                  </p>
                </div>
                <Badge variant="outline">
                  {governance.data.conversational_discovery.rollout_stage
                    .replaceAll("_", " ")
                    .replace(/^./, (value) => value.toUpperCase())}
                </Badge>
              </div>
              <div className="text-muted-foreground mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                <span>Cycle creation: server</span>
                <span>
                  Classifier entry:{" "}
                  {governance.data.conversational_discovery.classifier_entry
                    ? "on"
                    : "off"}
                </span>
                <span>
                  Project history:{" "}
                  {governance.data.conversational_discovery.project_history
                    ? "on"
                    : "off"}
                </span>
                <span>
                  Global memory:{" "}
                  {governance.data.conversational_discovery.global_memory
                    ? "on"
                    : "off"}
                </span>
              </div>
            </div>

            <div className="divide-y rounded-lg border">
              {governance.data.checks.map((check) => (
                <div
                  key={check.id}
                  className="flex items-start gap-3 px-4 py-3"
                >
                  {check.status === "passed" ? (
                    <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-700 dark:text-emerald-400" />
                  ) : check.status === "waiting" ? (
                    <Circle className="mt-0.5 size-4 shrink-0 text-amber-700 dark:text-amber-400" />
                  ) : (
                    <XCircle className="text-destructive mt-0.5 size-4 shrink-0" />
                  )}
                  <div>
                    <p className="text-sm font-medium">{check.title}</p>
                    <p className="text-muted-foreground mt-0.5 text-xs">
                      {check.detail}
                    </p>
                  </div>
                </div>
              ))}
            </div>

            {governance.data.projection_mismatches.length > 0 && (
              <details className="rounded-lg border p-3">
                <summary className="cursor-pointer text-sm font-medium">
                  View {governance.data.projection_mismatches.length} projection
                  mismatch(es)
                </summary>
                <div className="mt-3 space-y-2">
                  {governance.data.projection_mismatches.map((mismatch) => (
                    <code
                      key={mismatch.cycle_id}
                      className="bg-muted block rounded px-3 py-2 text-xs"
                    >
                      {mismatch.project_id} / {mismatch.cycle_id}
                    </code>
                  ))}
                </div>
              </details>
            )}

            <div className="bg-muted/35 rounded-lg border p-4">
              <p className="text-sm font-medium">Validation and recovery</p>
              <p className="text-muted-foreground mt-1 text-xs">
                {governance.data.last_validation
                  ? `Last validated ${new Date(
                      governance.data.last_validation.created_at,
                    ).toLocaleString()}${
                      isValidationStale(
                        governance.data.last_validation.created_at,
                      )
                        ? " · evidence is older than 24 hours"
                        : ""
                    }.`
                  : "No operator validation has been recorded."}
              </p>
              <p className="text-muted-foreground mt-2 text-xs">
                Rollback: {governance.data.rollback_posture}
              </p>
              {governance.data.operator_can_approve ? (
                <Button
                  type="button"
                  size="sm"
                  className="mt-3"
                  disabled={cutover.isPending}
                  onClick={() => {
                    const validationId =
                      governance.data.validation_id ??
                      governance.data.last_validation?.id;
                    if (validationId) cutover.mutate(validationId);
                  }}
                >
                  Approve PostgreSQL cutover
                </Button>
              ) : (
                <p className="text-muted-foreground mt-3 text-xs font-medium">
                  Cutover approval appears only after a fresh PostgreSQL
                  validation passes every technical check.
                </p>
              )}
              {(validation.error ?? cutover.error) && (
                <p className="text-destructive mt-2 text-xs">
                  {(validation.error ?? cutover.error)?.message}
                </p>
              )}
            </div>
          </div>
        )}
      </section>

      <section>
        <h3 className="text-sm font-semibold">Preflight checks</h3>
        <div className="mt-3 divide-y rounded-lg border">
          {report.checks.map((check) => (
            <div key={check.id} className="flex items-start gap-3 px-4 py-3">
              {check.status === "ready" ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-700 dark:text-emerald-400" />
              ) : (
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-700 dark:text-amber-400" />
              )}
              <div>
                <p className="text-sm font-medium capitalize">
                  {check.id.replaceAll("-", " ")}
                </p>
                <p className="text-muted-foreground mt-0.5 text-xs">
                  {check.summary}
                </p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold">Legacy record inventory</h3>
        <p className="text-muted-foreground mt-1 text-xs">
          Classification is advisory and read-only. Human review decides what
          may later be repaired or superseded.
        </p>
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          {groups.map((group) => (
            <div key={group.classification} className="rounded-lg border p-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium">
                  {GROUP_LABELS[group.classification]}
                </p>
                <Badge
                  variant="secondary"
                  className={cn(
                    group.classification === "invalid_or_ambiguous" &&
                      "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
                  )}
                >
                  {group.items.length}
                </Badge>
              </div>
              {group.items.length ? (
                <div className="mt-3 space-y-2">
                  {group.items.map((item) => (
                    <div
                      key={item.path}
                      className="bg-muted/45 rounded-md px-3 py-2"
                    >
                      <div className="flex items-center gap-2">
                        <FileJson2 className="text-muted-foreground size-3.5 shrink-0" />
                        <code className="min-w-0 truncate text-xs">
                          {item.path}
                        </code>
                      </div>
                      <p className="text-muted-foreground mt-1 text-xs">
                        {item.reason}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-muted-foreground mt-3 text-xs">
                  No records in this category.
                </p>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Phase 4: the internal evaluation drawer. Opt-in rather than always
          loaded, because the endpoint behind it is administrator-only and an
          ordinary member opening settings should not generate a 403. */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold">
              Classifier shadow evaluations
            </h3>
            <p className="text-muted-foreground mt-1 max-w-2xl text-sm">
              What the DBTL classifier believed about recent requests, and what
              the person chose. Review the false-upgrade and missed-cycle rates
              here before enabling proposals for general users.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowEvaluations((open) => !open)}
            aria-expanded={showEvaluations}
          >
            {showEvaluations ? "Hide evaluations" : "Show evaluations"}
          </Button>
        </div>

        {showEvaluations &&
          (evaluationProject ? (
            <div className="space-y-3">
              <label className="block max-w-sm">
                <span className="text-muted-foreground text-xs">
                  Project to review
                </span>
                <select
                  className="border-input bg-background mt-1 h-9 w-full rounded-md border px-3 text-sm"
                  value={evaluationProject.id}
                  onChange={(event) =>
                    setEvaluationProjectId(event.target.value)
                  }
                >
                  {evaluationProjects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>
              <DbtlEvaluationDrawer
                key={evaluationProject.id}
                projectId={evaluationProject.id}
                enabled
              />
            </div>
          ) : (
            <p className="text-muted-foreground text-sm">
              Evaluations are recorded per project; this workspace has none yet.
            </p>
          ))}
      </section>

      <section>
        <h3 className="text-sm font-semibold">Phase 0 review vocabulary</h3>
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          <div>
            <p className="text-muted-foreground text-xs font-medium uppercase">
              Test outcomes
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {report.test_outcomes.map((outcome) => (
                <Badge key={outcome} variant="outline">
                  {outcome.replace("_", " ")}
                </Badge>
              ))}
            </div>
          </div>
          <div>
            <p className="text-muted-foreground text-xs font-medium uppercase">
              Knowledge candidates
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {report.knowledge_candidate_statuses.map((status) => (
                <Badge key={status} variant="outline">
                  {status}
                </Badge>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
