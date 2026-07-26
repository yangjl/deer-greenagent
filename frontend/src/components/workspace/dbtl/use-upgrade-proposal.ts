"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type CycleRecord,
  type DbtlExplicitChoice,
  type EvaluationResponse,
  type ProposalAction,
  outcomeForAction,
  useCreateCycle,
  useDraftCycleSetup,
  useEvaluateRequest,
  useProjectCycles,
  useRecordProposalOutcome,
  isLive,
} from "@/core/dbtl";
import { uuid } from "@/core/utils/uuid";

import type { UpgradeProposalSubmission } from "./upgrade-proposal-card";

/**
 * Wires the inline upgrade proposal into a project conversation.
 *
 * `evaluate` is called alongside sending a message and is deliberately
 * fire-and-forget: an evaluation is an observation, so a classifier outage
 * must degrade to ordinary chat rather than block the user's message. That is
 * why every failure path here resolves to "no card" instead of surfacing an
 * error into the composer.
 */
export function useDbtlUpgradeProposal(projectId: string | null | undefined) {
  const [evaluation, setEvaluation] = useState<EvaluationResponse | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const evaluationSequence = useRef(0);
  const requestTextRef = useRef("");

  // Destructure the stable mutate functions rather than closing over the
  // mutation objects: those are new on every render, which would give the
  // chat page a new `onSubmit` each render and defeat memoized children.
  const { mutateAsync: runEvaluate } = useEvaluateRequest(projectId);
  const { mutateAsync: runDraftSetup } = useDraftCycleSetup(projectId);
  const { mutate: runOutcome } = useRecordProposalOutcome(projectId);
  const { mutateAsync: runCreateCycle, isPending: isConfirming } =
    useCreateCycle(projectId);
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
    setCreateError(null);
  }, [projectId]);

  const evaluate = useCallback(
    async (input: {
      text: string;
      threadId?: string | null;
      selectedCycleId?: string | null;
      explicitChoice?: DbtlExplicitChoice | null;
    }) => {
      if (!projectId || !input.text.trim()) {
        return;
      }
      // Remembered so the setup form can be drafted from the same words that
      // raised the proposal, without the page having to hold the message.
      requestTextRef.current = input.text;
      const sequence = ++evaluationSequence.current;
      try {
        const result = await runEvaluate({
          text: input.text,
          threadId: input.threadId ?? null,
          selectedCycleId: input.selectedCycleId ?? null,
          explicitChoice: input.explicitChoice ?? null,
          idempotencyKey: `eval-${uuid()}`,
        });
        if (sequence === evaluationSequence.current) {
          setCreateError(null);
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
      if (action !== "start_setup") {
        // Setup keeps the card open; the other three close it immediately so
        // the conversation is never left waiting on a network round trip.
        setEvaluation(null);
      }
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
   * Draft the setup form from the request that raised this proposal.
   *
   * Resolves to `null` on any failure so the card keeps the blank form rather
   * than surfacing an error: drafting is an assist, and losing it must not stop
   * a scientist from opening a cycle by hand.
   */
  const draftSetup = useCallback(
    async (fields: readonly string[]) => {
      const text = requestTextRef.current;
      if (!projectId || !text.trim() || fields.length === 0) return null;
      try {
        return await runDraftSetup({ text, fields: [...fields] });
      } catch {
        return null;
      }
    },
    [projectId, runDraftSetup],
  );

  /**
   * Create the durable cycle and hand it back.
   *
   * The created record is returned rather than discarded because the Design
   * council debate is a *cycle-scoped* request: it cannot be sent until a cycle
   * id exists. Returning it is what lets the caller start that debate on the
   * conversational path, the way `StartCycleDialog`'s `onCreated` does on the
   * form path. `null` means no record was created — a failed or absent
   * confirmation must never look like a cycle the caller can then act on.
   */
  const confirm = useCallback(
    async (
      submission: UpgradeProposalSubmission,
    ): Promise<CycleRecord | null> => {
      const proposal = evaluation?.proposal;
      if (!projectId || !proposal) return null;
      const currentEvaluationId = evaluation.evaluation_id;
      setCreateError(null);
      try {
        const cycle = await runCreateCycle({
          title: submission.title,
          cycleClass: "computational",
          cycleWeight: "full",
          researchQuestion: submission.objective || submission.title,
          objective: submission.objective,
          successCriteria: clarificationToCriteria(submission),
          parentCycleId: parentCycle?.id ?? null,
          idempotencyKey: `cycle-${currentEvaluationId}`,
        });
        runOutcome(
          {
            evaluationId: currentEvaluationId,
            outcome: "start_setup",
          },
          { onError: () => undefined },
        );
        setEvaluation(null);
        return cycle ?? null;
      } catch (error: unknown) {
        setCreateError(
          error instanceof Error ? error.message : "Could not start the cycle.",
        );
        return null;
      }
    },
    [runCreateCycle, runOutcome, evaluation, projectId, parentCycle?.id],
  );

  // Dismissal is not a separate entry point: the card's close control routes
  // through `recordOutcome("dismiss")` like every other choice, so there is
  // exactly one path that records what the human did.
  return {
    evaluation,
    evaluate,
    draftSetup,
    recordOutcome,
    confirm,
    isConfirming,
    createError,
    parentCycleTitle: parentCycle?.title ?? null,
  };
}

/**
 * Fold the clarification answers into the cycle's success criteria.
 *
 * They are the user's own words about what "done" means, so they belong on
 * the durable record rather than being discarded once the card closes.
 */
function clarificationToCriteria(
  submission: UpgradeProposalSubmission,
): string {
  return Object.entries(submission.clarification)
    .filter(([, value]) => value.trim().length > 0)
    .map(([field, value]) => `${field}: ${value.trim()}`)
    .join("\n");
}
