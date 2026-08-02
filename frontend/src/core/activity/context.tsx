"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { fetchActivityPage } from "./api";
import { activitySentence } from "./labels";
import {
  applyActivityEvent,
  closeOpenRows,
  reduceActivityEvents,
} from "./reducer";
import {
  EMPTY_ACTIVITY,
  type ActivityProjection,
  type PersistedActivityEvent,
} from "./types";
import { activityView } from "./view";

export interface ActivityContextValue {
  projection: ActivityProjection;
  /** Fold one live `agent_activity` frame. */
  push: (frame: unknown) => void;
  /** Explicitly drop the local projection; stream gaps use durable reload. */
  reset: () => void;
  /** Settle open rows when the authoritative run is terminal. */
  closeOpen: () => void;
  /** Reconcile the optimistic live tail with the durable conversation record. */
  reload: () => Promise<void>;
  /** Page backward through durable activity history. */
  loadOlder: () => Promise<void>;
  hasOlder: boolean;
  isLoadingOlder: boolean;
  /** False when the durable projection could not be read. */
  dataAvailable: boolean;
  /**
   * The one sentence a screen reader is told, written by the provider and read
   * by a live region mounted *outside* the visual block's subtree.
   */
  announcement: string;
}

function noop() {
  /* An unmounted provider renders no activity; the rail simply shows nothing. */
}

async function noopAsync() {
  /* The unscoped provider has no durable conversation to load. */
}

const ActivityContext = createContext<ActivityContextValue>({
  projection: EMPTY_ACTIVITY,
  push: noop,
  reset: noop,
  closeOpen: noop,
  reload: noopAsync,
  loadOlder: noopAsync,
  hasOlder: false,
  isLoadingOlder: false,
  dataAvailable: true,
  announcement: "",
});

/** How rarely the live region may speak. Announcements are coalesced to this. */
const ANNOUNCE_INTERVAL_MS = 4000;

