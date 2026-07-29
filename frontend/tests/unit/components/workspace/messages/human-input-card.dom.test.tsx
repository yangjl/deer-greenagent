import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { HumanInputCard } from "@/components/workspace/messages/human-input-card";
import { I18nContext } from "@/core/i18n/context";
import type { HumanInputRequest } from "@/core/messages/human-input";

const formRequest: HumanInputRequest = {
  version: 2,
  kind: "human_input_request",
  source: "ask_clarification",
  request_id: "clarification:call-form",
  question: "Please provide the expense details.",
  input_mode: "form",
  fields: [
    { name: "amount", label: "Amount", type: "number", required: true },
    { name: "category", label: "Category", type: "text", required: true },
  ],
};

function renderCard() {
  return render(
    <I18nContext.Provider
      value={{ locale: "en-US", setLocale: () => undefined }}
    >
      <HumanInputCard request={formRequest} onSubmit={() => undefined} />
    </I18nContext.Provider>,
  );
}

afterEach(cleanup);
afterEach(() => {
  rs.restoreAllMocks();
});

describe("HumanInputCard form validation (DOM)", () => {
  it("keeps the error node mounted while another field is still invalid", () => {
    renderCard();

    fireEvent.click(screen.getByRole("button", { name: "Submit" }));

    // Both required fields flagged; the error node exists and is referenced.
    const amount = screen.getByLabelText(/Amount/);
    const category = screen.getByLabelText(/Category/);
    expect(amount.getAttribute("aria-invalid")).toBe("true");
    expect(category.getAttribute("aria-invalid")).toBe("true");
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("required fields");

    // Fixing ONE field must not unmount the error node the other field's
    // aria-describedby still points at.
    fireEvent.change(amount, { target: { value: "300" } });
    expect(amount.getAttribute("aria-invalid")).toBeNull();
    expect(category.getAttribute("aria-invalid")).toBe("true");
    const describedBy = category.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy!)).not.toBeNull();

    // Fixing the last invalid field clears the error entirely.
    fireEvent.change(category, { target: { value: "travel" } });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps select fields controlled from placeholder through selection", () => {
    const warnSpy = rs.spyOn(console, "warn").mockImplementation(() => ({}));
    render(
      <I18nContext.Provider
        value={{ locale: "en-US", setLocale: () => undefined }}
      >
        <HumanInputCard
          request={{
            ...formRequest,
            fields: [
              {
                name: "category",
                label: "Category",
                type: "select",
                required: true,
                options: [
                  { id: "category-travel", label: "travel", value: "travel" },
                  { id: "category-meals", label: "meals", value: "meals" },
                ],
              },
            ],
          }}
          onSubmit={() => undefined}
        />
      </I18nContext.Provider>,
    );

    const trigger = screen.getByRole("combobox", {
      name: "Category required",
    });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("option", { name: "travel" }));

    expect(
      warnSpy.mock.calls.some(([message]) =>
        String(message).includes("uncontrolled to controlled"),
      ),
    ).toBe(false);
  });
});

describe("HumanInputCard meeting setup (DOM)", () => {
  it("shows the selected depth's seats before a separate confirmation", () => {
    const onSubmit = rs.fn();
    render(
      <I18nContext.Provider
        value={{ locale: "en-US", setLocale: () => undefined }}
      >
        <HumanInputCard
          request={{
            version: 1,
            kind: "human_input_request",
            source: "ask_clarification",
            request_id: "dbtl-council__cycle-1__run-1",
            clarification_type: "council_preflight",
            question: "How much debate should this design get?",
            input_mode: "single_choice",
            recommended_option_id: "medium",
            options: [
              { id: "light", label: "Light debate", value: "light" },
              { id: "medium", label: "Medium debate", value: "medium" },
            ],
            council_participants: [
              {
                id: "position-1",
                role: "position",
                role_label: "Independent position",
                agent_name: "general-purpose",
                via_generalist: false,
                focus: "first position",
                model: "gpt-5.4",
                model_options: ["gpt-5.4"],
                max_tokens: 400000,
                max_tokens_min: 10000,
                max_tokens_max: 2000000,
                token_limit_enforced: false,
                reasoning: "standard",
                reasoning_options: ["standard", "extended"],
                instructions: "Argue first.",
              },
              {
                id: "position-2",
                role: "position",
                role_label: "Independent position",
                agent_name: "experimental-design",
                via_generalist: false,
                focus: "second position",
                model: "gpt-5.3-codex-spark",
                model_options: ["gpt-5.3-codex-spark"],
                max_tokens: 400000,
                max_tokens_min: 10000,
                max_tokens_max: 2000000,
                token_limit_enforced: false,
                reasoning: "standard",
                reasoning_options: ["standard", "extended"],
                instructions: "Argue second.",
              },
              {
                id: "chair",
                role: "chair",
                role_label: "Chair",
                agent_name: "experimental-design",
                via_generalist: false,
                focus: "synthesis",
                model: "gpt-5.6-sol",
                model_options: ["gpt-5.6-sol"],
                max_tokens: 400000,
                max_tokens_min: 10000,
                max_tokens_max: 2000000,
                token_limit_enforced: false,
                reasoning: "standard",
                reasoning_options: ["standard", "extended"],
                instructions: "Synthesize.",
              },
            ],
          }}
          onSubmit={onSubmit}
        />
      </I18nContext.Provider>,
    );

    expect(screen.getByText("second position")).not.toBeNull();
    expect(screen.queryByText("Token budget")).toBeNull();
    expect(screen.getAllByText("Metered, no cap")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Light debate" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByText("second position")).toBeNull();
    expect(screen.getByText("first position")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Start meeting" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({
      option_id: "light",
      value: "light",
    });
  });
});
