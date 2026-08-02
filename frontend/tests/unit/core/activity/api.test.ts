import { beforeEach, describe, expect, rs, test } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "/backend",
}));

import { fetchActivityPage } from "@/core/activity/api";
import { fetch as fetcher } from "@/core/api/fetcher";

const mockedFetch = rs.mocked(fetcher);

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("fetchActivityPage", () => {
  test("reads conversation activity and pages backward by server sequence", async () => {
    mockedFetch.mockResolvedValueOnce(
      response(200, { events: [], next_before_seq: 41 }),
    );

    const page = await fetchActivityPage("thread/1", 90);

    expect(mockedFetch.mock.calls[0]?.[0]).toBe(
      "/backend/api/threads/thread%2F1/activity?limit=200&before_seq=90",
    );
    expect(page.nextBeforeSeq).toBe(41);
  });

  test("fails visibly when the authoritative history cannot be read", async () => {
    mockedFetch.mockResolvedValueOnce(response(503, {}));

    await expect(fetchActivityPage("thread-1")).rejects.toThrow(
      "Failed to load agent activity (503).",
    );
  });
});
