"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  ChevronRightIcon,
  FolderIcon,
  FolderOpenIcon,
  Link2Icon,
  LoaderIcon,
  RefreshCwIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip } from "@/components/workspace/tooltip";
import {
  clearThreadProject,
  formatFileSize,
  updateThreadProject,
  useProjectCandidates,
  useThreadProject,
  useWorkspaceFiles,
  WORKSPACE_FILES_ROOT,
  type WorkspaceFileEntry,
} from "@/core/files";
import { useI18n } from "@/core/i18n/hooks";
import { getFileIcon } from "@/core/utils/files";
import { cn } from "@/lib/utils";

import { useArtifacts } from "../artifacts/context";

import { useFilesPanel } from "./context";

const INDENT_PX = 14;
const NO_PROJECT_VALUE = "__none__";

function ProjectPicker({ threadId }: { threadId: string }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const { data: projectData } = useThreadProject({ threadId });
  const { data: candidatesData } = useProjectCandidates({ threadId });
  const [updating, setUpdating] = useState(false);

  const linkedPath = projectData?.project?.container_path ?? NO_PROJECT_VALUE;
  const candidates = candidatesData?.candidates ?? [];
  // Keep a stale link selectable so the UI shows what is currently stored
  // even if the folder vanished from the candidate scan.
  const options =
    projectData?.project &&
    !candidates.some(
      (candidate) =>
        candidate.container_path === projectData.project?.container_path,
    )
      ? [projectData.project, ...candidates]
      : candidates;

  const handleChange = useCallback(
    async (value: string) => {
      setUpdating(true);
      try {
        if (value === NO_PROJECT_VALUE) {
          await clearThreadProject(threadId);
        } else {
          await updateThreadProject(threadId, value);
        }
        await queryClient.invalidateQueries({
          queryKey: ["thread-project", threadId],
        });
        await queryClient.invalidateQueries({
          queryKey: ["workspace-files", threadId],
        });
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : t.workspaceFiles.linkFailed,
        );
      } finally {
        setUpdating(false);
      }
    },
    [queryClient, threadId, t.workspaceFiles.linkFailed],
  );

  return (
    <div className="flex shrink-0 items-center gap-2 pb-2">
      <span className="text-muted-foreground shrink-0 text-xs font-medium">
        {t.workspaceFiles.project}
      </span>
      <Select
        value={linkedPath}
        disabled={updating}
        onValueChange={(value) => void handleChange(value)}
      >
        <SelectTrigger className="h-7 min-w-0 flex-1 text-xs" size="sm">
          <SelectValue placeholder={t.workspaceFiles.noProject} />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={NO_PROJECT_VALUE}>
              {t.workspaceFiles.noProject}
            </SelectItem>
            {options.map((candidate) => (
              <SelectItem
                key={candidate.container_path}
                value={candidate.container_path}
              >
                {candidate.container_path}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </div>
  );
}

function TreeRow({
  depth,
  onClick,
  title,
  children,
}: {
  depth: number;
  onClick: () => void;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className="hover:bg-accent hover:text-accent-foreground focus-visible:ring-ring flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-sm outline-none focus-visible:ring-2"
      style={{ paddingLeft: `${8 + depth * INDENT_PX}px` }}
      title={title}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function FileTreeNode({
  threadId,
  entry,
  depth,
  onFileClick,
}: {
  threadId: string;
  entry: WorkspaceFileEntry;
  depth: number;
  onFileClick: (entry: WorkspaceFileEntry) => void;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);

  if (entry.type === "directory") {
    return (
      <div>
        <TreeRow
          depth={depth}
          title={entry.is_symlink ? `${entry.name} (${t.workspaceFiles.symlink})` : entry.name}
          onClick={() => setExpanded((value) => !value)}
        >
          <ChevronRightIcon
            className={cn(
              "text-muted-foreground size-3.5 shrink-0 transition-transform",
              expanded && "rotate-90",
            )}
          />
          {expanded ? (
            <FolderOpenIcon className="size-4 shrink-0 text-amber-500" />
          ) : (
            <FolderIcon className="size-4 shrink-0 text-amber-500" />
          )}
          <span className="min-w-0 truncate">{entry.name}</span>
          {entry.is_symlink && (
            <Link2Icon className="text-muted-foreground size-3 shrink-0" />
          )}
        </TreeRow>
        {expanded && (
          <DirectoryListing
            threadId={threadId}
            path={entry.path}
            depth={depth + 1}
            onFileClick={onFileClick}
          />
        )}
      </div>
    );
  }

  return (
    <TreeRow
      depth={depth}
      title={entry.is_symlink ? `${entry.name} (${t.workspaceFiles.symlink})` : entry.name}
      onClick={() => onFileClick(entry)}
    >
      <span className="size-3.5 shrink-0" aria-hidden />
      {getFileIcon(entry.name, "size-4 shrink-0")}
      <span className="min-w-0 truncate">{entry.name}</span>
      {entry.is_symlink && (
        <Link2Icon className="text-muted-foreground size-3 shrink-0" />
      )}
      <span className="text-muted-foreground ml-auto shrink-0 pl-2 text-xs tabular-nums">
        {formatFileSize(entry.size)}
      </span>
    </TreeRow>
  );
}

function DirectoryListing({
  threadId,
  path,
  depth,
  onFileClick,
}: {
  threadId: string;
  path: string;
  depth: number;
  onFileClick: (entry: WorkspaceFileEntry) => void;
}) {
  const { t } = useI18n();
  const { data, isLoading, isError, refetch } = useWorkspaceFiles({
    threadId,
    path,
  });

  if (isLoading) {
    return (
      <div
        className="text-muted-foreground flex items-center gap-2 px-2 py-1 text-sm"
        style={{ paddingLeft: `${8 + depth * INDENT_PX}px` }}
      >
        <LoaderIcon className="size-3.5 animate-spin" />
        {t.common.loading}
      </div>
    );
  }

  if (isError) {
    return (
      <div
        className="text-muted-foreground flex items-center gap-2 px-2 py-1 text-sm"
        style={{ paddingLeft: `${8 + depth * INDENT_PX}px` }}
      >
        <span>{t.workspaceFiles.loadFailed}</span>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-xs"
          onClick={() => void refetch()}
        >
          {t.workspaceFiles.retry}
        </Button>
      </div>
    );
  }

  if (!data || data.entries.length === 0) {
    return (
      <div
        className="text-muted-foreground px-2 py-1 text-sm italic"
        style={{ paddingLeft: `${8 + depth * INDENT_PX}px` }}
      >
        {t.workspaceFiles.empty}
      </div>
    );
  }

  return (
    <div>
      {data.entries.map((entry) => (
        <FileTreeNode
          key={entry.path}
          threadId={threadId}
          entry={entry}
          depth={depth}
          onFileClick={onFileClick}
        />
      ))}
      {data.truncated && (
        <div
          className="text-muted-foreground px-2 py-1 text-xs italic"
          style={{ paddingLeft: `${8 + depth * INDENT_PX}px` }}
        >
          {t.workspaceFiles.truncated}
        </div>
      )}
    </div>
  );
}

export function FileTreePanel({
  threadId,
  className,
}: {
  threadId: string;
  className?: string;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const filesPanel = useFilesPanel();
  const { select: selectArtifact, setOpen: setArtifactsOpen } = useArtifacts();

  const handleFileClick = useCallback(
    (entry: WorkspaceFileEntry) => {
      // Hand off to the existing artifacts viewer: it already handles code
      // highlighting, HTML/image/audio/video preview, download, and copy.
      selectArtifact(entry.path);
      filesPanel.setOpen(false);
      setArtifactsOpen(true);
    },
    [selectArtifact, filesPanel, setArtifactsOpen],
  );

  const handleRefresh = useCallback(() => {
    void queryClient.invalidateQueries({
      queryKey: ["workspace-files", threadId],
    });
  }, [queryClient, threadId]);

  return (
    <div className={cn("flex size-full min-h-0 flex-col", className)}>
      <header className="flex shrink-0 items-center gap-1 pb-2">
        <h2 className="min-w-0 flex-1 truncate text-lg font-medium">
          {t.workspaceFiles.title}
        </h2>
        <Tooltip content={t.workspaceFiles.refresh}>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={t.workspaceFiles.refresh}
            onClick={handleRefresh}
          >
            <RefreshCwIcon />
          </Button>
        </Tooltip>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={t.common.cancel}
          onClick={() => filesPanel.setOpen(false)}
        >
          <XIcon />
        </Button>
      </header>
      <ProjectPicker threadId={threadId} />
      <main className="min-h-0 grow overflow-y-auto pr-1">
        <DirectoryListing
          threadId={threadId}
          path={WORKSPACE_FILES_ROOT}
          depth={0}
          onFileClick={handleFileClick}
        />
      </main>
    </div>
  );
}