export function ActivityProvider({
  children,
  threadId,
}: {
  children: React.ReactNode;
  threadId?: string;
}) {
  const [projection, setProjection] =
    useState<ActivityProjection>(EMPTY_ACTIVITY);
  const [announcement, setAnnouncement] = useState("");
  const [hasOlder, setHasOlder] = useState(false);
  const [isLoadingOlder, setIsLoadingOlder] = useState(false);
  const [dataAvailable, setDataAvailable] = useState(true);

  const durableEventsRef = useRef(new Map<number, PersistedActivityEvent>());
  const liveFramesRef = useRef<unknown[]>([]);
  const pendingLiveFramesRef = useRef<unknown[]>([]);
  const beforeSeqRef = useRef<number | null>(null);
  const hydratedRef = useRef(false);
  const loadingRef = useRef(false);
  const olderLoadingRef = useRef(false);
  const requestGenerationRef = useRef(0);

  // Announcements are coalesced across renders, so their bookkeeping lives in
  // refs: putting it in state would make announcing cause the re-render that
  // decides whether to announce.
  const lastSpokenRef = useRef(0);
  const lastActorRef = useRef<string | null>(null);
  const pendingRef = useRef<string | null>(null);

  const push = useCallback((frame: unknown) => {
    if (loadingRef.current) {
      pendingLiveFramesRef.current.push(frame);
      return;
    }
    liveFramesRef.current = [...liveFramesRef.current.slice(-999), frame];
    // `applyActivityEvent` returns the identical object when nothing a reader
    // could see changed, so this setState is a no-op render for the common
    // token-rate case rather than a fresh reference every frame.
    setProjection((current) => applyActivityEvent(current, frame));
  }, []);

  const reset = useCallback(() => {
    durableEventsRef.current.clear();
    liveFramesRef.current = [];
    pendingLiveFramesRef.current = [];
    beforeSeqRef.current = null;
    hydratedRef.current = false;
    lastActorRef.current = null;
    pendingRef.current = null;
    setHasOlder(false);
    setDataAvailable(true);
    setProjection(EMPTY_ACTIVITY);
  }, []);

  const closeOpen = useCallback(() => {
    setProjection((current) => closeOpenRows(current));
  }, []);

  const rebuildProjection = useCallback(() => {
    const durable = [...durableEventsRef.current.values()].sort(
      (left, right) => left.seq - right.seq,
    );
    return reduceActivityEvents([...durable, ...liveFramesRef.current]);
  }, []);

  const canBackfill = Boolean(
    threadId && threadId !== "new" && threadId !== "unscoped-chat",
  );

  const reload = useCallback(async () => {
    if (!canBackfill || !threadId) return;
    const generation = ++requestGenerationRef.current;
    olderLoadingRef.current = false;
    setIsLoadingOlder(false);
    if (!loadingRef.current) {
      pendingLiveFramesRef.current = [];
    }
    loadingRef.current = true;
    try {
      const page = await fetchActivityPage(threadId);
      if (generation !== requestGenerationRef.current) return;
      for (const event of page.events) {
        durableEventsRef.current.set(event.seq, event);
      }
      const cursor = hydratedRef.current
        ? beforeSeqRef.current
        : page.nextBeforeSeq;
      beforeSeqRef.current = cursor;
      hydratedRef.current = true;
      const pending = pendingLiveFramesRef.current;
      liveFramesRef.current = [...liveFramesRef.current, ...pending].slice(
        -1000,
      );
      pendingLiveFramesRef.current = [];
      setProjection(rebuildProjection());
      setHasOlder(cursor !== null);
      setDataAvailable(true);
    } catch {
      if (generation === requestGenerationRef.current) {
        const pending = pendingLiveFramesRef.current;
        liveFramesRef.current = [...liveFramesRef.current, ...pending].slice(
          -1000,
        );
        pendingLiveFramesRef.current = [];
        setProjection((current) => reduceActivityEvents(pending, current));
        setDataAvailable(false);
      }
    } finally {
      if (generation === requestGenerationRef.current) {
        loadingRef.current = false;
      }
    }
  }, [canBackfill, rebuildProjection, threadId]);

  const loadOlder = useCallback(async () => {
    const cursor = beforeSeqRef.current;
    if (!canBackfill || !threadId || cursor === null || olderLoadingRef.current)
      return;
    const generation = requestGenerationRef.current;
    olderLoadingRef.current = true;
    setIsLoadingOlder(true);
    try {
      const page = await fetchActivityPage(threadId, cursor);
      if (generation !== requestGenerationRef.current) return;
      for (const event of page.events) {
        durableEventsRef.current.set(event.seq, event);
      }
      beforeSeqRef.current = page.nextBeforeSeq;
      setProjection(rebuildProjection());
      setHasOlder(page.nextBeforeSeq !== null);
      setDataAvailable(true);
    } catch {
      // The current page remains authoritative. A failed optional history page
      // must not turn already-loaded current activity into "unavailable".
    } finally {
      olderLoadingRef.current = false;
      if (generation === requestGenerationRef.current) {
        setIsLoadingOlder(false);
      }
    }
  }, [canBackfill, rebuildProjection, threadId]);

  useEffect(() => {
    void reload();
    return () => {
      requestGenerationRef.current += 1;
      loadingRef.current = false;
    };
  }, [reload]);

  useEffect(() => {
    // Only *actor-level* changes are spoken. A state change on the same actor
    // updates the visual row silently: announcing every transition would flood
    // a screen reader during a governed run, for a surface nobody asked to
    // monitor.
    const leaf = leafSentence(projection);
    if (leaf.actorId === lastActorRef.current) return;
    lastActorRef.current = leaf.actorId;
    if (!leaf.sentence) return;

    const elapsed = Date.now() - lastSpokenRef.current;
    if (elapsed >= ANNOUNCE_INTERVAL_MS) {
      lastSpokenRef.current = Date.now();
      setAnnouncement(leaf.sentence);
      return;
    }
    pendingRef.current = leaf.sentence;
    const timer = window.setTimeout(() => {
      if (pendingRef.current) {
        lastSpokenRef.current = Date.now();
        setAnnouncement(pendingRef.current);
        pendingRef.current = null;
      }
    }, ANNOUNCE_INTERVAL_MS - elapsed);
    return () => window.clearTimeout(timer);
  }, [projection]);

  const value = useMemo<ActivityContextValue>(
    () => ({
      projection,
      push,
      reset,
      closeOpen,
      reload,
      loadOlder,
      hasOlder,
      isLoadingOlder,
      dataAvailable,
      announcement,
    }),
    [
      projection,
      push,
      reset,
      closeOpen,
      reload,
      loadOlder,
      hasOlder,
      isLoadingOlder,
      dataAvailable,
      announcement,
    ],
  );

  return (
    <ActivityContext.Provider value={value}>
      {children}
    </ActivityContext.Provider>
  );
}

/**
 * Give each routed conversation its own activity projection.
 *
 * Two keys, and conflating them is a bug: the *provider* is scoped to the
 * conversation, which is what gets torn down and rebuilt, while an individual
 * activity's identity is `(run_id, activity_id)`, which is what dedupes rows
 * inside it. Without the keyed boundary a finished run's rows survive into the
 * next conversation the reader opens.
 */
export function ThreadScopedActivityProvider({
  scopeKey,
  children,
}: {
  scopeKey: string;
  children: React.ReactNode;
}) {
  return (
    <ActivityProvider key={scopeKey} threadId={scopeKey}>
      {children}
    </ActivityProvider>
  );
}

export function useActivityContext(): ActivityContextValue {
  return useContext(ActivityContext);
}

function leafSentence(projection: ActivityProjection): {
  actorId: string | null;
  sentence: string;
} {
  const view = activityView(projection);
  if (!view.leaf || view.mode === "settled") {
    return { actorId: null, sentence: "" };
  }
  return {
    actorId: view.leaf.activityId,
    sentence: activitySentence(view.leaf, view.chain[0]),
  };
}
