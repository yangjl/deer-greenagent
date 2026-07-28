import { expect, test } from "@rstest/core";

import {
  meetingTranscript,
  meetingTranscriptWithResult,
  toolCallCount,
} from "@/core/tasks/meeting-transcript";
import type { SubtaskStep } from "@/core/tasks/steps";
import type { Subtask } from "@/core/tasks/types";

const steps: SubtaskStep[] = [
  {
    message_index: 0,
    kind: "ai",
    text: "I need the prior design package first.",
    tool_calls: [
      { name: "read_file", args: { path: "/mnt/user-data/outputs/design.json" } },
    ],
  },
  {
    message_index: 1,
    kind: "tool",
    tool_name: "read_file",
    text: "Error: Only paths under /mnt/user-data/ are allowed",
  },
];

const task = (overrides: Partial<Subtask> = {}): Subtask => ({
  id: "dbtl-x-1-quantitative_genetics",
  status: "failed",
  subagent_type: "experimental-design",
  description: "independent position",
  prompt: "argue",
  steps,
  ...overrides,
});

test("a tool call is paired with the output it produced", () => {
  const entries = meetingTranscript(steps);
  expect(entries.map((entry) => entry.kind)).toEqual(["thinking", "tool"]);
  const tool = entries[1]!;
  expect(tool.title).toBe("read_file · /mnt/user-data/outputs/design.json");
  expect(tool.text).toContain("Only paths under /mnt/user-data/");
  expect(tool.pending).toBeUndefined();
  expect(toolCallCount(entries)).toBe(1);
});

test("a call still running is pending, not an empty success", () => {
  const entries = meetingTranscript([steps[0]!]);
  expect(entries[1]).toMatchObject({ kind: "tool", pending: true, text: "" });
});

test("a tool result whose requesting turn was compacted away is kept", () => {
  const entries = meetingTranscript([steps[1]!]);
  expect(entries).toHaveLength(1);
  expect(entries[0]).toMatchObject({ kind: "tool", title: "read_file" });
});

test("entries stay in step order regardless of input order", () => {
  const entries = meetingTranscript([steps[1]!, steps[0]!]);
  expect(entries.map((entry) => entry.stepIndex)).toEqual([0, 1]);
});

test("the final answer closes the transcript", () => {
  const entries = meetingTranscriptWithResult(
    task({ status: "completed", result: "My position is X." }),
  );
  const last = entries[entries.length - 1]!;
  expect(last).toMatchObject({ kind: "answer", title: "Final position" });
  expect(last.text).toBe("My position is X.");
});

test("a failure is closed with its error rather than silently omitted", () => {
  const entries = meetingTranscriptWithResult(
    task({ status: "failed", error: "returned prose instead of a result" }),
  );
  const last = entries[entries.length - 1]!;
  expect(last).toMatchObject({ kind: "answer", title: "Reported a failure" });
  expect(last.text).toBe("returned prose instead of a result");
});

test("a participant with no steps yields nothing to inspect", () => {
  expect(meetingTranscript(undefined)).toEqual([]);
  expect(meetingTranscriptWithResult(task({ steps: [], result: "" }))).toEqual(
    [],
  );
});
