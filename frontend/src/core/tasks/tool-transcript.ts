/**
 * What a delegated task actually did, as a reader follows it.
 *
 * A subtask card says *that* a tool ran and names it. It does not say what was
 * asked for or what came back — and when work fails ("every file read was
 * denied", "the script exited 1"), that is the only information that explains
 * why. The backend has recorded both since #3779; nothing rendered them.
 *
 * This pairing logic began life inside `meeting-transcript.ts`, which is where
 * the need first surfaced, but nothing about it is meeting-specific: a Design
 * council seat and an ordinary delegated subagent record steps in exactly the
 * same shape. It lives here so both read the same way and a Build worker never
 * needs a renderer of its own.
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

export type TranscriptEntryKind = "thinking" | "tool" | "answer";

export interface TranscriptEntry {
  /** Stable within one task; safe as a React key and selection id. */
  id: string;
  kind: TranscriptEntryKind;
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

export interface TaskTranscriptOptions {
  /**
   * Drop a trailing assistant turn that requested no tools.
   *
   * On a completed task that turn is the final answer, which every caller
   * already renders from `task.result`; including it here shows the answer
   * twice. Same rule as `stepsForDisplay`, applied before pairing so the
   * tool-call turns it depends on are still intact.
   */
  dropTrailingAnswer?: boolean;
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

/** Render a tool's request arguments for display, without ever throwing. */
export function formatToolArgs(args: unknown): string {
  if (args === undefined || args === null) {
    return "";
  }
  if (typeof args === "string") {
    return args;
  }
  try {
    return JSON.stringify(args, null, 2) ?? "";
  } catch {
    // A value JSON cannot serialize (a cycle, a BigInt) is still worth
    // acknowledging; naming its type beats rendering "[object Object]".
    return `(unserializable ${typeof args})`;
  }
}

/**
 * A task's steps as inspectable entries, oldest first.
 *
 * Tool results are matched to their requesting call positionally within the
 * following tool steps — the recorded step shape carries a tool name but no
 * call id, so position is the only honest join available. A result that
 * cannot be matched is still emitted rather than dropped.
 */
export function taskTranscript(
  steps: SubtaskStep[] | undefined,
  options: TaskTranscriptOptions = {},
): TranscriptEntry[] {
  const ordered = [...(steps ?? [])].sort(
    (a, b) => a.message_index - b.message_index,
  );

  if (options.dropTrailingAnswer) {
    const last = ordered[ordered.length - 1];
    if (last?.kind === "ai" && !last.tool_calls?.length) {
      ordered.pop();
    }
  }

  const entries: TranscriptEntry[] = [];
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
  // a transcript that silently omits them misreports what the task did.
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

/** How many tool calls this task made, for a collapsed summary. */
export function transcriptToolCallCount(entries: TranscriptEntry[]): number {
  return entries.filter((entry) => entry.kind === "tool").length;
}
