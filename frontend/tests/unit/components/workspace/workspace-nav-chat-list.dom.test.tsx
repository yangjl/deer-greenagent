import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

rs.mock("next/navigation", () => ({
  usePathname: () => "/workspace/test2/new",
  useRouter: () => ({ replace: rs.fn() }),
}));

rs.mock("@/hooks/use-mobile", () => ({
  useIsMobile: () => false,
}));

rs.mock("@/core/agents", () => ({
  useAgentsApiEnabled: () => ({ enabled: true }),
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      sidebar: {
        agents: "Agents",
        agentsDisabledTooltip: "Agents are disabled",
        chats: "Chats",
        newChat: "New chat",
        scheduledTasks: "Scheduled tasks",
      },
      settings: {
        sections: {
          skills: "Skills",
        },
      },
    },
  }),
}));

rs.mock("@/core/workspaces", () => ({
  pathOfProject: (slug: string) => `/workspace/${slug}`,
  useActiveWorkspaceProjects: () => ({
    workspaceId: "workspace-1",
    projects: {
      data: [
        {
          id: "project-1",
          name: "test2",
          slug: "test2",
        },
      ],
    },
  }),
  useArchiveProject: () => ({
    isPending: false,
    mutateAsync: rs.fn(),
  }),
}));

rs.mock("@/components/workspace/projects/create-dialogs", () => ({
  CreateProjectDialog: () => null,
}));

import { SidebarProvider, useSidebar } from "@/components/ui/sidebar";
import { WorkspaceNavChatList } from "@/components/workspace/workspace-nav-chat-list";

function ExpandSidebar() {
  const { setOpen } = useSidebar();
  return (
    <button type="button" onClick={() => setOpen(true)}>
      Expand sidebar for test
    </button>
  );
}

afterEach(cleanup);

describe("WorkspaceNavChatList", () => {
  it("hides project overflow actions while the first rail is collapsed", () => {
    render(
      <SidebarProvider defaultOpen={false}>
        <WorkspaceNavChatList selectedFile={null} onFileOpen={rs.fn()} />
        <ExpandSidebar />
      </SidebarProvider>,
    );

    expect(screen.getByRole("link", { name: "test2" })).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Skills" }).getAttribute("href"),
    ).toBe("/workspace/skills");
    expect(
      screen.queryByRole("button", { name: "Project actions for test2" }),
    ).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Expand sidebar for test" }),
    );

    expect(
      screen.getByRole("button", { name: "Project actions for test2" }),
    ).toBeTruthy();
  });

  it("offers New chat and Chats entries underneath the projects group", () => {
    render(
      <SidebarProvider defaultOpen>
        <WorkspaceNavChatList selectedFile={null} onFileOpen={rs.fn()} />
      </SidebarProvider>,
    );

    expect(
      screen.getByRole("link", { name: "New chat" }).getAttribute("href"),
    ).toBe("/workspace/chats/new");
    expect(
      screen.getByRole("link", { name: "Chats" }).getAttribute("href"),
    ).toBe("/workspace/chats");

    // Both entries belong to the group that follows the project folders, so a
    // project row must precede them in document order.
    const links = screen.getAllByRole("link");
    const indexOf = (name: string) =>
      links.findIndex((link) => link.textContent?.trim() === name);
    expect(indexOf("test2")).toBeGreaterThanOrEqual(0);
    expect(indexOf("New chat")).toBeGreaterThan(indexOf("test2"));
    expect(indexOf("Chats")).toBeGreaterThan(indexOf("New chat"));
  });

  it("marks New chat active only on the new-conversation route", () => {
    render(
      <SidebarProvider defaultOpen>
        <WorkspaceNavChatList selectedFile={null} onFileOpen={rs.fn()} />
      </SidebarProvider>,
    );

    // usePathname is mocked to a project route, so neither chat entry is the
    // current page and neither may claim the active treatment.
    expect(
      screen
        .getByRole("link", { name: "New chat" })
        .getAttribute("data-active"),
    ).toBe("false");
    expect(
      screen.getByRole("link", { name: "Chats" }).getAttribute("data-active"),
    ).toBe("false");
  });
});
