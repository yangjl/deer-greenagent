import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { PersistedActivityEvent } from "./types";

export interface ActivityPage {
  events: PersistedActivityEvent[];
  nextBeforeSeq: number | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export async function fetchActivityPage(
  threadId: string,
  beforeSeq?: number | null,
): Promise<ActivityPage> {
  const query = new URLSearchParams({ limit: "200" });
  if (beforeSeq !== null && beforeSeq !== undefined) {
    query.set("before_seq", String(beforeSeq));
  }
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/activity?${query}`,
  );
  if (!response.ok) {
    throw new Error(`Failed to load agent activity (${response.status}).`);
  }
  const body: unknown = await response.json();
  if (!isRecord(body) || !Array.isArray(body.events)) {
    throw new Error("Agent activity response is malformed.");
  }
  const events = body.events.filter(
    (event): event is PersistedActivityEvent =>
      isRecord(event) &&
      typeof event.seq === "number" &&
      Number.isInteger(event.seq) &&
      event.seq > 0 &&
      isRecord(event.content),
  );
  return {
    events,
    nextBeforeSeq:
      typeof body.next_before_seq === "number" ? body.next_before_seq : null,
  };
}
