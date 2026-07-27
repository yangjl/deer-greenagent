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
  /**
   * What the human answered when asked to pin the design, verbatim.
   *
   * The council debates whatever it is given, so sending it before those
   * answers exist produces a confident synthesis of nothing in particular.
   */
  designNotes?: string;
}

/**
 * A cycle that exists but whose design questions are still unanswered.
 *
 * Creation and the Design council are two steps and both must happen — a cycle
 * that never gets designed is a regression that has shipped here before. This
 * holds the second step open across the question card instead of dropping it:
 * the kickoff is armed at creation and released once the human answers.
 */
export interface ArmedDesignKickoff {
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
  requestDesignKickoff: (
    cycleId: string,
    cycleTitle: string,
    designNotes?: string,
  ) => void;
  consumeDesignKickoff: (nonce: number) => void;
  armedDesignKickoff: ArmedDesignKickoff | null;
  armDesignKickoff: (cycleId: string, cycleTitle: string) => void;
  releaseDesignKickoff: (designNotes: string) => void;
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
  armedDesignKickoff: null,
  armDesignKickoff: () => undefined,
  releaseDesignKickoff: () => undefined,
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
    (cycleId: string, cycleTitle: string, designNotes?: string) => {
      setPendingDesignKickoff({
        nonce: Date.now(),
        cycleId,
        cycleTitle,
        designNotes,
      });
    },
    [],
  );
  const consumeDesignKickoff = useCallback((nonce: number) => {
    setPendingDesignKickoff((current) =>
      current?.nonce === nonce ? null : current,
    );
  }, []);
  const [armedDesignKickoff, setArmedDesignKickoff] =
    useState<ArmedDesignKickoff | null>(null);
  const armDesignKickoff = useCallback(
    (cycleId: string, cycleTitle: string) => {
      setArmedDesignKickoff({ cycleId, cycleTitle });
    },
    [],
  );
  const releaseDesignKickoff = useCallback(
    (designNotes: string) => {
      setArmedDesignKickoff((armed) => {
        if (!armed) {
          return null;
        }
        requestDesignKickoff(armed.cycleId, armed.cycleTitle, designNotes);
        return null;
      });
    },
    [requestDesignKickoff],
  );
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
      armedDesignKickoff,
      armDesignKickoff,
      releaseDesignKickoff,
      pendingScopeRequest,
      requestComposerScope,
      consumeComposerScope,
    }),
    [
      armDesignKickoff,
      armedDesignKickoff,
      consumeComposerScope,
      consumeDesignKickoff,
      pendingDesignKickoff,
      pendingScopeRequest,
      releaseDesignKickoff,
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
