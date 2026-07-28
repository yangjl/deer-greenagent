import { expect, test } from "@rstest/core";

import { shouldRebaselineHumanCount } from "@/core/threads/hooks";

// The optimistic user bubble survives until the server's copy arrives, which
// is decided by comparing the human-message count against a baseline captured
// at send time. Re-baselining at the wrong moment strands the bubble and the
// user sees their message twice.

test("a send that created this thread keeps its send-time baseline", () => {
  // The DBTL new-cycle flow: send from /new, the thread is created, threadId
  // changes, the classifier card is raised — and the message rendered twice.
  expect(shouldRebaselineHumanCount("thread-new", "thread-new")).toBe(false);
});

test("switching to a different thread still re-baselines", () => {
  expect(shouldRebaselineHumanCount("thread-a", "thread-b")).toBe(true);
});

test("with nothing optimistic in flight the baseline is always refreshed", () => {
  expect(shouldRebaselineHumanCount(null, "thread-a")).toBe(true);
  expect(shouldRebaselineHumanCount(null, null)).toBe(true);
});

test("leaving a thread that has optimistic messages re-baselines", () => {
  expect(shouldRebaselineHumanCount("thread-a", null)).toBe(true);
});
