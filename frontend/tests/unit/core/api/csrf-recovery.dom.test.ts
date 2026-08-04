import { afterEach, expect, rs, test } from "@rstest/core";

import { getAPIClient } from "@/core/api/api-client";
import { fetch as fetchWithAuth } from "@/core/api/fetcher";

function clearCsrfCookie() {
  document.cookie = "csrf_token=; Max-Age=0; Path=/";
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

afterEach(() => {
  clearCsrfCookie();
  rs.unstubAllGlobals();
});

test("restores a missing CSRF cookie before a direct mutation", async () => {
  clearCsrfCookie();
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const fetchMock = rs.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      requests.push({ url, init });
      if (url === "/api/v1/auth/me") {
        document.cookie = "csrf_token=recovered-direct; Path=/";
        return new Response(JSON.stringify({ id: "user-1" }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    },
  );
  rs.stubGlobal("fetch", fetchMock);

  const response = await fetchWithAuth("/api/threads/search", {
    method: "POST",
  });

  expect(response.ok).toBe(true);
  expect(requests.map(({ url }) => url)).toEqual([
    "/api/v1/auth/me",
    "/api/threads/search",
  ]);
  expect(new Headers(requests[1]?.init?.headers).get("X-CSRF-Token")).toBe(
    "recovered-direct",
  );
});

test("shares one recovery request across concurrent mutations", async () => {
  clearCsrfCookie();
  let recoveryCalls = 0;
  const mutationHeaders: Array<string | null> = [];
  const fetchMock = rs.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === "/api/v1/auth/me") {
        recoveryCalls += 1;
        await Promise.resolve();
        document.cookie = "csrf_token=shared-token; Path=/";
        return new Response(JSON.stringify({ id: "user-1" }), { status: 200 });
      }
      mutationHeaders.push(new Headers(init?.headers).get("X-CSRF-Token"));
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    },
  );
  rs.stubGlobal("fetch", fetchMock);

  await Promise.all([
    fetchWithAuth("/api/threads/search", { method: "POST" }),
    fetchWithAuth("/api/projects/project-1/dbtl/proposals/evaluate", {
      method: "POST",
    }),
  ]);

  expect(recoveryCalls).toBe(1);
  expect(mutationHeaders).toEqual(["shared-token", "shared-token"]);
});

test("the LangGraph SDK also recovers before creating a thread", async () => {
  clearCsrfCookie();
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const fetchMock = rs.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      requests.push({ url, init });
      if (url === "/api/v1/auth/me") {
        document.cookie = "csrf_token=recovered-sdk; Path=/";
        return new Response(JSON.stringify({ id: "user-1" }), { status: 200 });
      }
      return new Response(
        JSON.stringify({
          thread_id: "thread-1",
          created_at: "2026-08-03T00:00:00Z",
          updated_at: "2026-08-03T00:00:00Z",
          metadata: {},
          status: "idle",
          values: {},
          interrupts: {},
        }),
        { status: 200 },
      );
    },
  );
  rs.stubGlobal("fetch", fetchMock);

  await getAPIClient(true).threads.create();

  expect(requests[0]?.url).toBe("/api/v1/auth/me");
  expect(new Headers(requests[1]?.init?.headers).get("X-CSRF-Token")).toBe(
    "recovered-sdk",
  );
});
