"use client";

import {
  Code2Icon,
  CopyIcon,
  DownloadIcon,
  EyeIcon,
  FileIcon,
  LoaderCircleIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ArtifactFilePreview } from "@/components/workspace/artifacts/artifact-file-detail";
import { CodeEditor } from "@/components/workspace/code-editor";
import { Tooltip } from "@/components/workspace/tooltip";
import { writeTextToClipboard } from "@/core/clipboard";
import {
  canBrowserPreviewFile,
  checkCodeFile,
  getFileExtensionDisplayName,
  getFileIcon,
  getFileName,
} from "@/core/utils/files";
import { urlOfProjectFile, useProjectFileContent } from "@/core/workspaces";

export function ProjectFilePreview({
  projectId,
  path,
  open,
  onOpenChange,
}: {
  projectId: string;
  path: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const filename = path ? getFileName(path) : "";
  const fileKind = useMemo(() => (path ? checkCodeFile(path) : null), [path]);
  const language = fileKind?.language ?? null;
  const canRenderDocument = language === "markdown" || language === "html";
  const [viewMode, setViewMode] = useState<"code" | "preview">(
    canRenderDocument ? "preview" : "code",
  );
  const fileUrl = path ? urlOfProjectFile({ projectId, path }) : undefined;
  const downloadUrl = path
    ? urlOfProjectFile({ projectId, path, download: true })
    : undefined;
  const fileContent = useProjectFileContent(projectId, path, {
    enabled: open && Boolean(fileKind?.isCodeFile),
  });

  useEffect(() => {
    setViewMode(canRenderDocument ? "preview" : "code");
  }, [canRenderDocument, path]);

  const content = fileContent.data?.content ?? "";
  const copyContent = async () => {
    if (!(await writeTextToClipboard(content))) {
      toast.error("Failed to copy file contents.");
      return;
    }
    toast.success("File contents copied.");
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[min(94vw,1040px)] max-w-none gap-0 p-0 sm:max-w-[1040px]">
        <SheetHeader className="border-border min-h-[88px] border-b px-5 py-4 pr-12">
          <div className="flex min-w-0 items-start justify-between gap-4">
            <div className="flex min-w-0 items-start gap-3">
              <div className="bg-muted/60 mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md">
                {path ? (
                  getFileIcon(path, "text-muted-foreground size-4")
                ) : (
                  <FileIcon className="text-muted-foreground size-4" />
                )}
              </div>
              <div className="min-w-0">
                <SheetTitle className="truncate text-base">
                  {filename || "Project file"}
                </SheetTitle>
                <SheetDescription
                  className="mt-1 truncate font-mono text-xs"
                  title={path ?? undefined}
                >
                  {path}
                </SheetDescription>
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-1">
              {canRenderDocument && (
                <ToggleGroup
                  type="single"
                  variant="outline"
                  size="sm"
                  value={viewMode}
                  onValueChange={(value) => {
                    if (value) {
                      setViewMode(value as "code" | "preview");
                    }
                  }}
                >
                  <ToggleGroupItem value="code" aria-label="View source">
                    <Code2Icon />
                  </ToggleGroupItem>
                  <ToggleGroupItem value="preview" aria-label="Preview file">
                    <EyeIcon />
                  </ToggleGroupItem>
                </ToggleGroup>
              )}
              {fileKind?.isCodeFile && (
                <Tooltip content="Copy contents">
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    aria-label="Copy file contents"
                    disabled={!fileContent.data}
                    onClick={() => void copyContent()}
                  >
                    <CopyIcon />
                  </Button>
                </Tooltip>
              )}
              {downloadUrl && (
                <Tooltip content="Download file">
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label="Download file"
                    asChild
                  >
                    <a href={downloadUrl}>
                      <DownloadIcon />
                    </a>
                  </Button>
                </Tooltip>
              )}
            </div>
          </div>
        </SheetHeader>

        <div className="bg-muted/10 min-h-0 flex-1 overflow-hidden">
          {fileKind?.isCodeFile && fileContent.isLoading && (
            <div className="text-muted-foreground flex size-full items-center justify-center gap-2 text-sm">
              <LoaderCircleIcon className="size-4 animate-spin" />
              Loading file…
            </div>
          )}
          {fileKind?.isCodeFile && fileContent.error && (
            <div className="flex size-full items-center justify-center p-8 text-center">
              <div>
                <p className="font-medium">Couldn’t open this file.</p>
                <p className="text-muted-foreground mt-1 text-sm">
                  {fileContent.error instanceof Error
                    ? fileContent.error.message
                    : "The project file could not be loaded."}
                </p>
              </div>
            </div>
          )}
          {fileKind?.isCodeFile && fileContent.data && viewMode === "code" && (
            <CodeEditor
              className="size-full resize-none rounded-none border-none"
              value={content}
              readonly
            />
          )}
          {fileKind?.isCodeFile &&
            fileContent.data &&
            viewMode === "preview" &&
            canRenderDocument && (
              <ArtifactFilePreview
                content={content}
                language={language}
                scrollKey={`project:${projectId}:${path}`}
                url={fileUrl}
                resolveArtifactLinks={false}
              />
            )}
          {path && !fileKind?.isCodeFile && canBrowserPreviewFile(path) && (
            <iframe
              title={filename}
              className="size-full border-0"
              sandbox=""
              src={fileUrl}
            />
          )}
          {path && !fileKind?.isCodeFile && !canBrowserPreviewFile(path) && (
            <div className="flex size-full items-center justify-center p-8">
              <div className="flex max-w-sm flex-col items-center gap-4 text-center">
                <div className="text-muted-foreground">
                  {getFileIcon(path, "size-12")}
                </div>
                <div>
                  <div className="font-medium break-all">{filename}</div>
                  <div className="text-muted-foreground mt-1 text-sm">
                    {getFileExtensionDisplayName(path)} file
                  </div>
                </div>
                <p className="text-muted-foreground text-sm">
                  This file type can’t be displayed in the browser.
                </p>
                {downloadUrl && (
                  <Button asChild>
                    <a href={downloadUrl}>
                      <DownloadIcon className="size-4" />
                      Download
                    </a>
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
