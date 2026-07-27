import { afterEach, expect, test, rs } from "@rstest/core";

afterEach(() => {
  rs.unstubAllGlobals();
});

function stubFetch(response: Response) {
  let requestedUrl = "";
  const fetchMock = rs.fn(async (input: RequestInfo | URL) => {
    if (typeof input === "string") {
      requestedUrl = input;
    } else if (input instanceof URL) {
      requestedUrl = input.toString();
    } else {
      requestedUrl = input.url;
    }
    return response;
  });
  rs.stubGlobal("fetch", fetchMock);
  return () => requestedUrl;
}

test("fetchWorkspaceFiles requests the thread files endpoint with the path", async () => {
  const getUrl = stubFetch(
    new Response(
      JSON.stringify({
        path: "/mnt/user-data/workspace",
        entries: [],
        truncated: false,
      }),
      { status: 200 },
    ),
  );

  const { fetchWorkspaceFiles } = await import("@/core/files/api");
  const result = await fetchWorkspaceFiles({
    threadId: "thread/1",
    path: "/mnt/user-data/workspace",
  });

  const url = new URL(getUrl(), "http://localhost");
  expect(url.pathname).toBe("/api/threads/thread%2F1/files");
  expect(url.searchParams.get("path")).toBe("/mnt/user-data/workspace");
  expect(result.path).toBe("/mnt/user-data/workspace");
  expect(result.entries).toEqual([]);
  expect(result.truncated).toBe(false);
});

test("fetchWorkspaceFiles surfaces the backend detail message on error", async () => {
  stubFetch(
    new Response(JSON.stringify({ detail: "Directory not found: /mnt/x" }), {
      status: 404,
    }),
  );

  const { fetchWorkspaceFiles } = await import("@/core/files/api");

  await expect(
    fetchWorkspaceFiles({ threadId: "t1", path: "/mnt/x" }),
  ).rejects.toThrow("Directory not found: /mnt/x");
});

test("fetchWorkspaceFiles falls back to a generic error for empty bodies", async () => {
  stubFetch(new Response("", { status: 500 }));

  const { fetchWorkspaceFiles } = await import("@/core/files/api");

  await expect(
    fetchWorkspaceFiles({ threadId: "t1", path: "/mnt/user-data" }),
  ).rejects.toThrow("Failed to load files.");
});

test("fetchThreadProject requests the project endpoint", async () => {
  const getUrl = stubFetch(
    new Response(JSON.stringify({ project: null }), { status: 200 }),
  );

  const { fetchThreadProject } = await import("@/core/files/api");
  const result = await fetchThreadProject("t1");

  const url = new URL(getUrl(), "http://localhost");
  expect(url.pathname).toBe("/api/threads/t1/project");
  expect(result.project).toBeNull();
});

test("updateThreadProject PUTs the container path", async () => {
  let method = "";
  let body = "";
  const fetchMock = rs.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      method = init?.method ?? "GET";
      body = typeof init?.body === "string" ? init.body : "";
      void input;
      return new Response(
        JSON.stringify({
          project: { container_path: "/mnt/projects/uav", name: "uav" },
        }),
        { status: 200 },
      );
    },
  );
  rs.stubGlobal("fetch", fetchMock);

  const { updateThreadProject } = await import("@/core/files/api");
  const result = await updateThreadProject("t1", "/mnt/projects/uav");

  expect(method).toBe("PUT");
  expect(JSON.parse(body)).toEqual({ container_path: "/mnt/projects/uav" });
  expect(result.project?.name).toBe("uav");
});

test("clearThreadProject issues a DELETE", async () => {
  let method = "";
  const fetchMock = rs.fn(
    async (_input: RequestInfo | URL, init?: RequestInit) => {
      method = init?.method ?? "GET";
      return new Response(JSON.stringify({ project: null }), { status: 200 });
    },
  );
  rs.stubGlobal("fetch", fetchMock);

  const { clearThreadProject } = await import("@/core/files/api");
  await clearThreadProject("t1");

  expect(method).toBe("DELETE");
});

test("fetchProjectCandidates requests the candidates endpoint", async () => {
  const getUrl = stubFetch(
    new Response(
      JSON.stringify({
        candidates: [{ container_path: "/mnt/projects", name: "projects" }],
      }),
      { status: 200 },
    ),
  );

  const { fetchProjectCandidates } = await import("@/core/files/api");
  const result = await fetchProjectCandidates("t1");

  const url = new URL(getUrl(), "http://localhost");
  expect(url.pathname).toBe("/api/threads/t1/project/candidates");
  expect(result.candidates).toHaveLength(1);
});
