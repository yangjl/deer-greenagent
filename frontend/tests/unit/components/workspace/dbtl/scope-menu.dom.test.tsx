import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { DbtlScopeMenu } from "@/components/workspace/dbtl/scope-menu";
import {
  ORDINARY_REQUEST_CONTEXT,
  START_CYCLE_REQUEST_CONTEXT,
} from "@/core/dbtl/composer-scope";
import type { CycleRecord } from "@/core/dbtl/cycle-view";

afterEach(cleanup);

function cycle(overrides: Partial<CycleRecord> = {}): CycleRecord {
  return {
    id: "cyc-1",
    project_id: "proj-1",
    parent_cycle_id: null,
    title: "Drought response",
    cycle_class: "computational",
    cycle_weight: "full",
    state: "design",
    db_revision: 1,
    research_question: "q",
    objective: "o",
    success_criteria: "s",
    created_by: "user-1",
    created_at: "2026-07-25T00:00:00Z",
    updated_at: "2026-07-25T00:00:00Z",
    stages: [],
    ...overrides,
  };
}

/**
 * Radix opens its dropdown on `pointerdown`, not `click`, and only for a
 * primary-button press.
 */
function openMenu() {
  fireEvent.pointerDown(screen.getByRole("button", { name: /DBTL scope/i }), {
    button: 0,
    ctrlKey: false,
    pointerType: "mouse",
  });
}

describe("DbtlScopeMenu", () => {
  it("stays wordless for ordinary work so it reads as one of the tool buttons", () => {
    // Ordinary work is the overwhelmingly common case. A permanent text label
    // would make the quiet default look like an active mode.
    render(
      <DbtlScopeMenu
        context={ORDINARY_REQUEST_CONTEXT}
        cycles={[]}
        onSelect={() => undefined}
      />,
    );

    const trigger = screen.getByRole("button", {
      name: /DBTL scope: Ordinary project work/i,
    });
    expect(trigger.textContent).toBe("");
  });

  it("names the scope on the trigger once a request is scoped to a cycle", () => {
    render(
      <DbtlScopeMenu
        context={{ kind: "cycle", cycleId: "cyc-1" }}
        cycles={[cycle({ id: "cyc-1", state: "build" })]}
        onSelect={() => undefined}
      />,
    );

    expect(screen.getByText("Cycle 01 · Build")).toBeTruthy();
  });

  it("names the scope when the next request will start a cycle", () => {
    render(
      <DbtlScopeMenu
        context={START_CYCLE_REQUEST_CONTEXT}
        cycles={[]}
        onSelect={() => undefined}
      />,
    );

    expect(screen.getByText("Start a new cycle")).toBeTruthy();
  });

  it("offers starting a cycle in the menu and reports the choice", () => {
    const onSelect = rs.fn();
    render(
      <DbtlScopeMenu
        context={ORDINARY_REQUEST_CONTEXT}
        cycles={[]}
        onSelect={onSelect}
      />,
    );

    openMenu();
    fireEvent.click(
      screen.getByRole("menuitem", { name: /Start a new cycle/i }),
    );

    expect(onSelect).toHaveBeenCalledWith({
      kind: "start_cycle",
      cycleId: null,
    });
  });

  it("says in the menu that starting a cycle records nothing yet", () => {
    // The durable record is written by the confirmation, not by this click.
    render(
      <DbtlScopeMenu
        context={ORDINARY_REQUEST_CONTEXT}
        cycles={[]}
        onSelect={() => undefined}
      />,
    );

    openMenu();
    expect(
      screen.getByText(/Nothing is recorded until you confirm/i),
    ).toBeTruthy();
  });

  it("states that the choice applies to the next request only", () => {
    render(
      <DbtlScopeMenu
        context={ORDINARY_REQUEST_CONTEXT}
        cycles={[]}
        onSelect={() => undefined}
      />,
    );

    openMenu();
    expect(screen.getByText(/next request only/i)).toBeTruthy();
  });

  it("cannot be changed while the composer is locked", () => {
    render(
      <DbtlScopeMenu
        context={ORDINARY_REQUEST_CONTEXT}
        cycles={[]}
        onSelect={() => undefined}
        disabled
      />,
    );

    expect(
      screen
        .getByRole("button", { name: /DBTL scope/i })
        .hasAttribute("disabled"),
    ).toBe(true);
  });
});
