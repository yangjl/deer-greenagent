"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
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
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
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
}: {
  projectId: string;
  projectName: string;
  cycles: readonly CycleRecord[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
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
  const hasLiveTopLevel = cycles.some(
    (item) => item.parent_cycle_id === null && isLive(item),
  );
  const parentRequired = hasLiveTopLevel;
  const canCreateChild = cycleClass === "computational" && parents.length > 0;
  const ready =
    title.trim().length > 0 &&
    researchQuestion.trim().length > 0 &&
    (!parentRequired || (canCreateChild && parentCycleId.length > 0));

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
        onSuccess: () => {
          setTitle("");
          setResearchQuestion("");
          setObjective("");
          setSuccessCriteria("");
          setParentCycleId("");
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Start a cycle · {projectName}</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Title">
            <Input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Drought tolerance screen"
              autoFocus
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
              hint={
                parentRequired
                  ? "Required because this project already has a live top-level cycle."
                  : "Optional. A computational cycle can hang off a season or program."
              }
            >
              <select
                value={parentCycleId}
                onChange={(event) => setParentCycleId(event.target.value)}
                className="border-border bg-background focus:border-foreground/40 w-full rounded-md border px-3 py-2 text-sm outline-none"
              >
                <option value="" disabled={parentRequired}>
                  {parentRequired
                    ? "Select the active season / program"
                    : "No parent — top-level cycle"}
                </option>
                {parents.map((parent) => (
                  <option key={parent.id} value={parent.id}>
                    {parent.title}
                  </option>
                ))}
              </select>
            </Field>
          )}
          {parentRequired && !canCreateChild && (
            <p className="border-border text-muted-foreground rounded-md border border-dashed px-3 py-2 text-xs">
              This project already has a live top-level cycle. Finish or abandon
              it before starting another, or create a computational child under
              a live season/program cycle.
            </p>
          )}

          <Field label="Research question">
            <Textarea
              value={researchQuestion}
              onChange={(event) => setResearchQuestion(event.target.value)}
              rows={2}
              placeholder="Which lines hold yield under late-season drought?"
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
            <strong className="font-medium">durable research record</strong>. It
            is stored in the project database, appears in the activity log with
            your name, and is retired rather than deleted.
          </p>

          {create.error && (
            <p className="text-destructive text-sm">{create.error.message}</p>
          )}

          <div className="flex justify-end gap-2">
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
        </form>
      </DialogContent>
    </Dialog>
  );
}
