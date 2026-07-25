"use client";

import { useCallback, useEffect, useState } from "react";

import { uuid } from "@/core/utils/uuid";
import {
  type CyclePlan,
  cyclePlanStorageKey,
  parseCyclePlan,
  seedCyclePlan,
  serializeCyclePlan,
} from "@/core/workspaces";

/**
 * Browser-local cycle plan for a project (foundation demo): loads the
 * persisted plan or seeds one cycle from the project's current DBTL phase,
 * and persists every update. Durable backend cycles are a later cycle.
 */
export function useCyclePlan(
  projectId: string | null | undefined,
  projectPhase: string | null | undefined,
) {
  const [plan, setPlan] = useState<CyclePlan | null>(null);

  useEffect(() => {
    if (!projectId || projectPhase == null) {
      setPlan(null);
      return;
    }
    let stored: CyclePlan | null = null;
    try {
      stored = parseCyclePlan(
        window.localStorage.getItem(cyclePlanStorageKey(projectId)),
      );
    } catch {
      stored = null;
    }
    setPlan(stored ?? seedCyclePlan(projectPhase, uuid()));
  }, [projectId, projectPhase]);

  const update = useCallback(
    (updater: (current: CyclePlan) => CyclePlan) => {
      setPlan((current) => {
        if (!current || !projectId) {
          return current;
        }
        const next = updater(current);
        try {
          window.localStorage.setItem(
            cyclePlanStorageKey(projectId),
            serializeCyclePlan(next),
          );
        } catch {
          // Quota/private-mode failures degrade to in-memory state.
        }
        return next;
      });
    },
    [projectId],
  );

  return { plan, update };
}
