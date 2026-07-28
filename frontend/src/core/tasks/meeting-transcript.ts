/**
 * One participant's meeting transcript, as a reader follows it.
 *
 * The debate panel says *who* is arguing and how far along they are. It does
 * not say what they actually did — which files they opened, what came back,
 * what they concluded — and when a meeting fails ("every file read was
 * denied") that is the only information that explains why. This module turns
 * a participant's raw step list into the entries an inspector renders, so the
 * ordering, labelling, and truncation rules are testable without a DOM.
 *
 * Two rules worth stating:
 *
 * **A tool result belongs to the call that asked for it.** The backend records
 * an assistant turn carrying `tool_calls` and, separately, each tool's output.
 * Presented as a flat list they read as unrelated events; paired, they read as
 * "opened X → got Y", which is the unit a person actually reasons about.
 *
 * **Nothing is invented.** A missing result stays missing (`pending`) rather
 * than being rendered as an empty success — during a live run that difference
 * is the whole question the reader has.
 */

import type { SubtaskStep, SubtaskStepToolCall } from "./steps";
import type { Subtask } from "./types";

export type MeetingEntryKind = "thinking" | "tool" | "answer";

export interface MeetingTranscriptEntry {
  /** Stable within one participant; safe as a React key and selection id. */
  id: string;
  kind: MeetingEntryKind;
  /** One-line label for the collapsed row. */
  title: string;
  /** Free text: the assistant's words, or the tool's output. */
  text: string;
  /** Present for `tool` entries that recorded their request arguments. */
  args?: unknown;
  toolName?: string;
  /** The tool ran but no output has been recorded yet (still running). */
  pending?: boolean;
  truncated?: boolean;
  stepIndex: number;
}

function firstLine(text: string, limit = 120): string {
  const line = (text ?? "").trim().split("\n", 1)[0] ?? "";
  return line.length > limit ? `${line.slice(0, limit - 1)}…` : line;
}

function toolTitle(call: SubtaskStepToolCall): string {
  const name = (call.name ?? "").trim() || "tool";
  const args = call.args;
  if (args && typeof args === "object" && !Array.isArray(args)) {
    const record = args as Record<string, unknown>;
    // Path-shaped arguments are what a reader scans for; showing the whole
    // JSON blob in the row title buries them.
    for (const key of ["path", "file_path", "command", "pattern", "query"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) {
        return `${name} · ${firstLine(value, 80)}`;
      }
    }
  }
  return name;
}

/**
 * A participant's steps as inspectable entries, oldest first.
 *
 * Tool results are matched to their requesting call positionally within the
 * following tool steps — the recorded step shape carries a tool name but no
 * call id, so position is the only honest join available. A result that
 * cannot be matched is still emitted rather than dropped.
 */
export function meetingTranscript(steps: SubtaskStep[] | undefined): MeetingTranscriptEntry[] {
  const ordered = [...(steps ?? [])].sort(
    (a, b) => a.message_index - b.message_index,
  );
  const entries: MeetingTranscriptEntry[] = [];
  const toolSteps = ordered.filter((step) => step.kind === "tool");
  let consumed = 0;

  for (const step of ordered) {
    if (step.kind === "tool") {
      continue;
    }
    const text = (step.text ?? "").trim();
    if (text) {
      entries.push({
        id: `ai-${step.message_index}`,
        kind: "thinking",
        title: firstLine(text),
        text,
        truncated: step.truncated,
        stepIndex: step.message_index,
      });
    }
    for (const [callIndex, call] of (step.tool_calls ?? []).entries()) {
      const result = toolSteps[consumed];
      consumed += 1;
      entries.push({
        id: `tool-${step.message_index}-${callIndex}`,
        kind: "tool",
        title: toolTitle(call),
        text: (result?.text ?? "").trim(),
        args: call.args,
        toolName: call.name ?? result?.tool_name,
        // Set only when true, so "still running" is one shape across every
        // path that builds an entry rather than false here and absent there.
        ...(result === undefined ? { pending: true } : {}),
        truncated: result?.truncated,
        stepIndex: result?.message_index ?? step.message_index,
      });
    }
  }

  // Tool results whose requesting turn was compacted away still happened, and
  // a transcript that silently omits them misreports what the participant did.
  for (const orphan of toolSteps.slice(consumed)) {
    entries.push({
      id: `tool-orphan-${orphan.message_index}`,
      kind: "tool",
      title: (orphan.tool_name ?? "tool").trim() || "tool",
      text: (orphan.text ?? "").trim(),
      toolName: orphan.tool_name,
      truncated: orphan.truncated,
      stepIndex: orphan.message_index,
    });
  }

  return entries.sort((a, b) => a.stepIndex - b.stepIndex);
}

/**
 * The participant's transcript plus its final answer as one closing entry.
 *
 * The answer is appended rather than read out of the steps: a completed
 * subagent's last assistant turn is dropped from the step timeline (it is the
 * result), so a transcript built from steps alone ends mid-investigation.
 */
