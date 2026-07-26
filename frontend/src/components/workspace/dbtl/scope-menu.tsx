"use client";

import { CheckIcon, FlaskConicalIcon } from "lucide-react";

import {
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
} from "@/components/ai-elements/prompt-input";
import {
  DropdownMenuGroup,
  DropdownMenuLabel,
} from "@/components/ui/dropdown-menu";
import type { CycleRecord } from "@/core/dbtl";
import {
  SCOPE_NOTE,
  type RequestContext,
  type ScopeMenuOption,
  scopeLabel,
  scopeMenuOptions,
} from "@/core/dbtl/composer-scope";
import { cn } from "@/lib/utils";

/**
 * The DBTL scope selector, in the composer's tool row beside attachments,
 * voice, and the model mode.
 *
 * It lives here rather than above the composer because the composer is the
 * app's one input surface: choosing a scope is part of composing the request,
 * not a separate mode the user enters first. For ordinary work — the
 * overwhelmingly common case — it stays an icon, matching the other tool-row
 * buttons. It gains a text label only when the next request is scoped to a
 * cycle or about to start one, which is the state worth noticing because it
 * changes what the message means.
 *
 * The scope note lives inside the menu rather than beside the trigger: it
 * answers a question the user only has while choosing.
 */
export function DbtlScopeMenu({
  context,
  cycles,
  onSelect,
  disabled = false,
}: {
  context: RequestContext;
  cycles: readonly CycleRecord[];
  onSelect: (next: RequestContext) => void;
  disabled?: boolean;
}) {
  const options = scopeMenuOptions(cycles);
  const label = scopeLabel(context, cycles);
  const isScoped = context.kind !== "ordinary";

  const isActive = (option: ScopeMenuOption) =>
    option.kind === context.kind && option.cycleId === context.cycleId;

  return (
    <PromptInputActionMenu>
      <PromptInputActionMenuTrigger
        aria-label={`DBTL scope: ${label}. Change scope.`}
        className={cn(
          "max-w-40 gap-1! px-2! sm:max-w-none",
          isScoped && "text-emerald-700 dark:text-emerald-400",
        )}
        disabled={disabled}
        title={isScoped ? label : "DBTL scope for your next request"}
      >
        <FlaskConicalIcon className="size-3" />
        {/* Ordinary work needs no words: the icon plus the other quiet tool
            buttons already say "nothing special is happening". */}
        {isScoped ? (
          <span className="truncate text-xs font-normal">{label}</span>
        ) : null}
      </PromptInputActionMenuTrigger>
      <PromptInputActionMenuContent className="w-80">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="text-muted-foreground text-xs">
            DBTL scope
          </DropdownMenuLabel>
          {options.map((option) => {
            const active = isActive(option);
            return (
              <PromptInputActionMenuItem
                key={`${option.kind}:${option.cycleId ?? "none"}`}
                className={cn(
                  active
                    ? "text-accent-foreground"
                    : "text-muted-foreground/65",
                )}
                onSelect={() =>
                  onSelect({ kind: option.kind, cycleId: option.cycleId })
                }
              >
                <div className="flex min-w-0 flex-col gap-1">
                  <div className="font-bold">{option.label}</div>
                  <div className="text-xs leading-snug">
                    {option.description}
                  </div>
                </div>
                {active ? (
                  <CheckIcon className="ml-auto size-4 shrink-0" />
                ) : (
                  <div className="ml-auto size-4 shrink-0" />
                )}
              </PromptInputActionMenuItem>
            );
          })}
          <p className="text-muted-foreground border-t px-2 pt-2 text-[11px] leading-snug">
            {SCOPE_NOTE}
          </p>
        </DropdownMenuGroup>
      </PromptInputActionMenuContent>
    </PromptInputActionMenu>
  );
}
