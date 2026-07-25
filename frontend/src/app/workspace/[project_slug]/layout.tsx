import { ChatProviders } from "@/app/workspace/chats/[thread_id]/providers";

import { ProjectRailShell } from "./project-rail-shell";

export default function ProjectLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ChatProviders>
      <ProjectRailShell>{children}</ProjectRailShell>
    </ChatProviders>
  );
}
