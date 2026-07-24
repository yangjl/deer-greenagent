export const WORKSPACE_FILES_ROOT = "/mnt/user-data";

export interface WorkspaceFileEntry {
  name: string;
  path: string;
  type: "file" | "directory";
  size: number | null;
  modified_at: string | null;
  is_symlink: boolean;
}

export interface WorkspaceFilesListResponse {
  path: string;
  entries: WorkspaceFileEntry[];
  truncated: boolean;
}

export interface ThreadProject {
  container_path: string;
  name: string;
}

export interface ThreadProjectResponse {
  project: ThreadProject | null;
}

export interface ProjectCandidatesResponse {
  candidates: ThreadProject[];
}
