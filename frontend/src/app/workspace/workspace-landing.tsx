"use client";

import { Plus, Sprout } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  CreateProjectDialog,
  CreateWorkspaceDialog,
} from "@/components/workspace/projects/create-dialogs";
import { pathOfProject, useActiveWorkspaceProjects } from "@/core/workspaces";

/**
 * `/workspace` has no surface of its own: it forwards into the first project
 * and otherwise runs the first-run create flow.
 */
export function WorkspaceLanding() {
  const router = useRouter();
  const { workspaceId, workspaces, projects } = useActiveWorkspaceProjects();
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [projectOpen, setProjectOpen] = useState(false);
  const firstProjectSlug = projects.data?.[0]?.slug;

  useEffect(() => {
    if (firstProjectSlug) {
      router.replace(pathOfProject(firstProjectSlug));
    }
  }, [firstProjectSlug, router]);

  const isLoading = workspaces.isLoading || (workspaceId && projects.isLoading);
  const needsWorkspace = !workspaces.isLoading && !workspaces.data?.length;

  if (isLoading || firstProjectSlug) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <div className="bg-muted h-24 w-64 animate-pulse rounded" />
      </div>
    );
  }

  return (
    <div className="flex flex-1 items-center justify-center px-6 py-16">
      <div className="max-w-lg text-center">
        <Sprout className="mx-auto mb-6 size-10 text-emerald-700 dark:text-emerald-400" />
        <h1 className="text-2xl font-semibold">
          {needsWorkspace
            ? "Create your breeding workspace"
            : "Create your first project"}
        </h1>
        <p className="text-muted-foreground mt-3 text-sm leading-6">
          {needsWorkspace
            ? "A workspace is the security boundary for people, agents, projects, data, and validated knowledge."
            : "A project holds one breeding objective: its cycles, to-dos, and every conversation with your agent coworkers."}
        </p>
        <Button
          className="mt-7"
          onClick={() =>
            needsWorkspace ? setWorkspaceOpen(true) : setProjectOpen(true)
          }
        >
          <Plus className="size-4" />
          {needsWorkspace ? "New workspace" : "New project"}
        </Button>
      </div>

      <CreateWorkspaceDialog
        open={workspaceOpen}
        onOpenChange={setWorkspaceOpen}
      />
      {workspaceId && (
        <CreateProjectDialog
          open={projectOpen}
          onOpenChange={setProjectOpen}
          workspaceId={workspaceId}
        />
      )}
    </div>
  );
}
