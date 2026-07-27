import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

rs.mock("next/navigation", () => ({
  useRouter: () => ({ push: rs.fn() }),
}));

rs.mock("@/core/agents", () => ({
  useDeleteAgent: () => ({
    isPending: false,
    mutateAsync: rs.fn(),
  }),
  useAgentInventory: () => ({
    items: [
      {
        name: "lead_agent",
        description: "Coordinates work.",
        kind: "agent",
        origin: "builtin",
        model: null,
        tool_groups: null,
        tools: null,
        skills: null,
        model_settings: null,
        thinking_enabled: null,
        reasoning_effort: null,
        can_chat: true,
        can_manage: false,
      },
      {
        name: "general-purpose",
        description: "Handles delegated work.",
        kind: "subagent",
        origin: "builtin",
        model: "inherit",
        tool_groups: null,
        tools: null,
        skills: null,
        model_settings: null,
        thinking_enabled: null,
        reasoning_effort: null,
        can_chat: false,
        can_manage: false,
      },
      {
        name: "field-researcher",
        description: "Reviews field observations.",
        kind: "agent",
        origin: "custom",
        model: null,
        tool_groups: null,
        tools: null,
        skills: null,
        model_settings: null,
        thinking_enabled: null,
        reasoning_effort: null,
        can_chat: true,
        can_manage: true,
      },
      {
        name: "trial-auditor",
        description: "Audits trial data.",
        kind: "subagent",
        origin: "custom",
        model: "inherit",
        tool_groups: null,
        tools: ["read_file"],
        skills: null,
        model_settings: null,
        thinking_enabled: null,
        reasoning_effort: null,
        can_chat: false,
        can_manage: false,
      },
    ],
    isLoading: false,
    error: null,
  }),
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { loading: "Loading" },
      agents: {
        title: "Agents",
        description: "View built-in and custom agents.",
        newAgent: "New Agent",
        builtinTab: "Built-in",
        customTab: "Custom",
        builtinDescription: "Agents included with GreenAgent.",
        customDescription: "Agents configured for this workspace.",
        emptyTitle: "No custom agents yet",
        emptyDescription: "Create your first custom agent.",
        inventoryError: "Could not load agents.",
        chat: "Chat",
        delegatedOnly: "Used through delegation",
        agentType: "Agent",
        subagentType: "Subagent",
        builtinType: "Built-in",
        customType: "Custom",
        settings: "Model settings",
        delete: "Delete",
        deleteConfirm: "Delete this agent?",
        deleteSuccess: "Agent deleted",
      },
    },
  }),
}));

import { AgentGallery } from "@/components/workspace/agents/agent-gallery";

afterEach(cleanup);

describe("AgentGallery inventory tabs", () => {
  it("lists built-in entries first and custom agents in their own tab", () => {
    render(<AgentGallery />);

    expect(screen.getByText("lead_agent")).toBeTruthy();
    expect(screen.getByText("general-purpose")).toBeTruthy();
    expect(screen.queryByText("field-researcher")).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();

    const customTab = screen.getByRole("tab", { name: /Custom/ });
    fireEvent.pointerDown(customTab, { button: 0, ctrlKey: false });
    fireEvent.mouseDown(customTab, { button: 0, ctrlKey: false });
    fireEvent.click(customTab);

    expect(screen.getByText("field-researcher")).toBeTruthy();
    expect(screen.getByText("trial-auditor")).toBeTruthy();
    expect(screen.queryByText("lead_agent")).toBeNull();
  });
});
