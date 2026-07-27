"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  CYCLE_CLASS_LABELS,
  CYCLE_WEIGHT_LABELS,
  type CycleClass,
  type CycleRecord,
  type CycleWeight,
  isLive,
  useCreateCycle,
} from "@/core/dbtl";
import { uuid } from "@/core/utils/uuid";
import { cn } from "@/lib/utils";

const CYCLE_CLASSES: CycleClass[] = [
  "season/program",
  "computational",
  "other",
];
const CYCLE_WEIGHTS: CycleWeight[] = ["full", "light", "retroactive"];

function Field({
  label,
  hint,
  required = false,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="flex items-baseline justify-between gap-3 text-sm font-medium">
        {label}
        {required && (
          <span className="text-muted-foreground text-[11px] font-normal">
            Required
          </span>
        )}
      </span>
      {hint && (
        <span className="text-muted-foreground mt-0.5 block text-xs">
          {hint}
        </span>
      )}
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

export function StartCycleDialog({
  projectId,
  projectName,
  cycles,
  open,
  onOpenChange,
  onCreated,
}: {
  projectId: string;
  projectName: string;
  cycles: readonly CycleRecord[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (cycle: CycleRecord) => void;
}) {
  const create = useCreateCycle(projectId);
  const [title, setTitle] = useState("");
  const [cycleClass, setCycleClass] = useState<CycleClass>("computational");
  const [cycleWeight, setCycleWeight] = useState<CycleWeight>("full");
  const [researchQuestion, setResearchQuestion] = useState("");
  const [objective, setObjective] = useState("");
  const [successCriteria, setSuccessCriteria] = useState("");
  const [parentCycleId, setParentCycleId] = useState<string>("");

  // Minted once per opening, so a double-submit or a retry after a dropped
  // response resolves to the same durable record rather than a second one.
  const [idempotencyKey, setIdempotencyKey] = useState(() => `cycle-${uuid()}`);
  useEffect(() => {
    if (open) {
      setIdempotencyKey(`cycle-${uuid()}`);
    }
  }, [open]);

  const parents = cycles.filter(
    (item) => item.cycle_class === "season/program" && isLive(item),
  );
  // A parent is optional. The backend used to permit only one live top-level
  // cycle per project, so an existing one forced every new cycle to be a child;
  // migration 0018 dropped that rule because parallel cycles across different
  // traits, populations, or seasons are ordinary research work.
  const ready = title.trim().length > 0 && researchQuestion.trim().length > 0;
  const missingRequirements = [
    title.trim().length === 0 ? "title" : null,
    researchQuestion.trim().length === 0 ? "research question" : null,
  ].filter((item): item is string => item !== null);
  const readinessMessage =
    missingRequirements.length > 0
      ? `Add ${missingRequirements.join(" and ")} to continue.`
      : "Ready to create the durable cycle record.";

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || create.isPending) return;
    create.mutate(
      {
        title: title.trim(),
        cycleClass,
        cycleWeight,
        researchQuestion: researchQuestion.trim(),
        objective: objective.trim(),
        successCriteria: successCriteria.trim(),
        parentCycleId: parentCycleId || null,
        idempotencyKey,
      },
      {
        onSuccess: (cycle) => {
          setTitle("");
          setResearchQuestion("");
          setObjective("");
          setSuccessCriteria("");
          setParentCycleId("");
          onOpenChange(false);
          onCreated?.(cycle);
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[calc(100svh-1rem)] flex-col gap-0 overflow-hidden p-0 sm:max-h-[min(90svh,52rem)] sm:max-w-xl">
        <DialogHeader className="border-border/70 shrink-0 border-b px-5 py-4 pr-12 sm:px-6 sm:py-5">
          <DialogTitle>Start a cycle · {projectName}</DialogTitle>
          <DialogDescription>
            Define the research record. Title and research question are
            required.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
          <div
            className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain px-5 py-5 sm:px-6"
            data-testid="cycle-form-scroll-region"
          >
            <Field label="Title" required>
              <Input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Drought tolerance screen"
                autoFocus
                required
              />
            </Field>

            <Field label="Cycle class">
              <div className="flex flex-wrap gap-1.5">
                {CYCLE_CLASSES.map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => {
                      setCycleClass(option);
                      if (option !== "computational") setParentCycleId("");
                    }}
                    aria-pressed={cycleClass === option}
                    className={cn(
                      "rounded-full border px-3 py-1 text-sm transition-colors",
                      "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                      cycleClass === option
                        ? "border-foreground/20 bg-foreground text-background"
                        : "border-border hover:border-foreground/30 hover:bg-muted",
                    )}
                  >
                    {CYCLE_CLASS_LABELS[option]}
                  </button>
                ))}
              </div>
            </Field>

            <Field
              label="Workflow weight"
              hint="Controls how much evidence and review ceremony this cycle expects."
            >
              <div className="flex flex-wrap gap-1.5">
                {CYCLE_WEIGHTS.map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setCycleWeight(option)}
                    aria-pressed={cycleWeight === option}
                    className={cn(
                      "rounded-full border px-3 py-1 text-sm transition-colors",
                      "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                      cycleWeight === option
                        ? "border-foreground/20 bg-foreground text-background"
                        : "border-border hover:border-foreground/30 hover:bg-muted",
                    )}
                  >
                    {CYCLE_WEIGHT_LABELS[option]}
                  </button>
                ))}
              </div>
            </Field>

            {cycleClass === "computational" && parents.length > 0 && (
              <Field
                label="Parent cycle"
                hint="Optional. A computational cycle can hang off a season or program."
              >
                <select
                  value={parentCycleId}
                  onChange={(event) => setParentCycleId(event.target.value)}
                  className="border-border bg-background focus:border-foreground/40 w-full rounded-md border px-3 py-2 text-sm outline-none"
                >
                  <option value="">No parent — top-level cycle</option>
                  {parents.map((parent) => (
                    <option key={parent.id} value={parent.id}>
                      {parent.title}
                    </option>
                  ))}
                </select>
              </Field>
            )}

            <Field label="Research question" required>
              <Textarea
                value={researchQuestion}
                onChange={(event) => setResearchQuestion(event.target.value)}
                rows={2}
                placeholder="Which lines hold yield under late-season drought?"
                required
              />
            </Field>

            <Field label="Objective">
              <Textarea
                value={objective}
                onChange={(event) => setObjective(event.target.value)}
                rows={2}
                placeholder="Rank 200 candidate lines by drought index."
              />
            </Field>

            <Field label="Success criteria">
              <Textarea
                value={successCriteria}
                onChange={(event) => setSuccessCriteria(event.target.value)}
                rows={2}
                placeholder="Top decile reproducible across two sites."
              />
            </Field>

            <p className="border-border text-muted-foreground rounded-md border border-dashed px-3 py-2 text-xs leading-relaxed">
              This creates a{" "}
              <strong className="font-medium">durable research record</strong>.
              It is stored in the project database, appears in the activity log
              with your name, and is retired rather than deleted.
            </p>
          </div>

          <div
            className="border-border/70 bg-background/95 supports-[backdrop-filter]:bg-background/85 shrink-0 border-t px-5 py-3 backdrop-blur-sm sm:px-6 sm:py-4"
            data-testid="cycle-form-actions"
          >
            {create.error && (
              <p className="text-destructive mb-2 text-sm" role="alert">
                {create.error.message}
              </p>
            )}
            <DialogFooter className="items-center sm:justify-between">
              <p
                className={cn(
                  "text-left text-xs",
                  ready
                    ? "text-emerald-700 dark:text-emerald-400"
                    : "text-muted-foreground",
                )}
                aria-live="polite"
              >
                {readinessMessage}
              </p>
              <div className="flex shrink-0 justify-end gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => onOpenChange(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={!ready || create.isPending}>
                  {create.isPending ? "Starting…" : "Start cycle"}
                </Button>
              </div>
            </DialogFooter>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
