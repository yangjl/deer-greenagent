import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  AlertTriangleIcon,
  BotIcon,
  CircleDashedIcon,
  MessagesSquareIcon,
} from "lucide-react";

import { ProjectRailFrame } from "@/components/workspace/project-rail/project-rail-frame";

afterEach(cleanup);

describe("ProjectRailFrame", () => {
  it("collapses to section icons and expands at the selected section", () => {
    let selectedSection = "";
    const { container } = render(
      <ProjectRailFrame
        collapsedItems={[
          { icon: CircleDashedIcon, label: "Cycles" },
          { icon: AlertTriangleIcon, label: "Blockers" },
          {
            icon: BotIcon,
            label: "Agents",
            onSelect: () => {
              selectedSection = "Agents";
            },
          },
          { icon: MessagesSquareIcon, label: "Conversations" },
        ]}
      >
        <div>Cycle content</div>
      </ProjectRailFrame>,
    );

    const rail = container.querySelector("aside");
    expect(rail?.getAttribute("data-state")).toBe("expanded");
    expect(screen.getByText("Cycle content")).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: "Collapse project rail" }),
    );
    expect(rail?.getAttribute("data-state")).toBe("collapsed");
    expect(rail?.className).toContain("w-12");
    expect(screen.queryByText("Cycle content")).toBeNull();
    expect(
      screen.getByRole("navigation", { name: "Project rail sections" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cycles" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Blockers" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Conversations" })).toBeTruthy();

    const agentControl = screen.getByRole("button", { name: "Agents" });
    expect(agentControl.querySelector("svg")).toBeTruthy();
    fireEvent.click(agentControl);
    expect(rail?.getAttribute("data-state")).toBe("expanded");
    expect(rail?.className).toContain("w-64");
    expect(screen.getByText("Cycle content")).toBeTruthy();
    expect(selectedSection).toBe("Agents");
  });

  it("keeps the panel toggle and section icons mounted after folding", () => {
    render(
      <ProjectRailFrame
        collapsedItems={[
          { icon: CircleDashedIcon, label: "Cycles" },
          { icon: AlertTriangleIcon, label: "Blockers" },
          { icon: BotIcon, label: "Agents" },
          { icon: MessagesSquareIcon, label: "Conversations" },
        ]}
      >
        <div>Cycle content</div>
      </ProjectRailFrame>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Collapse project rail" }),
    );

    const expandControl = screen.getByRole("button", {
      name: "Expand project rail",
    });
    expect(expandControl.querySelector("svg")).toBeTruthy();
    expect(expandControl.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByRole("button", { name: "Cycles" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Blockers" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Agents" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Conversations" })).toBeTruthy();
  });
});
