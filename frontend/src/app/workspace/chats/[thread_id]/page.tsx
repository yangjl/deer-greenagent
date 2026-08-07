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
import { ContextUsageBadge } from "@/components/workspace/context-usage-badge";
import {
  DiscoveryComposerIndicator,
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
  AUTO_REQUEST_CONTEXT,
  type RequestContext,
  canReplayConversation,
  findNewDiscoveryCycle,
  findNewSetupCycle,
  humanInputRunContext,
  nextContextAfterSend,
  normalizeContext,
  proposalContextPayload,
  runActivityMetadata,
  runContextPayload,
  useDbtlFeature,
  useDiscoveryStatus,
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
import {
  selectContextUsage,
  threadTokenUsageToTokenUsage,
} from "@/core/threads/token-usage";
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
    { enabled: !isMock },
  );
  const threadMetadata = useThreadMetadata(threadId, {
    enabled: !isNewThread && !isMock,
    isMock,
  });
  const branchThread = useBranchThread();
  const backendTokenUsage = threadTokenUsageToTokenUsage(threadTokenUsage.data);
  const contextUsage = selectContextUsage(threadTokenUsage.data);
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
  const showDbtlScope = Boolean(
    projectId && dbtlFeature?.graph_execution_enabled,
  );
  const { data: discoveryStatusData, refetch: refetchDiscoveryStatus } =
    useDiscoveryStatus(projectId, isNewThread ? null : threadId, {
      enabled: showDbtlScope && !isMock,
    });

  const {
    thread,
    pendingUsageMessages,
    sendMessage,
    regenerateMessage,
    editAndRegenerateMessage,
    isUploading,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    displayThreadId: threadId,
    projectSupervisorEnabled: showDbtlScope,
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
      if (showDbtlScope && !isMock && !isNewThread) {
        void refetchDiscoveryStatus();
      }
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
    selectCycle,
    pendingScopeRequest,
    consumeComposerScope,
  } = useProjectCycleSelection();
  const { evaluate: evaluateForUpgrade, recordOutcome: recordProposalOutcome } =
    upgradeProposal;

  // The scope selector only appears where the supervisor can actually route, so
  // the control is never offered when changing it would do nothing.
  const { data: projectCycles, refetch: refetchProjectCycles } =
    useProjectCycles(projectId);
  // No scope control: the chatbox is the only input surface, so the resting
  // state is "let the assistant judge" and the rail is what arms anything else.
  // Sending an explicit "ordinary" here would settle every request on the first
  // rung of the backend's precedence ladder and the classifier would never run.
  const [requestContext, setRequestContext] =
    useState<RequestContext>(AUTO_REQUEST_CONTEXT);
  const [composerFocusSignal, setComposerFocusSignal] = useState(0);
  const cycleList = useMemo(
    () => projectCycles?.cycles ?? [],
    [projectCycles?.cycles],
  );
  // Resolve against the live list on every render: a cycle can be completed in
  // another tab, and the label must not keep claiming a scope that is gone.
  const effectiveContext = normalizeContext(
    requestContext.kind === "auto" && selectedCycleId
      ? { kind: "auto", cycleId: selectedCycleId }
      : requestContext,
    cycleList,
  );

  // A cycle picked in the project rail scopes the conversation it was picked
  // in, and nothing else.
  //
  // The selection lives in a provider above this route, so it used to survive
  // navigation: expand a cycle row to look at its to-dos, open a *new*
  // conversation, and that conversation's very first message was routed as a
  // continuation of the old cycle. The backend's precedence ladder puts a
  // selected cycle above the classifier on purpose, so the request went
  // straight into that cycle's Design meeting and the "is this a DBTL cycle?"
  // proposal never ran — for a message that was describing new work.
  //
  // Clearing on the active conversation is what makes the ladder's first rung
  // mean "the cycle this person is working in", rather than "the last cycle
  // they clicked on, ever". A rail click inside one conversation still holds
  // until they leave it, and the design kickoff is unaffected because it
  // carries its own cycle id rather than reading this one.
  useEffect(() => {
    selectCycle(null);
  }, [selectCycle, threadId]);

  // A rail action arms the composer; it never sends. The human still writes the
  // request in the chatbox, which is why the cursor moves there.
  useEffect(() => {
    if (!pendingScopeRequest || !showDbtlScope) return;
    setRequestContext({ kind: pendingScopeRequest.kind, cycleId: null });
    setComposerFocusSignal((value) => value + 1);
    consumeComposerScope(pendingScopeRequest.nonce);
  }, [consumeComposerScope, pendingScopeRequest, showDbtlScope]);

  const handleSubmit = useCallback(
    (message: PromptInputMessage, options?: InputBoxSubmitOptions) => {
      // Travels in the run request's `context`, never `configurable`, which is
      // checkpointed and would make a one-off selection permanent.
      const dbtlContext = showDbtlScope
        ? runContextPayload(effectiveContext)
        : undefined;
      const proposalContext = showDbtlScope
        ? proposalContextPayload(effectiveContext)
        : { selectedCycleId, explicitChoice: null };
      const sentContext = effectiveContext;
      const sendPromise = sendMessage(threadId, message, dbtlContext, {
        ...options,
        runMetadata: showDbtlScope
          ? runActivityMetadata(sentContext)
          : undefined,
        onSent: () => {
          options?.onSent?.();
          // Consume the one-shot choice only when the stream hook accepts the
          // send. A click dropped by the in-flight guard must not lose it.
          //
          // An empty message keeps the selection: the DBTL routes read the
          // request's *text*, so a textless send cannot start or continue
          // anything, and silently disarming here would strand a user who
          // armed the composer and then sent nothing.
          if (showDbtlScope && message.text.trim()) {
            setRequestContext(nextContextAfterSend(sentContext));
            // This provider selection is the other half of effectiveContext.
            // Leaving it set immediately re-hydrates AUTO as the same cycle,
            // so the next follow-up can restart the meeting despite the
            // selector promising that scope applies to one request.
            selectCycle(null);
          }
          // Shadow evaluation runs beside the accepted send, never in front
          // of it, and uses the exact same routing inputs as the supervisor.
          void evaluateForUpgrade({
            text: message.text,
            threadId,
            isNewConversation: isWelcomeMode,
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
      showDbtlScope,
      effectiveContext,
      isWelcomeMode,
      selectCycle,
    ],
  );
  const handleSubmitHumanInput = useCallback(
    async (request: HumanInputRequest, response: HumanInputResponse) => {
      let sent = false;
      const cycleIdsBeforeSend = new Set(cycleList.map((cycle) => cycle.id));
      // Cycle creation is server-owned and finishes asynchronously after this
      // reply is sent, so a single immediate refetch races ahead of it. Poll
      // briefly until the new cycle appears and select it, instead of asking
      // the human to refresh the project by hand.
      const pollForNewCycle = async (
        finder: typeof findNewDiscoveryCycle,
      ): Promise<ReturnType<typeof findNewDiscoveryCycle>> => {
        for (let attempt = 0; attempt < 15; attempt++) {
          try {
            const refreshed = await refetchProjectCycles({
              throwOnError: true,
            });
            const cycle = finder(
              refreshed.data?.cycles ?? [],
              cycleIdsBeforeSend,
              threadId,
            );
            if (cycle) return cycle;
          } catch {
            // Transient fetch error; keep polling within the window.
          }
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
        return null;
      };
      await sendMessage(
        threadId,
        {
          text: buildHumanInputResponseText(request, response),
          files: [],
        },
        showDbtlScope
          ? humanInputRunContext(request, selectedCycleId, response.value)
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
      if (sent && request.clarification_type === "cycle_setup_confirmation") {
        if (response.value === "create_cycle") {
          const cycle = await pollForNewCycle(findNewSetupCycle);
          if (!cycle) {
            toast.error(
              "No new cycle was recorded. Review the server response, then start a new cycle request to retry.",
            );
            return sent;
          }
          selectCycle(cycle.id);
          recordProposalOutcome("start_setup");
        } else if (response.value === "keep_ordinary") {
          recordProposalOutcome("keep_ordinary");
        } else {
          recordProposalOutcome("not_sure");
        }
      }
      if (
        sent &&
        request.clarification_type === "dbtl_discovery_start" &&
        response.value === "start_cycle"
      ) {
        const cycle = await pollForNewCycle(findNewDiscoveryCycle);
        if (!cycle) {
          toast.error(
            "The cycle was created, but the project cycle list could not refresh. Refresh the project to select it.",
          );
          return sent;
        }
        selectCycle(cycle.id);
      }
      if (sent && request.clarification_type === "dbtl_discovery_start") {
        void refetchDiscoveryStatus();
      }
      return sent;
    },
    [
      refetchDiscoveryStatus,
      recordProposalOutcome,
      refetchProjectCycles,
      selectCycle,
      selectedCycleId,
      sendMessage,
      showDbtlScope,
      threadId,
      cycleList,
    ],
  );
  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);
  const handleRegenerate = useCallback(
    (messageId: string, supersededMessageIds: string[]) =>
      regenerateMessage(threadId, messageId, supersededMessageIds),
    [regenerateMessage, threadId],
  );
  const handleEditAndRegenerate = useCallback(
    (messageId: string, replacementText: string) =>
      editAndRegenerateMessage(threadId, messageId, replacementText),
    [editAndRegenerateMessage, threadId],
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
  const browserEnabled = !isNewThread && !isMock && browserControlEnabled;
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
  const canReplay = canReplayConversation(cycleList);

  return (
    <ThreadContext.Provider value={{ thread, isMock }}>
      <SidecarProvider
        parentThreadId={threadId}
        context={settings.context}
        isMock={isMock}
      >
        <ChatBox
          threadId={threadId}
          projectId={projectId}
          browserEnabled={browserEnabled}
        >
          <div className="relative flex size-full min-h-0 justify-between">
            <header
              className={cn(
                "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center gap-2 px-2 sm:px-4",
                isWelcomeMode
                  ? "bg-background/0 backdrop-blur-none"
                  : "bg-background/80 shadow-xs backdrop-blur",
              )}
            >
              {!isMock && <SidebarTrigger className="md:hidden" />}
              <div className="flex min-w-0 flex-1 items-center text-sm font-medium">
                <ThreadTitle threadId={threadId} thread={thread} />
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {!isNewThread && !isMock && (
                  <ThreadScheduledTasksLink threadId={threadId} />
                )}
                {tokenUsageEnabled ? (
                  <TokenUsageIndicator
                    threadId={isNewThread ? undefined : threadId}
                    backendUsage={backendTokenUsage}
                    contextUsage={contextUsage}
                    enabled={tokenUsageEnabled}
                    messages={thread.messages}
                    pendingMessages={pendingUsageMessages}
                    preferences={localSettings.tokenUsage}
                    onPreferencesChange={(preferences) =>
                      setLocalSettings("tokenUsage", preferences)
                    }
                  />
                ) : (
                  <ContextUsageBadge contextUsage={contextUsage} />
                )}
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
                    !thread.isLoading &&
                    canReplay
                  }
                  onRegenerateMessage={handleRegenerate}
                  canEdit={
                    !isNewThread &&
                    !isMock &&
                    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true" &&
                    !isUploading &&
                    !thread.isLoading &&
                    !branchThread.isPending &&
                    !hasGoal &&
                    !hasOpenHumanInputCard &&
                    canReplay
                  }
                  onEditAndRegenerateMessage={handleEditAndRegenerate}
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
                      focusSignal={composerFocusSignal}
                      extraTools={
                        <DiscoveryComposerIndicator
                          discovery={discoveryStatusData?.discovery}
                        />
                      }
                      disabled={
                        isMock ||
                        env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ||
                        isUploading ||
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
