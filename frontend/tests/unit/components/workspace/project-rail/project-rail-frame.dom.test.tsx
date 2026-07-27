import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ProjectRailFrame } from "@/components/workspace/project-rail/project-rail-frame";

afterEach(cleanup);

describe("ProjectRailFrame", () => {
  it("collapses to an icon rail and expands again", () => {
    const { container } = render(
      <ProjectRailFrame>
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
    expect(screen.queryByText("Cycle content")).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Expand project rail" }),
    );
    expect(rail?.getAttribute("data-state")).toBe("expanded");
    expect(screen.getByText("Cycle content")).toBeTruthy();
  });
});
