import { beforeEach, describe, expect, it, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

import { fetch } from "@/core/api/fetcher";
import {
  createProject,
  createWorkspace,
  fetchProjectFolders,
  fetchProjects,
  fetchWorkspaces,
} from "@/core/workspaces/api";

const mockedFetch = rs.mocked(fetch);

function jsonResponse(body: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 409,
    statusText: ok ? "OK" : "Conflict",
    json: async () => body,
  } as Response;
}

describe("workspace api", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  it("loads the current user's workspace portfolio", async () => {
    const payload = [{ id: "ws-1", name: "Maize Breeding" }];
    mockedFetch.mockResolvedValue(jsonResponse(payload));

    await expect(fetchWorkspaces()).resolves.toEqual(payload);
    expect(mockedFetch.mock.calls[0]?.[0] as string).toContain(
      "/api/workspaces",
    );
  });

  it("creates a workspace with a JSON body", async () => {
    mockedFetch.mockResolvedValue(
      jsonResponse({ id: "ws-1", name: "Maize Breeding" }),
    );

    await createWorkspace({
      name: "Maize Breeding",
      slug: "maize-breeding",
      description: "Long-term breeding program",
    });

    expect(mockedFetch.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(JSON.parse(mockedFetch.mock.calls[0]?.[1]?.body as string)).toEqual({
      name: "Maize Breeding",
      slug: "maize-breeding",
      description: "Long-term breeding program",
    });
  });

  it("loads and creates projects within the encoded workspace id", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse([]));
    await fetchProjects("ws/maize");
    expect(mockedFetch.mock.calls[0]?.[0] as string).toContain(
      "/api/workspaces/ws%2Fmaize/projects",
    );

    mockedFetch.mockResolvedValueOnce(
      jsonResponse({ id: "project-1", name: "Drought Resilience" }),
    );
    await createProject("ws/maize", {
      name: "Drought Resilience",
      crop_profile: "maize-v1",
    });
    expect(mockedFetch.mock.calls[1]?.[1]?.method).toBe("POST");
  });

  it("browses an encoded human and AI accessible local folder", async () => {
    mockedFetch.mockResolvedValue(
      jsonResponse({
        current_path: "/Users/me/My Projects",
        directories: [],
      }),
    );

    await fetchProjectFolders("/Users/me/My Projects");

    expect(mockedFetch.mock.calls[0]?.[0] as string).toContain(
      "/api/project-folders?path=%2FUsers%2Fme%2FMy+Projects",
    );
  });

  it("surfaces the gateway conflict detail", async () => {
    mockedFetch.mockResolvedValue(
      jsonResponse({ detail: "Workspace slug already exists" }, false),
    );

    await expect(fetchWorkspaces()).rejects.toThrow(
      "Workspace slug already exists",
    );
  });
});
