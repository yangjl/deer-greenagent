"use client";

import { FolderTreeIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/workspace/tooltip";
import { useI18n } from "@/core/i18n/hooks";

import { useArtifacts } from "../artifacts/context";
import { useMaybeBrowserView } from "../browser-view";
import { useMaybeSidecar } from "../sidecar/context";

import { useMaybeFilesPanel } from "./context";

export const FilesTrigger = () => {
  const { t } = useI18n();
  const filesPanel = useMaybeFilesPanel();
  const { setOpen: setArtifactsOpen } = useArtifacts();
  const sidecar = useMaybeSidecar();
  const browserView = useMaybeBrowserView();

  if (!filesPanel) {
    return null;
  }
  return (
    <Tooltip content={t.workspaceFiles.show}>
      <Button
        aria-label={t.workspaceFiles.show}
        className="text-muted-foreground hover:text-foreground"
        variant="ghost"
        data-testid="files-trigger"
        onClick={() => {
          sidecar?.close();
          browserView?.close();
          setArtifactsOpen(false);
          filesPanel.setOpen(true);
        }}
      >
        <FolderTreeIcon />
        <span className="hidden sm:inline">{t.workspaceFiles.title}</span>
      </Button>
    </Tooltip>
  );
};
