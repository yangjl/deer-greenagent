export type WorkspaceRole = "owner" | "admin" | "member" | "viewer";

export type Workspace = {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  status: string;
  current_user_role: WorkspaceRole;
  project_count: number;
  created_at: string;
  updated_at: string;
};

export type Project = {
  id: string;
  workspace_id: string;
  name: string;
  slug: string;
  description: string | null;
  crop_profile: string;
  status: string;
  dbtl_phase: "design" | "build" | "test" | "learn" | string;
  reconciliation_status: "in-sync" | "reconciliation-blocked" | string;
  /** The project's human-visible folder (e.g. ~/Documents/projects/G2F). */
  root_path?: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkspaceCreatePayload = {
  name: string;
  slug?: string;
  description?: string;
};

export type ProjectCreatePayload = {
  name: string;
  slug?: string;
  description?: string;
  crop_profile?: string;
};
