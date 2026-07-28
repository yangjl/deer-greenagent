"use client";

import {
  BrainIcon,
  ChevronRightIcon,
  GavelIcon,
  Loader2Icon,
  MessageSquareTextIcon,
  TriangleAlertIcon,
  WrenchIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  meetingTranscriptWithResult,
  parseMeetingResult,
  type MeetingResultView,
  type MeetingTranscriptEntry,
} from "@/core/tasks/meeting-transcript";
import type { Subtask } from "@/core/tasks/types";
import { cn } from "@/lib/utils";

/**
 * What one meeting participant actually did, opened from its lane.
 *
 * The debate panel answers "who is arguing and how far along are they"; this
 * answers "what did they do, and why did it end that way" — the question a
 * reader has the moment a participant reports no result. Each entry is a step
 * in the participant's run: a reasoning turn, a tool call paired with what it
 * returned, or the closing position. Selecting one shows it in full on the
 * right, so a long tool output does not push the timeline off screen.
 */

const ENTRY_ICONS: Record<MeetingTranscriptEntry["kind"], typeof WrenchIcon> = {
  thinking: BrainIcon,
  tool: WrenchIcon,
  answer: GavelIcon,
};

function formatArgs(args: unknown): string {
  if (args === undefined || args === null) {
    return "";
  }
  if (typeof args === "string") {
    return args;
  }
  try {
    return JSON.stringify(args, null, 2) ?? "";
  } catch {
    // A value JSON cannot serialize (a cycle, a BigInt) is still worth
    // acknowledging; naming its type beats rendering "[object Object]".
    return `(unserializable ${typeof args})`;
  }
}

function EntryRow({
  entry,
  selected,
  onSelect,
}: {
  entry: MeetingTranscriptEntry;
  selected: boolean;
  onSelect: () => void;
}) {
  const Icon = ENTRY_ICONS[entry.kind];
  return (
    <li>
      <button
        aria-current={selected || undefined}
        className={cn(
          "hover:bg-muted/60 flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors",
          selected && "bg-muted",
        )}
        type="button"
        onClick={onSelect}
      >
        <span
          className={cn(
            "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded",
            entry.kind === "tool"
              ? "bg-muted text-muted-foreground"
              : "bg-primary/10 text-primary",
          )}
          aria-hidden
        >
          <Icon className="size-3" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="text-foreground block truncate font-mono text-xs leading-5">
            {entry.title || "(no output)"}
          </span>
          {entry.pending ? (
            <span className="text-muted-foreground flex items-center gap-1 text-[11px]">
              <Loader2Icon className="size-2.5 animate-spin" />
              running
            </span>
          ) : null}
        </span>
        <ChevronRightIcon className="text-muted-foreground mt-1 size-3 shrink-0" />
      </button>
    </li>
  );
}

