/**
 * The parent's half of the Design deck bridge — pure, and deliberately strict.
 *
 * A Design deck is HTML the workflow rendered, running in an opaque-origin
 * iframe. It may collect a decision; it may never authorize one. Everything it
 * sends is an *intent* that this module parses into a small closed vocabulary,
 * and everything the parent sends back is state. No endpoint, credential, or
 * write capability crosses the boundary in either direction.
 *
 * Two things are worth stating plainly, because both are easy to erode later.
 *
 * Parsing here is a UX boundary, **not** an authorization boundary. The server
 * repeats every check — membership, surface identity, evidence revision, deck
 * hash — on the write itself. If the backend ever trusts this module's verdict,
 * the model collapses to "any page that can talk to the parent can write".
 *
 * A stale surface never rebases. Once the evidence a person was shown has moved
 * on, their pending answer is about a document that no longer exists, and
 * quietly retrying it against the new revision is how an approval ends up bound
 * to something nobody read.
 */

/** The envelope both sides stamp. Must match `council_deck.DECK_MESSAGE_SOURCE`. */
export const DECK_MESSAGE_SOURCE = "deerflow-design-deck";
export const DECK_PROTOCOL_VERSION = 1;

const MAX_COMMENT_CHARS = 4_000;
const MAX_OPTION_ID_CHARS = 64;

/** Option ids are slugs; anything else did not come from a rendered deck. */
const OPTION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

/**
 * What the parent may be told a person did. Closed on purpose: an action the
 * parent has no handler for must not be expressible, let alone forwarded.
 */
export const DECK_ACTION_KINDS = [
  "chair_option",
  "chair_text",
  "submit_for_review",
  "approve",
  "request_changes",
  "reject",
] as const;
export type DeckActionKind = (typeof DECK_ACTION_KINDS)[number];

export interface DeckSubmitAction {
  kind: DeckActionKind;
  optionIds: string[];
}

export type DeckIntent =
  | { type: "ready"; surfaceId: string }
  | { type: "submit_intent"; surfaceId: string; action: DeckSubmitAction; comment: string }
  | { type: "open_evidence_intent"; surfaceId: string }
  | { type: "open_originating_conversation_intent"; surfaceId: string };

const INTENT_TYPES = new Set([
  "ready",
  "submit_intent",
  "open_evidence_intent",
  "open_originating_conversation_intent",
]);

export type DeckStateMessageType = "initialize" | "pending" | "accepted" | "stale" | "failed";

export interface DeckStateMessage {
  source: typeof DECK_MESSAGE_SOURCE;
  protocol: typeof DECK_PROTOCOL_VERSION;
  surfaceId: string;
  channel: string;
  type: DeckStateMessageType;
  allowedActions?: DeckActionKind[];
  note?: string;
}

