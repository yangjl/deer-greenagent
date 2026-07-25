"use client";

import { ChevronRight, FileIcon, Folder, FolderOpen } from "lucide-react";
import { useState } from "react";

import { WORKSPACE_FILES_ROOT } from "@/core/files";
import { useProjectFiles } from "@/core/workspaces";
import { cn } from "@/lib/utils";

const INDENT_PX = 12;

function FileRow({
  name,
  depth,
  isDirectory,
  expanded,
  onToggle,
  onOpen,
  selected,
}: {
  name: string;
  depth: number;
  isDirectory: boolean;
  expanded?: boolean;
  onToggle?: () => void;
  onOpen?: () => void;
  selected?: boolean;
}) {
  const Icon = isDirectory ? (expanded ? FolderOpen : Folder) : FileIcon;
  const content = (
    <>
      {isDirectory ? (
        <ChevronRight
          className={cn(
            "size-3 shrink-0 transition-transform",
            expanded && "rotate-90",
          )}
        />
      ) : (
        <span className="size-3 shrink-0" />
      )}
      <Icon className="text-muted-foreground size-3.5 shrink-0" />
      <span className="min-w-0 truncate">{name}</span>
    </>
  );
  const className =
    "focus-visible:ring-ring flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs outline-none transition-colors hover:bg-muted/60 focus-visible:ring-2";
  const style = { paddingLeft: 8 + depth * INDENT_PX };

  return isDirectory ? (
    <button
      type="button"
      onClick={onToggle}
      className={className}
      style={style}
    >
      {content}
    </button>
  ) : (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        className,
        selected && "bg-muted text-foreground font-medium",
      )}
      style={style}
    >
      {content}
    </button>
  );
}

function FileBranch({
  projectId,
  path,
  depth,
  selectedPath,
  onFileOpen,
}: {
  projectId: string;
  path: string;
  depth: number;
  selectedPath: string | null;
  onFileOpen: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const { data, isLoading, error } = useProjectFiles(projectId, path);

  if (isLoading) {
    return (
      <div
        className="text-muted-foreground px-2 py-1 text-xs"
        style={{ paddingLeft: 8 + depth * INDENT_PX }}
      >
        Loading…
      </div>
    );
  }
  if (error) {
    return (
      <div
        className="text-muted-foreground px-2 py-1 text-xs"
        style={{ paddingLeft: 8 + depth * INDENT_PX }}
      >
        No files yet.
      </div>
    );
  }
  if (!data?.entries.length) {
    return (
      <div
        className="text-muted-foreground px-2 py-1 text-xs"
        style={{ paddingLeft: 8 + depth * INDENT_PX }}
      >
        {depth === 0
          ? "No files yet — work done in this project's conversations lands here."
          : "Empty."}
      </div>
    );
  }

  return (
    <>
      {data.entries.map((entry) => {
        const isOpen = expanded.has(entry.path);
        return (
          <div key={entry.path}>
            <FileRow
              name={entry.name}
              depth={depth}
              isDirectory={entry.type === "directory"}
              expanded={isOpen}
              selected={selectedPath === entry.path}
              onToggle={() =>
                setExpanded((current) => {
                  const next = new Set(current);
                  if (next.has(entry.path)) {
                    next.delete(entry.path);
                  } else {
                    next.add(entry.path);
                  }
                  return next;
                })
              }
              onOpen={() => onFileOpen(entry.path)}
            />
            {entry.type === "directory" && isOpen && (
              <FileBranch
                projectId={projectId}
                path={entry.path}
                depth={depth + 1}
                selectedPath={selectedPath}
                onFileOpen={onFileOpen}
              />
            )}
          </div>
        );
      })}
    </>
  );
}

/**
 * The project's own folder tree. It is addressed by project id, so it renders
 * whether or not a conversation is open — every conversation in the project
 * reads and writes this same workspace.
 */
export function ProjectFiles({
  projectId,
  selectedPath,
  onFileOpen,
}: {
  projectId: string | null;
  selectedPath: string | null;
  onFileOpen: (path: string) => void;
}) {
  if (!projectId) {
    return (
      <div className="text-muted-foreground px-4 py-1 text-xs">Loading…</div>
    );
  }
  return (
    <>
      <div className="px-2">
        <FileBranch
          projectId={projectId}
          path={WORKSPACE_FILES_ROOT}
          depth={0}
          selectedPath={selectedPath}
          onFileOpen={onFileOpen}
        />
      </div>
    </>
  );
}
