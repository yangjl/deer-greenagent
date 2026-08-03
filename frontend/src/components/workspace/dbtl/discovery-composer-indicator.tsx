"use client";

import { FlaskConical } from "lucide-react";

import { discoveryStatusLabel, type DiscoveryStatusItem } from "@/core/dbtl";
import { cn } from "@/lib/utils";

/**
 * A read-only, server-derived composer signal. The chat and native card remain
 * the input surfaces; this control never starts, advances, or dismisses work.
 */
export function DiscoveryComposerIndicator({
  discovery,
  className,
}: {
  discovery: DiscoveryStatusItem | null | undefined;
  className?: string;
}) {
  if (!discovery) return null;

  return (
    <span
      aria-live="polite"
      className={cn(
        "text-muted-foreground inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs",
        discovery.status === "offered" && "text-foreground",
        className,
      )}
      title={`Discovery ${discovery.id}, revision ${discovery.revision}. No cycle exists until you accept the server card.`}
    >
      <FlaskConical aria-hidden="true" className="size-3.5" />
      <span>{discoveryStatusLabel(discovery.status)}</span>
    </span>
  );
}
