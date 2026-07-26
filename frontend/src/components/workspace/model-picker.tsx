"use client";

import { CheckIcon, ChevronRightIcon, InfoIcon } from "lucide-react";
import { useState } from "react";

import {
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorInput,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorName,
} from "@/components/ai-elements/model-selector";
import {
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
} from "@/components/ai-elements/prompt-input";
import {
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import type { Model } from "@/core/models/types";
import { cn } from "@/lib/utils";

/**
 * The composer's model picker: a plain named list, then "Custom".
 *
 * Two things already in the app are recycled here rather than rebuilt. The rows
 * are the same shape the mode menu beside them uses — bold name, one grey line
 * of description, check on the active row — so the two controls read as one
 * family. And "Custom" opens the existing searchable model dialog, which is the
 * only surface that shows raw provider ids (`anthropic/claude-sonnet-4.5`), so
 * it stays useful no matter how long the configured list grows.
 *
 * Each row's description comes from the model's own `description` in
 * config.yaml. It is deliberately not derived here: what distinguishes two
 * models is an operator's decision about their deployment, not something the
 * frontend can infer from a name.
 */
export function ModelPicker({
  models,
  selectedModel,
  onSelect,
  disabled = false,
  searchPlaceholder,
  className,
}: {
  models: readonly Model[];
  /**
   * The *resolved* selection, not the raw configured name. The composer already
   * falls back to the agent default (then the first model) when a thread has no
   * model set, and the check mark has to sit on the model that would actually
   * run — otherwise a fresh thread shows no selection at all.
   */
  selectedModel: Model | undefined;
  onSelect: (modelName: string) => void;
  disabled?: boolean;
  searchPlaceholder?: string;
  className?: string;
}) {
  const [browseOpen, setBrowseOpen] = useState(false);
  const selectedModelName = selectedModel?.name;

  /**
   * Radix returns focus to the menu trigger as it closes, which cancels a
   * dialog opened in the same tick. Deferring past that hand-off is what makes
   * "Custom" actually open the browser instead of flickering.
   */
  const openBrowser = () => {
    requestAnimationFrame(() => setBrowseOpen(true));
  };

  return (
    <>
      <PromptInputActionMenu>
        <PromptInputActionMenuTrigger
          aria-label={`Model: ${selectedModel?.display_name ?? "none selected"}. Change model.`}
          className={cn("max-w-40 min-w-0 gap-1! px-2! sm:max-w-56", className)}
          disabled={disabled}
        >
          <span className="truncate text-xs font-normal">
            {selectedModel?.display_name ?? "Model"}
          </span>
        </PromptInputActionMenuTrigger>
        <PromptInputActionMenuContent className="w-80">
          <DropdownMenuGroup>
            <DropdownMenuLabel className="text-muted-foreground text-xs">
              Model
            </DropdownMenuLabel>
            {models.map((model) => {
              const active = model.name === selectedModelName;
              return (
                <PromptInputActionMenuItem
                  key={model.name}
                  className={cn(
                    active
                      ? "text-accent-foreground"
                      : "text-muted-foreground/65",
                  )}
                  onSelect={() => onSelect(model.name)}
                >
                  <div className="flex min-w-0 flex-col gap-1">
                    <div className="font-bold">{model.display_name}</div>
                    {model.description ? (
                      <div className="text-xs leading-snug">
                        {model.description}
                      </div>
                    ) : null}
                  </div>
                  {active ? (
                    <CheckIcon className="ml-auto size-4 shrink-0" />
                  ) : (
                    <div className="ml-auto size-4 shrink-0" />
                  )}
                </PromptInputActionMenuItem>
              );
            })}
            <DropdownMenuSeparator />
            <PromptInputActionMenuItem
              className="text-muted-foreground/65"
              onSelect={openBrowser}
            >
              <div className="flex min-w-0 flex-col gap-1">
                <div className="flex items-center gap-1.5 font-bold">
                  Custom
                  <InfoIcon
                    className="size-3.5 shrink-0"
                    aria-label="Shows every configured model with its provider id"
                  />
                </div>
                <div className="text-xs leading-snug">
                  Select a specific model directly.
                </div>
              </div>
              <ChevronRightIcon className="ml-auto size-4 shrink-0" />
            </PromptInputActionMenuItem>
          </DropdownMenuGroup>
        </PromptInputActionMenuContent>
      </PromptInputActionMenu>

      {/* No trigger of its own: "Custom" above is what opens it. */}
      <ModelSelector open={browseOpen} onOpenChange={setBrowseOpen}>
        <ModelSelectorContent>
          <ModelSelectorInput placeholder={searchPlaceholder} />
          <ModelSelectorList>
            {models.map((model) => (
              <ModelSelectorItem
                key={model.name}
                value={`${model.display_name} ${model.name} ${model.model}`}
                onSelect={() => {
                  onSelect(model.name);
                  setBrowseOpen(false);
                }}
              >
                <div className="flex min-w-0 flex-1 flex-col">
                  <ModelSelectorName>{model.display_name}</ModelSelectorName>
                  <span className="text-muted-foreground truncate text-[10px]">
                    {model.model}
                  </span>
                </div>
                {model.name === selectedModelName ? (
                  <CheckIcon className="ml-auto size-4" />
                ) : (
                  <div className="ml-auto size-4" />
                )}
              </ModelSelectorItem>
            ))}
          </ModelSelectorList>
        </ModelSelectorContent>
      </ModelSelector>
    </>
  );
}
