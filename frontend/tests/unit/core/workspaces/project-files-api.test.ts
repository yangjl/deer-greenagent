import { beforeEach, describe, expect, it, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

import { fetch } from "@/core/api/fetcher";
import {
  loadProjectFileContent,
  urlOfProjectFile,
} from "@/core/workspaces/project-files-api";

const mockedFetch = rs.mocked(fetch);

describe("project file content API", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  it("builds an encoded project-scoped URL", () => {
    expect(
      urlOfProjectFile({
        projectId: "project/1",
        path: "/mnt/user-data/workspace/My README.md",
      }),
    ).toContain(
      "/api/projects/project%2F1/file?path=%2Fmnt%2Fuser-data%2Fworkspace%2FMy+README.md",
    );
  });

  it("loads text content without a conversation id", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      text: async () => "# G2F",
    } as Response);

    await expect(
      loadProjectFileContent({
        projectId: "project-1",
        path: "/mnt/user-data/workspace/README.md",
      }),
    ).resolves.toMatchObject({ content: "# G2F" });

    expect(mockedFetch).toHaveBeenCalledOnce();
  });
});
