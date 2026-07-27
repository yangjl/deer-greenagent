"use client";

import { PanelLeftCloseIcon, PanelLeftOpenIcon } from "lucide-react";
import { useId, useState } from "react";

import { cn } from "@/lib/utils";

export function ProjectRailFrame({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const contentId = useId();

  return (
    <aside
      data-state={collapsed ? "collapsed" : "expanded"}
      className={cn(
        "border-border bg-muted/20 hidden shrink-0 flex-col overflow-hidden border-r transition-[width] duration-200 ease-linear md:flex",
        collapsed ? "w-12" : "w-64",
      )}
    >
      <div
        className={cn(
          "flex h-12 shrink-0 items-center px-2",
          collapsed ? "justify-center" : "justify-end",
        )}
      >
        <button
          type="button"
          aria-controls={contentId}
          aria-expanded={!collapsed}
          aria-label={
            collapsed ? "Expand project rail" : "Collapse project rail"
          }
          title={collapsed ? "Expand project rail" : "Collapse project rail"}
          className="text-muted-foreground hover:text-foreground flex size-7 items-center justify-center rounded-md transition-colors"
          onClick={() => setCollapsed((value) => !value)}
        >
          {collapsed ? (
            <PanelLeftOpenIcon className="size-4" />
          ) : (
            <PanelLeftCloseIcon className="size-4" />
          )}
        </button>
      </div>
      {!collapsed && (
        <div id={contentId} className="min-h-0 flex-1 overflow-y-auto">
          {children}
        </div>
      )}
    </aside>
  );
}
