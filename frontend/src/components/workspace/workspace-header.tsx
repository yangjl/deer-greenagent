"use client";

import { Sprout } from "lucide-react";
import Link from "next/link";

import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export function WorkspaceHeader({ className }: { className?: string }) {
  const { state } = useSidebar();
  return (
    <>
      <div
        className={cn(
          "group/workspace-header flex h-12 flex-col justify-center",
          className,
        )}
      >
        {state === "collapsed" ? (
          <div className="group-has-data-[collapsible=icon]/sidebar-wrapper:-translate-y flex w-full cursor-pointer items-center justify-center">
            <div className="block pt-1 font-semibold text-emerald-700 group-hover/workspace-header:hidden dark:text-emerald-400">
              GA
            </div>
            <SidebarTrigger className="hidden pl-2 group-hover/workspace-header:block" />
          </div>
        ) : (
          <div className="flex items-center justify-between gap-2">
            {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ? (
              <Link
                href="/"
                className="ml-2 flex items-center gap-2 font-semibold"
              >
                <Sprout className="size-4 text-emerald-700 dark:text-emerald-400" />
                GreenAgent
              </Link>
            ) : (
              <div className="ml-2 flex cursor-default items-center gap-2 font-semibold">
                <Sprout className="size-4 text-emerald-700 dark:text-emerald-400" />
                GreenAgent
              </div>
            )}
            <SidebarTrigger />
          </div>
        )}
      </div>
    </>
  );
}
