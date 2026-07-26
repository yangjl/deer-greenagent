"use client";

import { FlaskConical, Loader2, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  PROPOSAL_ACTIONS,
  type ClarificationState,
  type EvaluationResponse,
  type ProposalAction,
  type ProposalPayload,
  advanceClarification,
  canConfirmSetup,
  clarificationPrompts,
  confirmationLines,
  hasProposalToShow,
  initialClarification,
  proposalHeadline,
  resolvedObjective,
} from "@/core/dbtl";
import { cn } from "@/lib/utils";

type Step = "offer" | "clarify" | "confirm";

export interface UpgradeProposalSubmission {
  title: string;
  objective: string;
  clarification: ClarificationState;
}

/**
 * The inline DBTL Upgrade Proposal.
 *
 * It sits above the composer and leaves the conversation untouched: no
 * message is written, no cycle exists, and dismissing it costs nothing. The
 * card walks offer → clarify → confirm, and only the final confirm calls the
 * durable create endpoint — which is why the "no cycle has been created yet"
 * line stays visible through the first two steps.
 */
export function UpgradeProposalCard({
  evaluation,
  onAction,
  onConfirm,
  isConfirming = false,
  error,
  parentCycleTitle,
  className,
}: {
  evaluation: EvaluationResponse | null;
  onAction: (action: ProposalAction) => void;
  onConfirm: (submission: UpgradeProposalSubmission) => void;
  isConfirming?: boolean;
  error?: string | null;
  parentCycleTitle?: string | null;
  className?: string;
}) {
  const proposal = evaluation?.proposal ?? null;
  const [step, setStep] = useState<Step>("offer");
  const [title, setTitle] = useState("");
  const [clarification, setClarification] = useState<ClarificationState>({});

  if (!hasProposalToShow(evaluation) || !proposal) {
    return null;
  }

  function begin() {
    setClarification(initialClarification(proposal!));
    setTitle(proposal!.proposed_objective.slice(0, 80));
    setStep("clarify");
  }

  return (
    <div
      className={cn(
        "border-border bg-card/80 relative rounded-lg border shadow-sm backdrop-blur-sm",
        className,
      )}
      role="region"
      aria-label="DBTL upgrade proposal"
    >
      <button
        type="button"
        onClick={() => onAction("dismiss")}
        disabled={isConfirming}
        aria-label="Dismiss suggestion"
        className="text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-ring absolute top-2.5 right-2.5 rounded-md p-1 transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
      >
        <X className="size-4" />
      </button>

      <div className="flex gap-3 px-4 py-3.5">
        <FlaskConical className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <div className="min-w-0 flex-1 space-y-3">
          <div className="space-y-1 pr-6">
            <p className="text-sm font-medium">{proposalHeadline(proposal)}</p>
            {/* Kept visible in every step until a record actually exists. */}
            <p className="text-muted-foreground text-xs">{proposal.notice}</p>
          </div>

          {step === "offer" && (
            <OfferStep
              proposal={proposal}
              onStart={begin}
              onAction={onAction}
            />
          )}

          {step === "clarify" && (
            <ClarifyStep
              proposal={proposal}
              title={title}
              onTitleChange={setTitle}
              clarification={clarification}
              onFieldChange={(field, value) =>
                setClarification((prev) => advanceClarification(prev, field, value))
              }
              onBack={() => setStep("offer")}
              onContinue={() => setStep("confirm")}
            />
          )}

          {step === "confirm" && (
            <ConfirmStep
              proposal={proposal}
              isConfirming={isConfirming}
              error={error}
              objective={resolvedObjective(proposal, clarification, title)}
              parentCycleTitle={parentCycleTitle}
              onBack={() => setStep("clarify")}
              onConfirm={() =>
                onConfirm({
                  title: title.trim(),
                  objective: resolvedObjective(proposal, clarification, title),
                  clarification,
                })
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}

function OfferStep({
  proposal,
  onStart,
  onAction,
}: {
  proposal: ProposalPayload;
  onStart: () => void;
  onAction: (action: ProposalAction) => void;
}) {
  return (
    <div className="space-y-3">
      {proposal.proposed_objective && (
        <Field label="Proposed objective">
          <p className="text-sm leading-relaxed">
            {proposal.proposed_objective}
          </p>
        </Field>
      )}

      {proposal.missing_fields.length > 0 && (
        <Field label="Missing before start">
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {proposal.missing_fields.map((field) => (
              <li key={field} className="text-sm">
                <span aria-hidden="true" className="text-muted-foreground mr-1.5">
                  •
                </span>
                {field}
              </li>
            ))}
          </ul>
        </Field>
      )}

      <div className="flex flex-wrap gap-2 pt-0.5">
        {proposal.kind === "cycle_continuation" ? (
          <Button
            type="button"
            size="sm"
            onClick={() => onAction("continue_cycle")}
          >
            Continue selected cycle
          </Button>
        ) : (
          PROPOSAL_ACTIONS.map((action) => (
            <Button
              key={action.id}
              type="button"
              size="sm"
              variant={action.id === "start_setup" ? "default" : "outline"}
              title={action.consequence}
              onClick={() =>
                action.id === "start_setup" ? onStart() : onAction(action.id)
              }
            >
              {action.label}
            </Button>
          ))
        )}
        {proposal.kind === "cycle_continuation" && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onAction("keep_ordinary")}
          >
            Keep as ordinary chat
          </Button>
        )}
      </div>
    </div>
  );
}

function ClarifyStep({
  proposal,
  title,
  onTitleChange,
  clarification,
  onFieldChange,
  onBack,
  onContinue,
}: {
  proposal: ProposalPayload;
  title: string;
  onTitleChange: (value: string) => void;
  clarification: ClarificationState;
  onFieldChange: (field: string, value: string) => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const ready = canConfirmSetup(proposal, clarification, title);
  return (
    <div className="space-y-3">
      <Field label="Cycle title">
        <Input
          value={title}
          onChange={(event) => onTitleChange(event.target.value)}
          placeholder="Drought tolerance screen"
          autoFocus
        />
      </Field>

      {clarificationPrompts(proposal).map((prompt) => (
        <Field key={prompt.field} label={prompt.question}>
          <Input
            value={clarification[prompt.field] ?? ""}
            onChange={(event) => onFieldChange(prompt.field, event.target.value)}
            placeholder={prompt.placeholder}
          />
        </Field>
      ))}

      <div className="flex justify-end gap-2 pt-0.5">
        <Button type="button" size="sm" variant="ghost" onClick={onBack}>
          Back
        </Button>
        <Button type="button" size="sm" onClick={onContinue} disabled={!ready}>
          Review and confirm
        </Button>
      </div>
    </div>
  );
}

function ConfirmStep({
  proposal,
  isConfirming,
  error,
  objective,
  parentCycleTitle,
  onBack,
  onConfirm,
}: {
  proposal: ProposalPayload;
  isConfirming: boolean;
  error?: string | null;
  objective: string;
  parentCycleTitle?: string | null;
  onBack: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="space-y-3">
      <dl className="border-border divide-border divide-y rounded-md border">
        {confirmationLines(
          proposal,
          "computational",
          objective,
          parentCycleTitle,
        ).map((line) => (
          <div key={line.label} className="flex gap-3 px-3 py-2">
            <dt className="text-muted-foreground w-40 shrink-0 text-xs">
              {line.label}
            </dt>
            <dd className="min-w-0 flex-1 text-sm">{line.value}</dd>
          </div>
        ))}
      </dl>

      <p className="text-muted-foreground text-xs leading-relaxed">
        {proposal.confirmation.notice}
      </p>

      {error && <p className="text-destructive text-sm">{error}</p>}

      <div className="flex justify-end gap-2 pt-0.5">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onBack}
          disabled={isConfirming}
        >
          Back
        </Button>
        <Button type="button" size="sm" onClick={onConfirm} disabled={isConfirming}>
          {isConfirming && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
          {isConfirming ? "Creating…" : "Create this cycle"}
        </Button>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-muted-foreground text-xs">{label}</p>
      <div className="mt-1">{children}</div>
    </div>
  );
}
