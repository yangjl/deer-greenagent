/**
 * Conversation inside a project workspace: the canonical chat surface,
 * rendered beside the project rail. `useParams().project_slug` scopes thread
 * creation and keeps in-project routing.
 */
export { default } from "@/app/workspace/chats/[thread_id]/page";
