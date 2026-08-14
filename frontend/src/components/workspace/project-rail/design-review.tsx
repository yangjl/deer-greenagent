"use client";

import {
  AlertTriangleIcon,
  CheckIcon,
  FileTextIcon,
  GitCompareArrowsIcon,
  HelpCircleIcon,
  Loader2,
} from "lucide-react";

import { ArtifactFilePreview } from "@/components/workspace/artifacts/artifact-file-detail";
import { workspacePathOfArtifactUri } from "@/core/dbtl";
import {
  parseDesignConsensusPackage,
  structuredPackagePath,
} from "@/core/dbtl/design-consensus-view";
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
    <div className="space-y-4">
      <DesignConsensusMap
        projectId={projectId}
        reviewMarkdown={content.data.content}
        reviewPath={path}
      />
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
    </div>
  );
}

function DesignConsensusMap({
  projectId,
  reviewMarkdown,
  reviewPath,
}: {
  projectId: string;
  reviewMarkdown: string;
  reviewPath: string;
}) {
  const packagePath = structuredPackagePath(reviewPath, reviewMarkdown);
  const content = useProjectFileContent(projectId, packagePath ?? "", {
    enabled: Boolean(packagePath),
  });
  const view = content.data?.content
    ? parseDesignConsensusPackage(content.data.content)
    : null;

  if (!packagePath) {
    return null;
  }
  if (content.isPending) {
    return (
      <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
        <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
        Loading the meeting decision map…
      </p>
    );
  }
  if (!view) {
    return null;
  }

  const unresolved =
    view.openQuestions.length +
    view.disagreements.filter((item) => !item.resolution).length;

  return (
    <section
      aria-label="Meeting decision map"
      className="border-border space-y-4 border-y py-4"
    >
      <header className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium">Meeting decision map</p>
          <p className="text-muted-foreground mt-0.5 text-xs">
            Read from the structured package referenced by the bound document
            below.
          </p>
        </div>
        <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
          {view.agreements.length} agreed · {view.disagreements.length}{" "}
          contested · {unresolved} unresolved
        </span>
      </header>

      {view.summary ? (
        <div>
          <p className="text-muted-foreground text-[11px] font-semibold tracking-widest uppercase">
            Chosen path
          </p>
          <p className="mt-1 text-sm leading-6">{view.summary}</p>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <DecisionList icon={CheckIcon} items={view.agreements} title="Agreed" />
        <DecisionList
          icon={HelpCircleIcon}
          items={view.openQuestions}
          title="Owner decisions"
        />
      </div>

      {view.disagreements.length ? (
        <div className="space-y-2">
          <p className="text-muted-foreground flex items-center gap-1.5 text-[11px] font-semibold tracking-widest uppercase">
            <GitCompareArrowsIcon className="size-3.5" aria-hidden="true" />
            Contested
          </p>
          <div className="border-border divide-border divide-y border-y">
            {view.disagreements.map((item, index) => (
              <div key={`${item.topic}-${index}`} className="space-y-2 py-3">
                <p className="text-sm font-medium">{item.topic}</p>
                <ul className="text-muted-foreground list-disc space-y-1 pl-4 text-xs leading-5">
                  {item.positions.map((position) => (
                    <li key={position}>{position}</li>
                  ))}
                </ul>
                <p className="text-xs leading-5">
                  <span className="font-medium">
                    {item.resolution ? "Resolution:" : "Unresolved:"}
                  </span>{" "}
                  {item.resolution ||
                    "The reviewer must decide before treating this as settled."}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {view.limitations.length || view.failedChecks.length ? (
        <DecisionList
          icon={AlertTriangleIcon}
          items={[...view.failedChecks, ...view.limitations]}
          title="Risks and limits"
        />
      ) : null}

      {view.nextActions.length ? (
        <DecisionList
          icon={FileTextIcon}
          items={view.nextActions}
          title="Review next"
        />
      ) : null}
    </section>
  );
}

function DecisionList({
  icon: Icon,
  items,
  title,
}: {
  icon: typeof CheckIcon;
  items: string[];
  title: string;
}) {
  if (!items.length) {
    return null;
  }
  return (
    <div>
      <p className="text-muted-foreground flex items-center gap-1.5 text-[11px] font-semibold tracking-widest uppercase">
        <Icon className="size-3.5" aria-hidden="true" />
        {title}
      </p>
      <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-5">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
