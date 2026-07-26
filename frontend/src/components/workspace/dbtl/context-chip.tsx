"use client";

import { Check, ChevronDown, FlaskConical, MessageSquare, Sparkles } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import type { CycleRecord } from "@/core/dbtl";
import {
  CHIP_SCOPE_NOTE,
  type ContextMenuOption,
  type RequestContext,
  chipLabel,
  contextMenuOptions,
} from "@/core/dbtl/context-chip";
import { cn } from "@/lib/utils";

const KIND_ICON = {
  ordinary: MessageSquare,
  cycle: FlaskConical,
  recommend: Sparkles,
} as const;

/**
 * The request-context chip above the composer.
 *
 * Deliberately quiet: ordinary work is the overwhelmingly common case, so the
 * chip reads as a status line rather than a control competing with the
 * composer. It gains weight only when a cycle is selected — that is the state
 * worth noticing, because it changes what the next request means.
 *
 * The scope note is inside the menu rather than beside the chip. It answers a
 * question the user only has while choosing ("does this stick?"), and repeating
 * it permanently above every composer would be noise.
 */
export function DbtlContextChip({
  context,
  cycles,
  onSelect,
  disabled = false,
  className,
}: {
  context: RequestContext;
  cycles: readonly CycleRecord[];
  onSelect: (next: RequestContext) => void;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const options = contextMenuOptions(cycles);
  const label = chipLabel(context, cycles);
  const isScoped = context.kind !== "ordinary";
  const ActiveIcon = KIND_ICON[context.kind];

  // Close on outside click and on Escape. Both are expected of a menu, and
  // Escape matters most: the chip sits directly above the composer, so a menu
  // that traps focus would block typing.
  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const choose = (option: ContextMenuOption) => {
    onSelect({ kind: option.kind, cycleId: option.cycleId });
    setOpen(false);
  };

  const isActive = (option: ContextMenuOption) =>
    option.kind === context.kind && option.cycleId === context.cycleId;

  return (
    <div ref={containerRef} className={cn("relative inline-flex", className)}>
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Request context: ${label}. Change context.`}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "group inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
          "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none",
          "disabled:cursor-not-allowed disabled:opacity-50",
          isScoped
            ? "border-primary/40 bg-primary/10 text-primary hover:bg-primary/15"
            : "text-muted-foreground hover:text-foreground border-border/70 bg-transparent hover:bg-muted/60",
        )}
      >
        <ActiveIcon className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="max-w-[18rem] truncate">{label}</span>
        <ChevronDown
          className={cn("size-3 shrink-0 transition-transform", open && "rotate-180")}
          aria-hidden="true"
        />
      </button>

      {open ? (
        <div
          id={menuId}
          role="menu"
          aria-label="Request context"
          className="bg-popover text-popover-foreground absolute bottom-full left-0 z-50 mb-1.5 w-72 overflow-hidden rounded-lg border shadow-lg"
        >
          <ul className="max-h-72 overflow-y-auto py-1">
            {options.map((option) => {
              const Icon = KIND_ICON[option.kind];
              const active = isActive(option);
              return (
                <li key={`${option.kind}:${option.cycleId ?? "none"}`}>
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={active}
                    onClick={() => choose(option)}
                    className={cn(
                      "hover:bg-accent flex w-full items-start gap-2 px-3 py-2 text-left transition-colors",
                      "focus-visible:bg-accent focus-visible:outline-none",
                    )}
                  >
                    <Icon
                      className="text-muted-foreground mt-0.5 size-3.5 shrink-0"
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium">
                        {option.label}
                      </span>
                      <span className="text-muted-foreground block truncate text-[11px]">
                        {option.description}
                      </span>
                    </span>
                    {active ? (
                      <Check className="text-primary mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="text-muted-foreground border-t px-3 py-2 text-[11px] leading-snug">
            {CHIP_SCOPE_NOTE}
          </p>
        </div>
      ) : null}
    </div>
  );
}
