"use client";

import { useId } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { type CouncilParticipant } from "@/core/messages/human-input";

/**
 * The editable participant cards on a design-meeting preflight.
 *
 * One card per participant, prefilled with the roster writer's suggestions:
 * the model, token budget, reasoning strength, and instructions shown are
 * exactly what runs if nothing is touched. Edits are collected by the parent
 * card and ride back on the depth reply; the backend re-validates every field,
 * so this editor only has to be honest about the prefills, not defensive.
 */

export type ParticipantFormValue = {
  model: string;
  maxTokens: string;
  reasoning: string;
  instructions: string;
};

export function buildInitialParticipantValues(
  participants: CouncilParticipant[],
): Record<string, ParticipantFormValue> {
  const values: Record<string, ParticipantFormValue> = {};
  for (const participant of participants) {
    values[participant.id] = {
      model: participant.model,
      maxTokens: String(participant.max_tokens),
      reasoning: participant.reasoning,
      instructions: participant.instructions,
    };
  }
  return values;
}

function reasoningLabel(level: string) {
  if (level === "extended") {
    return "Extended thinking";
  }
  if (level === "standard") {
    return "Standard";
  }
  return level;
}

function ParticipantCard({
  participant,
  value,
  disabled,
  onChange,
}: {
  participant: CouncilParticipant;
  value: ParticipantFormValue;
  disabled: boolean;
  onChange: (value: ParticipantFormValue) => void;
}) {
  const idBase = useId();
  const modelOptions = [
    ...new Set(
      [participant.model, ...participant.model_options].filter(Boolean),
    ),
  ];
  const reasoningOptions = participant.reasoning_options.length
    ? participant.reasoning_options
    : ["standard", "extended"];

  return (
    <details className="border-border bg-background/60 group rounded-md border">
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm leading-5 select-none">
        <span className="font-medium">{participant.role_label}</span>
        <span className="text-muted-foreground truncate">
          {participant.agent_name}
          {value.model ? ` · ${value.model}` : ""}
        </span>
        {participant.via_generalist ? (
          <Badge className="h-5 rounded px-1.5 text-[10px]" variant="outline">
            generalist stand-in
          </Badge>
        ) : null}
        {participant.focus ? (
          <span className="text-muted-foreground w-full text-xs leading-5">
            {participant.focus}
          </span>
        ) : null}
      </summary>
      <div className="space-y-3 border-t px-3 py-3">
        <div className="grid gap-3 sm:grid-cols-3">
          {modelOptions.length > 0 ? (
            <div className="space-y-1">
              <label
                className="text-xs leading-4 font-medium"
                htmlFor={`${idBase}-model`}
                id={`${idBase}-model-label`}
              >
                Model
              </label>
              <Select
                disabled={disabled}
                value={value.model}
                onValueChange={(next) => onChange({ ...value, model: next })}
              >
                <SelectTrigger
                  id={`${idBase}-model`}
                  className="w-full"
                  aria-labelledby={`${idBase}-model-label`}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {modelOptions.map((model) => (
                    <SelectItem key={model} value={model}>
                      {model}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <div className="space-y-1">
            <label
              className="text-xs leading-4 font-medium"
              htmlFor={`${idBase}-tokens`}
            >
              Token budget
            </label>
            <Input
              id={`${idBase}-tokens`}
              className="text-sm"
              disabled={disabled}
              max={participant.max_tokens_max}
              min={participant.max_tokens_min}
              step={10000}
              type="number"
              value={value.maxTokens}
              onChange={(event) =>
                onChange({ ...value, maxTokens: event.target.value })
              }
            />
          </div>
          <div className="space-y-1">
            <label
              className="text-xs leading-4 font-medium"
              htmlFor={`${idBase}-reasoning`}
              id={`${idBase}-reasoning-label`}
            >
              Reasoning
            </label>
            <Select
              disabled={disabled}
              value={value.reasoning}
              onValueChange={(next) => onChange({ ...value, reasoning: next })}
            >
              <SelectTrigger
                id={`${idBase}-reasoning`}
                className="w-full"
                aria-labelledby={`${idBase}-reasoning-label`}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {reasoningOptions.map((level) => (
                  <SelectItem key={level} value={level}>
                    {reasoningLabel(level)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="space-y-1">
          <label
            className="text-xs leading-4 font-medium"
            htmlFor={`${idBase}-instructions`}
          >
            Instructions
          </label>
          <Textarea
            id={`${idBase}-instructions`}
            className="min-h-16 resize-y text-sm"
            disabled={disabled}
            value={value.instructions}
            onChange={(event) =>
              onChange({ ...value, instructions: event.target.value })
            }
          />
          <p className="text-muted-foreground text-xs leading-4">
            Suggested by the roster writer — edit to tell this participant what
            to argue from or focus on. Your words are passed on exactly as
            typed.
          </p>
        </div>
      </div>
    </details>
  );
}

export function MeetingParticipantEditor({
  participants,
  values,
  disabled,
  onChange,
}: {
  participants: CouncilParticipant[];
  values: Record<string, ParticipantFormValue>;
  disabled: boolean;
  onChange: (id: string, value: ParticipantFormValue) => void;
}) {
  return (
    <div className="space-y-2" data-testid="meeting-participant-editor">
      <p className="text-muted-foreground text-xs leading-5">
        Participants in this meeting. Expand one to change its model, token
        budget, reasoning strength, or instructions before it starts.
      </p>
      {participants.map((participant) => {
        const value = Object.prototype.hasOwnProperty.call(
          values,
          participant.id,
        )
          ? values[participant.id]!
          : {
              model: participant.model,
              maxTokens: String(participant.max_tokens),
              reasoning: participant.reasoning,
              instructions: participant.instructions,
            };
        return (
          <ParticipantCard
            key={participant.id}
            disabled={disabled}
            participant={participant}
            value={value}
            onChange={(next) => onChange(participant.id, next)}
          />
        );
      })}
    </div>
  );
}
