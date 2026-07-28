"use client";

import { useParams } from "next/navigation";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { BrowserViewProvider } from "@/components/workspace/browser-view";
import { FilesPanelProvider } from "@/components/workspace/files";
import { ThreadScopedSubtasksProvider } from "@/core/tasks/context";

export function ChatProviders({ children }: { children: React.ReactNode }) {
  const { thread_id: threadId = "unscoped-chat" } = useParams<{
    thread_id?: string;
  }>();
  return (
    <ThreadScopedSubtasksProvider scopeKey={threadId}>
      <ArtifactsProvider>
        <BrowserViewProvider>
          <FilesPanelProvider>
            <PromptInputProvider>{children}</PromptInputProvider>
          </FilesPanelProvider>
        </BrowserViewProvider>
      </ArtifactsProvider>
    </ThreadScopedSubtasksProvider>
  );
}
