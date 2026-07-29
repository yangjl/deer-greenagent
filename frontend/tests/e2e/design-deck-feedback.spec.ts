import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  type FrameLocator,
  type Page,
  type Request,
  expect,
  test,
} from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * The whole deck-input path, driven through the real application.
 *
 * `design-deck-bridge.spec.ts` drives the deck's own script against a stub
 * parent. This drives the parent that ships: a Design deck opened in the
 * artifact panel of a project conversation, talking to the real
 * `ArtifactFilePreview` controller, which fetches the surface, hashes the bytes
 * it is about to trust, and posts the action.
 *
 * The distinction matters most for the hash check. Nothing in the unit or
 * bridge suites executes `crypto.subtle` against content the app actually
 * rendered, so "the preview is verified against the registered deck" was, until
 * this file, an untested claim about the one control that decides whether an
 * arbitrary HTML artifact can reach the feedback API.
 */

const THREAD_ID = "00000000-0000-0000-0000-0000000031a0";
const PROJECT_ID = "project-1";
const CYCLE_ID = "cycle-1";
const SURFACE_ID = "dfs-e2e-surface-001";
const DECK_PATH = "/mnt/user-data/outputs/dbtl/design-slides-rev1-e2e.html";

const deckHtml = readFileSync(
  join(process.cwd(), "tests/e2e/fixtures/design-deck.html"),
  "utf8",
);

/** What the parent must independently recompute before it enables anything. */
const DECK_HASH = createHash("sha256").update(deckHtml, "utf8").digest("hex");

type Surface = Record<string, unknown>;

function surface(overrides: Surface = {}): Surface {
  return {
    surface_id: SURFACE_ID,
    project_id: PROJECT_ID,
    cycle_id: CYCLE_ID,
    originating_thread_id: THREAD_ID,
    mode: "chair_feedback",
    deck_content_hash: DECK_HASH,
    deck_schema_version: "1",
    evidence_artifact_id: "artifact-design-rev1",
    evidence_artifact_revision: 1,
    evidence_content_hash: "e".repeat(64),
    is_current: true,
    newest_surface_id: null,
    allowed_actions: ["chair_option"],
    interactive: true,
    current_db_revision: 7,
    current_stage_status: "in_progress",
    receipt: null,
    note: "",
    ...overrides,
  };
}

function presentDeckMessages() {
  return [
    {
      type: "human",
      id: "msg-human-design",
      content: [{ type: "text", text: "Run the design meeting" }],
    },
    {
      type: "ai",
      id: "msg-ai-design",
      content: "The meeting paused on one decision. Here is the round deck.",
      tool_calls: [
        {
          id: "present-design-deck",
          name: "present_files",
          args: { filepaths: [DECK_PATH] },
        },
      ],
    },
  ];
}

/**
 * Everything the project route needs to resolve a slug to `project-1`, which is
 * what makes the controller's `projectId` non-null — without it the bridge
 * effect returns early and the deck stays inert for reasons unrelated to trust.
 */
