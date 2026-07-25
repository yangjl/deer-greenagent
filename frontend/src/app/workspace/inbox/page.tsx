"use client";

import { ArrowRight, Inbox, MessageSquarePlus } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import { useInfiniteThreads } from "@/core/threads/hooks";
import { pathOfThread, titleOfThread } from "@/core/threads/utils";

export default function InboxPage() {
  const query = useInfiniteThreads();
  const threads = useMemo(() => {
    const seen = new Set<string>();
    return (query.data?.pages.flat() ?? []).filter((thread) => {
      if (seen.has(thread.thread_id)) return false;
      seen.add(thread.thread_id);
      return true;
    });
  }, [query.data]);

  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody className="items-stretch overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-5 py-7 sm:px-8 sm:py-10">
          <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
            <div>
              <div className="text-muted-foreground mb-2 flex items-center gap-2 text-xs font-medium uppercase">
                <Inbox className="size-3.5" />
                Global inbox
              </div>
              <h1 className="text-3xl font-semibold">Conversations</h1>
              <p className="text-muted-foreground mt-2 max-w-2xl text-sm">
                Talk with people and agents before the work belongs to a
                project. Legacy conversations remain private here by default.
              </p>
            </div>
            <Button asChild>
              <Link href="/workspace/chats/new">
                <MessageSquarePlus className="size-4" />
                New conversation
              </Link>
            </Button>
          </div>

          <div className="mt-10 border-t">
            {query.isLoading ? (
              <div className="text-muted-foreground py-10 text-sm">
                Loading conversations…
              </div>
            ) : query.error ? (
              <div className="text-destructive py-10 text-sm">
                {query.error.message}
              </div>
            ) : threads.length ? (
              threads.map((thread) => (
                <Link
                  key={thread.thread_id}
                  href={pathOfThread(thread)}
                  className="group hover:bg-muted/50 grid grid-cols-[minmax(0,1fr)_24px] items-center gap-4 border-b px-1 py-5 transition-colors"
                >
                  <div className="min-w-0">
                    <div className="truncate font-medium">
                      {titleOfThread(thread)}
                    </div>
                    <div className="text-muted-foreground mt-1 text-xs">
                      Private owner · Global Inbox
                    </div>
                  </div>
                  <ArrowRight className="text-muted-foreground size-4 transition-transform group-hover:translate-x-1" />
                </Link>
              ))
            ) : (
              <div className="py-16 text-center">
                <p className="text-muted-foreground text-sm">
                  No conversations yet.
                </p>
                <Button asChild variant="outline" className="mt-5">
                  <Link href="/workspace/chats/new">Start in Inbox</Link>
                </Button>
              </div>
            )}
          </div>

          {query.hasNextPage && (
            <Button
              className="mt-6"
              variant="ghost"
              disabled={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            >
              {query.isFetchingNextPage ? "Loading…" : "Load older"}
            </Button>
          )}
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
