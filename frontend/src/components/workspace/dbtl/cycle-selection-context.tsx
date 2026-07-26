"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

import type { RequestContextKind } from "@/core/dbtl/composer-scope";

export interface PendingDesignKickoff {
  nonce: number;
  cycleId: string;
  cycleTitle: string;
}

/**
 * A request to *arm* the composer with a DBTL scope — not to send anything.
 *
 * This is how a rail action reaches chat without becoming an input surface
 * itself: the rail sets the scope and moves the cursor into the composer, and
 * the human types the actual request there.
 */
export interface PendingScopeRequest {
  nonce: number;
  kind: RequestContextKind;
}

interface ProjectCycleSelection {
  selectedCycleId: string | null;
  selectCycle: (cycleId: string | null) => void;
  pendingDesignKickoff: PendingDesignKickoff | null;
  requestDesignKickoff: (cycleId: string, cycleTitle: string) => void;
  consumeDesignKickoff: (nonce: number) => void;
  pendingScopeRequest: PendingScopeRequest | null;
  requestComposerScope: (kind: RequestContextKind) => void;
  consumeComposerScope: (nonce: number) => void;
}

const ProjectCycleSelectionContext =
  createContext<ProjectCycleSelection | null>(null);
const NO_PROJECT_SELECTION: ProjectCycleSelection = {
  selectedCycleId: null,
  selectCycle: () => undefined,
  pendingDesignKickoff: null,
  requestDesignKickoff: () => undefined,
  consumeDesignKickoff: () => undefined,
  pendingScopeRequest: null,
  requestComposerScope: () => undefined,
  consumeComposerScope: () => undefined,
};

/**
 * Shares an explicitly selected DBTL cycle between the project rail and chat.
 *
 * The default remains `null` even when the rail visually highlights its most
 * relevant cycle. Only a human click should route a later request as a cycle
 * continuation.
 */
export function ProjectCycleSelectionProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [selectedCycleId, selectCycle] = useState<string | null>(null);
  const [pendingDesignKickoff, setPendingDesignKickoff] =
    useState<PendingDesignKickoff | null>(null);
  const requestDesignKickoff = useCallback(
    (cycleId: string, cycleTitle: string) => {
      setPendingDesignKickoff({
        nonce: Date.now(),
        cycleId,
        cycleTitle,
      });
    },
    [],
  );
  const consumeDesignKickoff = useCallback((nonce: number) => {
    setPendingDesignKickoff((current) =>
      current?.nonce === nonce ? null : current,
    );
  }, []);
  const [pendingScopeRequest, setPendingScopeRequest] =
    useState<PendingScopeRequest | null>(null);
  const requestComposerScope = useCallback((kind: RequestContextKind) => {
    setPendingScopeRequest({ nonce: Date.now(), kind });
  }, []);
  const consumeComposerScope = useCallback((nonce: number) => {
    setPendingScopeRequest((current) =>
      current?.nonce === nonce ? null : current,
    );
  }, []);
  const value = useMemo(
    () => ({
      selectedCycleId,
      selectCycle,
      pendingDesignKickoff,
      requestDesignKickoff,
      consumeDesignKickoff,
      pendingScopeRequest,
      requestComposerScope,
      consumeComposerScope,
    }),
    [
      consumeComposerScope,
      consumeDesignKickoff,
      pendingDesignKickoff,
      pendingScopeRequest,
      requestComposerScope,
      requestDesignKickoff,
      selectedCycleId,
    ],
  );
  return (
    <ProjectCycleSelectionContext.Provider value={value}>
      {children}
    </ProjectCycleSelectionContext.Provider>
  );
}

export function useProjectCycleSelection(): ProjectCycleSelection {
  // Unfiled chats intentionally have no project shell. Returning the inert
  // value keeps the shared chat page usable there while project routes receive
  // the live provider mounted by ProjectRailShell.
  return useContext(ProjectCycleSelectionContext) ?? NO_PROJECT_SELECTION;
}
