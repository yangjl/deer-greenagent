import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { Phase7DemoDialog } from "@/components/workspace/project-rail/phase7-demo-dialog";

afterEach(cleanup);

describe("Phase7DemoDialog", () => {
  it("walks through all three outcomes without changing durable records", () => {
    render(<Phase7DemoDialog open onOpenChange={() => undefined} />);

    expect(screen.getByText("Preview only · no records changed")).toBeTruthy();
    expect(screen.getByText("Invalidated")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Missing evidence/i }));
    expect(screen.getByText("Inconclusive")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Supported result/i }));
    expect(screen.getByText("Supported")).toBeTruthy();
  });
});
