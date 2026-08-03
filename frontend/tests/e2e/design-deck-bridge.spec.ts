import { readFileSync } from "node:fs";
import { join } from "node:path";

import { type Page, expect, test } from "@playwright/test";

import {
  DECK_MESSAGE_SOURCE,
  DECK_PROTOCOL_VERSION,
} from "@/core/dbtl/design-deck-feedback";

/**
 * The deck's half of the bridge, driven in a real browser.
 *
 * The unit tests pin the parent's parser and the rendered source; this pins what
 * the deck's own script actually does when messages arrive. That distinction
 * matters: an earlier version of this suite is what caught a deck that would
 * happily re-arm itself after being superseded, which no source-level assertion
 * would have noticed.
 *
 * The fixture is generated from the Python renderer and guarded against drift by
 * `backend/tests/test_dbtl_deck_fixture_drift.py`.
 */

const SURFACE_ID = "dfs-e2e-surface-001";
const CHANNEL = "chan-e2e-1";

const deckHtml = readFileSync(
  join(process.cwd(), "tests/e2e/fixtures/design-deck.html"),
  "utf8",
);

/** A minimal parent: it frames the deck and records what the deck sends back. */
const HARNESS = `<!doctype html><html><body>
<iframe id="frame" width="900" height="600"></iframe>
<script>
  window.__intents = [];
  window.addEventListener('message', function (e) {
    if (e.data && e.data.source === '${DECK_MESSAGE_SOURCE}') { window.__intents.push(e.data); }
  });
</script>
</body></html>`;

async function openDeck(page: Page) {
  await page.setContent(HARNESS);
  await page.evaluate((html) => {
    const frame = document.getElementById("frame") as HTMLIFrameElement;
    frame.srcdoc = html;
    return new Promise<void>((resolve) => {
      frame.addEventListener("load", () => resolve(), { once: true });
    });
  }, deckHtml);
  // The deck announces itself on load; wait for that rather than a fixed delay.
  await page.waitForFunction(() => (window as never as { __intents: unknown[] }).__intents.length > 0);
}

async function send(page: Page, body: Record<string, unknown>) {
  await page.evaluate(
    ({ payload }) => {
      const frame = document.getElementById("frame") as HTMLIFrameElement;
      frame.contentWindow?.postMessage(payload, "*");
    },
    { payload: body },
  );
  // Let the deck's message handler run before the assertion reads the DOM.
  await page.waitForTimeout(50);
}

function envelope(extra: Record<string, unknown>) {
  return {
    source: DECK_MESSAGE_SOURCE,
    protocol: DECK_PROTOCOL_VERSION,
    surfaceId: SURFACE_ID,
    channel: CHANNEL,
    ...extra,
  };
}

async function controlsEnabled(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
    const fieldset = doc.querySelector<HTMLFieldSetElement>("fieldset.decision");
    const submit = doc.querySelector<HTMLButtonElement>("[data-deck-submit]");
    return !!fieldset && !fieldset.disabled && !!submit && !submit.disabled;
  });
}

async function intents(page: Page) {
  return page.evaluate(() => (window as never as { __intents: Record<string, unknown>[] }).__intents);
}