async function mockProjectScope(page: Page) {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: THREAD_ID,
        title: "Design meeting",
        messages: presentDeckMessages(),
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
          id: PROJECT_ID,
          workspace_id: "workspace-1",
          name: "maize",
          slug: "maize",
          root_path: "/Users/test/Documents/projects/maize",
          crop_profile: "maize-v1",
          dbtl_phase: "design",
          reconciliation_status: "ready",
        },
      ]),
    }),
  );
  await page.route(`**/api/projects/${PROJECT_ID}/threads`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          thread_id: THREAD_ID,
          display_name: "Design meeting",
          updated_at: "2026-07-29T01:50:00Z",
        },
      ]),
    }),
  );
  // The project rail loads this on mount; unmocked it 401s and the route
  // bounces back to /workspace before any artifact can be opened.
  // Matched exactly, so it cannot shadow the per-cycle action route below.
  await page.route(`**/api/projects/${PROJECT_ID}/dbtl/cycles`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        project_id: PROJECT_ID,
        stages: [],
        cycles: [],
      }),
    }),
  );
  // Served verbatim: the bytes the app renders are the bytes it must hash.
  await page.route(`**/api/threads/${THREAD_ID}/artifacts${DECK_PATH}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: deckHtml,
    }),
  );
}

async function openDeckArtifact(page: Page) {
  await page.goto(`/workspace/maize/${THREAD_ID}`);
  const card = page.getByText("design-slides-rev1-e2e.html").first();
  await expect(card).toBeVisible({ timeout: 15_000 });
  await card.click();
  return page.frameLocator('iframe[title="Artifact preview"]');
}

/**
 * The deck shows one slide at a time, and the decision lives on its own. A
 * person pages to it; so does this, rather than reaching past the UI, because
 * "the controls are reachable" is part of what makes the deck answerable.
 */
async function pageToDecision(deck: FrameLocator) {
  const fieldset = deck.locator("fieldset.decision");
  const next = deck.locator('[aria-label="Next slide"]');
  for (let step = 0; step < 10; step += 1) {
    if (await fieldset.isVisible()) return;
    await next.click();
  }
  await expect(fieldset).toBeVisible();
}

function deckSurfaceRoute(page: Page, body: Surface | (() => Surface)) {
  return page.route(
    `**/api/projects/${PROJECT_ID}/dbtl/design-feedback/${SURFACE_ID}*`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(typeof body === "function" ? body() : body),
      }),
  );
}

const ACTIONS_URL = `**/api/projects/${PROJECT_ID}/dbtl/cycles/${CYCLE_ID}/design-feedback/${SURFACE_ID}/actions`;

test.describe("design deck feedback, end to end", () => {
  test("verifies the bytes, records the choice, and settles the deck", async ({
    page,
  }) => {
    await mockProjectScope(page);
    await deckSurfaceRoute(page, surface());

    const posted: Request[] = [];
    await page.route(ACTIONS_URL, (route) => {
      posted.push(route.request());
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "recorded",
          replayed: false,
          receipt: { message: "Recorded. The meeting is resuming.", db_revision: 8 },
        }),
      });
    });

    const deck = await openDeckArtifact(page);

    // The prompt only appears once the surface resolved *and* the recomputed
    // digest matched, so this single assertion covers the whole handshake.
    await expect(deck.locator("[data-deck-status]")).toHaveText(
      "Choose an option, then send it to the meeting.",
    );
    await expect(deck.locator("[data-deck-submit]")).toBeEnabled();

    await pageToDecision(deck);
    await deck.locator("#inbreeding-route--doubled_haploid").check();
    await deck
      .locator("[data-deck-comment]")
      .fill("Induction bias is acceptable for this family.");
    await deck.locator("[data-deck-submit]").click();

    await expect
      .poll(() => posted.length, { timeout: 10_000 })
      .toBe(1);

    const body = posted[0]!.postDataJSON() as Record<string, unknown>;
    expect(body.action).toEqual({
      kind: "chair_option",
      option_ids: ["doubled_haploid"],
    });
    expect(body.comment).toBe("Induction bias is acceptable for this family.");
    expect(body.originating_thread_id).toBe(THREAD_ID);
    // The three bindings that make the write refusable server-side.
    expect(body.expected_deck_hash).toBe(DECK_HASH);
    expect(body.expected_db_revision).toBe(7);
    expect(body.expected_evidence).toEqual({
      artifact_id: "artifact-design-rev1",
      revision: 1,
      content_hash: "e".repeat(64),
    });
    expect(body.client_submission_id).toEqual(expect.any(String));

    await expect(deck.locator("[data-deck-status]")).toHaveText(
      "Recorded. The meeting is resuming.",
    );
    await expect(deck.locator("[data-deck-submit]")).toBeDisabled();
  });

  test("a preview that is not the registered deck never becomes answerable", async ({
    page,
  }) => {
    await mockProjectScope(page);
    // Same surface, different bytes: the shape a substituted or edited artifact
    // would take. Nothing else about the response says anything is wrong.
    await deckSurfaceRoute(page, surface({ deck_content_hash: "0".repeat(64) }));

    let attempted = 0;
    await page.route(ACTIONS_URL, (route) => {
      attempted += 1;
      return route.fulfill({ status: 200, body: "{}" });
    });

    const deck = await openDeckArtifact(page);

    await expect(deck.locator("[data-deck-status]")).toHaveText(
      "This preview does not match the registered deck bytes.",
    );
    await expect(deck.locator("[data-deck-submit]")).toBeDisabled();

    await pageToDecision(deck);
    // The option itself, not the fieldset: an enabled fieldset would not
    // re-enable a control carrying its own `disabled`, so this is the one that
    // decides whether a choice can actually be made.
    await expect(
      deck.locator("#inbreeding-route--doubled_haploid"),
    ).toBeDisabled();
    // Force the click past the disabled control; nothing may reach the API.
    await deck.locator("[data-deck-submit]").click({ force: true });
    expect(attempted).toBe(0);
  });

  test("stays read-only when the server allows no action", async ({ page }) => {
    await mockProjectScope(page);
    await deckSurfaceRoute(
      page,
      surface({
        allowed_actions: [],
        interactive: false,
        note: "This round has already been answered.",
      }),
    );

    const deck = await openDeckArtifact(page);

    await expect(deck.locator("[data-deck-status]")).toHaveText(
      "This round has already been answered.",
    );
    await expect(deck.locator("[data-deck-submit]")).toBeDisabled();
  });

  test("a superseded surface latches stale instead of rebasing the answer", async ({
    page,
  }) => {
    await mockProjectScope(page);

    // First read arms the deck; the read after the conflict reports that the
    // evidence moved on, which is the case that must never be retried.
    let reads = 0;
    await deckSurfaceRoute(page, () => {
      reads += 1;
      return reads === 1
        ? surface()
        : surface({
            is_current: false,
            interactive: false,
            allowed_actions: [],
            current_db_revision: 9,
            newest_surface_id: "dfs-e2e-surface-002",
            note: "A newer round replaced this one.",
          });
    });

    let attempts = 0;
    await page.route(ACTIONS_URL, (route) => {
      attempts += 1;
      return route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Design evidence has moved on." }),
      });
    });

    const deck = await openDeckArtifact(page);
    await expect(deck.locator("[data-deck-submit]")).toBeEnabled();

    await pageToDecision(deck);
    await deck.locator("#inbreeding-route--selfing_f5_f6").check();
    await deck.locator("[data-deck-submit]").click();

    await expect(deck.locator("[data-deck-status]")).toHaveText(
      "A newer round replaced this one.",
    );
    await expect(deck.locator("[data-deck-submit]")).toBeDisabled();
    expect(attempts).toBe(1);
  });

  test("a transient failure returns the draft and retries under one submission id", async ({
    page,
  }) => {
    await mockProjectScope(page);
    await deckSurfaceRoute(page, surface());

    const submissionIds: string[] = [];
    let calls = 0;
    await page.route(ACTIONS_URL, (route) => {
      calls += 1;
      const body = route.request().postDataJSON() as {
        client_submission_id: string;
      };
      submissionIds.push(body.client_submission_id);
      if (calls === 1) {
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "The meeting service is unavailable." }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "recorded",
          replayed: false,
          receipt: { message: "Recorded on retry." },
        }),
      });
    });

    const deck = await openDeckArtifact(page);
    await expect(deck.locator("[data-deck-submit]")).toBeEnabled();

    await pageToDecision(deck);
    await deck.locator("#inbreeding-route--doubled_haploid").check();
    await deck.locator("[data-deck-comment]").fill("Keep this text.");
    await deck.locator("[data-deck-submit]").click();

    await expect(deck.locator("[data-deck-status]")).toHaveText(
      "The meeting service is unavailable.",
    );
    // The draft is the person's, not the failed request's.
    await expect(deck.locator("[data-deck-submit]")).toBeEnabled();
    await expect(deck.locator("#inbreeding-route--doubled_haploid")).toBeChecked();
    await expect(deck.locator("[data-deck-comment]")).toHaveValue(
      "Keep this text.",
    );

    await deck.locator("[data-deck-submit]").click();
    await expect(deck.locator("[data-deck-status]")).toHaveText(
      "Recorded on retry.",
    );

    // A retry replays one answer; two ids would be two answers to one question.
    expect(submissionIds).toHaveLength(2);
    expect(submissionIds[0]).toBe(submissionIds[1]);
  });

  test("an unverifiable surface leaves the deck inert with the reason", async ({
    page,
  }) => {
    await mockProjectScope(page);
    await page.route(
      `**/api/projects/${PROJECT_ID}/dbtl/design-feedback/${SURFACE_ID}*`,
      (route) =>
        route.fulfill({
          status: 403,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Not a member of this project." }),
        }),
    );

    const deck = await openDeckArtifact(page);

    await expect(deck.locator("[data-deck-status]")).toHaveText(
      "Not a member of this project.",
    );
    await expect(deck.locator("[data-deck-submit]")).toBeDisabled();
  });
});
