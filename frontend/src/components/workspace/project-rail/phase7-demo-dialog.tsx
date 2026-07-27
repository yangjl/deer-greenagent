"use client";

import { ArrowRight, Check, ShieldCheck } from "lucide-react";
import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  OUTCOME_LABELS,
  PHASE7_DEMO_CASES,
  assessmentHeadline,
  projectedValidity,
} from "@/core/dbtl";
import { cn } from "@/lib/utils";

export function Phase7DemoDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [activeId, setActiveId] = useState(PHASE7_DEMO_CASES[0]!.id);
  const fixture =
    PHASE7_DEMO_CASES.find((item) => item.id === activeId) ??
    PHASE7_DEMO_CASES[0]!;
  const result = projectedValidity(fixture.metrics, fixture.checks);
  const failed = fixture.checks.filter((item) => item.status === "failed");
  const missing = fixture.checks.filter((item) => item.status === "missing");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[min(90svh,46rem)] overflow-y-auto p-0 sm:max-w-2xl">
        <DialogHeader className="border-border border-b px-6 py-5 text-left">
          <p className="text-[11px] font-semibold tracking-[0.18em] text-emerald-700 uppercase dark:text-emerald-400">
            Preview only · no records changed
          </p>
          <DialogTitle className="mt-1 text-xl">
            Phase 7 scientific validity demo
          </DialogTitle>
          <DialogDescription>
            One screen, three evidence patterns. Eyeball the outcome and the
            workflow route.
          </DialogDescription>
        </DialogHeader>

        <div className="px-6 py-5">
          <div className="grid gap-2 sm:grid-cols-3">
            {PHASE7_DEMO_CASES.map((item, index) => (
              <button
                key={item.id}
                type="button"
                aria-label={`${index + 1}. ${item.label}`}
                aria-pressed={item.id === fixture.id}
                onClick={() => setActiveId(item.id)}
                className={cn(
                  "border-border rounded-lg border px-3 py-3 text-left transition-colors",
                  item.id === fixture.id
                    ? "border-foreground bg-foreground text-background"
                    : "hover:bg-muted/60",
                )}
              >
                <span className="block text-[10px] font-semibold tracking-widest uppercase opacity-65">
                  Case {index + 1}
                </span>
                <span className="mt-1 block text-sm font-medium">
                  {item.label}
                </span>
              </button>
            ))}
          </div>

          <div className="mt-7 grid gap-7 sm:grid-cols-[1fr_1.1fr]">
            <section>
              <p className="text-muted-foreground text-[11px] font-semibold tracking-widest uppercase">
                {fixture.eyebrow}
              </p>
              <p className="mt-2 text-3xl font-semibold tracking-tight">
                {OUTCOME_LABELS[result.outcome]}
              </p>
              <p className="text-muted-foreground mt-3 text-sm leading-relaxed">
                {assessmentHeadline(result)}
              </p>
              <p className="mt-4 text-sm leading-relaxed">
                {fixture.explanation}
              </p>
            </section>

            <section className="border-border border-l pl-5">
              <div className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-emerald-700 dark:text-emerald-400" />
                <p className="text-sm font-semibold">Evidence readout</p>
              </div>
              <dl className="mt-4 space-y-3 text-sm">
                <div className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">Headline threshold</dt>
                  <dd className="font-medium">
                    {result.headline_success ? "Passed" : "Not met"}
                  </dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">Failed checks</dt>
                  <dd className="font-medium">{failed.length}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">Missing checks</dt>
                  <dd className="font-medium">{missing.length}</dd>
                </div>
              </dl>
              <div className="border-border mt-5 border-t pt-4">
                <p className="text-muted-foreground text-[10px] font-semibold tracking-widest uppercase">
                  Recommended route
                </p>
                <p className="mt-2 flex items-start gap-2 text-sm font-medium">
                  {result.outcome === "supported" ? (
                    <Check className="mt-0.5 size-4 shrink-0 text-emerald-700 dark:text-emerald-400" />
                  ) : (
                    <ArrowRight className="mt-0.5 size-4 shrink-0" />
                  )}
                  {fixture.expectedRoute}
                </p>
                <p className="text-muted-foreground mt-2 text-xs">
                  The route is a recommendation. A human still records the
                  decision.
                </p>
              </div>
            </section>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
