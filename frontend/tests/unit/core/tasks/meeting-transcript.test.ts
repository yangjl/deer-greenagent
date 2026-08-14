import { expect, test } from "@rstest/core";

import {
  meetingTranscript,
  meetingTranscriptWithResult,
  parseMeetingResult,
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
      {
        name: "read_file",
        args: { path: "/mnt/user-data/outputs/design.json" },
      },
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

const chairResult = JSON.stringify({
  status: "needs_input",
  summary: "The two positions converge on the same design-gating issue.",
  claims: ["No implementation-ready protocol exists."],
  limitations: ["Generation rules remain unfrozen."],
  evidence_refs: [
    {
      kind: "workspace_file",
      reference: "/mnt/user-data/outputs/x.json",
      description: "Latest package",
    },
    {
      kind: "external",
      reference: "experimental-design:red_team",
      description: "",
    },
  ],
  quality_checks: [
    { name: "threshold stated", passed: false, detail: "no denominator" },
    { name: "criterion measurable", passed: true, detail: "" },
  ],
  recommended_next_actions: ["Freeze the generation rules."],
  clarification_question: "Toy benchmark or credible simulator?",
  consensus: {
    agreements: ["The lineage is a restricted benchmark."],
    disagreements: [
      {
        topic: "Scope of inference",
        positions: ["not decidable", "already decided elsewhere"],
        resolution: "",
      },
      {
        topic: "Marker count",
        positions: ["10 is enough", "10 is too few"],
        resolution: "Use 10 for the benchmark only.",
      },
    ],
    open_questions: ["Which germplasm?"],
  },
});

test("a chair result parses into the sections a reviewer reads", () => {
  const view = parseMeetingResult(chairResult)!;
  expect(view.status).toBe("needs_input");
  expect(view.clarificationQuestion).toBe(
    "Toy benchmark or credible simulator?",
  );
  expect(view.consensus?.agreements).toEqual([
    "The lineage is a restricted benchmark.",
  ]);
  expect(view.consensus?.openQuestions).toEqual(["Which germplasm?"]);
  expect(view.claims).toHaveLength(1);
  expect(view.nextActions).toEqual(["Freeze the generation rules."]);
});

test("an unresolved disagreement keeps its empty resolution rather than vanishing", () => {
  const view = parseMeetingResult(chairResult)!;
  const [unresolved, settled] = view.consensus!.disagreements;
  expect(unresolved!.resolution).toBe("");
  expect(unresolved!.positions).toHaveLength(2);
  expect(settled!.resolution).toContain("benchmark only");
});

test("only failed quality checks surface, with their detail", () => {
  const view = parseMeetingResult(chairResult)!;
  expect(view.failedChecks).toEqual(["threshold stated: no denominator"]);
});

test("evidence renders as reference plus description", () => {
  const view = parseMeetingResult(chairResult)!;
  expect(view.evidence[0]).toBe(
    "/mnt/user-data/outputs/x.json — Latest package",
  );
  expect(view.evidence[1]).toBe("experimental-design:red_team");
});

test("prose and malformed payloads degrade to the raw text", () => {
  expect(parseMeetingResult("I could not read the files.")).toBeNull();
  expect(parseMeetingResult("{not json")).toBeNull();
  expect(
    parseMeetingResult(JSON.stringify({ status: "completed" })),
  ).toBeNull();
});

test("a result with no consensus block still renders its synthesis", () => {
  const view = parseMeetingResult(
    JSON.stringify({ summary: "A position.", claims: ["x"] }),
  )!;
  expect(view.consensus).toBeNull();
  expect(view.summary).toBe("A position.");
});
