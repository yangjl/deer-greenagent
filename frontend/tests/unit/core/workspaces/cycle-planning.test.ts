import { describe, expect, it } from "@rstest/core";

import {
  addCycle,
  addTodo,
  hasActiveCycle,
  parseCyclePlan,
  removeTodo,
  seedCyclePlan,
  selectCycle,
  selectedCycle,
  serializeCyclePlan,
  setCyclePhase,
  toggleCycleStatus,
  toggleTodo,
} from "@/core/workspaces/cycle-planning";

describe("cycle status", () => {
  it("seeds and adds cycles as running", () => {
    const plan = addCycle(seedCyclePlan("design", "c1"), "c2");
    expect(plan.cycles.map((c) => c.status)).toEqual(["active", "active"]);
    expect(hasActiveCycle(plan)).toBe(true);
  });

  it("reports no active cycle only once every cycle is done", () => {
    const plan = addCycle(seedCyclePlan("design", "c1"), "c2");
    const oneDone = toggleCycleStatus(plan, "c1");
    expect(hasActiveCycle(oneDone)).toBe(true);
    const allDone = toggleCycleStatus(oneDone, "c2");
    expect(hasActiveCycle(allDone)).toBe(false);
    expect(hasActiveCycle(toggleCycleStatus(allDone, "c2"))).toBe(true);
  });

  it("treats a null plan as having no active cycle", () => {
    expect(hasActiveCycle(null)).toBe(false);
  });

  it("keeps plans persisted before cycle status running", () => {
    const legacy = JSON.stringify({
      cycles: [{ id: "c1", name: "Cycle 01", phase: "build", todos: [] }],
      selectedCycleId: "c1",
    });
    expect(parseCyclePlan(legacy)?.cycles[0]?.status).toBe("active");
    expect(hasActiveCycle(parseCyclePlan(legacy))).toBe(true);
  });
});

describe("seedCyclePlan", () => {
  it("seeds one cycle from the project phase and selects it", () => {
    const plan = seedCyclePlan("test", "c1");
    expect(plan.cycles).toHaveLength(1);
    expect(plan.cycles[0]).toMatchObject({
      id: "c1",
      name: "Cycle 01",
      phase: "test",
      todos: [],
    });
    expect(plan.selectedCycleId).toBe("c1");
  });

  it("normalizes unknown phases to design", () => {
    expect(seedCyclePlan("weird", "c1").cycles[0]?.phase).toBe("design");
  });
});

describe("cycle operations", () => {
  it("addCycle appends, numbers, and selects the new cycle immutably", () => {
    const plan = seedCyclePlan("design", "c1");
    const next = addCycle(plan, "c2");
    expect(plan.cycles).toHaveLength(1);
    expect(next.cycles.map((c) => c.name)).toEqual(["Cycle 01", "Cycle 02"]);
    expect(next.selectedCycleId).toBe("c2");
  });

  it("selectCycle ignores unknown ids", () => {
    const plan = addCycle(seedCyclePlan("design", "c1"), "c2");
    expect(selectCycle(plan, "missing").selectedCycleId).toBe("c2");
    expect(selectCycle(plan, "c1").selectedCycleId).toBe("c1");
  });

  it("setCyclePhase updates only the target cycle", () => {
    const plan = addCycle(seedCyclePlan("design", "c1"), "c2");
    const next = setCyclePhase(plan, "c2", "learn");
    expect(next.cycles[0]?.phase).toBe("design");
    expect(next.cycles[1]?.phase).toBe("learn");
  });

  it("selectedCycle falls back to the first cycle", () => {
    const plan = { ...seedCyclePlan("design", "c1"), selectedCycleId: null };
    expect(selectedCycle(plan)?.id).toBe("c1");
  });
});

describe("todo operations", () => {
  it("adds trimmed todos and ignores blank input", () => {
    const plan = seedCyclePlan("design", "c1");
    const next = addTodo(plan, "c1", "t1", "  Score drought trial  ");
    expect(next.cycles[0]?.todos).toEqual([
      { id: "t1", text: "Score drought trial", done: false },
    ]);
    expect(addTodo(plan, "c1", "t2", "   ")).toBe(plan);
  });

  it("toggles and removes todos immutably", () => {
    const plan = addTodo(seedCyclePlan("design", "c1"), "c1", "t1", "Plan");
    const toggled = toggleTodo(plan, "c1", "t1");
    expect(plan.cycles[0]?.todos[0]?.done).toBe(false);
    expect(toggled.cycles[0]?.todos[0]?.done).toBe(true);
    expect(removeTodo(toggled, "c1", "t1").cycles[0]?.todos).toEqual([]);
  });
});

describe("persistence round-trip", () => {
  it("serializes and parses a plan losslessly", () => {
    const plan = toggleTodo(
      addTodo(addCycle(seedCyclePlan("build", "c1"), "c2"), "c2", "t1", "Sow"),
      "c2",
      "t1",
    );
    expect(parseCyclePlan(serializeCyclePlan(plan))).toEqual(plan);
  });

  it("rejects malformed payloads", () => {
    expect(parseCyclePlan(null)).toBeNull();
    expect(parseCyclePlan("not json")).toBeNull();
    expect(parseCyclePlan("{}")).toBeNull();
    expect(parseCyclePlan('{"cycles":[]}')).toBeNull();
    expect(parseCyclePlan('{"cycles":[{"id":1}]}')).toBeNull();
  });

  it("heals an unknown selectedCycleId to the first cycle", () => {
    const raw = serializeCyclePlan({
      ...seedCyclePlan("design", "c1"),
      selectedCycleId: "ghost",
    });
    expect(parseCyclePlan(raw)?.selectedCycleId).toBe("c1");
  });
});