test.describe("design deck bridge", () => {
  test("announces itself once and stays inert until a parent activates it", async ({ page }) => {
    await openDeck(page);

    const received = await intents(page);
    expect(received).toHaveLength(1);
    expect(received[0]!.type).toBe("ready");
    expect(received[0]!.surfaceId).toBe(SURFACE_ID);
    expect(await controlsEnabled(page)).toBe(false);
  });

  test("refuses activation from another surface or another protocol", async ({ page }) => {
    await openDeck(page);

    await send(page, envelope({ surfaceId: "dfs-someone-else", type: "initialize", allowedActions: ["chair_option"] }));
    expect(await controlsEnabled(page)).toBe(false);

    await send(page, envelope({ protocol: 99, type: "initialize", allowedActions: ["chair_option"] }));
    expect(await controlsEnabled(page)).toBe(false);
  });

  test("stays read-only when the server allows nothing", async ({ page }) => {
    await openDeck(page);

    await send(page, envelope({ type: "initialize", allowedActions: [] }));

    expect(await controlsEnabled(page)).toBe(false);
  });

  test("activates the choice, and preselects nothing", async ({ page }) => {
    await openDeck(page);

    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"] }));

    expect(await controlsEnabled(page)).toBe(true);
    const preselected = await page.evaluate(() => {
      const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
      return doc.querySelector("fieldset.decision input:checked") !== null;
    });
    expect(preselected).toBe(false);
  });

  test("emits one submit intent carrying the choice, the channel, and no credential", async ({ page }) => {
    await openDeck(page);
    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"] }));

    await page.evaluate(() => {
      const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
      const radios = doc.querySelectorAll<HTMLInputElement>('fieldset.decision input[type="radio"]');
      radios[1]!.checked = true;
      doc.querySelector<HTMLButtonElement>("[data-deck-submit]")!.click();
    });
    await page.waitForTimeout(50);

    const received = await intents(page);
    const submit = received.find((item) => item.type === "submit_intent");
    expect(submit).toBeTruthy();
    expect((submit!.action as { optionIds: string[] }).optionIds).toEqual(["doubled_haploid"]);
    expect(submit!.channel).toBe(CHANNEL);
    expect(JSON.stringify(submit)).not.toContain("token");
    // Frozen while in flight, so a second click cannot become a second answer.
    expect(await controlsEnabled(page)).toBe(false);
  });

  test("a failure returns the draft rather than discarding it", async ({ page }) => {
    await openDeck(page);
    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"] }));
    await page.evaluate(() => {
      const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
      doc.querySelectorAll<HTMLInputElement>('fieldset.decision input[type="radio"]')[1]!.checked = true;
      doc.querySelector<HTMLButtonElement>("[data-deck-submit]")!.click();
    });

    await send(page, envelope({ type: "failed", note: "Network error." }));

    expect(await controlsEnabled(page)).toBe(true);
    const stillChosen = await page.evaluate(() => {
      const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
      return doc.querySelector("fieldset.decision input:checked") !== null;
    });
    expect(stillChosen).toBe(true);
  });

  test("a superseded deck goes read-only and cannot be re-armed", async ({ page }) => {
    await openDeck(page);
    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"] }));

    await send(page, envelope({ type: "stale", note: "A newer round replaced this one." }));
    expect(await controlsEnabled(page)).toBe(false);

    // Defence in depth: the parent already refuses to re-initialize a settled
    // surface, and the file refuses to be re-armed even if one tried.
    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"] }));
    expect(await controlsEnabled(page)).toBe(false);
  });

  test("an accepted deck cannot be re-armed either", async ({ page }) => {
    await openDeck(page);
    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"] }));

    await send(page, envelope({ type: "accepted", note: "Recorded." }));
    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"] }));

    expect(await controlsEnabled(page)).toBe(false);
  });

  // `comment` arrives as unknown across the postMessage boundary; a note that
  // came back as a non-string is a failure to report, not one to stringify.
  const commentOf = (intent: { comment?: unknown } | undefined): string => {
    expect(intent).toBeTruthy();
    const value = intent!.comment;
    expect(typeof value).toBe("string");
    return value as string;
  };

  // The per-slide note boxes are the redesign's only new path to a durable
  // record. A note that silently failed to travel would look identical to a
  // reviewer who simply wrote nothing, so these drive the real DOM.
  test("note boxes stay inert until a parent activates them", async ({ page }) => {
    await openDeck(page);

    const disabledBefore = await page.evaluate(() => {
      const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
      const notes = Array.from(doc.querySelectorAll<HTMLTextAreaElement>("[data-deck-note]"));
      return { count: notes.length, allDisabled: notes.every((note) => note.disabled) };
    });
    expect(disabledBefore.count).toBeGreaterThan(0);
    expect(disabledBefore.allDisabled).toBe(true);

    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"] }));

    // An enabled fieldset does not clear a control's own `disabled`, which is
    // exactly how the option radios were once left unusable while live.
    const enabledAfter = await page.evaluate(() => {
      const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
      return Array.from(doc.querySelectorAll<HTMLTextAreaElement>("[data-deck-note]")).every((note) => !note.disabled);
    });
    expect(enabledAfter).toBe(true);
  });

  test("a slide note is folded into the comment of the decision actually taken", async ({ page }) => {
    await openDeck(page);
    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"] }));

    await page.evaluate(() => {
      const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
      const note = doc.querySelector<HTMLTextAreaElement>("[data-deck-note]")!;
      note.value = "the held-out split has to be by genotype";
      doc.querySelectorAll<HTMLInputElement>('fieldset.decision input[type="radio"]')[1]!.checked = true;
      const comment = doc.querySelector<HTMLTextAreaElement>("[data-deck-comment]");
      if (comment) comment.value = "going with this";
      doc.querySelector<HTMLButtonElement>("[data-deck-submit]")!.click();
    });
    await page.waitForTimeout(50);

    const submit = (await intents(page)).find((item) => item.type === "submit_intent");
    expect(submit).toBeTruthy();
    const comment = commentOf(submit);
    expect(comment).toContain("the held-out split has to be by genotype");
    // Labelled, so a reader of the record knows which slide the note is about,
    // and the verdict's own words are still distinguishable from it.
    expect(comment).toMatch(/^\[[^\]]+]/);
    expect(comment).toContain("going with this");
  });

  test("a restored comment clears the boxes so a retry cannot fold the note twice", async ({ page }) => {
    await openDeck(page);
    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"] }));

    await page.evaluate(() => {
      const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
      doc.querySelector<HTMLTextAreaElement>("[data-deck-note]")!.value = "one note";
      doc.querySelectorAll<HTMLInputElement>('fieldset.decision input[type="radio"]')[1]!.checked = true;
      doc.querySelector<HTMLButtonElement>("[data-deck-submit]")!.click();
    });
    await page.waitForTimeout(50);

    const firstComment = commentOf((await intents(page)).find((item) => item.type === "submit_intent"));
    await send(page, envelope({ type: "failed", note: "Network error." }));
    // The parent replays the comment it captured, which already contains the note.
    await send(page, envelope({ type: "initialize", allowedActions: ["chair_option"], comment: firstComment }));

    const boxesCleared = await page.evaluate(() => {
      const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
      return Array.from(doc.querySelectorAll<HTMLTextAreaElement>("[data-deck-note]")).every((note) => note.value === "");
    });
    expect(boxesCleared).toBe(true);

    await page.evaluate(() => {
      const doc = (document.getElementById("frame") as HTMLIFrameElement).contentDocument!;
      doc.querySelectorAll<HTMLInputElement>('fieldset.decision input[type="radio"]')[1]!.checked = true;
      doc.querySelector<HTMLButtonElement>("[data-deck-submit]")!.click();
    });
    await page.waitForTimeout(50);

    const retried = (await intents(page)).filter((item) => item.type === "submit_intent");
    const retryComment = commentOf(retried[retried.length - 1]);
    expect(retryComment.match(/one note/g)).toHaveLength(1);
  });
});
