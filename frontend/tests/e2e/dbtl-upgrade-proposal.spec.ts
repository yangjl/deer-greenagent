import { expect, test, type Page } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

/** DBTL setup reuses DeerFlow's transcript-native Human Input Card. */

const PROPOSAL_RESPONSE = {
  evaluation_id: "eval-1",
  route_kind: "proposal",
  route_source: "classifier",
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
};

const SETUP_REQUEST_ID = "dbtl-setup-confirm:e2e";
const SETUP_MESSAGES = [
  {
    type: "human",
    id: "msg-human-proposal",
    content: "Design a genomic selection experiment",
  },
  {
    type: "ai",
    id: `${SETUP_REQUEST_ID}:call`,
    content: "",
    tool_calls: [
      {
        id: SETUP_REQUEST_ID,
        name: "ask_clarification",
        type: "tool_call",
        args: {
          question: "How should this request proceed?",
          clarification_type: "cycle_setup_confirmation",
        },
      },
    ],
  },
  {
    type: "tool",
    id: SETUP_REQUEST_ID,
    name: "ask_clarification",
    tool_call_id: SETUP_REQUEST_ID,
    content: "Review the proposed DBTL cycle.",
    artifact: {
      human_input: {
        version: 1,
        kind: "human_input_request",
        source: "ask_clarification",
        request_id: SETUP_REQUEST_ID,
        clarification_type: "cycle_setup_confirmation",
        title: "Review DBTL cycle setup",
        question: "How should this request proceed?",
        context:
          "Proposed objective: compare genomic-selection models.\n\nNo cycle has been created yet.",
        input_mode: "single_choice",
        options: [
          {
            id: "create_cycle",
            label: "Create this DBTL cycle",
            value: "create_cycle",
          },
          {
            id: "keep_ordinary",
            label: "Keep as ordinary chat",
            value: "keep_ordinary",
          },
          { id: "not_sure", label: "Not sure", value: "not_sure" },
        ],
        dbtl_cycle_setup: {
          title: "Genomic selection experiment",
          objective: "Compare genomic-selection models",
          success_criteria: "Validate on held-out sites.",
        },
      },
    },
  },
];

const COUNCIL_REQUEST_ID = "dbtl-council__cycle-existing__e2e";
const COUNCIL_PREFLIGHT_MESSAGES = [
  {
    type: "human",
    id: "msg-human-council",
    content: "Start the Design council",
  },
  {
    type: "ai",
    id: `${COUNCIL_REQUEST_ID}:call`,
    content: "",
    tool_calls: [
      {
        id: COUNCIL_REQUEST_ID,
        name: "ask_clarification",
        type: "tool_call",
        args: {
          question: "How much debate should this design get?",
          clarification_type: "council_preflight",
        },
      },
    ],
  },
  {
    type: "tool",
    id: COUNCIL_REQUEST_ID,
    name: "ask_clarification",
    tool_call_id: COUNCIL_REQUEST_ID,
    content: "Choose the Design council depth.",
    artifact: {
      human_input: {
        version: 1,
        kind: "human_input_request",
        source: "ask_clarification",
        request_id: COUNCIL_REQUEST_ID,
        clarification_type: "council_preflight",
        title: "Before the Design council convenes",
        question: "How much debate should this design get?",
        context:
          "**claude-opus-5** · 3 workers\n\n- Independent position\n- Red team\n- Chair",
        input_mode: "single_choice",
        options: [
          {
            id: "light",
            label: "Light",
            value: "light",
            description: "One position, one challenge, one synthesis.",
          },
          {
            id: "medium",
            label: "Medium",
            value: "medium",
            description: "Two positions, one challenge, one synthesis.",
          },
        ],
        recommended_option_id: "medium",
      },
    },
  },
];

