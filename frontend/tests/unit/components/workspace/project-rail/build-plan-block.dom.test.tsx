import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import { BuildPlanBlock } from "@/components/workspace/project-rail/build-plan-block";
import type { BuildPlanProjection } from "@/core/dbtl";

afterEach(cleanup);

const empty: BuildPlanProjection = {
  rows: [],
  progress: "",
  emptyNote: "No build has started.",
  attention: "",
  stageStatusLabel: "",
};

describe("Build plan to-dos in the project rail", () => {
  it("stays hidden until a phased plan has to-dos", () => {
    const { container } = render(
      <BuildPlanBlock projection={empty} onOpenPhase={() => undefined} />,
    );

    expect(container.textContent).toBe("");
  });

  it("uses the fixed To-dos title and keeps phased progress", () => {
    render(
      <BuildPlanBlock
        projection={{
          ...empty,
          progress: "0 of 1",
          rows: [
            {
              phaseKey: "recover",
              index: 1,
              title: "Deterministically recover y = 2x + 1",
              status: "running",
              stateLabel: "Running",
              capability: "software_and_workflow_engineering",
              viaGeneralist: false,
              running: true,
            },
          ],
        }}
        onOpenPhase={() => undefined}
      />,
    );

    expect(screen.getByText("To-dos")).toBeTruthy();
    expect(screen.getByText("0 of 1")).toBeTruthy();
    expect(screen.queryByText(/Build plan ·/i)).toBeNull();
  });
});
