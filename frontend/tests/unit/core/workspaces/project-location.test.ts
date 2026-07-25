import { describe, expect, it } from "@rstest/core";

import {
  buildProjectLocationFields,
  joinLocalPath,
  projectFolderName,
} from "@/core/workspaces/project-location";

describe("project location helpers", () => {
  it("mirrors the backend's human-readable folder sanitization", () => {
    expect(projectFolderName("  G2F / Drought:2035  ")).toBe(
      "G2F - Drought-2035",
    );
    expect(projectFolderName("...")).toBe("project");
  });

  it("joins local paths without duplicate separators", () => {
    expect(joinLocalPath("/Users/me/projects/", "G2F")).toBe(
      "/Users/me/projects/G2F",
    );
  });

  it("builds the selected location contract", () => {
    expect(
      buildProjectLocationFields("existing", {
        existingPath: "/Users/me/projects/G2F",
        fullPath: "",
        parentPath: "",
        folderName: "",
      }),
    ).toEqual({
      location_mode: "existing",
      root_path: "/Users/me/projects/G2F",
    });

    expect(
      buildProjectLocationFields("new_under_parent", {
        existingPath: "",
        fullPath: "",
        parentPath: "/Users/me/projects",
        folderName: "G2F",
      }),
    ).toEqual({
      location_mode: "new_under_parent",
      parent_path: "/Users/me/projects",
      folder_name: "G2F",
    });
  });
});
