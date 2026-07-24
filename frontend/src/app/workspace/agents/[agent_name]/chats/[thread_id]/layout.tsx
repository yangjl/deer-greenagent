"use client";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { FilesPanelProvider } from "@/components/workspace/files";
import { SubtasksProvider } from "@/core/tasks/context";

export default function AgentChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <SubtasksProvider>
      <ArtifactsProvider>
        <FilesPanelProvider>
          <PromptInputProvider>{children}</PromptInputProvider>
        </FilesPanelProvider>
      </ArtifactsProvider>
    </SubtasksProvider>
  );
}
