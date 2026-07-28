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

function EntryDetail({ entry }: { entry: MeetingTranscriptEntry | null }) {
  if (!entry) {
    return (
      <p className="text-muted-foreground p-4 text-sm">
        Select a step to see its arguments and output.
      </p>
    );
  }
  const args = formatArgs(entry.args);
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
