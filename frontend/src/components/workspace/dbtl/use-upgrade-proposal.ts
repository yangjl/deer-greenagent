"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type CycleRecord,
  type DbtlExplicitChoice,
  type EvaluationResponse,
  type ProposalAction,
  outcomeForAction,
  useCreateCycle,
  useEvaluateRequest,
  useProjectCycles,
  useRecordProposalOutcome,
  isLive,
} from "@/core/dbtl";
import type { DbtlCycleSetup } from "@/core/messages/human-input";
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
  const { mutateAsync: runCreateCycle } = useCreateCycle(projectId);
  const cycles = useProjectCycles(projectId);
  const parentCycle =
    cycles.data?.cycles.find(
      (cycle) =>
        cycle.parent_cycle_id === null &&
        cycle.cycle_class === "season/program" &&
        isLive(cycle),
    ) ?? null;

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
          setEvaluation(result.proposal ? result : null);
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
    (action: ProposalAction) => {
      const current = evaluation;
      if (!current) return;
      setEvaluation(null);
      runOutcome(
        {
          evaluationId: current.evaluation_id,
          outcome: outcomeForAction(action),
        },
        // Telemetry only. A lost outcome must not disturb the conversation.
        { onError: () => undefined },
      );
    },
    [evaluation, runOutcome],
  );

  /**
   * Create the durable cycle only after the native card's explicit choice.
   *
   * The setup data is emitted by the server in the same `ask_clarification`
   * artifact that DeerFlow renders. Its request id is the idempotency boundary,
   * so retrying one answer cannot create two cycles.
   */
  const createFromNativeSetup = useCallback(
    async (
      setup: DbtlCycleSetup,
      requestId: string,
    ): Promise<CycleRecord | null> => {
      if (!projectId) return null;
      const currentEvaluationId = evaluation?.evaluation_id ?? null;
      try {
        const cycle = await runCreateCycle({
          title: setup.title,
          cycleClass: "computational",
          cycleWeight: "full",
          researchQuestion: setup.objective,
          objective: setup.objective,
          successCriteria: setup.success_criteria,
          parentCycleId: parentCycle?.id ?? null,
          idempotencyKey: `cycle-${requestId}`,
        });
        if (currentEvaluationId) {
          runOutcome(
            {
              evaluationId: currentEvaluationId,
              outcome: "start_setup",
            },
            { onError: () => undefined },
          );
        }
        setEvaluation(null);
        return cycle ?? null;
      } catch {
        return null;
      }
    },
    [runCreateCycle, runOutcome, evaluation, projectId, parentCycle?.id],
  );

  return {
    evaluate,
    recordOutcome,
    createFromNativeSetup,
  };
}