export function meetingTranscriptWithResult(task: Subtask): MeetingTranscriptEntry[] {
  const entries = meetingTranscript(task.steps);
  const closing = (task.result ?? task.error ?? "").trim();
  if (!closing) {
    return entries;
  }
  const lastIndex = entries.length ? entries[entries.length - 1]!.stepIndex : 0;
  return [
    ...entries,
    {
      id: "answer",
      kind: "answer",
      title: task.error ? "Reported a failure" : "Final position",
      text: closing,
      stepIndex: lastIndex + 1,
    },
  ];
}

/** How many tool calls this participant made, for the collapsed summary. */
export function toolCallCount(entries: MeetingTranscriptEntry[]): number {
  return entries.filter((entry) => entry.kind === "tool").length;
}

/**
 * A participant's closing result, rendered for a person instead of a parser.
 *
 * A worker returns its position as a JSON object because the stage contract is
 * validated, not because anyone wants to read it that way. Shown raw in the
 * inspector it is a wall of escaped braces, and the part a reviewer actually
 * needs — where the meeting agreed, where it did not, and what only they can
 * decide — is buried in the middle of it. Parsing is permissive: anything that
 * does not fit degrades to the raw text rather than costing the reader the
 * result entirely.
 */

export interface MeetingDisagreement {
  topic: string;
  positions: string[];
  resolution: string;
}

export interface MeetingConsensus {
  agreements: string[];
  disagreements: MeetingDisagreement[];
  openQuestions: string[];
}

export interface MeetingResultView {
  status: string;
  summary: string;
  claims: string[];
  limitations: string[];
  evidence: string[];
  nextActions: string[];
  failedChecks: string[];
  clarificationQuestion: string;
  consensus: MeetingConsensus | null;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => (typeof entry === "string" ? entry.trim() : ""))
    .filter((entry) => entry.length > 0);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseConsensus(value: unknown): MeetingConsensus | null {
  if (!isRecord(value)) {
    return null;
  }
  const disagreements: MeetingDisagreement[] = [];
  if (Array.isArray(value.disagreements)) {
    for (const raw of value.disagreements) {
      if (!isRecord(raw)) {
        continue;
      }
      const topic = typeof raw.topic === "string" ? raw.topic.trim() : "";
      if (!topic) {
        continue;
      }
      disagreements.push({
        topic,
        positions: stringList(raw.positions),
        // An unsettled disagreement keeps an empty resolution and renders as
        // "not resolved"; dropping it would report a cleaner debate than the
        // one that happened.
        resolution:
          typeof raw.resolution === "string" ? raw.resolution.trim() : "",
      });
    }
  }
  const agreements = stringList(value.agreements);
  const openQuestions = stringList(value.open_questions);
  if (!agreements.length && !disagreements.length && !openQuestions.length) {
    return null;
  }
  return { agreements, disagreements, openQuestions };
}

function evidenceLine(raw: unknown): string {
  if (typeof raw === "string") {
    return raw.trim();
  }
  if (!isRecord(raw)) {
    return "";
  }
  const reference =
    typeof raw.reference === "string" ? raw.reference.trim() : "";
  const description =
    typeof raw.description === "string" ? raw.description.trim() : "";
  if (!reference) {
    return description;
  }
  return description ? `${reference} — ${description}` : reference;
}

/** Parse a worker's result payload, or `null` when it is not one. */
export function parseMeetingResult(text: string): MeetingResultView | null {
  const trimmed = (text ?? "").trim();
  if (!trimmed.startsWith("{")) {
    return null;
  }
  let payload: unknown;
  try {
    payload = JSON.parse(trimmed);
  } catch {
    return null;
  }
  if (!isRecord(payload) || typeof payload.summary !== "string") {
    return null;
  }
  const checks = Array.isArray(payload.quality_checks)
    ? payload.quality_checks
    : [];
  return {
    status: typeof payload.status === "string" ? payload.status : "",
    summary: payload.summary.trim(),
    claims: stringList(payload.claims),
    limitations: stringList(payload.limitations),
    evidence: (Array.isArray(payload.evidence_refs) ? payload.evidence_refs : [])
      .map(evidenceLine)
      .filter((line) => line.length > 0),
    nextActions: stringList(payload.recommended_next_actions),
    failedChecks: checks
      .filter((check) => isRecord(check) && check.passed === false)
      .map((check) => {
        const record = check as Record<string, unknown>;
        const name = typeof record.name === "string" ? record.name : "check";
        const detail =
          typeof record.detail === "string" ? record.detail.trim() : "";
        return detail ? `${name}: ${detail}` : name;
      }),
    clarificationQuestion:
      typeof payload.clarification_question === "string"
        ? payload.clarification_question.trim()
        : "",
    consensus: parseConsensus(payload.consensus),
  };
}
