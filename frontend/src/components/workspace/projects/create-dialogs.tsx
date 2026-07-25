"use client";

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
import { Textarea } from "@/components/ui/textarea";
import {
  pathOfProject,
  projectSlugOfName,
  useCreateProject,
  useCreateWorkspace,
} from "@/core/workspaces";

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

  async function submit(event: FormEvent) {
    event.preventDefault();
    const created = await createProject.mutateAsync({
      name: name.trim(),
      slug: projectSlugOfName(name),
      description: description.trim() || undefined,
      crop_profile: "maize-v1",
    });
    setName("");
    setDescription("");
    onOpenChange(false);
    router.push(pathOfProject(created.slug));
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>New breeding project</DialogTitle>
            <DialogDescription>
              Starts with the generic breeding core and maize-v1 extensions.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-5">
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
            <Button type="submit" disabled={createProject.isPending}>
              Create project
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
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
