/**
 * Resolving a DBTL artifact URI to a project-relative path.
 *
 * Artifact references are deliberately heterogeneous: some are workspace files,
 * others are dataset fingerprints, cycle ids, or policy versions. Only the file
 * ones can be served by the project file API, so this returns `null` for
 * everything else rather than guessing — a guessed path produces a viewer that
 * is confidently broken.
 */

/** Sandbox mount prefixes that map onto the project folder. */
const SANDBOX_PREFIXES = [
  "/mnt/user-data/outputs/",
  "/mnt/user-data/workspace/",
  "/mnt/user-data/uploads/",
] as const;

/** The project-relative directory each prefix lands in. */
const PREFIX_TARGET: Record<string, string> = {
  "/mnt/user-data/outputs/": "outputs/",
  "/mnt/user-data/workspace/": "",
  "/mnt/user-data/uploads/": "uploads/",
};

export function workspacePathOfArtifactUri(uri: string): string | null {
  const value = (uri ?? "").trim();
  if (!value) return null;
  // A scheme means it is not a local file reference (`dataset:…`, `https://…`).
  if (/^[a-z][a-z0-9+.-]*:/i.test(value)) return null;

  let path: string | null = null;
  for (const prefix of SANDBOX_PREFIXES) {
    if (value.startsWith(prefix)) {
      path = PREFIX_TARGET[prefix] + value.slice(prefix.length);
      break;
    }
  }
  if (path === null) {
    // Accept an already-relative path, but nothing absolute we do not map: an
    // unmapped absolute path is a host path the file API must not be handed.
    if (value.startsWith("/")) return null;
    path = value;
  }

  // Traversal is rejected outright rather than normalized away, so a crafted
  // reference cannot reach outside the project folder.
  const segments = path.split("/");
  if (segments.includes("..")) return null;

  // A file reference has to look like a file. Cycle ids, work-unit ids, and
  // policy versions are all plausible relative strings otherwise, and handing
  // one to the file API produces a confidently broken viewer.
  const basename = segments[segments.length - 1] ?? "";
  if (!/\.[A-Za-z0-9]+$/.test(basename)) return null;

  return path || null;
}

/** Whether this artifact is the human-readable review document. */
export function isReviewDocumentUri(uri: string): boolean {
  const path = workspacePathOfArtifactUri(uri);
  return path?.toLowerCase().endsWith(".md") ?? false;
}