/** Every request that would create a durable DBTL record. Must stay empty. */
async function setupProject(
  page: Page,
  evaluation: unknown,
  cycles: unknown[] = [],
  messages?: unknown[],
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
        messages,
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
  await page.route("**/api/features", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        agents_api: { enabled: true },
        browser_control: { enabled: true },
        dbtl: {
          mode: "graph_enabled",
          mutations_enabled: true,
          graph_execution_enabled: true,
          reason: "DBTL browser fixture",
        },
      }),
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
      body: JSON.stringify({
        path: "/mnt/user-data",
        entries: [],
        truncated: false,
      }),
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

test("the supervisor setup artifact renders as DeerFlow's native inline card", async ({
  page,
}) => {
  const { cycleWrites } = await setupProject(
    page,
    PROPOSAL_RESPONSE,
    [],
    SETUP_MESSAGES,
  );
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  const card = page.getByTestId("human-input-card");
  await expect(card).toBeVisible();
  await expect(card.getByText("Review DBTL cycle setup")).toBeVisible();
  await expect(card.getByText("No cycle has been created yet.")).toBeVisible();
  await expect(
    page.getByRole("region", { name: "DBTL upgrade proposal" }),
  ).toHaveCount(0);
  expect(cycleWrites).toEqual([]);
});

test("the Design council preflight is an actionable native card", async ({
  page,
}) => {
  await setupProject(
    page,
    CONTINUATION_RESPONSE,
    [EXISTING_CYCLE],
    COUNCIL_PREFLIGHT_MESSAGES,
  );
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  const card = page.getByTestId("human-input-card");
  await expect(card).toBeVisible();
  await expect(
    card.getByRole("heading", {
      name: "Before the Design council convenes",
    }),
  ).toBeVisible();
  await expect(card.getByText("Red team")).toBeVisible();
  await expect(
    card.getByRole("button", { name: /Medium.*Recommended/s }),
  ).toBeEnabled();
  await expect(
    card.getByText("Two positions, one challenge, one synthesis."),
  ).toBeVisible();
});

test("the project rail omits Data reconciliation", async ({ page }) => {
  await setupProject(page, CONTINUATION_RESPONSE, [EXISTING_CYCLE]);
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  await expect(
    page.getByRole("button", {
      name: `${EXISTING_CYCLE.title} Design`,
      exact: true,
    }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(
    page.getByRole("button", { name: /^Data reconciliation/ }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Build/ })).toBeVisible();
});

test("the native card's explicit confirmation creates exactly one cycle", async ({
  page,
}) => {
  const { cycleWrites } = await setupProject(
    page,
    PROPOSAL_RESPONSE,
    [],
    SETUP_MESSAGES,
  );
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  const card = page.getByTestId("human-input-card");
  await expect(
    card.getByRole("button", { name: "Create this DBTL cycle" }),
  ).toBeEnabled();
  await card.getByRole("button", { name: "Create this DBTL cycle" }).click();

  await expect.poll(() => cycleWrites.length).toBe(1);
});

test("a selected-cycle continuation runs without a second card", async ({
  page,
}) => {
  const { cycleWrites, evaluateCalls } = await setupProject(
    page,
    CONTINUATION_RESPONSE,
    [EXISTING_CYCLE],
  );
  await page.goto(`/workspace/test2/${MOCK_THREAD_ID}`);

  const cycleButton = page.getByRole("button", {
    name: `${EXISTING_CYCLE.title} Design`,
    exact: true,
  });
  // The default cycle begins disclosed for review but is not an explicit chat
  // scope. Close and reopen it to exercise a deliberate human selection.
  await cycleButton.click();
  await cycleButton.click();
  await send(page, "Run the next validation analysis");
  await expect.poll(() => evaluateCalls.length).toBe(1);
  expect(JSON.parse(evaluateCalls[0]!)).toMatchObject({
    selected_cycle_id: EXISTING_CYCLE.id,
  });

  await expect(page.getByTestId("human-input-card")).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "DBTL upgrade proposal" }),
  ).toHaveCount(0);
  expect(cycleWrites).toEqual([]);
});
