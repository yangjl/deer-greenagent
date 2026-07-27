import { describe, expect, it } from "@rstest/core";

import {
  isReservedWorkspaceSegment,
  pathOfNewProjectConversation,
  pathOfProject,
  pathOfProjectThread,
  projectConversationsQueryKey,
  projectSlugOfName,
} from "@/core/workspaces/project-threads";

describe("project routes", () => {
  it("places the project slug directly under /workspace", () => {
    expect(pathOfProject("drought-resistent")).toBe(
      "/workspace/drought-resistent",
    );
    expect(pathOfNewProjectConversation("gwas")).toBe("/workspace/gwas/new");
  });

  it("percent-encodes both segments of a thread path", () => {
    expect(pathOfProjectThread("p/1", "t 2")).toBe("/workspace/p%2F1/t%202");
  });
});

describe("projectSlugOfName", () => {
  it("slugifies a project name", () => {
    expect(projectSlugOfName("Drought Resistent 2032")).toBe(
      "drought-resistent-2032",
    );
    expect(projectSlugOfName("  Maize —— Yield  ")).toBe("maize-yield");
  });

  it("never produces a slug that shadows a workspace route", () => {
    for (const reserved of [
      "agents",
      "chats",
      "inbox",
      "scheduled-tasks",
      "skills",
    ]) {
      const slug = projectSlugOfName(reserved);
      expect(isReservedWorkspaceSegment(slug)).toBe(false);
      expect(slug).toBe(`${reserved}-project`);
    }
  });

  it("falls back to a usable slug for symbol-only names", () => {
    expect(projectSlugOfName("!!!")).toBe("project");
  });
});

describe("projectConversationsQueryKey", () => {
  it("is scoped by project id", () => {
    expect(projectConversationsQueryKey("p1")).toEqual([
      "projects",
      "p1",
      "conversations",
    ]);
  });
});
