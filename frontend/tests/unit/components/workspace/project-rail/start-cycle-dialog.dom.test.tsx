import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

rs.mock("@/core/dbtl", () => ({
  CYCLE_CLASS_LABELS: {
    "season/program": "Season / program",
    computational: "Computational",
    other: "Other",
  },
  CYCLE_WEIGHT_LABELS: {
    full: "Full",
    light: "Light",
    retroactive: "Retroactive",
  },
  isLive: () => true,
  useCreateCycle: () => ({
    error: null,
    isPending: false,
    mutate: rs.fn(),
  }),
}));

import { StartCycleDialog } from "@/components/workspace/project-rail/start-cycle-dialog";

afterEach(cleanup);

describe("StartCycleDialog", () => {
  it("keeps actions outside the scroll region and explains disabled submit", () => {
    render(
      <StartCycleDialog
        projectId="project-1"
        projectName="G2F"
        cycles={[]}
        open
        onOpenChange={rs.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("max-h-[calc(100svh-1rem)]");
    expect(dialog.className).toContain("overflow-hidden");

    const scrollRegion = screen.getByTestId("cycle-form-scroll-region");
    const actions = screen.getByTestId("cycle-form-actions");
    expect(scrollRegion.className).toContain("overflow-y-auto");
    expect(scrollRegion.contains(actions)).toBe(false);

    const submit = screen.getByRole("button", { name: "Start cycle" });
    expect(submit.hasAttribute("disabled")).toBe(true);
    expect(
      screen.getByText("Add title and research question to continue."),
    ).toBeTruthy();
    expect(screen.getAllByText("Required")).toHaveLength(2);
  });

  it("enables submit once the required fields are complete", () => {
    render(
      <StartCycleDialog
        projectId="project-1"
        projectName="G2F"
        cycles={[]}
        open
        onOpenChange={rs.fn()}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Drought tolerance screen"), {
      target: { value: "Data check" },
    });
    fireEvent.change(
      screen.getByPlaceholderText(
        "Which lines hold yield under late-season drought?",
      ),
      {
        target: { value: "Are source records internally consistent?" },
      },
    );

    const submit = screen.getByRole("button", { name: "Start cycle" });
    expect(submit.hasAttribute("disabled")).toBe(false);
    expect(
      screen.getByText("Ready to create the durable cycle record."),
    ).toBeTruthy();
  });
});
