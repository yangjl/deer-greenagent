"use client";

import { CheckIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  OTHER_OPTION_ID,
  type SetupAnswer,
  type SetupAnswers,
  composeAnswerText,
  initialAnswers,
  isAnswered,
  isComplete,
  stepOptions,
} from "@/core/dbtl/setup-wizard";
import type { SetupQuestion } from "@/core/messages/human-input";
import { cn } from "@/lib/utils";

/**
 * The DBTL setup questions, asked one at a time.
 *
 * All progression rules live in `core/dbtl/setup-wizard.ts`; this renders
 * them. Each step opens on the model's recommendation, so a reader who agrees
 * can step through quickly, while "Other" is always present for the answer the
 * model did not think of.
 */
export function SetupQuestionWizard({
  questions,
  disabled = false,
  onSubmit,
}: {
  questions: SetupQuestion[];
  disabled?: boolean;
  onSubmit: (answerText: string) => void;
}) {
  const [answers, setAnswers] = useState<SetupAnswers>(() =>
    initialAnswers(questions),
  );
  const [step, setStep] = useState(0);

  const question = questions[step];
  const options = useMemo(
    () => (question ? stepOptions(question) : []),
    [question],
  );

  if (!question) {
    return null;
  }

  const answer: SetupAnswer = answers[question.id] ?? {
    optionId: "",
    text: "",
  };
  const answered = isAnswered(question, answer);
  const isLast = step === questions.length - 1;

  const update = (next: SetupAnswer) => {
    setAnswers((current) => ({ ...current, [question.id]: next }));
  };

  const advance = () => {
    // Strictly the next question. Every step opens pre-answered, so skipping
    // "settled" steps would skip all of them and land on the last one.
    setStep((current) => Math.min(questions.length - 1, current + 1));
  };

  const finish = () => {
    onSubmit(composeAnswerText(questions, answers));
  };

  return (
    <div className="space-y-3" data-testid="setup-question-wizard">
      <div className="flex items-center gap-2">
        {questions.map((item, index) => (
          <span
            key={item.id}
            aria-hidden
            className={cn(
              "h-1 flex-1 rounded-full transition-colors",
              index === step
                ? "bg-primary"
                : isAnswered(item, answers[item.id])
                  ? "bg-primary/40"
                  : "bg-border",
            )}
          />
        ))}
        <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
          {step + 1}/{questions.length}
        </span>
      </div>

      <div className="space-y-1">
        <p className="text-foreground text-sm leading-6 font-medium">
          {question.question}
        </p>
        {question.why ? (
          <p className="text-muted-foreground text-xs leading-5">
            {question.why}
          </p>
        ) : null}
      </div>

      {options.length > 0 ? (
        <div
          aria-label={question.question}
          className="grid gap-2"
          role="radiogroup"
        >
          {options.map((option) => {
            const selected = answer.optionId === option.id;
            const recommended = option.id === question.recommended_option_id;
            return (
              <button
                key={option.id}
                aria-checked={selected}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left transition-colors",
                  selected
                    ? "border-primary bg-primary/5"
                    : "border-border hover:bg-muted/50",
                  disabled && "cursor-not-allowed opacity-60",
                )}
                disabled={disabled}
                role="radio"
                type="button"
                onClick={() => update({ optionId: option.id, text: "" })}
              >
                <span
                  className={cn(
                    "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
                    selected ? "border-primary bg-primary" : "border-border",
                  )}
                >
                  {selected ? (
                    <CheckIcon className="text-primary-foreground size-3" />
                  ) : null}
                </span>
                <span className="min-w-0 space-y-0.5">
                  <span className="block text-sm leading-5">
                    {option.label}
                    {recommended ? (
                      <span className="text-muted-foreground ml-2 text-xs">
                        suggested
                      </span>
                    ) : null}
                  </span>
                  {option.description ? (
                    <span className="text-muted-foreground block text-xs leading-5">
                      {option.description}
                    </span>
                  ) : null}
                </span>
              </button>
            );
          })}
        </div>
      ) : null}

      {answer.optionId === OTHER_OPTION_ID || options.length === 0 ? (
        <Textarea
          className="min-h-16 text-sm"
          disabled={disabled}
          placeholder="Type your answer…"
          value={answer.text}
          onChange={(event) =>
            update({ optionId: OTHER_OPTION_ID, text: event.target.value })
          }
        />
      ) : null}

      <div className="flex items-center justify-between gap-2">
        <Button
          className="h-8"
          disabled={disabled || step === 0}
          size="sm"
          type="button"
          variant="ghost"
          onClick={() => setStep((current) => Math.max(0, current - 1))}
        >
          Back
        </Button>
        {isLast ? (
          <Button
            className="h-8"
            // Native disabled state, so a keyboard activation cannot submit a
            // half-answered record any more than a click can.
            disabled={disabled || !isComplete(questions, answers)}
            size="sm"
            type="button"
            onClick={finish}
          >
            Submit answers
          </Button>
        ) : (
          <Button
            className="h-8"
            disabled={disabled || !answered}
            size="sm"
            type="button"
            onClick={advance}
          >
            Next
          </Button>
        )}
      </div>
    </div>
  );
}
