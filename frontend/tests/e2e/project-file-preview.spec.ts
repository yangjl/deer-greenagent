import { expect, test } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

test("opens a project file in an inspector and minimizes the first rail", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Project conversation",
        updated_at: "2026-07-25T10:00:00Z",
      },
    ],
  });

  await page.route("**/api/workspaces**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ id: "workspace-1", name: "Program" }]),
    }),
  );
  await page.route("**/api/workspaces/workspace-1/projects**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "project-1",
          workspace_id: "workspace-1",
          name: "test2",
          slug: "test2",
          root_path: "/Users/test/Documents/projects/test2",
          crop_profile: "maize-v1",
          dbtl_phase: "design",
          reconciliation_status: "ready",
        },
      ]),
    }),
  );
  await page.route("**/api/projects/project-1/threads", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          thread_id: MOCK_THREAD_ID,
          display_name: "Project conversation",
          updated_at: "2026-07-25T10:00:00Z",
        },
      ]),
    }),
  );
  await page.route("**/api/projects/project-1/files?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        path: "/mnt/user-data",
        entries: [
          {
            name: "README.md",
            path: "/mnt/user-data/README.md",
            type: "file",
            size: 28,
            modified_at: "2026-07-25T10:00:00Z",
            is_symlink: false,
          },
        ],
        truncated: false,
      }),
    }),
  );
  await page.route("**/api/projects/project-1/file?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/markdown",
      body: "# test2\n\nProject file preview.",
    }),
  );

  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  await expect(
    page.getByRole("button", { name: "Show test2 files" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Project actions for test2" }),
  ).toBeVisible();
  await expect(
    page.getByText("/Users/test/Documents/projects/test2"),
  ).toHaveCount(0);

  await page.getByRole("button", { name: "Show test2 files" }).click();
  await page.getByRole("button", { name: "README.md" }).click();

  await expect(page.getByRole("heading", { name: "README.md" })).toBeVisible();
  await expect(page.getByText("Project file preview.")).toBeVisible();
  await expect(
    page.locator('[data-slot="sidebar"][data-state="collapsed"]'),
  ).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Collapse test2 files" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Project actions for test2" }),
  ).toHaveCount(0);

  await page.getByRole("button", { name: "Close" }).click();
  await expect(
    page.locator('[data-slot="sidebar"][data-state="expanded"]'),
  ).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Collapse test2 files" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Project actions for test2" }),
  ).toBeVisible();
});
