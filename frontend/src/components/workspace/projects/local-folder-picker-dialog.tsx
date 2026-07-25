"use client";

import {
  ChevronLeft,
  ChevronRight,
  Folder,
  FolderOpen,
  LoaderCircle,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useProjectFolders } from "@/core/workspaces";

interface LocalFolderPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialPath?: string;
  title: string;
  description: string;
  onSelect: (path: string) => void;
}

export function LocalFolderPickerDialog({
  open,
  onOpenChange,
  initialPath,
  title,
  description,
  onSelect,
}: LocalFolderPickerDialogProps) {
  const [currentPath, setCurrentPath] = useState<string | undefined>(
    initialPath,
  );
  const folders = useProjectFolders(currentPath, { enabled: open });

  useEffect(() => {
    if (open) {
      setCurrentPath(initialPath?.trim() ? initialPath : undefined);
    }
  }, [initialPath, open]);

  const listing = folders.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {listing && (
          <div className="flex flex-wrap gap-1.5">
            {listing.allowed_roots.map((root) => (
              <Button
                key={root}
                type="button"
                variant="outline"
                size="sm"
                className="max-w-full justify-start font-mono text-xs"
                onClick={() => setCurrentPath(root)}
                title={root}
              >
                <Folder className="size-3.5" />
                <span className="truncate">{root}</span>
              </Button>
            ))}
          </div>
        )}

        <div className="bg-muted/50 flex min-w-0 items-center gap-2 rounded-md px-2 py-2">
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            disabled={!listing?.parent_path}
            onClick={() =>
              listing?.parent_path && setCurrentPath(listing.parent_path)
            }
            aria-label="Open parent folder"
          >
            <ChevronLeft />
          </Button>
          <FolderOpen className="text-muted-foreground size-4 shrink-0" />
          <span
            className="min-w-0 truncate font-mono text-xs"
            title={listing?.current_path}
          >
            {listing?.current_path ?? currentPath ?? "Loading folders…"}
          </span>
        </div>

        <ScrollArea className="h-64 border-y">
          <div className="py-1">
            {folders.isLoading && (
              <div className="text-muted-foreground flex h-40 items-center justify-center gap-2 text-sm">
                <LoaderCircle className="size-4 animate-spin" />
                Loading folders
              </div>
            )}
            {folders.error && (
              <p className="text-destructive px-3 py-6 text-sm">
                {folders.error.message}
              </p>
            )}
            {listing?.directories.map((directory) => (
              <button
                key={directory.path}
                type="button"
                className="hover:bg-muted focus-visible:ring-ring flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors focus-visible:ring-2 focus-visible:outline-none"
                onClick={() => setCurrentPath(directory.path)}
              >
                <Folder className="size-4 shrink-0 text-amber-600" />
                <span className="min-w-0 flex-1 truncate">
                  {directory.name}
                </span>
                <ChevronRight className="text-muted-foreground size-4" />
              </button>
            ))}
            {listing?.directories.length === 0 && (
              <p className="text-muted-foreground px-3 py-12 text-center text-sm">
                No subfolders here.
              </p>
            )}
          </div>
        </ScrollArea>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!listing?.current_path}
            onClick={() => {
              if (listing?.current_path) {
                onSelect(listing.current_path);
                onOpenChange(false);
              }
            }}
          >
            Use this folder
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
