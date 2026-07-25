"use client";

import { Folder, FolderOpen, FolderPlus, MapPin, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  buildProjectLocationFields,
  joinLocalPath,
  pathOfProject,
  projectFolderName,
  projectSlugOfName,
  useCreateProject,
  useCreateWorkspace,
  useProjectFolders,
  type ProjectLocationMode,
} from "@/core/workspaces";

import { LocalFolderPickerDialog } from "./local-folder-picker-dialog";

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Creates a breeding project and navigates into it. The slug becomes the URL
 * segment (`/workspace/<slug>`), so it is guarded against shadowing a real
 * workspace route.
 */
export function CreateProjectDialog({
  open,
  onOpenChange,
  workspaceId,
}: DialogProps & { workspaceId: string }) {
  const router = useRouter();
  const createProject = useCreateProject(workspaceId);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [locationMode, setLocationMode] =
    useState<ProjectLocationMode>("default");
  const [existingPath, setExistingPath] = useState("");
  const [fullPath, setFullPath] = useState("");
  const [parentPath, setParentPath] = useState("");
  const [folderName, setFolderName] = useState("");
  const [folderPickerTarget, setFolderPickerTarget] = useState<
    "existing" | "parent" | null
  >(null);
  const locationRoots = useProjectFolders(undefined, { enabled: open });
  const defaultRoot = locationRoots.data?.allowed_roots[0] ?? "";
  const effectiveFolderName = folderName || projectFolderName(name);
  const resolvedPath =
    locationMode === "default"
      ? defaultRoot
        ? joinLocalPath(defaultRoot, projectFolderName(name))
        : ""
      : locationMode === "existing"
        ? existingPath
        : locationMode === "full_path"
          ? fullPath
          : parentPath
            ? joinLocalPath(parentPath, effectiveFolderName)
            : "";
  const locationComplete =
    locationMode === "default" ||
    (locationMode === "existing" && Boolean(existingPath)) ||
    (locationMode === "full_path" && Boolean(fullPath.trim())) ||
    (locationMode === "new_under_parent" &&
      Boolean(parentPath) &&
      Boolean(effectiveFolderName));

  function resetProjectForm() {
    setName("");
    setDescription("");
    setLocationMode("default");
    setExistingPath("");
    setFullPath("");
    setParentPath("");
    setFolderName("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const created = await createProject.mutateAsync({
      name: name.trim(),
      slug: projectSlugOfName(name),
      description: description.trim() || undefined,
      crop_profile: "maize-v1",
      ...buildProjectLocationFields(locationMode, {
        existingPath,
        fullPath,
        parentPath,
        folderName: effectiveFolderName,
      }),
    });
    resetProjectForm();
    onOpenChange(false);
    router.push(pathOfProject(created.slug));
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[92svh] overflow-y-auto sm:max-w-2xl">
          <form onSubmit={submit}>
            <DialogHeader>
              <DialogTitle>New breeding project</DialogTitle>
              <DialogDescription>
                Name the project and choose the local folder shared with its AI
                coworkers.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-5 py-5">
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="project-name">
                  Name
                </label>
                <Input
                  id="project-name"
                  autoFocus
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Drought Resilience 2032"
                  required
                />
                {name.trim() && (
                  <p className="text-muted-foreground text-xs">
                    /workspace/{projectSlugOfName(name)}
                  </p>
                )}
              </div>

              <div className="space-y-3">
                <div>
                  <p className="text-sm font-medium">Local project folder</p>
                  <p className="text-muted-foreground mt-1 text-xs">
                    DeerFlow only offers writable paths configured for both
                    human and AI access.
                  </p>
                </div>
                <Tabs
                  value={locationMode}
                  onValueChange={(value) =>
                    setLocationMode(value as ProjectLocationMode)
                  }
                >
                  <TabsList className="grid h-auto w-full grid-cols-2 gap-1 sm:grid-cols-4">
                    <TabsTrigger
                      className="h-9"
                      value="default"
                      title="Create under the configured projects root"
                    >
                      <Sparkles />
                      Default
                    </TabsTrigger>
                    <TabsTrigger
                      className="h-9"
                      value="existing"
                      title="Adopt an existing local folder"
                    >
                      <FolderOpen />
                      Existing
                    </TabsTrigger>
                    <TabsTrigger
                      className="h-9"
                      value="full_path"
                      title="Enter an absolute existing or new path"
                    >
                      <MapPin />
                      Full path
                    </TabsTrigger>
                    <TabsTrigger
                      className="h-9"
                      value="new_under_parent"
                      title="Create a new folder inside a selected parent"
                    >
                      <FolderPlus />
                      New folder
                    </TabsTrigger>
                  </TabsList>
                </Tabs>

                {locationMode === "default" && (
                  <p className="text-muted-foreground text-sm">
                    Creates a folder named after the project under{" "}
                    <span className="text-foreground font-mono text-xs">
                      {defaultRoot || "the configured projects root"}
                    </span>
                    .
                  </p>
                )}

                {locationMode === "existing" && (
                  <div className="flex gap-2">
                    <Input
                      value={existingPath}
                      readOnly
                      placeholder="Choose an existing folder"
                      className="font-mono text-xs"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setFolderPickerTarget("existing")}
                    >
                      <Folder />
                      Browse
                    </Button>
                  </div>
                )}

                {locationMode === "full_path" && (
                  <div className="space-y-2">
                    <Input
                      value={fullPath}
                      onChange={(event) => setFullPath(event.target.value)}
                      placeholder="/Users/you/Documents/projects/G2F"
                      className="font-mono text-xs"
                    />
                    <p className="text-muted-foreground text-xs">
                      The path may already exist or DeerFlow will create it. It
                      must be inside one of the configured accessible roots.
                    </p>
                  </div>
                )}

                {locationMode === "new_under_parent" && (
                  <div className="grid gap-3 sm:grid-cols-[1fr_0.65fr]">
                    <div className="space-y-2">
                      <label
                        className="text-muted-foreground text-xs font-medium"
                        htmlFor="project-parent-path"
                      >
                        Parent folder
                      </label>
                      <div className="flex gap-2">
                        <Input
                          id="project-parent-path"
                          value={parentPath}
                          readOnly
                          placeholder="Choose a parent folder"
                          className="font-mono text-xs"
                        />
                        <Button
                          type="button"
                          variant="outline"
                          size="icon"
                          onClick={() => setFolderPickerTarget("parent")}
                          aria-label="Browse parent folders"
                        >
                          <Folder />
                        </Button>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <label
                        className="text-muted-foreground text-xs font-medium"
                        htmlFor="project-folder-name"
                      >
                        New folder name
                      </label>
                      <Input
                        id="project-folder-name"
                        value={folderName}
                        onChange={(event) => setFolderName(event.target.value)}
                        placeholder={projectFolderName(name)}
                      />
                    </div>
                  </div>
                )}

                <div className="bg-muted/45 rounded-md px-3 py-2.5">
                  <div className="flex items-start gap-2">
                    <FolderOpen className="mt-0.5 size-4 shrink-0 text-emerald-700 dark:text-emerald-400" />
                    <div className="min-w-0">
                      <p className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
                        Project destination
                      </p>
                      <p
                        className="mt-0.5 truncate font-mono text-xs"
                        title={resolvedPath}
                      >
                        {resolvedPath || "Choose a folder to continue"}
                      </p>
                      <p className="text-muted-foreground mt-1 text-xs">
                        The AI always works here through{" "}
                        <span className="font-mono">
                          /mnt/user-data/workspace
                        </span>
                        .
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              <div className="space-y-2">
                <label
                  className="text-sm font-medium"
                  htmlFor="project-description"
                >
                  Breeding objective
                </label>
                <Textarea
                  id="project-description"
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="Improve yield stability under managed water stress"
                />
              </div>
              {createProject.error && (
                <p className="text-destructive text-sm">
                  {createProject.error.message}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button
                type="submit"
                disabled={
                  createProject.isPending || !name.trim() || !locationComplete
                }
              >
                Create project
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <LocalFolderPickerDialog
        open={folderPickerTarget !== null}
        onOpenChange={(pickerOpen) => {
          if (!pickerOpen) {
            setFolderPickerTarget(null);
          }
        }}
        initialPath={
          folderPickerTarget === "existing"
            ? existingPath || defaultRoot
            : parentPath || defaultRoot
        }
        title={
          folderPickerTarget === "existing"
            ? "Choose an existing project folder"
            : "Choose where to create the project folder"
        }
        description="Only operator-approved writable folders are shown."
        onSelect={(path) => {
          if (folderPickerTarget === "existing") {
            setExistingPath(path);
          } else {
            setParentPath(path);
          }
        }}
      />
    </>
  );
}

/** First-run only: a workspace is the collaboration and security boundary. */
export function CreateWorkspaceDialog({ open, onOpenChange }: DialogProps) {
  const createWorkspace = useCreateWorkspace();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    await createWorkspace.mutateAsync({
      name: name.trim(),
      slug: projectSlugOfName(name),
      description: description.trim() || undefined,
    });
    setName("");
    setDescription("");
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>New workspace</DialogTitle>
            <DialogDescription>
              Creates a governed collaboration boundary. You become its owner.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-5">
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="workspace-name">
                Name
              </label>
              <Input
                id="workspace-name"
                autoFocus
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Maize Breeding Program"
                required
              />
            </div>
            <div className="space-y-2">
              <label
                className="text-sm font-medium"
                htmlFor="workspace-description"
              >
                Description
              </label>
              <Textarea
                id="workspace-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Shared program scope and breeding objective"
              />
            </div>
            {createWorkspace.error && (
              <p className="text-destructive text-sm">
                {createWorkspace.error.message}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={createWorkspace.isPending}>
              Create workspace
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
