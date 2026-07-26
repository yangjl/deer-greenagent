"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { type PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import { BrowserTrigger } from "@/components/workspace/browser-view";
import {
  ChatBox,
  useSpecificChatMode,
  useThreadChat,
} from "@/components/workspace/chats";
import {
  DbtlContextChip,
  UpgradeProposalCard,
  useDbtlUpgradeProposal,
  useProjectCycleSelection,
} from "@/components/workspace/dbtl";
import { ExportTrigger } from "@/components/workspace/export-trigger";
import { FilesTrigger } from "@/components/workspace/files";
import { GoalStatus } from "@/components/workspace/goal-status";
import {
  InputBox,
  type InputBoxSubmitOptions,
} from "@/components/workspace/input-box";
import {
  MessageList,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
} from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import {
  SidecarProvider,
  SidecarTrigger,
} from "@/components/workspace/sidecar";
import { ThreadScheduledTasksLink } from "@/components/workspace/thread-scheduled-tasks-link";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { useActiveGoal } from "@/components/workspace/use-active-goal";
import { Welcome } from "@/components/workspace/welcome";
import {
  ORDINARY_REQUEST_CONTEXT,
  type RequestContext,
  nextContextAfterSend,
  normalizeContext,
  proposalContextPayload,
  runActivityMetadata,
  runContextPayload,
  useDbtlFeature,
  useProjectCycles,
} from "@/core/dbtl";
import { useBrowserControlEnabled } from "@/core/features";
import { useI18n } from "@/core/i18n/hooks";
import {
  buildHumanInputResponseText,
  hasOpenHumanInputRequest,
  type HumanInputRequest,
  type HumanInputResponse,
} from "@/core/messages/human-input";
import { isHiddenFromUIMessage } from "@/core/messages/utils";
import { useModels } from "@/core/models/hooks";
import { useNotification } from "@/core/notification/hooks";
import { useLocalSettings, useThreadSettings } from "@/core/settings";
import {
  useBranchThread,
  useThreadMetadata,
  useThreadStream,
  useThreadTokenUsage,
} from "@/core/threads/hooks";
import { threadTokenUsageToTokenUsage } from "@/core/threads/token-usage";
import { textOfMessage } from "@/core/threads/utils";
import { pathOfProject, useProjectBySlug } from "@/core/workspaces";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export default function ChatPage() {
  const { t } = useI18n();
  const router = useRouter();
  const { threadId, setThreadId, isNewThread, setIsNewThread, isMock } =
    useThreadChat();
  // Project scope (foundation demo): a conversation under
  // /workspace/<project-slug> keeps the project rail mounted and routes
  // within the project; the `?project_id=` form is the flat-route fallback.
  // Either way the project id is stamped onto the thread metadata on lazy
  // creation so the project can list its conversations.
  const searchParams = useSearchParams();
  const { project_slug: routeProjectSlug } = useParams<{
    project_slug?: string;
  }>();
  const { project: routeProject } = useProjectBySlug(routeProjectSlug);
  const projectId = routeProjectSlug
    ? (routeProject?.id ?? null)
    : searchParams.get("project_id");
  const chatBasePath = routeProjectSlug
    ? pathOfProject(routeProjectSlug)
    : "/workspace/chats";
  // `isNewThread` tracks whether the backend has the thread yet — gates the
  // SDK's history fetch (see issue #2746).  `isWelcomeMode` is the visual
  // welcome layout (centered input, hero, quick actions); we flip it to false
  // the moment the user submits so the UI animates immediately, even though
  // `isNewThread` stays true until the backend actually creates the thread.
  const [isWelcomeMode, setIsWelcomeMode] = useState(isNewThread);
  const [settings, setSettings] = useThreadSettings(threadId);
  const [localSettings, setLocalSettings] = useLocalSettings();
  const { enabled: browserControlEnabled } = useBrowserControlEnabled();
  const { tokenUsageEnabled } = useModels();
  const threadTokenUsage = useThreadTokenUsage(
    isNewThread || isMock ? undefined : threadId,
    { enabled: tokenUsageEnabled && !isMock },
  );
  const threadMetadata = useThreadMetadata(threadId, {
    enabled: !isNewThread && !isMock,
    isMock,
  });
  const branchThread = useBranchThread();
  const backendTokenUsage = threadTokenUsageToTokenUsage(threadTokenUsage.data);
  const mountedRef = useRef(false);
  useSpecificChatMode();

  useEffect(() => {
    mountedRef.current = true;
  }, []);

  // Keep welcome layout in sync when navigating between threads (sidebar
  // clicks, "new chat" button).  Submitting in /chats/new flips the layout
  // via onSend below — `isNewThread` stays true until onStart, so this effect
  // is harmless during the submit transition.
  useEffect(() => {
    setIsWelcomeMode(isNewThread);
  }, [isNewThread]);

  const { showNotification } = useNotification();
  const { feature: dbtlFeature } = useDbtlFeature();
  const showContextChip = Boolean(
    projectId && dbtlFeature?.graph_execution_enabled,
  );

  const {
    thread,
    pendingUsageMessages,
    sendMessage,
    regenerateMessage,
    isUploading,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    displayThreadId: threadId,
    projectSupervisorEnabled: showContextChip,
    context: settings.context,
    projectId,
    isMock,
    // onSend only animates the UI; do NOT flip `isNewThread` here — the
    // LangGraph SDK eagerly fetches /history the moment it receives a
    // thread id and assumes the thread exists on the backend (issue #2746).
    onSend: () => {
      setIsWelcomeMode(false);
    },
    onStart: (createdThreadId) => {
      // ! Important: Never use next.js router for navigation in this case, otherwise it will cause the thread to re-mount and lose all states. Use native history API instead.
      history.replaceState(null, "", `${chatBasePath}/${createdThreadId}`);
      setThreadId(createdThreadId);
      setIsNewThread(false);
    },
    onFinish: (state) => {
      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages.at(-1);
        if (lastMessage) {
          const textContent = textOfMessage(lastMessage);
          if (textContent) {
            body =
              textContent.length > 200
                ? textContent.substring(0, 200) + "..."
                : textContent;
          }
        }
        showNotification(state.title, { body });
      }
    },
  });

  const hasThreadMessages = thread.messages.length > 0;

  useEffect(() => {
    if (
      !isNewThread &&
      !isMock &&
      threadMetadata.data === null &&
      !threadMetadata.isLoading &&
      !threadMetadata.isFetching &&
      !isHistoryLoading &&
      !hasMoreHistory &&
      !hasThreadMessages
    ) {
      router.replace(`${chatBasePath}/new`);
    }
  }, [
    chatBasePath,
    hasMoreHistory,
    hasThreadMessages,
    isHistoryLoading,
    isMock,
    isNewThread,
    router,
    threadMetadata.data,
    threadMetadata.isFetching,
    threadMetadata.isLoading,
  ]);

  // A DBTL cycle belongs to a project, so the proposal only exists inside one.
  const upgradeProposal = useDbtlUpgradeProposal(projectId);
  const {
    selectedCycleId,
    pendingDesignKickoff,
    consumeDesignKickoff,
  } = useProjectCycleSelection();
  const { evaluate: evaluateForUpgrade } = upgradeProposal;

  // The context chip only appears where the supervisor can actually route, so
  // the control is never offered when changing it would do nothing.
  const { data: projectCycles } = useProjectCycles(projectId);
  const [requestContext, setRequestContext] = useState<RequestContext>(
    ORDINARY_REQUEST_CONTEXT,
  );
  const cycleList = projectCycles?.cycles ?? [];
  // Resolve against the live list on every render: a cycle can be completed in
  // another tab, and the chip must not keep claiming a scope that is gone.
  const effectiveContext = normalizeContext(requestContext, cycleList);

  const handleSubmit = useCallback(
    (message: PromptInputMessage, options?: InputBoxSubmitOptions) => {
      // Travels in the run request's `context`, never `configurable`, which is
      // checkpointed and would make a one-off selection permanent.
      const dbtlContext = showContextChip
        ? runContextPayload(effectiveContext)
        : undefined;
      const proposalContext = showContextChip
        ? proposalContextPayload(effectiveContext)
        : { selectedCycleId, explicitChoice: null };
      const sentContext = effectiveContext;
      const sendPromise = sendMessage(threadId, message, dbtlContext, {
        ...options,
        runMetadata: showContextChip
          ? runActivityMetadata(sentContext)
          : undefined,
        onSent: () => {
          options?.onSent?.();
          // Consume the one-shot choice only when the stream hook accepts the
          // send. A click dropped by the in-flight guard must not lose it.
          if (showContextChip) {
            setRequestContext(nextContextAfterSend(sentContext));
          }
          // Shadow evaluation runs beside the accepted send, never in front
          // of it, and uses the exact same routing inputs as the supervisor.
          void evaluateForUpgrade({
            text: message.text,
            threadId,
            ...proposalContext,
          });
        },
      });
      if (message.files.length > 0) {
        return sendPromise;
      }
      void sendPromise;
    },
    [
      sendMessage,
      threadId,
      selectedCycleId,
      evaluateForUpgrade,
      showContextChip,
      effectiveContext,
    ],
  );
  const designKickoffInFlight = useRef<number | null>(null);
  useEffect(() => {
    if (
      !pendingDesignKickoff ||
      designKickoffInFlight.current === pendingDesignKickoff.nonce ||
      thread.isLoading ||
      isMock
    ) {
      return;
    }
    const kickoff = pendingDesignKickoff;
    const cycleContext: RequestContext = {
      kind: "cycle",
      cycleId: kickoff.cycleId,
    };
    designKickoffInFlight.current = kickoff.nonce;
    setRequestContext(cycleContext);
    void Promise.resolve(
      sendMessage(
        threadId,
        {
          text: `Start the Design council for “${kickoff.cycleTitle}”. Ground the design in this project's files and cycle context. Have independent specialists debate assumptions, evidence, risks, success criteria, and rejection criteria. If a project-owner decision is missing, ask me one focused clarification; otherwise synthesize a Design package for human review. Do not advance the gate.`,
          files: [],
        },
        runContextPayload(cycleContext),
        {
          runMetadata: runActivityMetadata(cycleContext),
          onSent: () => {
            consumeDesignKickoff(kickoff.nonce);
            setRequestContext(ORDINARY_REQUEST_CONTEXT);
          },
        },
      ),
    ).catch(() => {
      designKickoffInFlight.current = null;
    });
  }, [
    consumeDesignKickoff,
    isMock,
    pendingDesignKickoff,
    sendMessage,
    thread.isLoading,
    threadId,
  ]);
  const handleSubmitHumanInput = useCallback(
    async (request: HumanInputRequest, response: HumanInputResponse) => {
      let sent = false;
      await sendMessage(
        threadId,
        {
          text: buildHumanInputResponseText(request, response),
          files: [],
        },
        showContextChip
          ? request.source === "ask_clarification" &&
            request.clarification_type === "design_decision" &&
            selectedCycleId
            ? runContextPayload({
                kind: "cycle",
                cycleId: selectedCycleId,
              })
            : {
                dbtl_supervisor_enabled: true,
                dbtl_explicit_choice: "ordinary",
              }
          : undefined,
        {
          additionalKwargs: {
            hide_from_ui: true,
            human_input_response: response,
          },
          onSent: () => {
            sent = true;
          },
        },
      );
      return sent;
    },
    [selectedCycleId, sendMessage, showContextChip, threadId],
  );
  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);
  const handleRegenerate = useCallback(
    (messageId: string, supersededMessageIds: string[]) =>
      regenerateMessage(threadId, messageId, supersededMessageIds),
    [regenerateMessage, threadId],
  );
  const handleBranchTurn = useCallback(
    async (messageId: string, messageIds: string[]) => {
      if (
        isNewThread ||
        isMock ||
        env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"
      ) {
        return;
      }

      try {
        const response = await branchThread.mutateAsync({
          threadId,
          messageId,
          messageIds,
        });
        toast.success(t.conversation.branchCreated);
        router.push(`${chatBasePath}/${response.thread_id}`);
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : t.conversation.branchFailed,
        );
      }
    },
    [branchThread, chatBasePath, isMock, isNewThread, router, t, threadId],
  );

  const tokenUsageInlineMode = tokenUsageEnabled
    ? localSettings.tokenUsage.inlineMode
    : "off";
  const hasTodos = (thread.values.todos?.length ?? 0) > 0;
  const browserEnabled = !isNewThread && browserControlEnabled;
  const { activeGoal, hasGoal, setLocalGoal } = useActiveGoal(
    threadId,
    thread.values.goal,
  );
  const hasOpenHumanInputCard = useMemo(
    () =>
      hasOpenHumanInputRequest(
        thread.messages,
        (message) => !isHiddenFromUIMessage(message),
      ),
    [thread.messages],
  );

  return (
    <ThreadContext.Provider value={{ thread, isMock }}>
      <SidecarProvider
        parentThreadId={threadId}
        context={settings.context}
        isMock={isMock}
      >
        <ChatBox threadId={threadId} browserEnabled={browserEnabled}>
          <div className="relative flex size-full min-h-0 justify-between">
            <header
              className={cn(
                "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center gap-2 px-2 sm:px-4",
                isWelcomeMode
                  ? "bg-background/0 backdrop-blur-none"
                  : "bg-background/80 shadow-xs backdrop-blur",
              )}
            >
              <SidebarTrigger className="md:hidden" />
              <div className="flex min-w-0 flex-1 items-center text-sm font-medium">
                <ThreadTitle threadId={threadId} thread={thread} />
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {!isNewThread && (
                  <ThreadScheduledTasksLink threadId={threadId} />
                )}
                <TokenUsageIndicator
                  threadId={isNewThread ? undefined : threadId}
                  backendUsage={backendTokenUsage}
                  enabled={tokenUsageEnabled}
                  messages={thread.messages}
                  pendingMessages={pendingUsageMessages}
                  preferences={localSettings.tokenUsage}
                  onPreferencesChange={(preferences) =>
                    setLocalSettings("tokenUsage", preferences)
                  }
                />
                <SidecarTrigger />
                {browserEnabled && <BrowserTrigger />}
                {/* Inside a project the rail already browses the files, so the
                    top-bar trigger would be a second door to the same tree; it
                    stays for unfiled chats, which have no rail. */}
                {!routeProjectSlug &&
                  !isNewThread &&
                  !isMock &&
                  env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true" && (
                    <FilesTrigger />
                  )}
                <ExportTrigger threadId={threadId} />
                <ArtifactTrigger />
              </div>
            </header>
            <main className="flex min-h-0 max-w-full grow flex-col">
              <div className="flex min-h-0 flex-1 justify-center">
                <MessageList
                  className={cn("size-full", !isWelcomeMode && "pt-10")}
                  testId="main-message-list"
                  threadId={threadId}
                  thread={thread}
                  paddingBottom={MESSAGE_LIST_DEFAULT_PADDING_BOTTOM}
                  hasMoreHistory={hasMoreHistory}
                  loadMoreHistory={loadMoreHistory}
                  isHistoryLoading={isHistoryLoading}
                  tokenUsageInlineMode={tokenUsageInlineMode}
                  canRegenerate={
                    !isNewThread &&
                    !isMock &&
                    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true" &&
                    !isUploading &&
                    !thread.isLoading
                  }
                  onRegenerateMessage={handleRegenerate}
                  onSubmitHumanInput={
                    isMock || env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"
                      ? undefined
                      : handleSubmitHumanInput
                  }
                  canBranch={
                    !isNewThread &&
                    !isMock &&
                    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true" &&
                    !isUploading &&
                    !thread.isLoading &&
                    !branchThread.isPending
                  }
                  onBranchTurn={handleBranchTurn}
                />
              </div>
              <div
                className={cn(
                  "right-0 bottom-0 left-0 z-30 flex justify-center px-3 sm:px-4",
                  isWelcomeMode ? "absolute" : "relative shrink-0 pb-4",
                )}
              >
                <div
                  className={cn(
                    "relative w-full",
                    isWelcomeMode &&
                      "-translate-y-[calc(50vh-48px)] sm:-translate-y-[calc(50vh-96px)]",
                    isWelcomeMode
                      ? "max-w-(--container-width-sm)"
                      : "max-w-(--container-width-md)",
                  )}
                >
                  {(hasGoal || hasTodos) && (
                    <div
                      className={cn(
                        "right-0 left-0 z-0",
                        isWelcomeMode ? "absolute -top-4" : "relative",
                      )}
                    >
                      <div
                        className={cn(
                          "right-0 bottom-0 left-0 flex flex-col",
                          isWelcomeMode ? "absolute" : "relative",
                        )}
                      >
                        {activeGoal && <GoalStatus goal={activeGoal} />}
                        {hasTodos && (
                          <TodoList
                            className="bg-background/5"
                            todos={thread.values.todos ?? []}
                            hidden={false}
                          />
                        )}
                      </div>
                    </div>
                  )}
                  {showContextChip && (
                    <div className="mb-1.5 flex w-full items-center">
                      <DbtlContextChip
                        context={effectiveContext}
                        cycles={cycleList}
                        onSelect={setRequestContext}
                        disabled={thread.isLoading}
                      />
                    </div>
                  )}
                  {upgradeProposal.evaluation && (
                    <UpgradeProposalCard
                      // Keyed so a second proposal starts clean instead of
                      // inheriting the previous card's half-filled setup form.
                      key={upgradeProposal.evaluation.evaluation_id}
                      className="mb-2 w-full"
                      evaluation={upgradeProposal.evaluation}
                      onAction={upgradeProposal.recordOutcome}
                      onConfirm={(submission) => {
                        void upgradeProposal.confirm(submission);
                      }}
                      isConfirming={upgradeProposal.isConfirming}
                      error={upgradeProposal.createError}
                      parentCycleTitle={upgradeProposal.parentCycleTitle}
                    />
                  )}
                  {mountedRef.current ? (
                    <InputBox
                      className={cn(
                        "bg-background/5 w-full",
                        isWelcomeMode && "-translate-y-2 sm:-translate-y-4",
                      )}
                      isWelcomeMode={isWelcomeMode}
                      threadId={threadId}
                      draftThreadId={isNewThread ? "new" : threadId}
                      autoFocus={isWelcomeMode}
                      status={
                        thread.error
                          ? "error"
                          : thread.isLoading
                            ? "streaming"
                            : "ready"
                      }
                      context={settings.context}
                      extraHeader={
                        isWelcomeMode &&
                        !hasGoal &&
                        !hasTodos && <Welcome mode={settings.context.mode} />
                      }
                      disabled={
                        isMock ||
                        env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ||
                        isUploading ||
                        hasOpenHumanInputCard ||
                        (!isNewThread && isHistoryLoading)
                      }
                      onContextChange={(context) =>
                        setSettings("context", context)
                      }
                      onGoalChange={setLocalGoal}
                      onSubmit={handleSubmit}
                      onStop={handleStop}
                    />
                  ) : (
                    <div
                      aria-hidden="true"
                      className={cn(
                        "bg-background/5 h-32 w-full rounded-2xl",
                        isWelcomeMode && "-translate-y-2 sm:-translate-y-4",
                      )}
                    />
                  )}
                  {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" && (
                    <div className="text-muted-foreground/67 w-full translate-y-12 text-center text-xs">
                      {t.common.notAvailableInDemoMode}
                    </div>
                  )}
                </div>
              </div>
            </main>
          </div>
        </ChatBox>
      </SidecarProvider>
    </ThreadContext.Provider>
  );
}
