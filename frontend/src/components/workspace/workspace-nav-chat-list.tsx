"use client";

import {
  BotIcon,
  CalendarClock,
  ChevronRight,
  FolderClosed,
  FolderOpen,
  MessagesSquare,
  Plus,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAgentsApiEnabled } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { pathOfProject, useActiveWorkspaceProjects } from "@/core/workspaces";
import { cn } from "@/lib/utils";

import { ProjectFiles } from "./project-rail/project-files";
import { CreateProjectDialog } from "./projects/create-dialogs";

export type SelectedProjectFile = {
  projectId: string;
  path: string;
};

/**
 * Project-first navigation (foundation demo): the active workspace's projects
 * are listed as folders and conversations live only inside a project, so
 * there are no independent Chats/Inbox entries.
 */
export function WorkspaceNavChatList({
  selectedFile,
  onFileOpen,
}: {
  selectedFile: SelectedProjectFile | null;
  onFileOpen: (file: SelectedProjectFile) => void;
}) {
  const { t } = useI18n();
  const pathname = usePathname();
  const { enabled: agentsEnabled } = useAgentsApiEnabled();
  const { workspaceId, projects } = useActiveWorkspaceProjects();
  const { state: sidebarState } = useSidebar();
  const [projectOpen, setProjectOpen] = useState(false);
  // Projects whose folder tree is expanded inline in the sidebar.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set(),
  );

  function toggleExpanded(projectId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(projectId)) {
        next.delete(projectId);
      } else {
        next.add(projectId);
      }
      return next;
    });
  }

  return (
    <>
      <SidebarGroup className="pt-1">
        {/* The group holds one entry per project; `+` is the only action. */}
        <SidebarGroupLabel className="text-muted-foreground/70 tracking-widest uppercase">
          <span className="flex-1">Projects</span>
          <button
            type="button"
            aria-label="New project"
            title="New project"
            disabled={!workspaceId}
            className="hover:text-foreground transition-colors disabled:opacity-40"
            onClick={() => setProjectOpen(true)}
          >
            <Plus className="size-4" />
          </button>
        </SidebarGroupLabel>
        <SidebarMenu>
          {/* The chevron expands the folder tree in place; the name opens
              the project workspace. Two targets, one row. */}
          {projects.data?.map((project) => {
            const active =
              pathname === pathOfProject(project.slug) ||
              pathname.startsWith(`${pathOfProject(project.slug)}/`);
            const isExpanded = expanded.has(project.id);
            const FolderIcon = active || isExpanded ? FolderOpen : FolderClosed;
            return (
              <SidebarMenuItem key={project.id}>
                {sidebarState === "collapsed" ? (
                  <SidebarMenuButton
                    isActive={active}
                    tooltip={project.name}
                    asChild
                  >
                    <Link
                      className="text-muted-foreground"
                      href={pathOfProject(project.slug)}
                    >
                      <FolderIcon
                        className={
                          active
                            ? "text-emerald-700 dark:text-emerald-400"
                            : undefined
                        }
                      />
                      <span className="truncate">{project.name}</span>
                    </Link>
                  </SidebarMenuButton>
                ) : (
                  <div className="flex items-center">
                    <button
                      type="button"
                      aria-label={
                        isExpanded
                          ? `Collapse ${project.name} files`
                          : `Show ${project.name} files`
                      }
                      aria-expanded={isExpanded}
                      onClick={() => toggleExpanded(project.id)}
                      className="text-muted-foreground hover:text-foreground shrink-0 p-1 transition-colors"
                    >
                      <ChevronRight
                        className={cn(
                          "size-3 transition-transform",
                          isExpanded && "rotate-90",
                        )}
                      />
                    </button>
                    <SidebarMenuButton
                      isActive={active}
                      tooltip={project.name}
                      asChild
                    >
                      <Link
                        className="text-muted-foreground"
                        href={pathOfProject(project.slug)}
                      >
                        <FolderIcon
                          className={
                            active || isExpanded
                              ? "text-emerald-700 dark:text-emerald-400"
                              : undefined
                          }
                        />
                        <span className="truncate">{project.name}</span>
                      </Link>
                    </SidebarMenuButton>
                  </div>
                )}
                {sidebarState === "expanded" && isExpanded && (
                  <div className="border-border/60 mt-0.5 ml-3 border-l pl-1">
                    <ProjectFiles
                      projectId={project.id}
                      selectedPath={
                        selectedFile?.projectId === project.id
                          ? selectedFile.path
                          : null
                      }
                      onFileOpen={(path) =>
                        onFileOpen({ projectId: project.id, path })
                      }
                    />
                  </div>
                )}
              </SidebarMenuItem>
            );
          })}
          {projects.data?.length === 0 && (
            <SidebarMenuItem>
              <SidebarMenuButton
                className="text-muted-foreground/70"
                onClick={() => setProjectOpen(true)}
              >
                <Plus />
                <span>Create a project</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          )}
        </SidebarMenu>
      </SidebarGroup>
      {workspaceId && (
        <CreateProjectDialog
          open={projectOpen}
          onOpenChange={setProjectOpen}
          workspaceId={workspaceId}
        />
      )}
      <SidebarGroup>
        <SidebarMenu>
          <SidebarMenuItem>
            {/* Legacy projectless conversations keep their existing route. */}
            <SidebarMenuButton
              isActive={pathname.startsWith("/workspace/chats")}
              asChild
            >
              <Link className="text-muted-foreground" href="/workspace/chats">
                <MessagesSquare />
                <span>Unfiled chats</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            {agentsEnabled ? (
              <SidebarMenuButton
                isActive={pathname.startsWith("/workspace/agents")}
                asChild
              >
                <Link
                  className="text-muted-foreground"
                  href="/workspace/agents"
                >
                  <BotIcon />
                  <span>{t.sidebar.agents}</span>
                </Link>
              </SidebarMenuButton>
            ) : (
              // Disabled: aria-disabled drives the sidebar CVA to suppress
              // pointer events on the button, so wrap it in a hoverable span
              // that still surfaces the "feature not enabled" tooltip for mouse
              // users. The button stays in the tab order (no tabIndex={-1}) and
              // is wired via aria-describedby to a visually-hidden reason, so
              // keyboard and screen-reader users also learn why it is disabled.
              <Tooltip>
                <TooltipTrigger asChild>
                  {/* cursor-not-allowed lives on the span (the element that
                      still receives pointer events), not the inert button. */}
                  <span className="block w-full cursor-not-allowed">
                    <SidebarMenuButton
                      className="text-muted-foreground/50"
                      aria-disabled
                      aria-describedby="agents-disabled-reason"
                    >
                      <BotIcon />
                      <span>{t.sidebar.agents}</span>
                    </SidebarMenuButton>
                    <span id="agents-disabled-reason" className="sr-only">
                      {t.sidebar.agentsDisabledTooltip}
                    </span>
                  </span>
                </TooltipTrigger>
                <TooltipContent side="right">
                  {t.sidebar.agentsDisabledTooltip}
                </TooltipContent>
              </Tooltip>
            )}
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              isActive={pathname.startsWith("/workspace/scheduled-tasks")}
              asChild
            >
              <Link
                className="text-muted-foreground"
                href="/workspace/scheduled-tasks"
              >
                <CalendarClock />
                <span>{t.sidebar.scheduledTasks}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarGroup>
    </>
  );
}
