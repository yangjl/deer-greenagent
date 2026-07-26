import { expect, test, type Page } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

/**
 * Phase 4: the inline DBTL Upgrade Proposal.
 *
 * The assertions that matter are negative ones. Every path a user can take
 * away from the card — dismiss, "keep as ordinary chat", or an ordinary
 * request that never produces a card — must leave zero DBTL domain writes.
 * The spec therefore records every cycle-creating request the page makes and
 * asserts the list is empty, rather than inspecting the rendered result.
 */

const PROPOSAL_RESPONSE = {
  evaluation_id: "eval-1",
  route_kind: "proposal",
  route_source: "classifier",
  proposals_visible: true,
  proposal: {
    kind: "proposal",
    proposed_objective:
      "Compare drought-response models across G2F environments",
    missing_fields: ["target trait", "season range"],
    band: "high",
    confidence: 0.84,
    project_name: "test2",
    cycle_id: null,
    creates_record: false,
    requires_confirmation: true,
    notice: "No cycle has been created yet.",
    confirmation: {
      project_name: "test2",
      required_gates: ["Design", "Data reconciliation"],
      record_effect: "Creates a durable research record in this project.",
      notice:
        "Starting this cycle creates a durable research record. Nothing is written until you confirm.",
    },
  },
};

const ORDINARY_RESPONSE = {
  evaluation_id: "eval-2",
  route_kind: "ordinary",
  route_source: "classifier",
  proposals_visible: true,
  proposal: null,
};

const EXISTING_CYCLE = {
  id: "cycle-existing",
  project_id: "project-1",
  parent_cycle_id: null,
  title: "Existing genomic-selection cycle",
  cycle_class: "computational",
  cycle_weight: "full",
  state: "design",
  db_revision: 1,
  research_question: "Compare genomic-selection models",
  objective: "Compare genomic-selection models",
  success_criteria: "Held-out environment accuracy",
  created_by: "default",
  created_at: "2026-07-25T10:00:00Z",
  updated_at: "2026-07-25T10:00:00Z",
  stages: [],
};

const CONTINUATION_RESPONSE = {
  evaluation_id: "eval-continuation",
  route_kind: "cycle_continuation",
  route_source: "selected_cycle",
  proposals_visible: true,
  proposal: {
    ...PROPOSAL_RESPONSE.proposal,
    kind: "cycle_continuation",
    cycle_id: EXISTING_CYCLE.id,
    proposed_objective: "",
    missing_fields: [],
  },
};

/** Every request that would create a durable DBTL record. Must stay empty. */
async function setupProject(
  page: Page,
  evaluation: unknown,
  cycles: unknown[] = [],
): Promise<{
  cycleWrites: string[];
  outcomes: string[];
  evaluateCalls: string[];
}> {
  const cycleWrites: string[] = [];
  const outcomes: string[] = [];
  const evaluateCalls: string[] = [];

  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Project conversation",
        updated_at: "2026-07-25T10:00:00Z",
      },
    ],
  });

  // `mockLangGraphAPI` registers routes without returning their promises.
  // Install these explicitly and await them so navigation cannot race auth
  // registration and silently turn this into a login-page test.
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "default",
        email: "default@test.local",
        system_role: "admin",
        needs_setup: false,
      }),
    }),
  );
  await page.route("**/api/v1/auth/setup-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ needs_setup: false }),
    }),
  );
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
      body: JSON.stringify([]),
    }),
  );
  await page.route("**/api/projects/project-1/files?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ path: "/mnt/user-data", entries: [], truncated: false }),
    }),
  );

  // Any cycle write is a failure for these paths — record, do not create.
  await page.route("**/api/projects/project-1/dbtl/cycles", async (route) => {
    if (route.request().method() === "POST") {
      cycleWrites.push(route.request().url());
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        route.request().method() === "POST"
          ? {
              ...EXISTING_CYCLE,
              id: "cycle-created",
              title: "Created from proposal",
            }
          : { project_id: "project-1", stages: [], cycles },
      ),
    });
  });
  await page.route(
    "**/api/projects/project-1/dbtl/cycles/cycle-existing",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(EXISTING_CYCLE),
      }),
  );

  await page.route(
    "**/api/projects/project-1/dbtl/proposals/evaluate",
    (route) => {
      evaluateCalls.push(route.request().postData() ?? "");
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(evaluation),
      });
    },
  );

  await page.route(
    "**/api/projects/project-1/dbtl/proposals/*/outcome",
    async (route) => {
      outcomes.push(route.request().postData() ?? "");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ evaluation_id: "eval-1" }),
      });
    },
  );

  return { cycleWrites, outcomes, evaluateCalls };
}

async function send(page: Page, text: string) {
  // The project rail can also contain a blocker textbox. Target the composer
  // textarea itself so adding a cycle to the fixture cannot redirect typing
  // into a disabled rail control.
  const textarea = page.locator("textarea").filter({ visible: true }).last();
  await expect(textarea).toBeVisible({ timeout: 15_000 });
  await textarea.fill(text);
  await textarea.press("Enter");
}

test("a proposal states that no cycle exists and creates none when dismissed", async ({
  page,
}) => {
  const { cycleWrites, outcomes, evaluateCalls } = await setupProject(
    page,
    PROPOSAL_RESPONSE,
  );
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  await send(page, "Compare drought-response models across G2F environments");

  // Prove the shadow evaluation actually ran before asserting on the card, so
  // a wiring break reads differently from a rendering break.
  await expect
    .poll(() => evaluateCalls.length, { timeout: 10_000 })
    .toBeGreaterThan(0);

  const card = page.getByRole("region", { name: "DBTL upgrade proposal" });
  await expect(card).toBeVisible();
  await expect(card.getByText("No cycle has been created yet.")).toBeVisible();

  await card.getByRole("button", { name: "Dismiss suggestion" }).click();
  await expect(card).toHaveCount(0);

  expect(cycleWrites).toEqual([]);
  expect(outcomes.join()).toContain("dismissed");
});