function Section({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone?: "warn";
}) {
  if (items.length === 0) {
    return null;
  }
  return (
    <section className="space-y-1">
      <h4
        className={cn(
          "text-[11px] font-medium tracking-wide uppercase",
          tone === "warn"
            ? "text-amber-600 dark:text-amber-500"
            : "text-muted-foreground",
        )}
      >
        {title}
      </h4>
      <ul className="list-disc space-y-1 pl-4 text-xs leading-5">
        {items.map((item, index) => (
          <li key={`${title}-${index}`}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The closing position as a reviewer reads it.
 *
 * Where the meeting disagreed comes **first**: it is what tells someone
 * whether the synthesis is a conclusion or an average, and it is worthless
 * once they have already read the synthesis as settled. The same ordering the
 * review document uses.
 */
function ResultDetail({ result }: { result: MeetingResultView }) {
  const { consensus } = result;
  return (
    <div className="space-y-4">
      {result.clarificationQuestion ? (
        <section className="border-primary/30 bg-primary/5 space-y-1 rounded-md border p-2">
          <h4 className="text-primary text-[11px] font-medium tracking-wide uppercase">
            Needs your decision
          </h4>
          <p className="text-xs leading-5">{result.clarificationQuestion}</p>
        </section>
      ) : null}

      {consensus ? (
        <div className="space-y-3">
          <Section title="Agreed" items={consensus.agreements} />
          {consensus.disagreements.length > 0 ? (
            <section className="space-y-1">
              <h4 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
                Contested
              </h4>
              <ul className="space-y-2">
                {consensus.disagreements.map((item, index) => (
                  <li
                    key={`${item.topic}-${index}`}
                    className="border-border rounded-md border p-2"
                  >
                    <p className="text-xs leading-5 font-medium">
                      {item.topic}
                    </p>
                    {item.positions.length > 0 ? (
                      <ul className="text-muted-foreground mt-1 list-disc space-y-0.5 pl-4 text-xs leading-5">
                        {item.positions.map((position, positionIndex) => (
                          <li key={positionIndex}>{position}</li>
                        ))}
                      </ul>
                    ) : null}
                    <p
                      className={cn(
                        "mt-1 text-xs leading-5",
                        item.resolution
                          ? "text-foreground"
                          : "text-amber-600 dark:text-amber-500",
                      )}
                    >
                      {item.resolution
                        ? `Settled: ${item.resolution}`
                        : "Not resolved"}
                    </p>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          <Section title="Open questions" items={consensus.openQuestions} />
        </div>
      ) : null}

      {result.summary ? (
        <section className="space-y-1">
          <h4 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
            Synthesis
          </h4>
          <p className="text-xs leading-5 whitespace-pre-wrap">
            {result.summary}
          </p>
        </section>
      ) : null}

      <Section title="Claims" items={result.claims} />
      <Section title="Failed checks" items={result.failedChecks} tone="warn" />
      <Section title="Limitations" items={result.limitations} />
      <Section title="Recommended next" items={result.nextActions} />
      <Section title="Evidence" items={result.evidence} />
    </div>
  );
}

function EntryDetail({ entry }: { entry: MeetingTranscriptEntry | null }) {
  if (!entry) {
    return (
      <p className="text-muted-foreground p-4 text-sm">
        Select a step to see its arguments and output.
      </p>
    );
  }
  const args = formatArgs(entry.args);
  // A closing position is a validated JSON contract, not something anyone
  // wants to read as JSON. When it parses, render it; when it does not, the
  // raw text below is still the honest fallback.
  const result = entry.kind === "answer" ? parseMeetingResult(entry.text) : null;
  if (result) {
    return (
      <div className="space-y-4 p-4">
        <div className="space-y-1">
          <p className="text-foreground text-xs font-medium">{entry.title}</p>
          <p className="text-muted-foreground text-[11px]">
            Closing position
            {result.status ? ` · ${result.status.replace(/_/g, " ")}` : ""}
          </p>
        </div>
        <ResultDetail result={result} />
      </div>
    );
  }
  return (
    <div className="space-y-4 p-4">
      <div className="space-y-1">
        <p className="text-foreground font-mono text-xs break-all">
          {entry.title}
        </p>
        <p className="text-muted-foreground text-[11px]">
          {entry.kind === "tool"
            ? "Tool call"
            : entry.kind === "answer"
              ? "Closing position"
              : "Reasoning"}
          {entry.truncated ? " · output truncated by the recorder" : ""}
        </p>
      </div>

      {args ? (
        <section className="space-y-1">
          <h4 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
            Request
          </h4>
          <pre className="bg-muted/60 max-h-56 overflow-auto rounded-md p-2 font-mono text-[11px] leading-5 whitespace-pre-wrap">
            {args}
          </pre>
        </section>
      ) : null}

      <section className="space-y-1">
        <h4 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
          {entry.kind === "tool" ? "Output" : "Text"}
        </h4>
        {entry.pending ? (
          <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
            <Loader2Icon className="size-3 animate-spin" />
            Still running — no output recorded yet.
          </p>
        ) : entry.text ? (
          <pre className="bg-muted/40 overflow-auto rounded-md p-2 font-mono text-[11px] leading-5 whitespace-pre-wrap">
            {entry.text}
          </pre>
        ) : (
          <p className="text-muted-foreground text-xs">
            This step recorded no output.
          </p>
        )}
      </section>
    </div>
  );
}

export function MeetingParticipantInspector({
  task,
  open,
  onOpenChange,
}: {
  task: Subtask | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const entries = useMemo(
    () => (task ? meetingTranscriptWithResult(task) : []),
    [task],
  );
  const selected =
    entries.find((entry) => entry.id === selectedId) ??
    entries[entries.length - 1] ??
    null;
  const seat = task?.councilSeat;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className="flex w-full flex-col gap-0 p-0 sm:max-w-3xl"
        side="right"
      >
        <SheetHeader className="border-b">
          <SheetTitle className="flex flex-wrap items-center gap-2 text-sm">
            {seat?.roleLabel ?? "Participant"}
            {seat?.focus ? (
              <span className="text-muted-foreground font-normal">
                {seat.focus}
              </span>
            ) : null}
            {task?.status === "failed" ? (
              <Badge className="gap-1" variant="outline">
                <TriangleAlertIcon className="size-3" />
                {task.stopReason?.replace(/_/g, " ") ?? "no result"}
              </Badge>
            ) : null}
          </SheetTitle>
          <SheetDescription className="text-xs">
            {seat?.agentName}
            {seat?.model ? ` · ${seat.model}` : ""} ·{" "}
            {entries.filter((entry) => entry.kind === "tool").length} tool call
            {entries.filter((entry) => entry.kind === "tool").length === 1
              ? ""
              : "s"}
          </SheetDescription>
        </SheetHeader>

        {entries.length === 0 ? (
          <p className="text-muted-foreground p-4 text-sm">
            <MessageSquareTextIcon className="mr-1.5 inline size-3.5" />
            No steps were recorded for this participant yet.
          </p>
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-1 sm:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
            <ScrollArea className="min-h-0 border-b sm:border-r sm:border-b-0">
              <ul className="space-y-0.5 p-2">
                {entries.map((entry) => (
                  <EntryRow
                    key={entry.id}
                    entry={entry}
                    selected={selected?.id === entry.id}
                    onSelect={() => setSelectedId(entry.id)}
                  />
                ))}
              </ul>
            </ScrollArea>
            <ScrollArea className="min-h-0">
              <EntryDetail entry={selected} />
            </ScrollArea>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
