import type { ProjectCreatePayload, ProjectLocationMode } from "./types";

export type ProjectLocationValues = {
  existingPath: string;
  fullPath: string;
  parentPath: string;
  folderName: string;
};

/** Mirror the backend's safe, human-readable project folder naming. */
export function projectFolderName(name: string): string {
  const cleaned = name
    .replace(/[^A-Za-z0-9 ._-]+/g, "-")
    .trim()
    .replace(/^\.+|\.+$/g, "")
    .trim();
  return cleaned || "project";
}

export function joinLocalPath(parent: string, child: string): string {
  return `${parent.replace(/[\\/]+$/, "")}/${child.replace(/^[\\/]+/, "")}`;
}

export function buildProjectLocationFields(
  mode: ProjectLocationMode,
  values: ProjectLocationValues,
): Pick<
  ProjectCreatePayload,
  "location_mode" | "root_path" | "parent_path" | "folder_name"
> {
  if (mode === "existing") {
    return {
      location_mode: mode,
      root_path: values.existingPath.trim(),
    };
  }
  if (mode === "full_path") {
    return {
      location_mode: mode,
      root_path: values.fullPath.trim(),
    };
  }
  if (mode === "new_under_parent") {
    return {
      location_mode: mode,
      parent_path: values.parentPath.trim(),
      folder_name: values.folderName.trim(),
    };
  }
  return { location_mode: "default" };
}
