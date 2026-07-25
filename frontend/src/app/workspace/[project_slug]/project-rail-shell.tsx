"use client";

import { useParams } from "next/navigation";

import { ProjectRail } from "@/components/workspace/project-rail";

/**
 * Project workspace shell: the rail (cycles, to-dos, conversations) stays
 * mounted while the main area renders the conversation.
 */
export function ProjectRailShell({ children }: { children: React.ReactNode }) {
  const { project_slug: projectSlug } = useParams<{ project_slug: string }>();
  return (
    <div className="flex h-screen min-w-0 flex-1">
      <ProjectRail projectSlug={projectSlug} />
      <div className="flex min-w-0 flex-1 flex-col">{children}</div>
    </div>
  );
}
