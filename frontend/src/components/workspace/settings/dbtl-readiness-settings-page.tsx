"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileJson2,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  buildReadinessExport,
  groupReadinessItems,
  useDbtlReadiness,
} from "@/core/dbtl";
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

export function DbtlReadinessSettingsPage() {
  const readiness = useDbtlReadiness();

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
