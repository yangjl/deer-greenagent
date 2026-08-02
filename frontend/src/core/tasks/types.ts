import type { AIMessage } from "@langchain/langgraph-sdk";

import type { TokenUsage } from "../messages/usage";

import type { SubtaskStep } from "./steps";

export interface Subtask {
  id: string;
  status: "in_progress" | "completed" | "failed";
  subagent_type: string;
  description: string;
  /** Effective DeerFlow model selected for this subagent run. */
  modelName?: string;
  /** Latest cumulative token snapshot reported while the subagent runs. */
  usage?: TokenUsage;
  latestMessage?: AIMessage;
  /**
   * Full ordered step history (assistant turns + tool outputs) of the subagent.
   * Accumulated live from `task_running` events and backfilled on expand for
   * historical runs (#3779). Replaces the old "only latestMessage" behavior.
   */
  steps?: SubtaskStep[];
  prompt: string;
  result?: string;
  error?: string;
  /**
   * Why a guardrail cap ended the run early (``token_capped`` / ``turn_capped``
   * / ``loop_capped``), or ``undefined`` for a clean run. The pill status stays
   * normal (``completed``/``failed``); this carries the cap detail so a future
   * badge can show "capped" without parsing result text (#3875 Phase 2).
   */
  stopReason?: string;
  /**
   * Who this worker is in a Design council, when it is one. Sent by the backend
   * on every task event rather than derived from the task id: a live view that
   * parses identifiers to decide who is speaking is one rename away from
   * labelling every seat wrong, silently.
   */
  councilSeat?: CouncilSeatIdentity;
  /**
   * The DBTL stage this worker belongs to, when the server declared one.
   *
   * Server-authoritative and the only honest way to tell a governed stage
   * worker from an ordinary delegated subtask: a DBTL worker's task id is a
   * work-unit id rather than a `task` tool-call id, so it has no assistant
   * message to hang from and would otherwise render nowhere. Deriving it from
   * the id or the description would break on the first rename.
   */
  dbtlStage?: string;
  /**
   * The run this task belongs to.
   *
   * Backfilling a task's step history is addressed by `(thread, run, task)`,
   * so a surface that assumed "the thread's latest run" fetched the wrong
   * run's events for every task from an earlier turn — and silently got
   * nothing back.
   */
  runId?: string;
}

export interface CouncilSeatIdentity {
  /** DBTL stage whose review meeting this participant belongs to. */
  stage: string;
  role: "position" | "red_team" | "chair" | string;
  roleLabel: string;
  /** A few words naming what this seat brings. Empty for a selected seat. */
  focus: string;
  capability: string;
  agentName: string;
  /** A generalist standing in for a specialist nobody registered. */
  viaGeneralist: boolean;
  model: string;
  round: number;
  /** Only the chair's synthesis is the stage's answer. */
  countsTowardStageOutput: boolean;
}
