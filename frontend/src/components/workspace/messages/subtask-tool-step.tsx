import { ChevronRightIcon, Loader2Icon } from "lucide-react";
import { useState } from "react";

import { formatTokenCount } from "@/core/messages/usage";
import {
  formatToolArgs,
  type TranscriptEntry,
} from "@/core/tasks/tool-transcript";
import { cn } from "@/lib/utils";

/**
 * One tool call inside a subtask, openable to show what was asked and returned.
 *
 * The card has always named the tool and stopped there, while the request
 * arguments and the tool's output sat unread in the step model. That is
 * precisely the information that explains a failure — which path was denied,
 * what the command printed, why a contract was rejected — so the row opens to
 * reveal it rather than sending the reader to a separate inspector.
 *
 * Bounded on purpose. A `bash` step can print megabytes, and a transcript that
 * renders all of it stops being readable and starts costing frames. The backend
 * already truncates what it persists (`SUBAGENT_STEP_MAX_CHARS`); this is the
 * second bound, and both say so rather than trailing off silently.
 */

const MAX_DETAIL_CHARS = 2_000;

function Detail({ title, body }: { title: string; body: string }) {
  const clipped = body.length > MAX_DETAIL_CHARS;
  return (
    <div className="space-y-1">
      <div className="text-muted-foreground text-[10px] font-medium tracking-wide uppercase">
        {title}
      </div>
      <pre className="bg-muted/60 text-foreground/90 max-h-56 overflow-auto rounded px-2 py-1.5 font-mono text-[11px] leading-5 whitespace-pre-wrap">
        {clipped ? body.slice(0, MAX_DETAIL_CHARS) : body}
      </pre>
      {clipped ? (
        <div className="text-muted-foreground text-[10px]">
          Truncated for display — {body.length.toLocaleString()} characters
          recorded.
        </div>
      ) : null}
    </div>
  );
}

export function SubtaskToolStep({ entry }: { entry: TranscriptEntry }) {
  const [open, setOpen] = useState(false);
  const args = formatToolArgs(entry.args);
  const output = entry.text;
  // A pending call has nothing to show yet, but saying "still running" is
  // itself the answer during a live run, so the row still opens.
  const hasDetail = Boolean(args || output || entry.pending);

  return (
    <div className="min-w-0">
      <button
        aria-expanded={hasDetail ? open : undefined}
        className={cn(
          "group flex w-full items-center gap-1.5 rounded text-left",
          hasDetail &&
            "hover:bg-muted/50 focus-visible:ring-ring -mx-1 px-1 focus-visible:ring-2 focus-visible:outline-none",
        )}
        disabled={!hasDetail}
        type="button"
        onClick={() => setOpen((value) => !value)}
      >
        {hasDetail ? (
          <ChevronRightIcon
            aria-hidden
            className={cn(
              "text-muted-foreground size-3 shrink-0 transition-transform",
              open && "rotate-90",
            )}
          />
        ) : null}
        <span className="text-foreground min-w-0 flex-1 truncate font-mono text-xs leading-5">
          {entry.title || "(no output)"}
        </span>
        {entry.pending ? (
          <span className="text-muted-foreground flex shrink-0 items-center gap-1 text-[11px]">
            <Loader2Icon className="size-2.5 animate-spin motion-reduce:animate-none" />
            running
          </span>
        ) : null}
        {entry.usage ? (
          <span
            className="text-muted-foreground shrink-0 text-[10px] tabular-nums"
            title={`${entry.usage.inputTokens.toLocaleString()} input + ${entry.usage.outputTokens.toLocaleString()} output`}
          >
            {formatTokenCount(entry.usage.inputTokens)} in ·{" "}
            {formatTokenCount(entry.usage.outputTokens)} out
          </span>
        ) : null}
      </button>

      {open && hasDetail ? (
        <div className="mt-1.5 ml-4 space-y-2">
          {args ? <Detail title="Request" body={args} /> : null}
          {output ? (
            <Detail title="Output" body={output} />
          ) : entry.pending ? (
            <div className="text-muted-foreground text-[11px] italic">
              No output recorded yet.
            </div>
          ) : (
            <div className="text-muted-foreground text-[11px] italic">
              The tool returned no output.
            </div>
          )}
          {entry.truncated ? (
            <div className="text-muted-foreground text-[10px]">
              The recorded output was truncated when it was captured.
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
