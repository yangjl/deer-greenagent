/**
 * One participant's meeting transcript, as a reader follows it.
 *
 * The debate panel says *who* is arguing and how far along they are. It does
 * not say what they actually did — which files they opened, what came back,
 * what they concluded — and when a meeting fails ("every file read was
 * denied") that is the only information that explains why.
 *
 * The pairing and labelling rules that answer that question are not
 * meeting-specific and now live in `./tool-transcript`, where an ordinary
 * delegated subtask reads them the same way. This module keeps the meeting's
 * own vocabulary and the parts a debate genuinely adds: a closing position and
 * the structured consensus a chair reports.
 */

import {
  taskTranscript,
  transcriptToolCallCount,
  type TranscriptEntry,
  type TranscriptEntryKind,
} from "./tool-transcript";
import type { Subtask } from "./types";

export type MeetingEntryKind = TranscriptEntryKind;
export type MeetingTranscriptEntry = TranscriptEntry;

/**
 * A participant's steps as inspectable entries, oldest first.
 *
 * Deliberately keeps a completed participant's closing turn: the inspector
 * shows a meeting as an argument in progress, and its final position is part
 * of the transcript rather than a separate result field.
 */
export function meetingTranscript(
  steps: Parameters<typeof taskTranscript>[0],
): MeetingTranscriptEntry[] {
  return taskTranscript(steps);
}

/**
 * The participant's transcript plus its final answer as one closing entry.
 *
 * The answer is appended rather than read out of the steps: a completed
 * subagent's last assistant turn is dropped from the step timeline (it is the
 * result), so a transcript built from steps alone ends mid-investigation.
 */
export function meetingTranscriptWithResult(
  task: Subtask,
): MeetingTranscriptEntry[] {
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
  return transcriptToolCallCount(entries);
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
    evidence: (Array.isArray(payload.evidence_refs)
      ? payload.evidence_refs
      : []
    )
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
