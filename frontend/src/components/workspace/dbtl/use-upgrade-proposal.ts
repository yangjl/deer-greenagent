"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type DbtlExplicitChoice,
  type EvaluationResponse,
  type ProposalOutcome,
  useEvaluateRequest,
  useRecordProposalOutcome,
} from "@/core/dbtl";
import { uuid } from "@/core/utils/uuid";

/**
 * Keeps DBTL proposal evaluation as shadow telemetry behind a project chat.
 *
 * `evaluate` is called alongside sending a message and is deliberately
 * fire-and-forget: an evaluation is an observation, so a classifier outage
 * must degrade to ordinary chat rather than block the user's message. Visible
 * setup interactions come from the supervisor's native Human Input Card.
 */
export function useDbtlUpgradeProposal(projectId: string | null | undefined) {
  const [evaluation, setEvaluation] = useState<EvaluationResponse | null>(null);
  const evaluationSequence = useRef(0);

  // Destructure the stable mutate functions rather than closing over the
  // mutation objects: those are new on every render, which would give the
  // chat page a new `onSubmit` each render and defeat memoized children.
  const { mutateAsync: runEvaluate } = useEvaluateRequest(projectId);
  const { mutate: runOutcome } = useRecordProposalOutcome(projectId);

  useEffect(() => {
    // A late response from the previous project must never render a proposal
    // in the project the user just opened.
    evaluationSequence.current += 1;
    setEvaluation(null);
  }, [projectId]);

  const evaluate = useCallback(
    async (input: {
      text: string;
      threadId?: string | null;
      selectedCycleId?: string | null;
      explicitChoice?: DbtlExplicitChoice | null;
      isNewConversation?: boolean;
    }) => {
      if (!projectId || !input.text.trim()) {
        return;
      }
      const sequence = ++evaluationSequence.current;
      try {
        const result = await runEvaluate({
          text: input.text,
          threadId: input.threadId ?? null,
          selectedCycleId: input.selectedCycleId ?? null,
          explicitChoice: input.explicitChoice ?? null,
          isNewConversation: input.isNewConversation ?? false,
          idempotencyKey: `eval-${uuid()}`,
        });
        if (sequence === evaluationSequence.current) {
          setEvaluation(
            result.route_kind === "proposal" ||
              result.route_kind === "cycle_setup"
              ? result
              : null,
          );
        }
      } catch {
        // Ordinary chat is the safe default when classification is unavailable.
        if (sequence === evaluationSequence.current) {
          setEvaluation(null);
        }
      }
    },
    [runEvaluate, projectId],
  );

  const recordOutcome = useCallback(
    (outcome: ProposalOutcome) => {
      const current = evaluation;
      if (!current) return;
      setEvaluation(null);
      runOutcome(
        {
          evaluationId: current.evaluation_id,
          outcome,
        },
        // Telemetry only. A lost outcome must not disturb the conversation.
        { onError: () => undefined },
      );
    },
    [evaluation, runOutcome],
  );

  return {
    evaluate,
    recordOutcome,
  };
}
