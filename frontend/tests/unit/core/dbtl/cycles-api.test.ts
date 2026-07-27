import { beforeEach, describe, expect, it, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

import { fetch } from "@/core/api/fetcher";
import { abandonCycle } from "@/core/dbtl/cycles-api";

const mockedFetch = rs.mocked(fetch);

describe("DBTL cycle API", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  it("retires a cycle with its revision, rationale, and idempotency key", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ id: "cycle-1", state: "abandoned" }),
    } as Response);

    await abandonCycle({
      projectId: "project/1",
      cycleId: "cycle/1",
      rationale: "Duplicate setup test.",
      expectedDbRevision: 3,
      idempotencyKey: "abandon-1",
    });

    expect(mockedFetch.mock.calls[0]?.[0]).toContain(
      "/projects/project%2F1/dbtl/cycles/cycle%2F1/abandon",
    );
    expect(mockedFetch.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(
      JSON.parse(mockedFetch.mock.calls[0]?.[1]?.body as string),
    ).toEqual({
      rationale: "Duplicate setup test.",
      expected_db_revision: 3,
      idempotency_key: "abandon-1",
    });
  });
});
