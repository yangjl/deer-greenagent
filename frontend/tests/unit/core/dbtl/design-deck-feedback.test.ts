import { describe, expect, it } from "@rstest/core";

import {
  DECK_MESSAGE_SOURCE,
  DECK_PROTOCOL_VERSION,
  type DeckSurfaceState,
  initialDeckState,
  isDeckIntent,
  parseDeckIntent,
  reduceDeckState,
  toDeckMessage,
} from "@/core/dbtl/design-deck-feedback";

const SURFACE_ID = "dfs-0123456789abcdef";
const CHANNEL = "chan-abc";

function readyIntent(overrides: Record<string, unknown> = {}) {
  return {
    source: DECK_MESSAGE_SOURCE,
    protocol: DECK_PROTOCOL_VERSION,
    surfaceId: SURFACE_ID,
    type: "ready",
    ...overrides,
  };
}

function submitIntent(overrides: Record<string, unknown> = {}) {
  return {
    source: DECK_MESSAGE_SOURCE,
    protocol: DECK_PROTOCOL_VERSION,
    surfaceId: SURFACE_ID,
    channel: CHANNEL,
    type: "submit_intent",
    action: { kind: "chair_option", optionIds: ["family_holdout"] },
    comment: "Keep one site external if sample size allows.",
    ...overrides,
  };
}

describe("parseDeckIntent", () => {
  it("accepts a well-formed ready announcement", () => {
    const parsed = parseDeckIntent(readyIntent(), { surfaceId: SURFACE_ID, channel: null });

    expect(parsed).not.toBeNull();
    expect(parsed?.type).toBe("ready");
  });

  it("accepts a submit intent carrying one option and a comment", () => {
    const parsed = parseDeckIntent(submitIntent(), { surfaceId: SURFACE_ID, channel: CHANNEL });

    expect(parsed?.type).toBe("submit_intent");
    if (parsed?.type !== "submit_intent") throw new Error("expected submit_intent");
    expect(parsed.action.optionIds).toEqual(["family_holdout"]);
    expect(parsed.comment).toBe("Keep one site external if sample size allows.");
  });

  // Each of these is a way a hostile or confused page could try to be mistaken
  // for the deck. None of them may parse.
  it.each([
    ["a foreign source", readyIntent({ source: "some-other-widget" })],
    ["a different protocol", readyIntent({ protocol: DECK_PROTOCOL_VERSION + 1 })],
    ["another surface", readyIntent({ surfaceId: "dfs-someone-elses" })],
    ["an unknown type", readyIntent({ type: "approve_everything" })],
    ["a missing type", readyIntent({ type: undefined })],
    ["a non-object payload", "ready"],
    ["null", null],
    ["an array", []],
  ])("refuses %s", (_label, payload) => {
    expect(parseDeckIntent(payload, { surfaceId: SURFACE_ID, channel: null })).toBeNull();
  });

  it("refuses a submit intent that does not carry the issued channel", () => {
    expect(parseDeckIntent(submitIntent({ channel: "chan-guessed" }), { surfaceId: SURFACE_ID, channel: CHANNEL })).toBeNull();
  });

  it("refuses a submit intent before any channel has been issued", () => {
    expect(parseDeckIntent(submitIntent(), { surfaceId: SURFACE_ID, channel: null })).toBeNull();
  });

  it("refuses an unknown action kind", () => {
    const payload = submitIntent({
      action: { kind: "delete_everything", optionIds: [] },
    });

    expect(parseDeckIntent(payload, { surfaceId: SURFACE_ID, channel: CHANNEL })).toBeNull();
  });

  it("accepts formal review actions with their bounded shapes", () => {
    const approve = submitIntent({
      action: { kind: "approve", optionIds: [] },
    });
    const changes = submitIntent({
      action: { kind: "request_changes", optionIds: ["issue-1"] },
    });

    expect(
      parseDeckIntent(approve, { surfaceId: SURFACE_ID, channel: CHANNEL }),
    ).not.toBeNull();
    expect(
      parseDeckIntent(changes, { surfaceId: SURFACE_ID, channel: CHANNEL }),
    ).not.toBeNull();
  });

  it("accepts a written change request without a recorded issue card", () => {
    const changes = submitIntent({
      action: { kind: "request_changes", optionIds: [] },
      comment: "Add an external-site validation criterion.",
    });

    expect(
      parseDeckIntent(changes, { surfaceId: SURFACE_ID, channel: CHANNEL }),
    ).not.toBeNull();
  });

  it("refuses a change request with neither an issue nor a comment", () => {
    const changes = submitIntent({
      action: { kind: "request_changes", optionIds: [] },
      comment: "",
    });

    expect(
      parseDeckIntent(changes, { surfaceId: SURFACE_ID, channel: CHANNEL }),
    ).toBeNull();
  });

  it("refuses a chair option that selects nothing", () => {
    const payload = submitIntent({ action: { kind: "chair_option", optionIds: [] } });

    expect(parseDeckIntent(payload, { surfaceId: SURFACE_ID, channel: CHANNEL })).toBeNull();
  });

  it("refuses a chair option that selects more than one thing", () => {
    const payload = submitIntent({
      action: { kind: "chair_option", optionIds: ["a", "b"] },
    });

    expect(parseDeckIntent(payload, { surfaceId: SURFACE_ID, channel: CHANNEL })).toBeNull();
  });

  it("refuses option ids that are not plain slugs", () => {
    const payload = submitIntent({
      action: { kind: "chair_option", optionIds: ["<script>alert(1)</script>"] },
    });

    expect(parseDeckIntent(payload, { surfaceId: SURFACE_ID, channel: CHANNEL })).toBeNull();
  });

  it("bounds an over-long comment rather than accepting it whole", () => {
    const payload = submitIntent({ comment: "x".repeat(10_000) });

    const parsed = parseDeckIntent(payload, { surfaceId: SURFACE_ID, channel: CHANNEL });

    if (parsed?.type !== "submit_intent") throw new Error("expected submit_intent");
    expect(parsed.comment.length).toBeLessThan(10_000);
  });

  it("treats a non-string comment as absent rather than coercing it", () => {
    const parsed = parseDeckIntent(submitIntent({ comment: { toString: "nope" } }), {
      surfaceId: SURFACE_ID,
      channel: CHANNEL,
    });

    if (parsed?.type !== "submit_intent") throw new Error("expected submit_intent");
    expect(parsed.comment).toBe("");
  });
});

