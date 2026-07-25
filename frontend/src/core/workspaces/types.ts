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
  location_mode?: ProjectLocationMode;
  root_path?: string;
  parent_path?: string;
  folder_name?: string;
};

export type ProjectLocationMode =
  | "default"
  | "existing"
  | "full_path"
  | "new_under_parent";

export type ProjectFolderEntry = {
  name: string;
  path: string;
};

export type ProjectFolderListing = {
  current_path: string;
  parent_path: string | null;
  allowed_roots: string[];
  directories: ProjectFolderEntry[];
};