export interface DeckIntentContext {
  surfaceId: string;
  /** The per-mount channel this parent issued, or null before the handshake. */
  channel: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Whether a message even claims to be from a deck.
 *
 * Cheap and envelope-only, so a listener can ignore unrelated frames (the
 * artifact scroll-restoration protocol shares the same window) without paying
 * for full validation.
 */
export function isDeckIntent(value: unknown): boolean {
  return isRecord(value) && value.source === DECK_MESSAGE_SOURCE;
}

function parseAction(value: unknown): DeckSubmitAction | null {
  if (!isRecord(value)) return null;
  const kind = value.kind;
  if (typeof kind !== "string" || !DECK_ACTION_KINDS.includes(kind as DeckActionKind)) {
    return null;
  }
  const rawIds = value.optionIds;
  if (!Array.isArray(rawIds)) return null;
  const optionIds = rawIds.filter(
    (id): id is string =>
      typeof id === "string" && id.length <= MAX_OPTION_ID_CHARS && OPTION_ID_PATTERN.test(id),
  );
  if (optionIds.length !== rawIds.length) return null;
  if (kind === "chair_option" && optionIds.length !== 1) return null;
  if (
    kind !== "chair_option" &&
    kind !== "request_changes" &&
    optionIds.length !== 0
  ) {
    return null;
  }
  return { kind: kind as DeckActionKind, optionIds };
}

/**
 * Read one message from the deck, or refuse it.
 *
 * Returns `null` rather than throwing: a malformed frame is an ordinary event
 * on a window anyone can post to, not an exceptional condition.
 */
export function parseDeckIntent(value: unknown, context: DeckIntentContext): DeckIntent | null {
  if (!isRecord(value)) return null;
  if (value.source !== DECK_MESSAGE_SOURCE) return null;
  if (value.protocol !== DECK_PROTOCOL_VERSION) return null;
  if (typeof value.surfaceId !== "string" || value.surfaceId !== context.surfaceId) return null;

  const type = value.type;
  if (typeof type !== "string" || !INTENT_TYPES.has(type)) return null;

  if (type === "ready") {
    return { type: "ready", surfaceId: context.surfaceId };
  }

  // Everything past the handshake must carry the channel this parent issued.
  if (context.channel === null || value.channel !== context.channel) return null;

  if (type === "submit_intent") {
    const action = parseAction(value.action);
    if (action === null) return null;
    const comment = typeof value.comment === "string" ? value.comment.slice(0, MAX_COMMENT_CHARS) : "";
    if (
      action.kind === "request_changes" &&
      action.optionIds.length === 0 &&
      comment.trim().length === 0
    ) {
      return null;
    }
    return { type: "submit_intent", surfaceId: context.surfaceId, action, comment };
  }

  return { type, surfaceId: context.surfaceId } as DeckIntent;
}

export type DeckStatus = "idle" | "ready" | "submitting" | "accepted" | "stale" | "failed";

export interface DeckSurfaceState {
  surfaceId: string;
  status: DeckStatus;
  channel: string | null;
  allowedActions: DeckActionKind[];
  submissionId: string | null;
  note: string;
  newestSurfaceId: string | null;
}

export type DeckStateEvent =
  | { kind: "server_state"; channel: string; allowedActions: DeckActionKind[]; note?: string }
  | { kind: "submitting"; submissionId: string }
  | { kind: "accepted"; note?: string }
  | { kind: "stale"; newestSurfaceId?: string | null; note?: string }
  | { kind: "failed"; note?: string };

export function initialDeckState(surfaceId: string): DeckSurfaceState {
  return {
    surfaceId,
    status: "idle",
    channel: null,
    allowedActions: [],
    submissionId: null,
    note: "",
    newestSurfaceId: null,
  };
}

/** Terminal states. Nothing re-opens them; a newer surface is a different one. */
const SETTLED: ReadonlySet<DeckStatus> = new Set<DeckStatus>(["accepted", "stale"]);

export function reduceDeckState(state: DeckSurfaceState, event: DeckStateEvent): DeckSurfaceState {
  if (SETTLED.has(state.status) && event.kind === "server_state") {
    // Refusing to rebase is the point: a settled surface describes a document
    // the person already decided about, or one that has been replaced.
    return state;
  }

  switch (event.kind) {
    case "server_state": {
      const allowedActions = event.allowedActions.filter((action) =>
        DECK_ACTION_KINDS.includes(action),
      );
      return {
        ...state,
        status: "ready",
        channel: event.channel,
        allowedActions,
        note: event.note ?? state.note,
      };
    }
    case "submitting":
      return { ...state, status: "submitting", submissionId: event.submissionId };
    case "accepted":
      return { ...state, status: "accepted", allowedActions: [], note: event.note ?? "" };
    case "stale":
      return {
        ...state,
        status: "stale",
        allowedActions: [],
        newestSurfaceId: event.newestSurfaceId ?? null,
        note: event.note ?? "",
      };
    case "failed":
      // The submission id survives so a retry replays rather than becoming a
      // second answer to the same question.
      return { ...state, status: "failed", note: event.note ?? "" };
    default:
      return state;
  }
}

export function toDeckMessage(
  surfaceId: string,
  channel: string,
  body: { type: DeckStateMessageType; allowedActions?: DeckActionKind[]; note?: string },
): DeckStateMessage {
  return {
    source: DECK_MESSAGE_SOURCE,
    protocol: DECK_PROTOCOL_VERSION,
    surfaceId,
    channel,
    ...body,
  };
}
