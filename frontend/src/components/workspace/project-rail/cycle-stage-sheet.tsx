"use client";

import {
  AlertTriangle,
  FileText,
  GitCompare,
  History,
  Lock,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  CYCLE_STATE_LABELS,
  type CycleTimeline as CycleTimelineType,
  type CycleTimelineEntry,
  type CycleTransitionGate,
  type DbtlStage,
  STAGE_LABELS,
  STATUS_LABELS,
  deriveCycleTimeline,
  activityRevision,
  describeActivity,
  isReviewDocumentUri,
  latestArtifactsForStage,
  openWorkItems,
  stageBlockReason,
  stageOf,
  useCycleActivity,
  useCycleDetail,
  useBuildTest,
  useDbtlFeature,
} from "@/core/dbtl";
import { useI18n } from "@/core/i18n/hooks";
import { pathOfProjectThread } from "@/core/workspaces/project-threads";
import { cn } from "@/lib/utils";

import { BuildTestReview } from "./build-test-review";
import { DesignReviewDocument } from "./design-review";
import { LearnReview } from "./learn-review";
import { ReconciliationMatrix } from "./reconciliation-matrix";

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof FileText;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-muted-foreground flex items-center gap-1.5 text-[11px] font-semibold tracking-widest uppercase">
        <Icon className="size-3.5" />
        {title}
      </h3>
      {children}
    </section>
  );
}

function stageName(stage: string): string {
  return (
    STAGE_LABELS[stage as DbtlStage] ??
    stage.charAt(0).toUpperCase() + stage.slice(1)
  );
}

/**
 * The audit detail for one decided edge. Rendered only when a reader asks for
 * it: a loop of six attempts each carrying an assessment, an override, the
 * routes not taken, and two hashes is unreadable as a default, and the compact
 * walk is what most people come here for.
 */
function TimelineEntryDetail({
  entry,
  projectSlug,
  evidenceUriByHash,
}: {
  entry: CycleTimelineEntry;
  projectSlug: string | null;
  evidenceUriByHash: Map<string, string>;
}) {
  const { t } = useI18n();
  const rows: Array<[string, string]> = [];
  if (entry.assessedDifficulty) {
    rows.push([
      t.dbtl.timeline.assessed,
      entry.assessmentRationale
        ? `${entry.assessedDifficulty} — ${entry.assessmentRationale}`
        : entry.assessedDifficulty,
    ]);
  }
  // Only a genuine disagreement is shown. A reviewer recording the same value
  // the agent assessed agreed with it, and labelling that "overridden" would
  // misreport the rate the default-on decision is gated behind.
  if (entry.overrodeAssessment && entry.humanOverride) {
    rows.push([t.dbtl.timeline.overrode, entry.humanOverride]);
  }
  const notTaken = entry.offeredRoutes.filter(
    (route) => route !== entry.chosenRoute,
  );
  if (notTaken.length > 0) {
    rows.push([t.dbtl.timeline.offered, notTaken.join(", ")]);
  }
  if (entry.decidedBy) {
    rows.push([
      t.dbtl.timeline.decidedBy,
      entry.backfilled
        ? `${entry.decidedBy} · ${t.dbtl.pathStrip.backfilled}`
        : entry.decidedBy,
    ]);
  }
  if (entry.evidenceHash) {
    // The exact document the decision bound: name the file when the hash still
    // matches an attached artifact, and keep the hash prefix either way so a
    // renamed or detached file cannot silently stand in for it.
    const uri = evidenceUriByHash.get(entry.evidenceHash);
    rows.push([
      t.dbtl.timeline.evidence,
      uri
        ? `${uri} · ${entry.evidenceHash.slice(0, 12)}`
        : entry.evidenceHash.slice(0, 12),
    ]);
  }
  if (rows.length === 0 && !entry.recordId) return null;
  // Opening the deck means returning to the conversation it was registered
  // against — the deck itself only activates through that authenticated
  // parent, so this link navigates and never grants anything.
  const deckPath =
    entry.decisionSurfaceId && entry.decidedInThreadId && projectSlug
      ? pathOfProjectThread(projectSlug, entry.decidedInThreadId)
      : null;
  return (
    <dl className="text-muted-foreground mt-1 ml-4 space-y-0.5 text-[11px]">
      {rows.map(([label, value]) => (
        <div key={label} className="flex gap-x-1.5">
          <dt className="shrink-0">{label}:</dt>
          <dd className="min-w-0 break-words">{value}</dd>
        </div>
      ))}
      {deckPath && (
        <div>
          <Link
            href={deckPath}
            className="hover:text-foreground underline underline-offset-2"
          >
            {t.dbtl.timeline.deck}
          </Link>
        </div>
      )}
      {entry.recordId && (
        <div className="flex gap-x-1.5">
          <dt className="shrink-0">{t.dbtl.pathStrip.record}:</dt>
          <dd className="font-mono break-all">{entry.recordId}</dd>
        </div>
      )}
    </dl>
  );
}

