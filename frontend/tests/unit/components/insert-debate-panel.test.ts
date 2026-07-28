import { expect, test } from "@rstest/core";

import {
  debatePanelPosition,
  insertDebatePanel,
  shouldRenderDebatePanel,
} from "@/components/workspace/messages/message-list";

// The nodes are opaque to the helper, so plain strings stand in for rendered
// message groups — what is under test is *where* the panel lands.
const nodes = ["human", "assistant:steps", "assistant:answer"];

test("a running meeting keeps the panel at the bottom, where the reader is", () => {
  const at = debatePanelPosition({ groupCount: nodes.length, isLoading: true });
  expect(insertDebatePanel(nodes, at, "panel")).toEqual([...nodes, "panel"]);
});

test("a settled run puts the closing answer below the meeting", () => {
  const at = debatePanelPosition({
    groupCount: nodes.length,
    isLoading: false,
  });
  const placed = insertDebatePanel(nodes, at, "panel");
  expect(placed).toEqual([
    "human",
    "assistant:steps",
    "panel",
    "assistant:answer",
  ]);
  expect(placed.indexOf("panel")).toBeLessThan(
    placed.indexOf("assistant:answer"),
  );
});

test("a single group has no answer to sit above, so the panel follows it", () => {
  const at = debatePanelPosition({ groupCount: 1, isLoading: false });
  expect(insertDebatePanel(["human"], at, "panel")).toEqual(["human", "panel"]);
});

test("an empty transcript has no anchor for a previous conversation's panel", () => {
  const at = debatePanelPosition({ groupCount: 0, isLoading: false });
  expect(shouldRenderDebatePanel(0)).toBe(false);
  expect(insertDebatePanel([], at, null)).toEqual([null]);
});

test("a conversation with a transcript can render its meeting", () => {
  expect(shouldRenderDebatePanel(1)).toBe(true);
});

test("an out-of-range position clamps rather than dropping the panel", () => {
  expect(insertDebatePanel(nodes, 99, "panel")).toEqual([...nodes, "panel"]);
  expect(insertDebatePanel(nodes, -5, "panel")).toEqual(["panel", ...nodes]);
});
