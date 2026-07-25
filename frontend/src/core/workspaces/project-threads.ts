/**
 * Project route helpers and conversation shape.
 *
 * Conversation membership itself is a durable backend record: the Gateway
 * writes `threads_meta.project_id` through `PUT /api/projects/{id}/threads/
 * {thread}` and lists a project's conversations from it. See
 * `project-files-api.ts` for those calls.
 */

export interface ProjectConversation {
  threadId: string;
  title: string | null;
  updatedAt: string;
}

export function projectConversationsQueryKey(projectId: string) {
  return ["projects", projectId, "conversations"] as const;
}

/**
 * Workspace path segments that are real routes, so a project slug can never
 * shadow one. Next.js resolves static segments before the dynamic
 * `[project_slug]` segment, so a project slugged `agents` would otherwise be
 * unreachable.
 */
export const RESERVED_WORKSPACE_SEGMENTS: readonly string[] = [
  "agents",
  "chats",
  "inbox",
  "projects",
  "scheduled-tasks",
  "settings",
];

export function isReservedWorkspaceSegment(slug: string): boolean {
  return RESERVED_WORKSPACE_SEGMENTS.includes(slug);
}

/** Slugifies a project name into a URL segment that cannot shadow a route. */
export function projectSlugOfName(name: string): string {
  const base = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  if (!base) {
    return "project";
  }
  return isReservedWorkspaceSegment(base) ? `${base}-project` : base;
}

/** A project workspace lives directly under /workspace/<slug>. */
export function pathOfProject(slug: string): string {
  return `/workspace/${encodeURIComponent(slug)}`;
}

export function pathOfNewProjectConversation(slug: string): string {
  return `${pathOfProject(slug)}/new`;
}

export function pathOfProjectThread(slug: string, threadId: string): string {
  return `${pathOfProject(slug)}/${encodeURIComponent(threadId)}`;
}
