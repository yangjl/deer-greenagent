/**
 * Opening a tool row to see what was asked and what came back.
 *
 * The card named the tool and stopped, while the request and output sat unread
 * in the step model — which is exactly the information that explains a failure.
 * These tests cover the disclosure itself: that the detail is hidden until
 * asked for, that it is bounded, and that "still running" is never rendered as
 * an empty success.
 */

import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { SubtaskToolStep } from "@/components/workspace/messages/subtask-tool-step";
import type { TranscriptEntry } from "@/core/tasks/tool-transcript";

afterEach(cleanup);

function entry(overrides: Partial<TranscriptEntry> = {}): TranscriptEntry {
  return {
    id: "tool-1-0",
    kind: "tool",
    title: "bash · python fit.py",
    text: "Traceback: no such file",
    args: { command: "python fit.py" },
    toolName: "bash",
    stepIndex: 2,
    ...overrides,
  };
}

describe("the detail is available but not in the way", () => {
  it("shows the tool row collapsed", () => {
    render(<SubtaskToolStep entry={entry()} />);

    expect(screen.getByText("bash · python fit.py")).toBeTruthy();
    expect(screen.queryByText("Request")).toBeNull();
    expect(screen.queryByText("Output")).toBeNull();
  });

  it("reveals the request and the output when opened", () => {
    render(<SubtaskToolStep entry={entry()} />);

    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("Request")).toBeTruthy();
    expect(screen.getByText("Output")).toBeTruthy();
    expect(screen.getByText(/Traceback: no such file/)).toBeTruthy();
  });

  it("closes again", () => {
    render(<SubtaskToolStep entry={entry()} />);
    const button = screen.getByRole("button");

    fireEvent.click(button);
    fireEvent.click(button);

    expect(screen.queryByText("Output")).toBeNull();
  });

  it("reports its expanded state to assistive technology", () => {
    render(<SubtaskToolStep entry={entry()} />);
    const button = screen.getByRole("button");

    expect(button.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
  });
});

describe("a running call is not an empty success", () => {
  it("says it is running", () => {
    render(<SubtaskToolStep entry={entry({ pending: true, text: "" })} />);

    expect(screen.getByText("running")).toBeTruthy();
  });

  it("says no output has arrived rather than showing a blank result", () => {
    render(<SubtaskToolStep entry={entry({ pending: true, text: "" })} />);

    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("No output recorded yet.")).toBeTruthy();
  });

  it("distinguishes a finished call that genuinely returned nothing", () => {
    render(<SubtaskToolStep entry={entry({ text: "" })} />);

    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("The tool returned no output.")).toBeTruthy();
  });

  it("does not animate the running indicator under reduced motion", () => {
    const { container } = render(
      <SubtaskToolStep entry={entry({ pending: true, text: "" })} />,
    );

    const spinner = container.querySelector(".animate-spin");
    expect(spinner?.className).toContain("motion-reduce:animate-none");
  });
});

describe("output is bounded", () => {
  it("truncates a very long output and says so", () => {
    render(<SubtaskToolStep entry={entry({ text: "x".repeat(5_000) })} />);

    fireEvent.click(screen.getByRole("button"));

    expect(
      screen.getByText(/Truncated for display — 5,000 characters/),
    ).toBeTruthy();
  });

  it("does not claim truncation for output that fits", () => {
    render(<SubtaskToolStep entry={entry({ text: "short" })} />);

    fireEvent.click(screen.getByRole("button"));

    expect(screen.queryByText(/Truncated for display/)).toBeNull();
  });

  it("reports truncation that happened when the step was captured", () => {
    render(<SubtaskToolStep entry={entry({ truncated: true })} />);

    fireEvent.click(screen.getByRole("button"));

    expect(
      screen.getByText(
        "The recorded output was truncated when it was captured.",
      ),
    ).toBeTruthy();
  });
});

describe("a row with nothing behind it does not pretend to open", () => {
  it("disables the control", () => {
    render(
      <SubtaskToolStep
        entry={entry({ args: undefined, text: "", pending: undefined })}
      />,
    );

    const button = screen.getByRole("button");
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.getAttribute("aria-expanded")).toBeNull();
  });
});
