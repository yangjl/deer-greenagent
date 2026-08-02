/**
 * Pairing a task's tool calls with what they returned.
 *
 * These rules were written for the meeting inspector and are now shared with
 * every delegated subtask, so they are tested here rather than through one
 * caller. The behaviour the meeting inspector already relied on must not move:
 * `meeting-transcript.test.ts` still covers that surface, and this file covers
 * the shared implementation plus the one option the subtask card added.
 */

import { describe, expect, it } from "@rstest/core";

import type { SubtaskStep } from "@/core/tasks/steps";
import {
  formatToolArgs,
  taskTranscript,
  transcriptToolCallCount,
} from "@/core/tasks/tool-transcript";

function ai(index: number, text: string, calls: { name: string; args?: unknown }[] = []): SubtaskStep {
  return { message_index: index, kind: "ai", text, tool_calls: calls };
}

function tool(index: number, name: string, text: string, extra: Partial<SubtaskStep> = {}): SubtaskStep {
  return { message_index: index, kind: "tool", text, tool_name: name, ...extra };
}

describe("a tool result belongs to the call that asked for it", () => {
  it("pairs a request with the output that followed it", () => {
    const entries = taskTranscript([
      ai(1, "Reading the inputs", [{ name: "read_file", args: { path: "/mnt/user-data/x.csv" } }]),
      tool(2, "read_file", "col_a,col_b"),
    ]);

    const toolEntry = entries.find((entry) => entry.kind === "tool");
    expect(toolEntry?.args).toEqual({ path: "/mnt/user-data/x.csv" });
    expect(toolEntry?.text).toBe("col_a,col_b");
  });

  it("names the path in the row title rather than the whole payload", () => {
    const entries = taskTranscript([
      ai(1, "", [{ name: "read_file", args: { path: "/mnt/user-data/design.md" } }]),
      tool(2, "read_file", "# Design"),
    ]);

    expect(entries[0]?.title).toBe("read_file · /mnt/user-data/design.md");
  });

  it("shows a bash command in the title", () => {
    const entries = taskTranscript([
      ai(1, "", [{ name: "bash", args: { command: "python fit.py --seed 7" } }]),
      tool(2, "bash", "done"),
    ]);

    expect(entries[0]?.title).toBe("bash · python fit.py --seed 7");
  });

  it("marks a call whose output has not arrived as pending", () => {
    const entries = taskTranscript([ai(1, "", [{ name: "bash", args: { command: "sleep 60" } }])]);

    expect(entries[0]?.pending).toBe(true);
    expect(entries[0]?.text).toBe("");
  });

  it("keeps a result whose requesting turn was compacted away", () => {
    // Dropping it would misreport what the task did.
    const entries = taskTranscript([tool(5, "bash", "exit 1")]);

    expect(entries).toHaveLength(1);
    expect(entries[0]?.title).toBe("bash");
    expect(entries[0]?.text).toBe("exit 1");
  });

  it("pairs several calls in one turn positionally", () => {
    const entries = taskTranscript([
      ai(1, "", [{ name: "read_file", args: { path: "a" } }, { name: "read_file", args: { path: "b" } }]),
      tool(2, "read_file", "contents of a"),
      tool(3, "read_file", "contents of b"),
    ]);

    expect(entries.map((entry) => entry.text)).toEqual(["contents of a", "contents of b"]);
  });
});

describe("a completed task does not show its answer twice", () => {
  const steps = [
    ai(1, "", [{ name: "bash", args: { command: "run" } }]),
    tool(2, "bash", "ok"),
    ai(3, "The build succeeded."),
  ];

  it("drops the trailing answer turn when asked to", () => {
    const entries = taskTranscript(steps, { dropTrailingAnswer: true });
    expect(entries.map((entry) => entry.kind)).toEqual(["tool"]);
  });

  it("keeps it otherwise", () => {
    const entries = taskTranscript(steps);
    expect(entries.map((entry) => entry.kind)).toEqual(["tool", "thinking"]);
  });

  it("keeps a trailing turn that requested tools", () => {
    // That turn is not an answer; its calls are still pending.
    const entries = taskTranscript([ai(1, "Checking", [{ name: "bash" }])], {
      dropTrailingAnswer: true,
    });
    expect(entries.map((entry) => entry.kind)).toEqual(["thinking", "tool"]);
  });

  it("still pairs the calls of an earlier turn after dropping the answer", () => {
    const entries = taskTranscript(steps, { dropTrailingAnswer: true });
    expect(entries[0]?.text).toBe("ok");
  });
});

describe("ordering and counting", () => {
  it("returns entries oldest first regardless of input order", () => {
    const entries = taskTranscript([tool(4, "bash", "second"), ai(1, "", [{ name: "bash" }])]);
    expect(entries.map((entry) => entry.stepIndex)).toEqual([4]);
  });

  it("counts tool calls for a collapsed summary", () => {
    const entries = taskTranscript([
      ai(1, "thinking", [{ name: "bash" }, { name: "read_file" }]),
      tool(2, "bash", "a"),
      tool(3, "read_file", "b"),
    ]);
    expect(transcriptToolCallCount(entries)).toBe(2);
  });

  it("reads an empty step list as an empty transcript", () => {
    expect(taskTranscript(undefined)).toEqual([]);
    expect(taskTranscript([])).toEqual([]);
  });
});

describe("request arguments are rendered without ever throwing", () => {
  it("pretty-prints an object", () => {
    expect(formatToolArgs({ path: "x" })).toBe('{\n  "path": "x"\n}');
  });

  it("passes a string through", () => {
    expect(formatToolArgs("ls -la")).toBe("ls -la");
  });

  it("reads absent arguments as nothing to show", () => {
    expect(formatToolArgs(undefined)).toBe("");
    expect(formatToolArgs(null)).toBe("");
  });

  it("names a value it cannot serialize instead of failing", () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(formatToolArgs(cyclic)).toBe("(unserializable object)");
  });
});