describe("isDeckIntent", () => {
  it("recognizes the envelope without validating the body", () => {
    expect(isDeckIntent(readyIntent())).toBe(true);
    expect(isDeckIntent({ source: "scroll-restoration", type: "save" })).toBe(false);
  });
});

describe("reduceDeckState", () => {
  const base: DeckSurfaceState = initialDeckState(SURFACE_ID);

  it("starts inert with nothing allowed", () => {
    expect(base.status).toBe("idle");
    expect(base.allowedActions).toEqual([]);
    expect(base.channel).toBeNull();
  });

  it("activates only once the server has spoken", () => {
    const next = reduceDeckState(base, {
      kind: "server_state",
      channel: CHANNEL,
      allowedActions: ["chair_option"],
    });

    expect(next.channel).toBe(CHANNEL);
    expect(next.allowedActions).toEqual(["chair_option"]);
    expect(next.status).toBe("ready");
  });

  it("drops an action the server did not name", () => {
    const next = reduceDeckState(base, {
      kind: "server_state",
      channel: CHANNEL,
      allowedActions: ["chair_option", "approve", "delete_everything"] as never,
    });

    expect(next.allowedActions).toEqual(["chair_option", "approve"]);
  });

  it("marks a submission pending and keeps its idempotency key", () => {
    const ready = reduceDeckState(base, {
      kind: "server_state",
      channel: CHANNEL,
      allowedActions: ["chair_option"],
    });

    const pending = reduceDeckState(ready, { kind: "submitting", submissionId: "sub-1" });

    expect(pending.status).toBe("submitting");
    expect(pending.submissionId).toBe("sub-1");
  });

  it("reuses the same submission id after a failure so a retry is not a second answer", () => {
    const pending = reduceDeckState(
      reduceDeckState(base, { kind: "server_state", channel: CHANNEL, allowedActions: ["chair_option"] }),
      { kind: "submitting", submissionId: "sub-1" },
    );

    const failed = reduceDeckState(pending, { kind: "failed", note: "Network error." });

    expect(failed.status).toBe("failed");
    expect(failed.submissionId).toBe("sub-1");
  });

  it("freezes after acceptance", () => {
    const pending = reduceDeckState(
      reduceDeckState(base, { kind: "server_state", channel: CHANNEL, allowedActions: ["chair_option"] }),
      { kind: "submitting", submissionId: "sub-1" },
    );

    const accepted = reduceDeckState(pending, { kind: "accepted", note: "Recorded." });

    expect(accepted.status).toBe("accepted");
    expect(accepted.allowedActions).toEqual([]);
  });

  it("stops accepting anything once superseded", () => {
    const ready = reduceDeckState(base, {
      kind: "server_state",
      channel: CHANNEL,
      allowedActions: ["chair_option"],
    });

    const stale = reduceDeckState(ready, { kind: "stale", newestSurfaceId: "dfs-newer" });

    expect(stale.status).toBe("stale");
    expect(stale.allowedActions).toEqual([]);
    expect(stale.newestSurfaceId).toBe("dfs-newer");
  });

  it("does not rebase a stale surface back into readiness", () => {
    // A rejected-for-staleness response must never be retried against the new
    // revision: the person decided about a specific document.
    const stale = reduceDeckState(
      reduceDeckState(base, { kind: "server_state", channel: CHANNEL, allowedActions: ["chair_option"] }),
      { kind: "stale", newestSurfaceId: "dfs-newer" },
    );

    const attempted = reduceDeckState(stale, {
      kind: "server_state",
      channel: CHANNEL,
      allowedActions: ["chair_option"],
    });

    expect(attempted.status).toBe("stale");
    expect(attempted.allowedActions).toEqual([]);
  });
});

describe("toDeckMessage", () => {
  it("stamps the envelope the deck validates", () => {
    const message = toDeckMessage(SURFACE_ID, CHANNEL, {
      type: "initialize",
      allowedActions: ["chair_option"],
      note: "Choose an option.",
    });

    expect(message.source).toBe(DECK_MESSAGE_SOURCE);
    expect(message.protocol).toBe(DECK_PROTOCOL_VERSION);
    expect(message.surfaceId).toBe(SURFACE_ID);
    expect(message.channel).toBe(CHANNEL);
    expect(message.type).toBe("initialize");
  });

  it("carries no endpoint, token, or credential", () => {
    const message = toDeckMessage(SURFACE_ID, CHANNEL, { type: "accepted", note: "Recorded." });
    const serialized = JSON.stringify(message);

    expect(serialized).not.toContain("/api/");
    expect(serialized).not.toContain("token");
    expect(serialized).not.toContain("cookie");
  });
});
