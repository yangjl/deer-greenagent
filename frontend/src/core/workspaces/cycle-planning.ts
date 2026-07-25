/**
 * Client-side DBTL cycle planning model (foundation demo, revision 2).
 *
 * Cycles ("Tasks" in the Biomni reference) and their per-cycle to-do lists
 * ("Drive" analog) are a browser-local projection persisted in localStorage.
 * They demonstrate the project-rail interaction for human review; durable
 * database-backed cycles arrive in a later cycle once the direction is
 * confirmed. All operations are immutable.
 */

export const DBTL_PHASES = ["design", "build", "test", "learn"] as const;

export type DbtlPhase = (typeof DBTL_PHASES)[number];

export interface CycleTodo {
  id: string;
  text: string;
  done: boolean;
}

export type CycleStatus = "active" | "done";

export interface DbtlCycle {
  id: string;
  name: string;
  phase: DbtlPhase;
  /** A cycle is running until it is explicitly completed. */
  status: CycleStatus;
  todos: CycleTodo[];
}

export interface CyclePlan {
  cycles: DbtlCycle[];
  selectedCycleId: string | null;
}

export function cyclePlanStorageKey(projectId: string): string {
  return `deer-flow:project-cycle-plan:${projectId}`;
}

export function normalizePhase(value: unknown): DbtlPhase {
  return DBTL_PHASES.includes(value as DbtlPhase)
    ? (value as DbtlPhase)
    : "design";
}

function cycleName(index: number): string {
  return `Cycle ${String(index + 1).padStart(2, "0")}`;
}

/** Initial plan: one cycle seeded from the project's current DBTL phase. */
export function seedCyclePlan(
  projectPhase: unknown,
  cycleId: string,
): CyclePlan {
  const cycle: DbtlCycle = {
    id: cycleId,
    name: cycleName(0),
    phase: normalizePhase(projectPhase),
    status: "active",
    todos: [],
  };
  return { cycles: [cycle], selectedCycleId: cycle.id };
}

export function addCycle(plan: CyclePlan, cycleId: string): CyclePlan {
  const cycle: DbtlCycle = {
    id: cycleId,
    name: cycleName(plan.cycles.length),
    phase: "design",
    status: "active",
    todos: [],
  };
  return { cycles: [...plan.cycles, cycle], selectedCycleId: cycle.id };
}

export function selectCycle(plan: CyclePlan, cycleId: string): CyclePlan {
  if (!plan.cycles.some((cycle) => cycle.id === cycleId)) {
    return plan;
  }
  return { ...plan, selectedCycleId: cycleId };
}

export function setCyclePhase(
  plan: CyclePlan,
  cycleId: string,
  phase: DbtlPhase,
): CyclePlan {
  return {
    ...plan,
    cycles: plan.cycles.map((cycle) =>
      cycle.id === cycleId ? { ...cycle, phase } : cycle,
    ),
  };
}

/** Completing the last running cycle is what minimizes the Cycles section. */
export function toggleCycleStatus(plan: CyclePlan, cycleId: string): CyclePlan {
  return {
    ...plan,
    cycles: plan.cycles.map((cycle) =>
      cycle.id === cycleId
        ? { ...cycle, status: cycle.status === "active" ? "done" : "active" }
        : cycle,
    ),
  };
}

export function hasActiveCycle(plan: CyclePlan | null): boolean {
  return (plan?.cycles ?? []).some((cycle) => cycle.status === "active");
}

export function selectedCycle(plan: CyclePlan): DbtlCycle | null {
  return (
    plan.cycles.find((cycle) => cycle.id === plan.selectedCycleId) ??
    plan.cycles[0] ??
    null
  );
}

export function addTodo(
  plan: CyclePlan,
  cycleId: string,
  todoId: string,
  text: string,
): CyclePlan {
  const trimmed = text.trim();
  if (!trimmed) {
    return plan;
  }
  return {
    ...plan,
    cycles: plan.cycles.map((cycle) =>
      cycle.id === cycleId
        ? {
            ...cycle,
            todos: [...cycle.todos, { id: todoId, text: trimmed, done: false }],
          }
        : cycle,
    ),
  };
}

export function toggleTodo(
  plan: CyclePlan,
  cycleId: string,
  todoId: string,
): CyclePlan {
  return {
    ...plan,
    cycles: plan.cycles.map((cycle) =>
      cycle.id === cycleId
        ? {
            ...cycle,
            todos: cycle.todos.map((todo) =>
              todo.id === todoId ? { ...todo, done: !todo.done } : todo,
            ),
          }
        : cycle,
    ),
  };
}

export function removeTodo(
  plan: CyclePlan,
  cycleId: string,
  todoId: string,
): CyclePlan {
  return {
    ...plan,
    cycles: plan.cycles.map((cycle) =>
      cycle.id === cycleId
        ? { ...cycle, todos: cycle.todos.filter((todo) => todo.id !== todoId) }
        : cycle,
    ),
  };
}

/** Validates untrusted persisted JSON back into a CyclePlan (or null). */
export function parseCyclePlan(raw: string | null): CyclePlan | null {
  if (!raw) {
    return null;
  }
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const candidate = value as { cycles?: unknown; selectedCycleId?: unknown };
  if (!Array.isArray(candidate.cycles)) {
    return null;
  }
  const cycles: DbtlCycle[] = [];
  for (const entry of candidate.cycles) {
    if (typeof entry !== "object" || entry === null) {
      return null;
    }
    const cycle = entry as {
      id?: unknown;
      name?: unknown;
      phase?: unknown;
      status?: unknown;
      todos?: unknown;
    };
    if (typeof cycle.id !== "string" || typeof cycle.name !== "string") {
      return null;
    }
    const todos: CycleTodo[] = [];
    for (const todoEntry of Array.isArray(cycle.todos) ? cycle.todos : []) {
      if (typeof todoEntry !== "object" || todoEntry === null) {
        return null;
      }
      const todo = todoEntry as { id?: unknown; text?: unknown; done?: unknown };
      if (typeof todo.id !== "string" || typeof todo.text !== "string") {
        return null;
      }
      todos.push({ id: todo.id, text: todo.text, done: todo.done === true });
    }
    cycles.push({
      id: cycle.id,
      name: cycle.name,
      phase: normalizePhase(cycle.phase),
      // Plans persisted before cycle status existed are still running.
      status: cycle.status === "done" ? "done" : "active",
      todos,
    });
  }
  if (cycles.length === 0) {
    return null;
  }
  const selectedCycleId =
    typeof candidate.selectedCycleId === "string" &&
    cycles.some((cycle) => cycle.id === candidate.selectedCycleId)
      ? candidate.selectedCycleId
      : (cycles[0]?.id ?? null);
  return { cycles, selectedCycleId };
}

export function serializeCyclePlan(plan: CyclePlan): string {
  return JSON.stringify(plan);
}
