"use client";

import { FlaskConical, Loader2, X } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ASSUMPTION_NOTE,
  DRAFTED_NOTE,
  PROPOSAL_ACTIONS,
  type ClarificationState,
  type EvaluationResponse,
  type ProposalAction,
  type ProposalPayload,
  type SetupDraftResponse,
  advanceClarification,
  applySetupDraft,
  assumedFieldLabel,
  canConfirmSetup,
  clarificationPrompts,
  confirmationLines,
  hasProposalToShow,
  initialClarification,
  isDraftUseful,
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
  onDraftSetup,
  isConfirming = false,
  error,
  parentCycleTitle,
  className,
}: {
  evaluation: EvaluationResponse | null;
  onAction: (action: ProposalAction) => void;
  onConfirm: (submission: UpgradeProposalSubmission) => void;
  /**
   * Draft the form from the user's own request. Called once, when setup opens,
   * rather than on mount — a card the user dismisses should cost no model call.
   * Resolving to `null` (or throwing) leaves the blank form intact.
   */
  onDraftSetup?: (
    fields: readonly string[],
  ) => Promise<SetupDraftResponse | null>;
  isConfirming?: boolean;
  error?: string | null;
  parentCycleTitle?: string | null;
  className?: string;
}) {
  const proposal = evaluation?.proposal ?? null;
  const [step, setStep] = useState<Step>("offer");
  // Title and answers move together so an arriving draft can be merged into
  // both in one pass against a single consistent snapshot.
  const [form, setForm] = useState<{
    title: string;
    clarification: ClarificationState;
  }>({ title: "", clarification: {} });
  const [assumed, setAssumed] = useState<readonly string[]>([]);
  const [isDrafting, setIsDrafting] = useState(false);
  // Mirrors the latest form so a slow draft merges into what the user has typed
  // meanwhile, not into the snapshot captured when the request went out.
  const formRef = useRef(form);
  formRef.current = form;

  if (!hasProposalToShow(evaluation) || !proposal) {
    return null;
  }

  const setTitle = (value: string) =>
    setForm((prev) => ({ ...prev, title: value }));

  function begin() {
    const current = proposal!;
    setForm({
      title: current.proposed_objective.slice(0, 80),
      clarification: initialClarification(current),
    });
    setAssumed([]);
    setStep("clarify");
    if (!onDraftSetup) return;

    setIsDrafting(true);
    void onDraftSetup(current.missing_fields)
      .then((draft) => {
        if (!isDraftUseful(draft)) return;
        const applied = applySetupDraft({ draft, ...formRef.current });
        setForm({
          title: applied.title,
          clarification: applied.clarification,
        });
        setAssumed(applied.assumed);
      })
      // A failed draft is not an error the scientist needs to see: the blank
      // form they already have is a correct, complete fallback.
      .catch(() => undefined)
      .finally(() => setIsDrafting(false));
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
              title={form.title}
              onTitleChange={setTitle}
              clarification={form.clarification}
              assumed={assumed}
              isDrafting={isDrafting}
              onFieldChange={(field, value) =>
                setForm((prev) => ({
                  ...prev,
                  clarification: advanceClarification(
                    prev.clarification,
                    field,
                    value,
                  ),
                }))
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
              objective={resolvedObjective(
                proposal,
                form.clarification,
                form.title,
              )}
              parentCycleTitle={parentCycleTitle}
              onBack={() => setStep("clarify")}
              onConfirm={() =>
                onConfirm({
                  title: form.title.trim(),
                  objective: resolvedObjective(
                    proposal,
                    form.clarification,
                    form.title,
                  ),
                  clarification: form.clarification,
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
                <span
                  aria-hidden="true"
                  className="text-muted-foreground mr-1.5"
                >
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
  assumed,
  isDrafting,
  onFieldChange,
  onBack,
  onContinue,
}: {
  proposal: ProposalPayload;
  title: string;
  onTitleChange: (value: string) => void;
  clarification: ClarificationState;
  assumed: readonly string[];
  isDrafting: boolean;
  onFieldChange: (field: string, value: string) => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const ready = canConfirmSetup(proposal, clarification, title);
  const assumedSet = new Set(assumed);
  const drafted = Object.values(clarification).some((value) => value.trim());

  return (
    <div className="space-y-3">
      {isDrafting && (
        <p
          className="text-muted-foreground flex items-center gap-1.5 text-xs"
          role="status"
        >
          <Loader2 className="size-3 animate-spin" aria-hidden="true" />
          Drafting from your request…
        </p>
      )}
      {!isDrafting && drafted && (
        <div className="text-muted-foreground space-y-0.5 text-xs">
          <p>{DRAFTED_NOTE}</p>
          {assumedSet.size > 0 && <p>{ASSUMPTION_NOTE}</p>}
        </div>
      )}

      <Field label="Cycle title">
        <Input
          value={title}
          onChange={(event) => onTitleChange(event.target.value)}
          placeholder="Drought tolerance screen"
          autoFocus
        />
      </Field>

      {clarificationPrompts(proposal).map((prompt) => (
        <Field
          key={prompt.field}
          label={prompt.question}
          // Named in words, never by colour alone: the scientist has to be able
          // to tell the model's suggestion from their own answer.
          note={assumedSet.has(prompt.field) ? assumedFieldLabel() : undefined}
        >
          <Input
            value={clarification[prompt.field] ?? ""}
            onChange={(event) =>
              onFieldChange(prompt.field, event.target.value)
            }
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
        <Button
          type="button"
          size="sm"
          onClick={onConfirm}
          disabled={isConfirming}
        >
          {isConfirming && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
          {isConfirming ? "Creating…" : "Create this cycle"}
        </Button>
      </div>
    </div>
  );
}

function Field({
  label,
  note,
  children,
}: {
  label: string;
  /** Provenance for this field, e.g. that its value is the model's assumption. */
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
        {label}
        {note && (
          <span className="border-border rounded border px-1 py-px text-[10px] tracking-wide uppercase">
            {note}
          </span>
        )}
      </p>
      <div className="mt-1">{children}</div>
    </div>
  );
}
