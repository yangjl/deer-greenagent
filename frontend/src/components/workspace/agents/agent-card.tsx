"use client";

import {
  BotIcon,
  MessageSquareIcon,
  Settings2Icon,
  Trash2Icon,
  WorkflowIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { type ComponentProps, type ReactElement, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useDeleteAgent } from "@/core/agents";
import type { AgentInventoryItem } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { AgentSettingsDialog } from "./agent-settings-dialog";

interface AgentCardProps {
  agent: AgentInventoryItem;
}

/**
 * Reveals the full text in a tooltip ONLY when its trigger is actually clipped.
 * Clipping is measured on pointer enter against the trigger's own box, covering
 * both single-line `truncate` (width) and multi-line `line-clamp` (height), so
 * untruncated content never pops a redundant tooltip.
 */
function TruncatedTooltip({
  text,
  children,
}: {
  text: string;
  children: ReactElement;
}) {
  const [truncated, setTruncated] = useState(false);
  return (
    <Tooltip>
      <TooltipTrigger
        asChild
        onPointerEnter={(e) => {
          const el = e.currentTarget;
          setTruncated(
            el.scrollWidth > el.clientWidth ||
              el.scrollHeight > el.clientHeight,
          );
        }}
      >
        {children}
      </TooltipTrigger>
      {truncated && (
        <TooltipContent className="max-w-xs text-wrap break-words">
          {text}
        </TooltipContent>
      )}
    </Tooltip>
  );
}

/**
 * Long, user-controlled labels (agent model, skills, tool groups) that must
 * never break the card layout: width is capped to the parent and the text is
 * truncated with an ellipsis, with the full value revealed on hover.
 */
function TruncatedBadge({
  label,
  variant,
  className,
}: {
  label: string;
  variant: ComponentProps<typeof Badge>["variant"];
  className?: string;
}) {
  return (
    <TruncatedTooltip text={label}>
      <Badge
        variant={variant}
        className={cn("block max-w-full truncate", className)}
      >
        {label}
      </Badge>
    </TruncatedTooltip>
  );
}

export function AgentCard({ agent }: AgentCardProps) {
  const { t } = useI18n();
  const router = useRouter();
  const deleteAgent = useDeleteAgent();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  function handleChat() {
    router.push(
      agent.origin === "builtin"
        ? "/workspace/chats/new"
        : `/workspace/agents/${encodeURIComponent(agent.name)}/chats/new`,
    );
  }

  async function handleDelete() {
    try {
      await deleteAgent.mutateAsync(agent.name);
      toast.success(t.agents.deleteSuccess);
      setDeleteOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <Card className="group flex flex-col transition-shadow hover:shadow-md">
        <CardHeader className="pb-3">
          <div className="flex min-w-0 items-start justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <div className="bg-primary/10 text-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                <BotIcon className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <TruncatedTooltip text={agent.name}>
                  <CardTitle className="truncate text-base">
                    {agent.name}
                  </CardTitle>
                </TruncatedTooltip>
                <div className="mt-1 flex min-w-0 flex-wrap gap-1">
                  <Badge variant="outline" className="text-xs">
                    {agent.kind === "agent"
                      ? t.agents.agentType
                      : t.agents.subagentType}
                  </Badge>
                  <Badge variant="secondary" className="text-xs">
                    {agent.origin === "builtin"
                      ? t.agents.builtinType
                      : t.agents.customType}
                  </Badge>
                  {agent.model && (
                    <TruncatedBadge
                      label={agent.model}
                      variant="secondary"
                      className="text-xs"
                    />
                  )}
                </div>
              </div>
            </div>
          </div>
          {agent.description && (
            <TruncatedTooltip text={agent.description}>
              <CardDescription className="mt-2 line-clamp-2 text-sm">
                {agent.description}
              </CardDescription>
            </TruncatedTooltip>
          )}
        </CardHeader>

        {(agent.tool_groups?.length ?? 0) +
          (agent.tools?.length ?? 0) +
          (agent.skills?.length ?? 0) +
          (agent.dbtl_capabilities?.length ?? 0) >
          0 && (
          <CardContent className="pt-0 pb-3">
            <div className="flex flex-wrap gap-1">
              {agent.tool_groups?.map((group) => (
                <TruncatedBadge
                  key={`tg:${group}`}
                  label={group}
                  variant="outline"
                  className="text-xs"
                />
              ))}
              {agent.tools?.map((tool) => (
                <TruncatedBadge
                  key={`tool:${tool}`}
                  label={tool}
                  variant="outline"
                  className="text-xs"
                />
              ))}
              {agent.skills?.map((skill) => (
                <TruncatedBadge
                  key={`sk:${skill}`}
                  label={skill}
                  variant="secondary"
                  className="text-xs"
                />
              ))}
              {agent.dbtl_capabilities?.map((capability) => (
                <TruncatedBadge
                  key={`dbtl:${capability}`}
                  label={capability}
                  variant="secondary"
                  className="text-xs"
                />
              ))}
            </div>
          </CardContent>
        )}

        <CardFooter className="mt-auto flex items-center justify-between gap-2 pt-3">
          {agent.can_chat ? (
            <Button size="sm" className="flex-1" onClick={handleChat}>
              <MessageSquareIcon className="mr-1.5 h-3.5 w-3.5" />
              {t.agents.chat}
            </Button>
          ) : (
            <div className="text-muted-foreground flex min-h-8 flex-1 items-center gap-1.5 text-xs">
              <WorkflowIcon className="h-3.5 w-3.5" />
              {t.agents.delegatedOnly}
            </div>
          )}
          {agent.can_manage && (
            <div className="flex gap-1">
              <Button
                size="icon"
                variant="ghost"
                className="h-8 w-8 shrink-0"
                onClick={() => setSettingsOpen(true)}
                title={t.agents.settings}
              >
                <Settings2Icon className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                className="text-destructive hover:text-destructive h-8 w-8 shrink-0"
                onClick={() => setDeleteOpen(true)}
                title={t.agents.delete}
              >
                <Trash2Icon className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}
        </CardFooter>
      </Card>

      {/* Model settings — mounted only while open so its form state always
          re-seeds from the latest agent props (avoids stale values on reopen). */}
      {agent.can_manage && settingsOpen && (
        <AgentSettingsDialog
          agent={agent}
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
        />
      )}

      {/* Delete Confirm */}
      <Dialog
        open={agent.can_manage && deleteOpen}
        onOpenChange={setDeleteOpen}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.agents.delete}</DialogTitle>
            <DialogDescription>{t.agents.deleteConfirm}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteOpen(false)}
              disabled={deleteAgent.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleteAgent.isPending}
            >
              {deleteAgent.isPending ? t.common.loading : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