test("keeping it as ordinary chat creates no record", async ({ page }) => {
  const { cycleWrites, outcomes, evaluateCalls } = await setupProject(
    page,
    PROPOSAL_RESPONSE,
  );
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  await send(page, "Compare drought-response models across G2F environments");
  await expect.poll(() => evaluateCalls.length).toBe(1);

  const card = page.getByRole("region", { name: "DBTL upgrade proposal" });
  await card.getByRole("button", { name: "Keep as ordinary chat" }).click();

  await expect(card).toHaveCount(0);
  expect(cycleWrites).toEqual([]);
  expect(outcomes.join()).toContain("keep_ordinary");
});

test("an ordinary request never shows a card", async ({ page }) => {
  const { cycleWrites, evaluateCalls } = await setupProject(
    page,
    ORDINARY_RESPONSE,
  );
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  await send(page, "Explain this README");
  await expect.poll(() => evaluateCalls.length).toBe(1);

  await expect(
    page.getByRole("region", { name: "DBTL upgrade proposal" }),
  ).toHaveCount(0);
  expect(cycleWrites).toEqual([]);
});

test("starting setup still creates nothing until the final confirmation", async ({
  page,
}) => {
  const { cycleWrites, evaluateCalls } = await setupProject(
    page,
    PROPOSAL_RESPONSE,
  );
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  await send(page, "Compare drought-response models across G2F environments");
  await expect.poll(() => evaluateCalls.length).toBe(1);

  const card = page.getByRole("region", { name: "DBTL upgrade proposal" });
  await card.getByRole("button", { name: "Start DBTL setup" }).click();

  // Clarification is open; nothing has been written.
  await expect(card.getByText("No cycle has been created yet.")).toBeVisible();
  await expect(card.getByRole("button", { name: "Review and confirm" })).toBeDisabled();
  expect(cycleWrites).toEqual([]);

  await card.getByPlaceholder("grain yield").fill("grain yield");
  await card.getByPlaceholder("2023–2024").fill("2023-2024");
  await card.getByRole("button", { name: "Review and confirm" }).click();

  // The confirmation names every consequence — and has still written nothing.
  await expect(card.getByText("Required human gates")).toBeVisible();
  await expect(card.getByText("Design · Data reconciliation")).toBeVisible();
  await expect(
    card.getByText("Creates a durable research record in this project."),
  ).toBeVisible();
  expect(cycleWrites).toEqual([]);
});

test("card actions are reachable and operable by keyboard", async ({ page }) => {
  const { evaluateCalls } = await setupProject(page, PROPOSAL_RESPONSE);
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  await send(page, "Compare drought-response models across G2F environments");
  await expect.poll(() => evaluateCalls.length).toBe(1);

  const card = page.getByRole("region", { name: "DBTL upgrade proposal" });
  const start = card.getByRole("button", { name: "Start DBTL setup" });
  await expect(start).toBeVisible();

  // Focusable and activatable without a pointer. The disabled "Review and
  // confirm" uses native `disabled`, so keyboard activation cannot bypass the
  // same guard a click hits.
  await start.focus();
  await expect(start).toBeFocused();
  await page.keyboard.press("Enter");

  const review = card.getByRole("button", { name: "Review and confirm" });
  await expect(review).toBeDisabled();
  await review.focus();
  await page.keyboard.press("Enter");
  await expect(card.getByText("Required human gates")).toHaveCount(0);
});

test("final confirmation creates exactly one cycle and records the accepted outcome", async ({
  page,
}) => {
  const { cycleWrites, outcomes, evaluateCalls } = await setupProject(
    page,
    PROPOSAL_RESPONSE,
  );
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);
  await send(page, "Compare drought-response models across G2F environments");
  await expect.poll(() => evaluateCalls.length).toBe(1);

  const card = page.getByRole("region", { name: "DBTL upgrade proposal" });
  await card.getByRole("button", { name: "Start DBTL setup" }).click();
  await card.getByPlaceholder("grain yield").fill("grain yield");
  await card.getByPlaceholder("2023–2024").fill("2023-2024");
  await card.getByRole("button", { name: "Review and confirm" }).click();
  await card.getByRole("button", { name: "Create this cycle" }).click();

  await expect(card).toHaveCount(0);
  expect(cycleWrites).toHaveLength(1);
  expect(outcomes.join()).toContain("start_setup");
});

test("an explicitly selected cycle continues without creating another cycle", async ({
  page,
}) => {
  const { cycleWrites, outcomes, evaluateCalls } = await setupProject(
    page,
    CONTINUATION_RESPONSE,
    [EXISTING_CYCLE],
  );
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  await page
    .getByRole("button", { name: EXISTING_CYCLE.title })
    .click();
  await send(page, "Run the next validation analysis");
  await expect.poll(() => evaluateCalls.length).toBe(1);
  expect(JSON.parse(evaluateCalls[0]!)).toMatchObject({
    selected_cycle_id: EXISTING_CYCLE.id,
  });

  const card = page.getByRole("region", { name: "DBTL upgrade proposal" });
  await card
    .getByRole("button", { name: "Continue selected cycle" })
    .click();
  await expect(card).toHaveCount(0);
  expect(cycleWrites).toEqual([]);
  expect(outcomes.join()).toContain("continue_cycle");
});
