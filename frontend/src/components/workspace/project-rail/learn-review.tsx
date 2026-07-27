"use client";

import { BookOpenCheck, ExternalLink, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  CLAIM_STATUS_LABELS,
  GRADE_LABELS,
  activePublicationTargets,
  candidateCanBeReviewed,
  candidateNotice,
  promotionReviewReady,
  publicationChoices,
  partitionClaims,
  publicationReviewReady,
  retractionScopePreview,
  type KnowledgeCandidate,
  type KnowledgeClaim,
  type KnowledgeView,
  useDecideCandidate,
  useKnowledge,
  usePromoteCandidate,
  usePublishClaim,
  useRetractClaim,
} from "@/core/dbtl";
import { uuid } from "@/core/utils/uuid";
import { type Project, useProject, useProjects } from "@/core/workspaces";

function CandidateCard({
  candidate,
  activeClaims,
  projectId,
}: {
  candidate: KnowledgeCandidate;
  activeClaims: KnowledgeClaim[];
  projectId: string;
}) {
  const decide = useDecideCandidate(projectId);
  const promote = usePromoteCandidate(projectId);
  const [statement, setStatement] = useState(candidate.statement);
  const [limitations, setLimitations] = useState(
    candidate.limitations.join("\n"),
  );
  const [rationale, setRationale] = useState("");
  const [supersedes, setSupersedes] = useState("");
  const reviewable = candidateCanBeReviewed(candidate);
  const busy = decide.isPending || promote.isPending;
  const mutationError = decide.error ?? promote.error;

  function decideAs(decision: "keep" | "discard") {
    if (!rationale.trim() || busy) return;
    decide.mutate({
      candidateId: candidate.id,
      decision,
      rationale: rationale.trim(),
      idempotencyKey: `candidate-${uuid()}`,
    });
  }

  return (
    <article className="border-border space-y-3 rounded-lg border p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm leading-relaxed font-medium">
            {candidate.statement}
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            {candidateNotice(candidate)}
          </p>
        </div>
        <Badge variant="outline">{GRADE_LABELS[candidate.grade]}</Badge>
      </div>
      <p className="text-muted-foreground text-xs">
        {candidate.evidence.length} evidence reference
        {candidate.evidence.length === 1 ? "" : "s"}
        {candidate.limitations.length > 0
          ? ` · ${candidate.limitations.length} limitation${candidate.limitations.length === 1 ? "" : "s"}`
          : ""}
      </p>

      {reviewable && (
        <div className="border-border space-y-3 border-t pt-3">
          <label className="space-y-1.5">
            <span className="text-xs font-medium">Reviewed statement</span>
            <Textarea
              value={statement}
              onChange={(event) => setStatement(event.target.value)}
              rows={3}
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-xs font-medium">
              Limitations, one per line
            </span>
            <Textarea
              value={limitations}
              onChange={(event) => setLimitations(event.target.value)}
              rows={2}
            />
          </label>
          {activeClaims.length > 0 && (
            <label className="space-y-1.5">
              <span className="text-xs font-medium">
                Supersedes an active claim (optional)
              </span>
              <select
                className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                value={supersedes}
                onChange={(event) => setSupersedes(event.target.value)}
              >
                <option value="">No supersession</option>
                {activeClaims.map((claim) => (
                  <option key={claim.id} value={claim.id}>
                    {claim.statement}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="space-y-1.5">
            <span className="text-xs font-medium">
              Human rationale — required
            </span>
            <Textarea
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
              rows={2}
              placeholder="Why should this candidate be kept, discarded, or promoted?"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={!rationale.trim() || busy}
              onClick={() => decideAs("keep")}
            >
              Keep as working memory
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!rationale.trim() || busy}
              onClick={() => decideAs("discard")}
            >
              Discard candidate
            </Button>
            <Button
              size="sm"
              disabled={
                !promotionReviewReady(candidate, statement, rationale) || busy
              }
              onClick={() =>
                promote.mutate({
                  candidateId: candidate.id,
                  statement: statement.trim(),
                  grade: candidate.grade,
                  limitations: limitations
                    .split("\n")
                    .map((item) => item.trim())
                    .filter(Boolean),
                  rationale: rationale.trim(),
                  supersedesClaimId: supersedes ? supersedes : undefined,
                  idempotencyKey: `promotion-${uuid()}`,
                })
              }
            >
              Promote to project knowledge
            </Button>
          </div>
          {mutationError && (
            <p className="text-destructive text-sm" role="alert">
              {mutationError.message}
            </p>
          )}
        </div>
      )}
    </article>
  );
}

function ClaimCard({
  claim,
  projectId,
  projects,
  view,
}: {
  claim: KnowledgeClaim;
  projectId: string;
  projects: Project[];
  view: KnowledgeView;
}) {
  const publish = usePublishClaim(projectId);
  const retract = useRetractClaim(projectId);
  const [selected, setSelected] = useState<string[]>([]);
  const [publicationRationale, setPublicationRationale] = useState("");
  const [retractionRationale, setRetractionRationale] = useState("");
  const activeTargets = activePublicationTargets(view, claim.id);
  const retractionScope = retractionScopePreview(view, claim.id, projects);
  const mutationError = publish.error ?? retract.error;
  const available = publicationChoices(projects, {
    sourceProjectId: projectId,
    activeTargetIds: activeTargets,
  });
  const validSelected = selected.filter((id) =>
    available.some((project) => project.id === id),
  );

  return (
    <article className="border-border space-y-3 rounded-lg border p-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm leading-relaxed font-medium">{claim.statement}</p>
        <Badge variant={claim.status === "active" ? "secondary" : "outline"}>
          {CLAIM_STATUS_LABELS[claim.status]}
        </Badge>
      </div>
      <p className="text-muted-foreground text-xs">
        {GRADE_LABELS[claim.grade]} · source scope: this project
      </p>
      {claim.limitations.length > 0 && (
        <p className="text-muted-foreground text-xs">
          Limitations: {claim.limitations.join("; ")}
        </p>
      )}
      {activeTargets.length > 0 && (
        <p className="text-xs">
          Published to{" "}
          {activeTargets
            .map(
              (id) => projects.find((project) => project.id === id)?.name ?? id,
            )
            .join(", ")}
        </p>
      )}

      {claim.status === "active" && (
        <div className="border-border grid gap-4 border-t pt-3">
          <div className="space-y-2">
            <p className="flex items-center gap-1.5 text-xs font-semibold">
              <ExternalLink className="size-3.5" />
              Publish to selected projects
            </p>
            <p className="text-muted-foreground text-xs">
              Promotion did not publish this claim. Select every additional
              project that may retrieve it.
            </p>
            <div className="grid gap-1">
              {available.map((project) => (
                <label
                  key={project.id}
                  className="flex items-center gap-2 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(project.id)}
                    onChange={(event) =>
                      setSelected((current) =>
                        event.target.checked
                          ? [...current, project.id]
                          : current.filter((id) => id !== project.id),
                      )
                    }
                  />
                  {project.name}
                </label>
              ))}
              {available.length === 0 && (
                <p className="text-muted-foreground text-xs">
                  No unpublished projects are available.
                </p>
              )}
            </div>
            <Input
              value={publicationRationale}
              onChange={(event) => setPublicationRationale(event.target.value)}
              placeholder="Publication rationale (required)"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={
                !publicationReviewReady(validSelected, publicationRationale) ||
                publish.isPending
              }
              onClick={() =>
                publish.mutate(
                  {
                    claimId: claim.id,
                    targetProjectIds: validSelected,
                    rationale: publicationRationale.trim(),
                    idempotencyKey: `publication-${uuid()}`,
                  },
                  {
                    onSuccess: () => {
                      setSelected([]);
                      setPublicationRationale("");
                    },
                  },
                )
              }
            >
              Publish to selected projects
            </Button>
          </div>

          <div className="space-y-2">
            <p className="flex items-center gap-1.5 text-xs font-semibold">
              <ShieldAlert className="size-3.5" />
              Retract claim
            </p>
            <p className="text-muted-foreground text-xs">
              {retractionScope.summary}
            </p>
            {/* Name every scope, not just how many. Retraction is destructive
                to retrieval, and a count gives the reviewer nothing to check
                against what they meant to withdraw. */}
            {retractionScope.targets.length > 0 && (
              <ul className="text-muted-foreground space-y-0.5 text-xs">
                {retractionScope.targets.map((target) => (
                  <li key={target.id}>
                    Loses current retrieval: {target.name}
                  </li>
                ))}
              </ul>
            )}
            <Input
              value={retractionRationale}
              onChange={(event) => setRetractionRationale(event.target.value)}
              placeholder="Retraction rationale (required)"
            />
            <Button
              size="sm"
              variant="destructive"
              disabled={!retractionRationale.trim() || retract.isPending}
              onClick={() =>
                retract.mutate({
                  claimId: claim.id,
                  rationale: retractionRationale.trim(),
                  idempotencyKey: `retraction-${uuid()}`,
                })
              }
            >
              Retract from every active scope
            </Button>
          </div>
          {mutationError && (
            <p className="text-destructive text-sm" role="alert">
              {mutationError.message}
            </p>
          )}
        </div>
      )}
    </article>
  );
}

export function LearnReview({
  projectId,
  cycleId,
}: {
  projectId: string;
  cycleId: string;
}) {
  const knowledge = useKnowledge(projectId, cycleId);
  // Publication targets must come from *this* project's workspace. The
  // active-workspace hook auto-selects the first workspace, so for anyone
  // whose current project lives elsewhere it listed the wrong projects and
  // every publish failed server-side (targets outside the claim's workspace
  // are refused).
  const project = useProject(projectId);
  const projects = useProjects(project.data?.workspace_id ?? null);
  const view = knowledge.data;
  const { current: currentClaims, history: historyClaims } = useMemo(
    () => partitionClaims(view?.claims ?? []),
    [view],
  );
  const activeClaims = currentClaims;
  const synthesisSummary = useMemo(() => {
    const event = [...(view?.events ?? [])]
      .reverse()
      .find((item) => item.event_type === "learn.synthesized");
    const summary = event?.payload.summary;
    return typeof summary === "string" ? summary : "";
  }, [view]);
  const testOutcome = useMemo(() => {
    const event = [...(view?.events ?? [])]
      .reverse()
      .find((item) => item.event_type === "learn.synthesized");
    const outcome = event?.payload.test_outcome;
    return typeof outcome === "string" ? outcome : "";
  }, [view]);

  if (knowledge.isPending)
    return (
      <p className="text-muted-foreground text-sm">Loading Learn review…</p>
    );
  if (!view)
    return (
      <p className="text-muted-foreground text-sm">
        {knowledge.error?.message ?? "Learn review is unavailable."}
      </p>
    );

  return (
    <div className="space-y-5">
      <div className="border-border bg-muted/30 rounded-lg border p-3">
        <p className="flex items-center gap-1.5 text-sm font-semibold">
          <BookOpenCheck className="size-4" />
          Learn closeout
          {testOutcome && (
            <Badge variant="outline">
              Test: {testOutcome.replaceAll("_", " ")}
            </Badge>
          )}
        </p>
        <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
          Agent output below is candidate working memory. Only a recorded human
          promotion creates project knowledge; publication to another project is
          a separate decision.
        </p>
        {synthesisSummary && (
          <p className="border-border mt-3 border-t pt-3 text-sm leading-relaxed">
            {synthesisSummary}
          </p>
        )}
      </div>

      <div className="space-y-2">
        <p className="text-xs font-semibold tracking-wide uppercase">
          Candidates
        </p>
        {view.candidates.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            Learn produced no eligible knowledge candidate. Inconclusive and
            invalidated Test outcomes close without promotion.
          </p>
        ) : (
          view.candidates.map((candidate) => (
            <CandidateCard
              key={candidate.id}
              candidate={candidate}
              activeClaims={activeClaims}
              projectId={projectId}
            />
          ))
        )}
      </div>

      <div className="space-y-2">
        <p className="text-xs font-semibold tracking-wide uppercase">
          {CLAIM_STATUS_LABELS.active}
        </p>
        {currentClaims.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No candidate has been promoted.
          </p>
        ) : (
          currentClaims.map((claim) => (
            <ClaimCard
              key={claim.id}
              claim={claim}
              projectId={projectId}
              projects={projects.data ?? []}
              view={view}
            />
          ))
        )}
      </div>

      {/* Withdrawn claims stay visible as history, but never under the
          heading that means "current project knowledge" — a reader scanning
          headings would otherwise treat a retracted claim as still in force. */}
      {historyClaims.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold tracking-wide uppercase">
            Superseded or retracted history
          </p>
          <p className="text-muted-foreground text-xs">
            No longer current project knowledge. Retained as an audit record.
          </p>
          {historyClaims.map((claim) => (
            <ClaimCard
              key={claim.id}
              claim={claim}
              projectId={projectId}
              projects={projects.data ?? []}
              view={view}
            />
          ))}
        </div>
      )}
    </div>
  );
}
