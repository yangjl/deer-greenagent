import { useQueryClient } from "@tanstack/react-query";
import {
  Code2Icon,
  CopyIcon,
  DownloadIcon,
  EyeIcon,
  LoaderIcon,
  PackageIcon,
  SquareArrowOutUpRightIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  Artifact,
  ArtifactAction,
  ArtifactActions,
  ArtifactContent,
  ArtifactHeader,
  ArtifactTitle,
} from "@/components/ai-elements/artifact";
import { Button } from "@/components/ui/button";
import { Select, SelectItem } from "@/components/ui/select";
import {
  SelectContent,
  SelectGroup,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { CodeEditor } from "@/components/workspace/code-editor";
import { useArtifactContent } from "@/core/artifacts/hooks";
import {
  appendHtmlPreviewBaseHref,
  appendHtmlPreviewScrollRestoration,
  createHtmlPreviewScrollKey,
  getArtifactViewState,
  HTML_PREVIEW_SCROLL_MESSAGE_SOURCE,
} from "@/core/artifacts/preview";
import { urlOfArtifact } from "@/core/artifacts/utils";
import { useAuth } from "@/core/auth/AuthProvider";
import { extractCitationSources } from "@/core/citations/sources";
import { writeTextToClipboard } from "@/core/clipboard";
import {
  applyDesignFeedbackAction,
  DbtlRequestError,
  fetchDesignFeedbackSurface,
  type DesignFeedbackSurface,
} from "@/core/dbtl/cycles-api";
import {
  DECK_PROTOCOL_VERSION,
  isDeckIntent,
  parseDeckIntent,
  toDeckMessage,
} from "@/core/dbtl/design-deck-feedback";
import { useI18n } from "@/core/i18n/hooks";
import { findToolCallResult } from "@/core/messages/utils";
import { installSkill, SkillRequestError } from "@/core/skills/api";
import {
  SafeStreamdown,
  toStreamdownComponents,
} from "@/core/streamdown/components";
import { threadHistoryQueryKey } from "@/core/threads/hooks";
import {
  canBrowserPreviewFile,
  checkCodeFile,
  getFileExtensionDisplayName,
  getFileIcon,
  getFileName,
} from "@/core/utils/files";
import { uuid } from "@/core/utils/uuid";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { ArtifactLink } from "../citations/artifact-link";
import { CitationSourcesPanel } from "../citations/citation-sources-panel";
import { useThread } from "../messages/context";
import { Tooltip } from "../tooltip";

import { useArtifacts } from "./context";
import { artifactMarkdownPlugins } from "./markdown-preview-plugins";

const WRITE_FILE_PREVIEW_REFRESH_INTERVAL_MS = 3000;
const DESIGN_CHAIR_POLL_INTERVAL_MS = 750;
const DESIGN_CHAIR_MAX_POLLS = 800;

function isRetryableDeckReceipt(status: string | undefined): boolean {
  return status === "failed" || status === "handoff_failed";
}

type DeckProgress = {
  surfaceId: string;
  state: "submitting" | "running" | "completed" | "failed";
  note: string;
};

export function ArtifactFileDetail({
  className,
  filepath: filepathFromProps,
  threadId,
  projectId,
}: {
  className?: string;
  filepath: string;
  threadId: string;
  projectId?: string | null;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const isAdmin = user?.system_role === "admin";
  const { artifacts, setOpen, select } = useArtifacts();
  const { thread, isMock } = useThread();
  const isWriteFile = useMemo(() => {
    return filepathFromProps.startsWith("write-file:");
  }, [filepathFromProps]);
  const filepath = useMemo(() => {
    if (isWriteFile) {
      const url = new URL(filepathFromProps);
      return decodeURIComponent(url.pathname);
    }
    return filepathFromProps;
  }, [filepathFromProps, isWriteFile]);
  // Keep these local because ChatBox replaces context artifacts with thread state.
  const [openedPresentedFilepaths, setOpenedPresentedFilepaths] = useState<
    string[]
  >(() => {
    if (isWriteFile || artifacts.includes(filepath)) {
      return [];
    }
    return [filepath];
  });
  useEffect(() => {
    if (isWriteFile || artifacts.includes(filepath)) {
      return;
    }
    setOpenedPresentedFilepaths((current) => {
      if (current.includes(filepath)) {
        return current;
      }
      return [...current, filepath];
    });
  }, [artifacts, filepath, isWriteFile]);
  const artifactOptions = useMemo(() => {
    if (isWriteFile) {
      return artifacts;
    }
    const currentIsPresented = !artifacts.includes(filepath);
    const presentedFilepaths =
      currentIsPresented && !openedPresentedFilepaths.includes(filepath)
        ? [...openedPresentedFilepaths, filepath]
        : openedPresentedFilepaths;
    const presentedSet = new Set(presentedFilepaths);
    return [
      ...presentedFilepaths,
      ...artifacts.filter((artifact) => !presentedSet.has(artifact)),
    ];
  }, [artifacts, filepath, isWriteFile, openedPresentedFilepaths]);
  const isSkillFile = useMemo(() => {
    return filepath.endsWith(".skill");
  }, [filepath]);
  const { isCodeFile, language } = useMemo(() => {
    if (isWriteFile) {
      const codeResult = checkCodeFile(filepath);
      // Non-code browser-previewable files (PDF, images, audio, video)
      // should render in the sandboxed iframe, not the code editor.
      if (!codeResult.isCodeFile && canBrowserPreviewFile(filepath)) {
        return codeResult;
      }
      let language = codeResult.language;
      language ??= "text";
      return { isCodeFile: true, language };
    }
    // Treat .skill files as markdown (they contain SKILL.md)
    if (isSkillFile) {
      return { isCodeFile: true, language: "markdown" };
    }
    return checkCodeFile(filepath);
  }, [filepath, isWriteFile, isSkillFile]);
  const canPreviewInBrowser = useMemo(() => {
    return canBrowserPreviewFile(filepath);
  }, [filepath]);
  const isSupportPreview = useMemo(() => {
    return language === "html" || language === "markdown";
  }, [language]);
  const toolResult = (() => {
    if (!isWriteFile) {
      return undefined;
    }
    const url = new URL(filepathFromProps);
    const toolCallId = url.searchParams.get("tool_call_id");
    if (!toolCallId) {
      return undefined;
    }
    return findToolCallResult(toolCallId, thread.messages);
  })();
  const artifactViewState = getArtifactViewState({
    filepath: filepathFromProps,
    isSupportPreview,
    toolResult,
  });
  const { content, url } = useArtifactContent({
    threadId,
    filepath: filepathFromProps,
    enabled: isCodeFile && !isWriteFile,
  });

  const displayContent = content ?? "";
  const isWritingFile = isWriteFile && toolResult === undefined;
  const visibleContent = useThrottledValue(
    displayContent,
    isWritingFile ? WRITE_FILE_PREVIEW_REFRESH_INTERVAL_MS : 0,
    filepathFromProps,
  );

  const [viewMode, setViewMode] = useState<"code" | "preview">(
    artifactViewState.initialViewMode,
  );
  const [isInstalling, setIsInstalling] = useState(false);
  useEffect(() => {
    setViewMode(artifactViewState.initialViewMode);
  }, [artifactViewState.initialViewMode]);

  const handleInstallSkill = useCallback(async () => {
    if (isInstalling) return;

    setIsInstalling(true);
    try {
      const result = await installSkill({
        thread_id: threadId,
        path: filepath,
      });
      if (result.success) {
        toast.success(result.message);
      } else {
        toast.error(result.message ?? "Failed to install skill");
      }
    } catch (error) {
      console.error("Failed to install skill:", error);
      if (error instanceof SkillRequestError && error.isAdminRequired) {
        toast.error(t.settings.skills.installAdminRequired);
      } else {
        toast.error("Failed to install skill");
      }
    } finally {
      setIsInstalling(false);
    }
  }, [threadId, filepath, isInstalling, t]);
  return (
    <Artifact className={cn(className)}>
      <ArtifactHeader className="px-2">
        <div className="flex items-center gap-2">
          <ArtifactTitle>
            {isWriteFile ? (
              <div className="px-2">{getFileName(filepath)}</div>
            ) : (
              <Select value={filepath} onValueChange={select}>
                <SelectTrigger className="border-none bg-transparent! shadow-none select-none focus:outline-0 active:outline-0">
                  <SelectValue placeholder="Select a file" />
                </SelectTrigger>
                <SelectContent className="select-none">
                  <SelectGroup>
                    {artifactOptions.map((option) => (
                      <SelectItem key={option} value={option}>
                        {getFileName(option)}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            )}
          </ArtifactTitle>
        </div>
        <div className="flex min-w-0 grow items-center justify-center">
          {artifactViewState.canPreview && (
            <ToggleGroup
              className="mx-auto"
              type="single"
              variant="outline"
              size="sm"
              value={viewMode}
              onValueChange={(value) => {
                if (value) {
                  setViewMode(value as "code" | "preview");
                }
              }}
            >
              <ToggleGroupItem value="code">
                <Code2Icon />
              </ToggleGroupItem>
              <ToggleGroupItem value="preview">
                <EyeIcon />
              </ToggleGroupItem>
            </ToggleGroup>
          )}
        </div>
        <div className="flex items-center gap-2">
          <ArtifactActions>
            {!isWriteFile && filepath.endsWith(".skill") && isAdmin && (
              <Tooltip content={t.toolCalls.skillInstallTooltip}>
                <ArtifactAction
                  icon={isInstalling ? LoaderIcon : PackageIcon}
                  label={t.common.install}
                  tooltip={t.common.install}
                  disabled={
                    isInstalling ||
                    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"
                  }
                  onClick={handleInstallSkill}
                />
              </Tooltip>
            )}
            {!isWriteFile && (
              <ArtifactAction
                icon={SquareArrowOutUpRightIcon}
                label={t.common.openInNewWindow}
                tooltip={t.common.openInNewWindow}
                onClick={() => {
                  const w = window.open(
                    urlOfArtifact({ filepath, threadId, isMock }),
                    "_blank",
                    "noopener,noreferrer",
                  );
                  if (w) w.opener = null;
                }}
              />
            )}
            {isCodeFile && (
              <ArtifactAction
                icon={CopyIcon}
                label={t.clipboard.copyToClipboard}
                disabled={!content}
                onClick={() => {
                  void (async () => {
                    const didCopy = await writeTextToClipboard(
                      visibleContent ?? "",
                    );
                    if (!didCopy) {
                      toast.error(t.clipboard.failedToCopyToClipboard);
                      return;
                    }

                    toast.success(t.clipboard.copiedToClipboard);
                  })().catch(() => {
                    toast.error(t.clipboard.failedToCopyToClipboard);
                  });
                }}
                tooltip={t.clipboard.copyToClipboard}
              />
            )}
            {!isWriteFile && (
              <ArtifactAction
                icon={DownloadIcon}
                label={t.common.download}
                tooltip={t.common.download}
                onClick={() => {
                  const w = window.open(
                    urlOfArtifact({
                      filepath,
                      threadId,
                      download: true,
                      isMock,
                    }),
                    "_blank",
                    "noopener,noreferrer",
                  );
                  if (w) w.opener = null;
                }}
              />
            )}
            <ArtifactAction
              icon={XIcon}
              label={t.common.close}
              onClick={() => setOpen(false)}
              tooltip={t.common.close}
            />
          </ArtifactActions>
        </div>
      </ArtifactHeader>
      <ArtifactContent className="p-0">
        {artifactViewState.canPreview &&
          viewMode === "preview" &&
          (language === "markdown" || language === "html") && (
            <ArtifactFilePreview
              content={visibleContent}
              language={language ?? "text"}
              scrollKey={filepathFromProps}
              url={url}
              projectId={projectId}
              threadId={threadId}
            />
          )}
        {isCodeFile && viewMode === "code" && (
          <CodeEditor
            className="size-full resize-none rounded-none border-none"
            value={visibleContent ?? ""}
            readonly
          />
        )}
        {!isCodeFile && canPreviewInBrowser && (
          <iframe
            className="size-full"
            sandbox=""
            src={urlOfArtifact({ filepath, threadId, isMock })}
          />
        )}
        {!isCodeFile && !canPreviewInBrowser && (
          <ArtifactDownloadFallback
            filepath={filepath}
            threadId={threadId}
            isMock={isMock}
          />
        )}
      </ArtifactContent>
    </Artifact>
  );
}

function ArtifactDownloadFallback({
  filepath,
  threadId,
  isMock,
}: {
  filepath: string;
  threadId: string;
  isMock?: boolean;
}) {
  const filename = getFileName(filepath);
  const fileType = getFileExtensionDisplayName(filepath);

  return (
    <div className="flex size-full items-center justify-center p-6">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">
        <div className="text-muted-foreground">
          {getFileIcon(filepath, "size-12")}
        </div>
        <div className="space-y-1">
          <div className="font-medium break-all">{filename}</div>
          <div className="text-muted-foreground text-sm">{fileType} file</div>
        </div>
        <p className="text-muted-foreground text-sm">
          This file type cannot be previewed in the browser.
        </p>
        <Button asChild>
          <a
            href={urlOfArtifact({
              filepath,
              threadId,
              download: true,
              isMock,
            })}
            target="_blank"
            rel="noopener noreferrer"
          >
            <DownloadIcon className="size-4" />
            Download
          </a>
        </Button>
      </div>
    </div>
  );
}

export function ArtifactFilePreview({
  content,
  language,
  scrollKey,
  url,
  resolveArtifactLinks = true,
  projectId = null,
  threadId = "",
}: {
  content: string;
  language: string;
  scrollKey: string;
  url?: string;
  resolveArtifactLinks?: boolean;
  projectId?: string | null;
  threadId?: string;
}) {
  const queryClient = useQueryClient();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const scrollPositionRef = useRef({ x: 0, y: 0 });
  const scrollMessageKey = useMemo(
    () => createHtmlPreviewScrollKey(scrollKey),
    [scrollKey],
  );
  const [htmlPreviewUrl, setHtmlPreviewUrl] = useState<string>();
  const [deckProgress, setDeckProgress] = useState<DeckProgress | null>(null);
  const deckChannelRef = useRef<string | null>(null);
  const deckSurfaceRef = useRef<DesignFeedbackSurface | null>(null);
  const [deckSurface, setDeckSurface] = useState<DesignFeedbackSurface | null>(
    null,
  );
  const deckSubmissionIdRef = useRef<string | null>(null);
  const deckChairPollingSurfaceRef = useRef<string | null>(null);
  const citationSources = useMemo(
    () =>
      language === "markdown" ? extractCitationSources(content ?? "") : [],
    [content, language],
  );

  useEffect(() => {
    scrollPositionRef.current = { x: 0, y: 0 };
  }, [scrollMessageKey]);

  useEffect(() => {
    if (language !== "html") {
      return;
    }

    const handleMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) {
        return;
      }
      if (!isArtifactScrollMessage(event.data, scrollMessageKey)) {
        return;
      }

      if (event.data.type === "save") {
        const x = scrollCoordinate(event.data.x);
        const y = scrollCoordinate(event.data.y);
        if (x !== undefined && y !== undefined) {
          scrollPositionRef.current = { x, y };
        }
        return;
      }

      iframeRef.current?.contentWindow?.postMessage(
        {
          source: HTML_PREVIEW_SCROLL_MESSAGE_SOURCE,
          key: scrollMessageKey,
          type: "restore",
          ...scrollPositionRef.current,
        },
        "*",
      );
    };

    window.addEventListener("message", handleMessage);
    return () => {
      window.removeEventListener("message", handleMessage);
    };
  }, [language, scrollMessageKey]);

  useEffect(() => {
    if (language !== "html" || !projectId || !threadId) {
      return;
    }

    let cancelled = false;
    const send = (
      surfaceId: string,
      channel: string,
      body: Parameters<typeof toDeckMessage>[2],
    ) => {
      iframeRef.current?.contentWindow?.postMessage(
        toDeckMessage(surfaceId, channel, body),
        "*",
      );
    };

    const verifyBytes = async (expectedHash: string) => {
      const digest = await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(content ?? ""),
      );
      return (
        Array.from(new Uint8Array(digest))
          .map((value) => value.toString(16).padStart(2, "0"))
          .join("") === expectedHash
      );
    };

    const refreshConversation = () => {
      void queryClient.invalidateQueries({ queryKey: ["thread", threadId] });
      void queryClient.invalidateQueries({
        queryKey: threadHistoryQueryKey(threadId),
      });
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
    };

    /**
     * A verdict recorded here changes the cycle, and the rail is the surface
     * that shows it. The rail's own controls invalidate these keys; a decision
     * taken on the deck did not, so approving a Design left the rail showing
     * the stage list from before the approval — Build still "Locked" while the
     * server had already opened it. Broad on purpose: one verdict can move the
     * cycle state, the stage statuses, and the activity feed at once.
     */
    const refreshCycles = () => {
      void queryClient.invalidateQueries({ queryKey: ["dbtl-cycles"] });
    };

    const waitForChairOutcome = async (surfaceId: string, channel: string) => {
      if (deckChairPollingSurfaceRef.current === surfaceId) {
        return;
      }
      deckChairPollingSurfaceRef.current = surfaceId;
      const activeStage = deckSurfaceRef.current?.stage ?? "design";
      const followUpLabel =
        activeStage === "design"
          ? "Design chair"
          : `${activeStage} review meeting`;
      setDeckProgress({
        surfaceId,
        state: "running",
        note: `The ${followUpLabel} is running. The follow-up deck will appear in this conversation.`,
      });
      try {
        for (let poll = 0; poll < DESIGN_CHAIR_MAX_POLLS; poll += 1) {
          await new Promise((resolve) =>
            window.setTimeout(resolve, DESIGN_CHAIR_POLL_INTERVAL_MS),
          );
          if (cancelled) return;
          try {
            const refreshed = await fetchDesignFeedbackSurface({
              projectId,
              surfaceId,
              viewerThreadId: threadId,
            });
            if (cancelled) return;
            deckSurfaceRef.current = refreshed;
            setDeckSurface(refreshed);

            if (
              refreshed.receipt?.status === "failed" &&
              refreshed.allowed_actions.some(
                (action) =>
                  action.startsWith("chair_") ||
                  action === "convene_review_meeting",
              )
            ) {
              const note =
                refreshed.note ||
                "The chair could not produce a follow-up deck. Your answer is still here — try again.";
              setDeckProgress({
                surfaceId,
                state: "failed",
                note,
              });
              send(surfaceId, channel, {
                type: "failed",
                note,
              });
              return;
            }

            if (
              !refreshed.is_current ||
              (refreshed.newest_surface_id !== null &&
                refreshed.newest_surface_id !== surfaceId)
            ) {
              const note =
                refreshed.note ||
                "The meeting continued. Its follow-up deck is now in the conversation.";
              refreshConversation();
              setDeckProgress({
                surfaceId,
                state: "completed",
                note,
              });
              send(surfaceId, channel, {
                type: "accepted",
                note,
              });
              return;
            }
          } catch {
            // A transient read failure says nothing about the run. Keep waiting;
            // the original write and its idempotency key remain authoritative.
          }
        }

        const note =
          "The chair is taking longer than expected. Your answer is still here; you can try sending it again.";
        setDeckProgress({
          surfaceId,
          state: "failed",
          note,
        });
        send(surfaceId, channel, {
          type: "failed",
          note,
        });
      } finally {
        if (deckChairPollingSurfaceRef.current === surfaceId) {
          deckChairPollingSurfaceRef.current = null;
        }
      }
    };

    const handleMessage = async (event: MessageEvent) => {
      if (
        event.source !== iframeRef.current?.contentWindow ||
        !isDeckIntent(event.data)
      ) {
        return;
      }
      const raw = event.data as Record<string, unknown>;
      if (
        raw.protocol !== DECK_PROTOCOL_VERSION ||
        typeof raw.surfaceId !== "string"
      ) {
        return;
      }
      const surfaceId = raw.surfaceId;

      if (raw.type === "ready") {
        try {
          const surface = await fetchDesignFeedbackSurface({
            projectId,
            surfaceId,
            viewerThreadId: threadId,
          });
          if (cancelled) return;
          const channel = uuid();
          deckChannelRef.current = channel;
          deckSurfaceRef.current = surface;
          setDeckSurface(surface);
          if (
            isRetryableDeckReceipt(surface.receipt?.status) &&
            surface.receipt?.client_submission_id
          ) {
            // A remount must retry under the action ledger's original id.
            // Generating a new UUID would turn the same answer into a second
            // answer and the backend would correctly refuse it.
            deckSubmissionIdRef.current = surface.receipt.client_submission_id;
          }
          const bytesMatch = await verifyBytes(surface.deck_content_hash);
          if (cancelled) return;
          send(surfaceId, channel, {
            type: "initialize",
            allowedActions: bytesMatch ? surface.allowed_actions : [],
            selectedOptionIds:
              bytesMatch && isRetryableDeckReceipt(surface.receipt?.status)
                ? surface.receipt?.selected_card_ids
                : undefined,
            comment:
              bytesMatch && isRetryableDeckReceipt(surface.receipt?.status)
                ? (surface.receipt?.human_comment ?? "")
                : undefined,
            note: bytesMatch
              ? surface.note
              : "This preview does not match the registered deck bytes.",
          });
          if (bytesMatch && surface.receipt?.status === "resume_started") {
            // A deck may be closed or remounted while its background chair run
            // is active. Resume both the visible progress state and successor
            // polling from the authenticated receipt; otherwise the old deck
            // looks inert and the conversation never learns that the follow-up
            // deck arrived.
            refreshConversation();
            void waitForChairOutcome(surfaceId, channel);
          } else {
            setDeckProgress(null);
          }
        } catch (error) {
          if (cancelled) return;
          const channel = uuid();
          deckChannelRef.current = channel;
          const note =
            error instanceof Error
              ? error.message
              : "This deck could not be verified.";
          setDeckProgress({
            surfaceId,
            state: "failed",
            note,
          });
          send(surfaceId, channel, {
            type: "initialize",
            allowedActions: [],
            note,
          });
        }
        return;
      }

      const channel = deckChannelRef.current;
      const surface = deckSurfaceRef.current;
      if (!channel || surface?.surface_id !== surfaceId) return;
      const intent = parseDeckIntent(event.data, { surfaceId, channel });
      if (intent?.type !== "submit_intent") return;

      const submissionId = (deckSubmissionIdRef.current ??= uuid());
      const stageLabel =
        surface.stage.charAt(0).toUpperCase() + surface.stage.slice(1);
      setDeckProgress({
        surfaceId,
        state: "submitting",
        note: `Recording your ${stageLabel} feedback…`,
      });
      send(surfaceId, channel, { type: "pending" });
      try {
        const result = await applyDesignFeedbackAction({
          projectId,
          surface,
          viewerThreadId: threadId,
          action: intent.action,
          comment: intent.comment,
          clientSubmissionId: submissionId,
        });
        if (cancelled) return;
        // Every recorded intent can move the cycle, so this fires before the
        // per-intent branches rather than inside one of them.
        refreshCycles();
        if (
          intent.action.kind === "submit_for_review" ||
          intent.action.kind === "park"
        ) {
          const refreshed = await fetchDesignFeedbackSurface({
            projectId,
            surfaceId,
            viewerThreadId: threadId,
          });
          deckSurfaceRef.current = refreshed;
          setDeckSurface(refreshed);
          deckSubmissionIdRef.current = null;
          send(surfaceId, channel, {
            type: "initialize",
            allowedActions: refreshed.allowed_actions,
            note:
              result.receipt?.message ??
              (intent.action.kind === "park"
                ? "Cycle parked. You can continue from this deck when ready."
                : `Submitted. Choose the final ${stageLabel} verdict.`),
          });
          setDeckProgress({
            surfaceId,
            state: "completed",
            note:
              result.receipt?.message ??
              (intent.action.kind === "park"
                ? "Cycle parked. You can continue from this deck when ready."
                : `Submitted. Choose the final ${stageLabel} verdict.`),
          });
        } else if (result.status === "handoff_failed") {
          const refreshed = await fetchDesignFeedbackSurface({
            projectId,
            surfaceId,
            viewerThreadId: threadId,
          });
          deckSurfaceRef.current = refreshed;
          setDeckSurface(refreshed);
          const note =
            result.receipt?.message ??
            "The approval is recorded, but its next-stage prompt could not start. Retry the same decision.";
          setDeckProgress({
            surfaceId,
            state: "failed",
            note,
          });
          send(surfaceId, channel, {
            type: "initialize",
            allowedActions: refreshed.allowed_actions,
            selectedOptionIds: refreshed.receipt?.selected_card_ids ?? [],
            comment: refreshed.receipt?.human_comment ?? "",
            note,
          });
        } else if (
          result.status === "resume_started" &&
          (intent.action.kind.startsWith("chair_") ||
            intent.action.kind === "convene_review_meeting")
        ) {
          // This REST action starts a run outside the page's normal LangGraph
          // stream, so waiting for the POST alone would freeze the old deck
          // while the conversation never learns that a successor exists. Poll
          // the authenticated read model until it either exposes the next deck
          // or releases this exact answer for retry.
          refreshConversation();
          await waitForChairOutcome(surfaceId, channel);
        } else {
          setDeckProgress({
            surfaceId,
            state: "completed",
            note: result.receipt?.message ?? "Recorded.",
          });
          send(surfaceId, channel, {
            type: "accepted",
            note: result.receipt?.message ?? "Recorded.",
          });
        }
      } catch (error) {
        if (cancelled) return;
        if (error instanceof DbtlRequestError && error.status === 409) {
          try {
            const refreshed = await fetchDesignFeedbackSurface({
              projectId,
              surfaceId,
              viewerThreadId: threadId,
            });
            if (cancelled) return;
            deckSurfaceRef.current = refreshed;
            setDeckSurface(refreshed);
            if (
              !refreshed.is_current ||
              refreshed.current_db_revision !== surface.current_db_revision ||
              !refreshed.interactive
            ) {
              send(surfaceId, channel, {
                type: "stale",
                note:
                  refreshed.note ||
                  "This deck changed or was already answered. Open the latest feedback deck.",
              });
              setDeckProgress({
                surfaceId,
                state: "failed",
                note:
                  refreshed.note ||
                  "This deck changed or was already answered. Open the latest feedback deck.",
              });
              return;
            }
            if (
              isRetryableDeckReceipt(refreshed.receipt?.status) &&
              refreshed.allowed_actions.some(
                (action) =>
                  action.startsWith("chair_") ||
                  action === "convene_review_meeting" ||
                  action === "approve" ||
                  action === "advance",
              )
            ) {
              if (refreshed.receipt?.client_submission_id) {
                deckSubmissionIdRef.current =
                  refreshed.receipt.client_submission_id;
              }
              // A failed chair run may be retried only with the exact audited
              // answer. Restore it from the authenticated action ledger
              // instead of leaving a changed draft trapped in a permanent
              // payload-conflict loop.
              send(surfaceId, channel, {
                type: "initialize",
                allowedActions: refreshed.allowed_actions,
                selectedOptionIds: refreshed.receipt?.selected_card_ids ?? [],
                comment: refreshed.receipt?.human_comment ?? "",
                note:
                  error.message +
                  " The original recorded answer has been restored for retry.",
              });
              setDeckProgress({
                surfaceId,
                state: "failed",
                note:
                  error.message +
                  " The original recorded answer has been restored for retry.",
              });
              return;
            }
          } catch {
            // The original conflict remains authoritative. Never retry it
            // against a new revision merely because refresh also failed.
          }
        }
        const note =
          error instanceof Error
            ? error.message
            : `That ${stageLabel} feedback could not be recorded.`;
        setDeckProgress({
          surfaceId,
          state: "failed",
          note,
        });
        send(surfaceId, channel, {
          type: "failed",
          note,
        });
      }
    };

    const onMessage = (event: MessageEvent) => {
      void handleMessage(event);
    };
    window.addEventListener("message", onMessage);
    return () => {
      cancelled = true;
      window.removeEventListener("message", onMessage);
      deckChannelRef.current = null;
      deckSurfaceRef.current = null;
      setDeckSurface(null);
      deckSubmissionIdRef.current = null;
      deckChairPollingSurfaceRef.current = null;
    };
  }, [content, language, projectId, queryClient, threadId]);

  useEffect(() => {
    if (language !== "html") {
      setHtmlPreviewUrl(undefined);
      return;
    }

    const previewContent = appendHtmlPreviewScrollRestoration(
      appendHtmlPreviewBaseHref(content ?? "", url),
      scrollKey,
    );
    const blob = new Blob([previewContent], {
      type: "text/html;charset=utf-8",
    });
    const objectUrl = URL.createObjectURL(blob);
    setHtmlPreviewUrl(objectUrl);

    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [content, language, scrollKey, url]);

  if (language === "markdown") {
    return (
      <div className="size-full overflow-auto px-4 py-3">
        <SafeStreamdown
          className="min-w-0"
          {...artifactMarkdownPlugins}
          components={
            resolveArtifactLinks
              ? toStreamdownComponents({ a: ArtifactLink })
              : undefined
          }
        >
          {content ?? ""}
        </SafeStreamdown>
        <CitationSourcesPanel sources={citationSources} className="mb-4" />
      </div>
    );
  }
  if (language === "html") {
    return (
      <div className="flex size-full min-h-0 flex-col">
        {deckSurface && (
          <div
            className={cn(
              "flex shrink-0 items-center justify-between border-b px-4 py-2 text-xs",
              deckSurface.lifecycle_state === "superseded"
                ? "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100"
                : "bg-muted/40 text-muted-foreground",
            )}
            data-testid="dbtl-stage-surface-header"
          >
            <span className="font-medium">
              {deckSurface.stage.charAt(0).toUpperCase() +
                deckSurface.stage.slice(1)}{" "}
              · {deckSurface.lifecycle_state}
            </span>
            <span className="flex items-center gap-3">
              <span>Surface revision {deckSurface.surface_revision}</span>
              {deckSurface.lifecycle_state === "superseded" &&
                deckSurface.newest_surface_uri && (
                  <a
                    className="underline underline-offset-2"
                    href={urlOfArtifact({
                      filepath: deckSurface.newest_surface_uri,
                      threadId,
                      isMock: false,
                    })}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open latest surface
                  </a>
                )}
            </span>
          </div>
        )}
        {deckProgress && (
          <div
            className={cn(
              "flex shrink-0 items-center gap-2 border-b px-4 py-2 text-sm",
              deckProgress.state === "failed"
                ? "border-destructive/30 bg-destructive/10 text-destructive"
                : "bg-muted/60 text-foreground",
            )}
            role="status"
            aria-live="polite"
          >
            {(deckProgress.state === "submitting" ||
              deckProgress.state === "running") && (
              <LoaderIcon className="size-4 shrink-0 animate-spin" />
            )}
            <span>{deckProgress.note}</span>
          </div>
        )}
        <iframe
          ref={iframeRef}
          className="min-h-0 flex-1"
          title="Artifact preview"
          // allow-scripts is needed for the scroll-restoration injected
          // script (appendHtmlPreviewScrollRestoration) which communicates
          // via postMessage. allow-same-origin is deliberately omitted: the
          // opaque origin prevents access to parent.document and cookies,
          // and postMessage(..., "*") works fine from it.
          sandbox="allow-scripts allow-forms"
          src={htmlPreviewUrl}
        />
      </div>
    );
  }
  return null;
}

function isArtifactScrollMessage(
  data: unknown,
  key: string,
): data is {
  type: "save" | "restore-request";
  x?: unknown;
  y?: unknown;
} {
  return (
    typeof data === "object" &&
    data !== null &&
    "source" in data &&
    data.source === HTML_PREVIEW_SCROLL_MESSAGE_SOURCE &&
    "key" in data &&
    data.key === key &&
    "type" in data &&
    (data.type === "save" || data.type === "restore-request")
  );
}

function scrollCoordinate(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function useThrottledValue(
  value: string,
  intervalMs: number,
  resetKey: string,
) {
  const [throttledValue, setThrottledValue] = useState(value);
  const latestValueRef = useRef(value);
  const lastFlushAtRef = useRef(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resetKeyRef = useRef(resetKey);

  useEffect(() => {
    latestValueRef.current = value;

    if (resetKeyRef.current !== resetKey) {
      resetKeyRef.current = resetKey;
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      lastFlushAtRef.current = Date.now();
      setThrottledValue(value);
      return;
    }

    if (intervalMs <= 0) {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      lastFlushAtRef.current = Date.now();
      setThrottledValue(value);
      return;
    }

    const now = Date.now();
    const elapsed = now - lastFlushAtRef.current;
    if (lastFlushAtRef.current === 0 || elapsed >= intervalMs) {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      lastFlushAtRef.current = now;
      setThrottledValue(value);
      return;
    }

    if (timeoutRef.current) {
      return;
    }

    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      lastFlushAtRef.current = Date.now();
      setThrottledValue(latestValueRef.current);
    }, intervalMs - elapsed);
  }, [intervalMs, resetKey, value]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  return intervalMs <= 0 || resetKeyRef.current !== resetKey
    ? value
    : throttledValue;
}
