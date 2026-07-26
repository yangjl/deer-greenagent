"use client";

import { FileTextIcon, Loader2 } from "lucide-react";

import { ArtifactFilePreview } from "@/components/workspace/artifacts/artifact-file-detail";
import { workspacePathOfArtifactUri } from "@/core/dbtl";
import { urlOfProjectFile, useProjectFileContent } from "@/core/workspaces";

/**
 * The review package, rendered as the document a person reads.
 *
 * A reviewer is being asked for a scientific judgement, so the reading surface
 * is the Markdown the backend wrote — not a second rendering of the JSON. That
 * matters beyond convenience: the approval binds to the Markdown's hash, so what
 * is shown here is exactly the bytes the decision covers. A parallel React view
 * of the same data could drift from it and nobody would notice.
 *
 * The structured JSON stays reachable beside it for anyone who wants the machine
 * record; the document names it and its hash.
 */
export function DesignReviewDocument({
  projectId,
  artifactUri,
}: {
  projectId: string;
  artifactUri: string;
}) {
  const path = workspacePathOfArtifactUri(artifactUri);
  const content = useProjectFileContent(projectId, path, {
    enabled: Boolean(path),
  });

  if (!path) {
    // Not a workspace-relative artifact — the generic evidence row already
    // shows its URI, so inventing a viewer here would only mislead.
    return null;
  }

  if (content.isPending) {
    return (
      <p
        className="text-muted-foreground flex items-center gap-1.5 text-sm"
        role="status"
      >
        <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
        Loading the review package…
      </p>
    );
  }

  if (content.error || !content.data?.content) {
    return (
      <p className="text-muted-foreground text-sm">
        The review package could not be read in place.{" "}
        <a
          className="underline"
          href={urlOfProjectFile({ projectId, path, download: true })}
        >
          Download it
        </a>{" "}
        to review the evidence.
      </p>
    );
  }

  return (
    <div className="border-border overflow-hidden rounded-md border">
      <div className="text-muted-foreground border-border bg-muted/40 flex items-center gap-1.5 border-b px-3 py-1.5 text-xs">
        <FileTextIcon className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate font-mono">{path}</span>
        <a
          className="shrink-0 underline"
          href={urlOfProjectFile({ projectId, path, download: true })}
        >
          Download
        </a>
      </div>
      <div className="max-h-[28rem] overflow-y-auto px-3 py-2">
        <ArtifactFilePreview
          content={content.data.content}
          language="markdown"
          scrollKey={`dbtl-review:${projectId}:${path}`}
          resolveArtifactLinks={false}
        />
      </div>
    </div>
  );
}
