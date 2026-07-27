"use client";

import {
  type LucideIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
} from "lucide-react";
import { useId, useState } from "react";

import { cn } from "@/lib/utils";

export interface ProjectRailCollapsedItem {
  icon: LucideIcon;
  label: string;
  onSelect?: () => void;
}

export function ProjectRailFrame({
  children,
  collapsedItems = [],
}: {
  children: React.ReactNode;
  collapsedItems?: readonly ProjectRailCollapsedItem[];
}) {
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
          "flex h-12 w-full shrink-0 items-center px-2",
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
      {collapsed ? (
        <nav
          id={contentId}
          aria-label="Project rail sections"
          className="flex min-h-0 flex-1 flex-col items-center gap-1 px-2 py-2"
        >
          {collapsedItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.label}
                type="button"
                aria-label={item.label}
                title={item.label}
                className="text-muted-foreground hover:bg-muted hover:text-foreground flex size-8 shrink-0 items-center justify-center rounded-md transition-colors"
                onClick={() => {
                  setCollapsed(false);
                  item.onSelect?.();
                }}
              >
                <Icon className="size-4" />
              </button>
            );
          })}
        </nav>
      ) : (
        <div id={contentId} className="min-h-0 flex-1 overflow-y-auto">
          {children}
        </div>
      )}
    </aside>
  );
}
