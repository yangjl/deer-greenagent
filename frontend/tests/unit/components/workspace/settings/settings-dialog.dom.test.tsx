import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      settings: {
        title: "Settings",
        description: "Manage your workspace preferences.",
        sections: {
          account: "Account",
          appearance: "Appearance",
          notification: "Notifications",
          channels: "Channels",
          memory: "Memory",
          memoryScope: "Memory scope",
          dbtl: "DBTL",
          tools: "Tools",
          skills: "Skills",
          about: "About",
        },
      },
    },
  }),
}));

rs.mock("@/components/workspace/settings/about-settings-page", () => ({
  AboutSettingsPage: () => <div>About settings</div>,
}));
rs.mock("@/components/workspace/settings/account-settings-page", () => ({
  AccountSettingsPage: () => <div>Account settings</div>,
}));
rs.mock("@/components/workspace/settings/appearance-settings-page", () => ({
  AppearanceSettingsPage: () => <div>Appearance settings</div>,
}));
rs.mock("@/components/workspace/settings/channels-settings-page", () => ({
  ChannelsSettingsPage: () => <div>Channel settings</div>,
}));
rs.mock("@/components/workspace/settings/dbtl-readiness-settings-page", () => ({
  DbtlReadinessSettingsPage: () => <div>DBTL settings</div>,
}));
rs.mock("@/components/workspace/settings/memory-scope-settings-page", () => ({
  MemoryScopeSettingsPage: () => <div>Memory scope settings</div>,
}));
rs.mock("@/components/workspace/settings/memory-settings-page", () => ({
  MemorySettingsPage: () => <div>Memory settings</div>,
}));
rs.mock("@/components/workspace/settings/notification-settings-page", () => ({
  NotificationSettingsPage: () => <div>Notification settings</div>,
}));
rs.mock("@/components/workspace/settings/tool-settings-page", () => ({
  ToolSettingsPage: () => <div>Tool settings</div>,
}));

import { SettingsDialog } from "@/components/workspace/settings/settings-dialog";

afterEach(cleanup);

describe("SettingsDialog navigation", () => {
  it("does not include Skills after Skills moves to the first rail", () => {
    render(<SettingsDialog open onOpenChange={rs.fn()} />);

    expect(screen.getByRole("button", { name: "Tools" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Skills" })).toBeNull();
  });
});
