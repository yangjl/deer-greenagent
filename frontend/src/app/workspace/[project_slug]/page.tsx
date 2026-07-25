import { redirect } from "next/navigation";

import { pathOfNewProjectConversation } from "@/core/workspaces/project-threads";

/**
 * Opening a project lands directly in a new conversation — the project
 * workspace is the chat, with the rail beside it. No intermediate brief page.
 */
export default async function ProjectPage({
  params,
}: {
  params: Promise<{ project_slug: string }>;
}) {
  const { project_slug: projectSlug } = await params;
  redirect(pathOfNewProjectConversation(projectSlug));
}