/**
 * The cycle's non-linear walk (progressive gate, Phase 4), replacing the Phase
 * 0 path strip. Purely presentational: every entry comes from the server's
 * durable transition records via `deriveCycleTimeline`.
 *
 * Compact by default, audit detail on demand — the plan's own requirement, and
 * the reason a repeated `D→B→T→B→T→L` still reads as one cycle rather than a
 * wall of decisions.
 */
function CycleTimeline({
  timeline,
  projectSlug,
  evidenceUriByHash,
}: {
  timeline: CycleTimelineType;
  projectSlug: string | null;
  evidenceUriByHash: Map<string, string>;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  if (timeline.entries.length === 0) return null;
  return (
    <div aria-label={t.dbtl.pathStrip.label} className="space-y-1 text-xs">
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        {timeline.entries.map((entry, index) => (
          <span
            key={`${entry.stage}-${entry.round}-${index}`}
            className="flex items-center gap-x-1.5"
          >
            {index > 0 && (
              <span aria-hidden className="text-muted-foreground">
                →
              </span>
            )}
            <span
              title={
                entry.status === "invalidated"
                  ? t.dbtl.pathStrip.invalidated
                  : undefined
              }
              className={cn(
                "tabular-nums",
                entry.status === "current" && "text-foreground font-semibold",
                entry.status === "invalidated" &&
                  "text-muted-foreground line-through",
                entry.status === "closed" && "text-muted-foreground",
              )}
            >
              {stageName(entry.stage)} {entry.round}
            </span>
          </span>
        ))}
      </div>
      {/* Both states carry a word, never a colour or a strikethrough alone. */}
      {timeline.parked && (
        <p className="text-muted-foreground">{t.dbtl.timeline.parked}</p>
      )}
      {timeline.hasInvalidatedWork && (
        <p className="text-muted-foreground">
          {t.dbtl.timeline.invalidatedWork}
        </p>
      )}
      <button
        type="button"
        onClick={() => setExpanded((open) => !open)}
        className="text-muted-foreground hover:text-foreground underline underline-offset-2"
      >
        {expanded ? t.dbtl.timeline.hideDetail : t.dbtl.timeline.showDetail}
      </button>
      {expanded && (
        <ol className="space-y-1.5">
          {timeline.entries.map((entry, index) => (
            <li key={`detail-${entry.stage}-${entry.round}-${index}`}>
              <span className="font-medium">
                {stageName(entry.stage)} {entry.round}
              </span>
              <span className="text-muted-foreground">
                {" · "}
                {entry.chosenRoute ?? t.dbtl.timeline.current}
              </span>
              <TimelineEntryDetail
                entry={entry}
                projectSlug={projectSlug}
                evidenceUriByHash={evidenceUriByHash}
              />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function TransitionGateFallback({
  gate,
  override,
}: {
  gate: CycleTransitionGate | undefined;
  override: string | null;
}) {
  if (!gate) return null;
  const difficulty = gate.assessment.difficulty.replace("_", " ");
  return (
    <div className="border-border space-y-3 rounded-md border border-dashed p-4">
      <div>
        <p className="text-sm font-medium">
          Agent assessment: <span className="capitalize">{difficulty}</span>
        </p>
        <p className="text-muted-foreground mt-1 text-sm leading-relaxed">
          {gate.assessment.rationale}
        </p>
        {override && (
          <p className="mt-1 text-xs">
            Human override recorded:{" "}
            <span className="font-medium capitalize">
              {override.replace("_", " ")}
            </span>
          </p>
        )}
      </div>
      <div>
        <p className="text-muted-foreground text-[11px] font-semibold tracking-widest uppercase">
          Legal next routes
        </p>
        <ul className="mt-1.5 space-y-1.5">
          {gate.routes.map((route) => (
            <li key={route.slug} className="text-sm">
              <span className={cn(route.blocked && "text-muted-foreground")}>
                {route.label}
              </span>
              {route.blocked && route.blocked_reason && (
                <span className="text-muted-foreground block text-xs">
                  {route.blocked_reason}
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>
      {gate.parked && (
        <p className="text-sm font-medium">
          Parked — ordinary requests go to the lead agent with this Design
          explicitly marked unapproved.
        </p>
      )}
      <p className="text-muted-foreground text-xs">
        Open the registered feedback deck in conversation{" "}
        <span className="font-mono">{gate.originating_thread_id}</span> to
        override the assessment or choose a route.
      </p>
    </div>
  );
}

export function CycleStageSheet({
  projectId,
  projectSlug = null,
  cycleId,
  stage,
  open,
  onOpenChange,
}: {
  projectId: string;
  /** Needed only to build the timeline's deck links; null renders no link. */
  projectSlug?: string | null;
  cycleId: string | null;
  stage: DbtlStage | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const detail = useCycleDetail(projectId, open ? cycleId : null);
  const activity = useCycleActivity(projectId, open ? cycleId : null);
  const buildTest = useBuildTest(
    projectId,
    open && (stage === "build" || stage === "test") ? cycleId : null,
  );
  const dbtl = useDbtlFeature();

  const cycle = detail.data ?? null;
  const record = stage ? stageOf(cycle, stage) : null;
  const block = stage
    ? stageBlockReason(
        cycle,
        stage,
        dbtl.feature?.reconciliation_required ?? true,
      )
    : null;
  const artifacts = useMemo(
    () => (stage ? latestArtifactsForStage(cycle, stage) : []),
    [cycle, stage],
  );
  const blockers = useMemo(() => openWorkItems(cycle), [cycle]);
  // The most recent artifact that is a readable document. Derived from the same
  // durable list as the evidence rows, so the two can never disagree about what
  // is attached.
  const reviewDocument = useMemo(
    () =>
      artifacts.filter((item) => isReviewDocumentUri(item.uri)).at(-1) ?? null,
    [artifacts],
  );
  // Present only when the server both enables the progressive gate and sends
  // the transition walk; with either half missing the sheet is unchanged.
  const timeline = useMemo(
    () =>
      dbtl.feature?.progressive_gate === true && cycle?.transitions
        ? deriveCycleTimeline(cycle.transitions, cycle.state)
        : null,
    [cycle, dbtl.feature?.progressive_gate],
  );
  // The full cycle-wide artifact list, not the open stage's slice: timeline
  // entries span every stage, and matching by content hash is what lets an
  // edge name the exact file its decision bound.
  const evidenceUriByHash = useMemo(() => {
    const byHash = new Map<string, string>();
    for (const artifact of cycle?.artifacts ?? []) {
      byHash.set(artifact.content_hash, artifact.uri);
    }
    return byHash;
  }, [cycle?.artifacts]);
  const latestDifficultyOverride = useMemo(() => {
    if (!cycle?.transitions?.length) return null;
    return (
      [...cycle.transitions].sort((left, right) => left.seq - right.seq).at(-1)
        ?.human_override ?? null
    );
  }, [cycle?.transitions]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            {stage ? STAGE_LABELS[stage] : "Stage"}
            {record && (
              <Badge variant="outline">{STATUS_LABELS[record.status]}</Badge>
            )}
          </SheetTitle>
          <SheetDescription className="sr-only">
            Inspect evidence, review history, and the current state of this
            cycle stage.
          </SheetDescription>
        </SheetHeader>

        {detail.isPending ? (
          <p className="text-muted-foreground p-6 text-sm">Loading stage…</p>
        ) : !cycle || !stage || !record ? (
          <p className="text-muted-foreground p-6 text-sm">
            {detail.error?.message ?? "This stage is not available."}
          </p>
        ) : (
          <div className="space-y-7 p-6 pt-2">
            {timeline && (
              <CycleTimeline
                timeline={timeline}
                projectSlug={projectSlug}
                evidenceUriByHash={evidenceUriByHash}
              />
            )}
            <header>
              <p className="text-sm font-medium">{cycle.title}</p>
              <p className="text-muted-foreground mt-0.5 text-xs">
                {CYCLE_STATE_LABELS[cycle.state]} · revision{" "}
                <span className="tabular-nums">{cycle.db_revision}</span>
              </p>
              {cycle.research_question && (
                <p className="mt-3 text-sm leading-relaxed">
                  {cycle.research_question}
                </p>
              )}
            </header>

            {block?.blocked && (
              <p className="border-border text-muted-foreground flex items-start gap-2 rounded-md border border-dashed px-3 py-2 text-sm">
                <Lock className="mt-0.5 size-4 shrink-0" />
                {block.reason}
              </p>
            )}

            {/* The bridge between Design and Build gets the top of the sheet:
                on this stage the matrix *is* the review, and burying it under
                the generic evidence list would invert what the reviewer came
                here to do. */}
            {stage === "reconciliation" && cycleId && (
              <Section icon={GitCompare} title="Data readiness">
                <ReconciliationMatrix projectId={projectId} cycleId={cycleId} />
              </Section>
            )}

            {(stage === "build" || stage === "test") && cycleId && (
              <Section
                icon={stage === "build" ? GitCompare : AlertTriangle}
                title={
                  stage === "build" ? "Reproducibility" : "Scientific validity"
                }
              >
                <BuildTestReview
                  projectId={projectId}
                  cycleId={cycleId}
                  stage={stage}
                  stageStatus={record.status}
                  view={buildTest.data ?? null}
                  isPending={buildTest.isPending}
                  error={buildTest.error}
                />
              </Section>
            )}

            {stage === "learn" && cycleId && (
              <Section icon={History} title="Learn and project knowledge">
                <LearnReview projectId={projectId} cycleId={cycleId} />
              </Section>
            )}

            {/* The reviewed document itself, above the evidence references: on
                a stage awaiting review, reading the package *is* the task, and
                a list of hashes is not a reading surface. */}
            {reviewDocument && (
              <Section icon={FileText} title="Review package">
                <DesignReviewDocument
                  projectId={projectId}
                  artifactUri={reviewDocument.uri}
                />
              </Section>
            )}

            <Section icon={FileText} title="Evidence">
              {artifacts.length === 0 ? (
                <p className="text-muted-foreground text-sm">
                  No artifact attached yet. A stage cannot be reviewed without
                  evidence.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {artifacts.map((artifact) => (
                    <li
                      key={artifact.id}
                      className="border-border flex items-baseline justify-between gap-3 rounded-md border px-3 py-2"
                    >
                      <span className="min-w-0 truncate text-sm">
                        {artifact.artifact_type}
                        <span className="text-muted-foreground mt-0.5 block truncate text-xs">
                          {artifact.uri}
                        </span>
                      </span>
                      <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                        rev {artifact.revision} ·{" "}
                        {artifact.content_hash.slice(0, 10)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            {/* Recording a blocker lives here, in the review surface, rather
                than in the project rail: the rails navigate and summarize, and
                a durable record is written from the surface that shows the
                evidence it refers to. */}
            <Section icon={AlertTriangle} title="Open blockers">
              {blockers.length === 0 && (
                <p className="text-muted-foreground text-sm">
                  Nothing is blocking this cycle.
                </p>
              )}
              {blockers.length > 0 && (
                <ul className="space-y-2">
                  {blockers.map((item) => (
                    <li
                      key={item.id}
                      className="border-border rounded-md border px-3 py-2"
                    >
                      <p className="text-sm">{item.title}</p>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            {/* Design is inspection-only here. Submitting it and recording a
                verdict are clicks a person makes on an authenticated surface
                that can prove who made them — the meeting's registered slide
                deck — so this sheet keeps the evidence and the history but
                offers no decision. */}
            <Section icon={History} title="Review">
              <div className="space-y-3">
                {dbtl.feature?.progressive_gate && cycle.transition_gate && (
                  <TransitionGateFallback
                    gate={cycle.transition_gate}
                    override={latestDifficultyOverride}
                  />
                )}
                <div className="border-border rounded-md border border-dashed p-4">
                  <p className="text-sm font-medium">Continue in chat</p>
                  <p className="text-muted-foreground mt-1 text-sm leading-relaxed">
                    This panel shows evidence and history only. Return to the
                    originating conversation to submit, convene a meeting, or
                    record a human decision against the bound evidence.
                  </p>
                </div>
              </div>
            </Section>

            <Section icon={History} title="Activity">
              <ol className="space-y-1.5">
                {(activity.data ?? []).map((event) => (
                  <li key={event.id} className="flex items-baseline gap-2">
                    <span className="text-muted-foreground shrink-0 font-mono text-[11px] tabular-nums">
                      #{event.sequence}
                    </span>
                    <span className="min-w-0 flex-1 text-sm">
                      {describeActivity(event)}
                    </span>
                    <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                      rev {activityRevision(event)}
                    </span>
                  </li>
                ))}
                {(activity.data ?? []).length === 0 && (
                  <li className="text-muted-foreground text-sm">
                    No activity recorded yet.
                  </li>
                )}
              </ol>
            </Section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
