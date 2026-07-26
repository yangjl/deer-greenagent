import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ModelPicker } from "@/components/workspace/model-picker";
import type { Model } from "@/core/models/types";

afterEach(cleanup);

function model(overrides: Partial<Model> = {}): Model {
  return {
    id: "claude-fable-5",
    name: "claude-fable-5",
    model: "claude-fable-5",
    display_name: "Claude Fable 5",
    description: "Everyday tasks. Fast, capable, and the usual default.",
    ...overrides,
  };
}

const MODELS: Model[] = [
  model(),
  model({
    id: "claude-opus-5",
    name: "claude-opus-5",
    model: "claude-opus-5",
    display_name: "Claude Opus 5",
    description: "Deepest reasoning. Best for architecture and hard analysis.",
  }),
];

/** Radix opens its dropdown on a primary `pointerdown`, not a click. */
function openMenu() {
  fireEvent.pointerDown(screen.getByRole("button", { name: /Model:/i }), {
    button: 0,
    ctrlKey: false,
    pointerType: "mouse",
  });
}

describe("ModelPicker", () => {
  it("names the active model on the trigger", () => {
    render(
      <ModelPicker
        models={MODELS}
        selectedModel={MODELS[1]}
        onSelect={() => undefined}
      />,
    );

    expect(screen.getByText("Claude Opus 5")).toBeTruthy();
  });

  it("lists every configured model with its description", () => {
    render(
      <ModelPicker
        models={MODELS}
        selectedModel={MODELS[0]}
        onSelect={() => undefined}
      />,
    );

    openMenu();
    expect(
      screen.getByRole("menuitem", { name: /Claude Opus 5/i }),
    ).toBeTruthy();
    expect(screen.getByText(/Deepest reasoning/i)).toBeTruthy();
    expect(screen.getByText(/Everyday tasks/i)).toBeTruthy();
  });

  it("reports the chosen model by its configured name", () => {
    const onSelect = rs.fn();
    render(
      <ModelPicker
        models={MODELS}
        selectedModel={MODELS[0]}
        onSelect={onSelect}
      />,
    );

    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Claude Opus 5/i }));
    expect(onSelect).toHaveBeenCalledWith("claude-opus-5");
  });

  it("offers Custom for picking a specific model directly", () => {
    render(
      <ModelPicker
        models={MODELS}
        selectedModel={MODELS[0]}
        onSelect={() => undefined}
      />,
    );

    openMenu();
    expect(screen.getByText("Select a specific model directly.")).toBeTruthy();
  });

  it("renders a model with no configured description without inventing one", () => {
    // Descriptions are an operator's decision in config.yaml. A model without
    // one should show its name alone rather than a guessed line.
    const bare = model({
      id: "bare",
      name: "bare",
      display_name: "Bare Model",
      description: null,
    });
    render(
      <ModelPicker
        models={[bare]}
        selectedModel={bare}
        onSelect={() => undefined}
      />,
    );

    openMenu();
    expect(screen.getByRole("menuitem", { name: /Bare Model/i })).toBeTruthy();
  });

  it("cannot be changed while the composer is locked", () => {
    render(
      <ModelPicker
        models={MODELS}
        selectedModel={MODELS[0]}
        onSelect={() => undefined}
        disabled
      />,
    );

    expect(
      screen.getByRole("button", { name: /Model:/i }).hasAttribute("disabled"),
    ).toBe(true);
  });
});
