"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  Sidebar,
  SidebarHeader,
  SidebarContent,
  SidebarFooter,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";

import { WorkspaceChannelsList } from "./channels/workspace-channels-list";
import { ProjectFilePreview } from "./project-rail/project-file-preview";
import { WorkspaceHeader } from "./workspace-header";
import {
  type SelectedProjectFile,
  WorkspaceNavChatList,
} from "./workspace-nav-chat-list";
import { WorkspaceNavMenu } from "./workspace-nav-menu";

export function WorkspaceSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const { isMobile, open, openMobile, setOpen, setOpenMobile } = useSidebar();
  const [selectedFile, setSelectedFile] = useState<SelectedProjectFile | null>(
    null,
  );
  const restoreNavigationRef = useRef<"desktop" | "mobile" | null>(null);

  useEffect(() => {
    return () => {
      if (restoreNavigationRef.current === "desktop") {
        setOpen(true);
      } else if (restoreNavigationRef.current === "mobile") {
        setOpenMobile(true);
      }
    };
  }, [setOpen, setOpenMobile]);

  const openProjectFile = useCallback(
    (file: SelectedProjectFile) => {
      if (isMobile) {
        restoreNavigationRef.current = openMobile ? "mobile" : null;
        setSelectedFile(file);
        setOpenMobile(false);
        return;
      }
      restoreNavigationRef.current = open ? "desktop" : null;
      setSelectedFile(file);
      setOpen(false);
    },
    [isMobile, open, openMobile, setOpen, setOpenMobile],
  );

  const setPreviewOpen = useCallback(
    (previewOpen: boolean) => {
      if (previewOpen) {
        return;
      }
      setSelectedFile(null);
      if (restoreNavigationRef.current === "desktop") {
        setOpen(true);
      } else if (restoreNavigationRef.current === "mobile") {
        setOpenMobile(true);
      }
      restoreNavigationRef.current = null;
    },
    [setOpen, setOpenMobile],
  );

  return (
    <>
      <Sidebar variant="sidebar" collapsible="icon" {...props}>
        <SidebarHeader className="py-0">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent>
          <WorkspaceNavChatList
            selectedFile={selectedFile}
            onFileOpen={openProjectFile}
          />
          <WorkspaceChannelsList />
        </SidebarContent>
        <SidebarFooter>
          <WorkspaceNavMenu />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      {selectedFile && (
        <ProjectFilePreview
          projectId={selectedFile.projectId}
          path={selectedFile.path}
          open
          onOpenChange={setPreviewOpen}
        />
      )}
    </>
  );
}
