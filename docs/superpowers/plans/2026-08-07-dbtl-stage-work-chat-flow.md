# DBTL Stage Work Native Chat Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render every governed DBTL worker as its own expandable native activity card followed immediately by that worker's model-written summary as ordinary chat prose.

**Architecture:** Keep the backend worker stream, durable `Subtask` records, run selection, and hydration unchanged. Add one display-only seam to `SubtaskCard` so `StageWorkPanel` can own terminal narration placement, then compose each task as `card → prose` in server event order using the existing safe summary helper and Markdown renderer.

**Tech Stack:** React 19, TypeScript 5.8, Tailwind CSS 4, Rstest, Testing Library, existing DeerFlow task context and Markdown components.

## Global Constraints

- Native chat first: reuse `SubtaskCard`, `MarkdownContent`, existing task presentation helpers, and ordinary chat spacing.
- Add no backend event, API, persistence field, model call, dependency, custom timeline, decorative connector, or new status vocabulary.
- Keep worker title, model, tokens, status, expandable tool history, task IDs, run IDs, hydration, and refresh recovery unchanged.
- Raw governed JSON must remain hidden.
- Running workers receive no invented narration; completed and failed workers use only existing safe terminal prose.
- Existing Design, Build, Test, and Learn review decks and human gates remain unchanged.
- Do not commit the refactor until the user explicitly requests that follow-up commit.

---

### Task 1: Compose each governed worker as card then prose

**Files:**
- Modify: `frontend/src/components/workspace/messages/subtask-card.tsx`
- Modify: `frontend/src/components/workspace/messages/stage-work-panel.tsx`
- Modify: `frontend/tests/unit/components/workspace/messages/subtask-card.dom.test.tsx`
- Modify: `frontend/tests/unit/components/workspace/messages/stage-work-panel.dom.test.tsx`
- Modify: `frontend/AGENTS.md`

**Interfaces:**
- Consumes: `terminalStageReportForDisplay(task, cappedFailureMessages): string | undefined`, existing `Subtask`, `MarkdownContent`, and `SubtaskCard` props.
- Produces: `SubtaskCard` prop `showTerminalReport?: boolean`, defaulting to `true`; `StageWorkPanel` passes `false` and renders the selected report immediately after the card.

- [ ] **Step 1: Write the failing `SubtaskCard` suppression test**

Add a test beside the governed progress-report tests in `subtask-card.dom.test.tsx`:

```tsx
it("lets the stage flow place terminal prose outside the worker card", () => {
  render(
    <CardHarness
      showTerminalReport={false}
      task={{
        id: "build-phase",
        status: "completed",
        subagent_type: "subagent",
        description: "Implement exact fixture",
        prompt: "",
        dbtlStage: "build",
        displaySummary: "Recovered slope 2 and intercept 1.",
      }}
    />,
  );

  expect(screen.queryByText("Progress report")).toBeNull();
  expect(
    screen.queryByText("Recovered slope 2 and intercept 1."),
  ).toBeNull();

  fireEvent.click(
    screen.getByRole("button", { name: /Implement exact fixture/i }),
  );
  expect(
    screen.queryByText("Recovered slope 2 and intercept 1."),
  ).toBeNull();
});
```

Change `CardHarness` to forward the optional prop to the real component:

```tsx
function CardHarness({
  task,
  showTerminalReport,
}: {
  task: Subtask;
  showTerminalReport?: boolean;
}) {
  const tasks = { [task.id]: task };
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;
  return (
    <SubtaskContext.Provider
      value={{
        tasks,
        tasksRef,
        setTasks: () => {
          /* static test fixture */
        },
      }}
    >
      <SubtaskCard
        taskId={task.id}
        isLoading={false}
        showTerminalReport={showTerminalReport}
      />
    </SubtaskContext.Provider>
  );
}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd frontend
pnpm test -- tests/unit/components/workspace/messages/subtask-card.dom.test.tsx
```

Expected: FAIL because `CardHarness`/`SubtaskCard` does not accept `showTerminalReport` and the model summary remains inside the card.

- [ ] **Step 3: Add the minimal `SubtaskCard` display seam**

Add the optional prop with a backward-compatible default:

```tsx
export function SubtaskCard({
  className,
  taskId,
  threadId,
  runId,
  isLoading,
  flat = false,
  showTerminalReport = true,
}: {
  className?: string;
  taskId: string;
  threadId?: string;
  runId?: string;
  isLoading: boolean;
  flat?: boolean;
  showTerminalReport?: boolean;
}) {
```

Guard both terminal-summary locations:

```tsx
{showTerminalReport && collapsed && terminalStageReport ? (/* existing report */) : null}

{showTerminalReport && task.status === "completed" && (/* existing completed result */)}
{showTerminalReport && task.status === "failed" && (/* existing failure result */)}
```

Do not change ordinary subtask defaults or the tool timeline.

- [ ] **Step 4: Run the focused card test and verify GREEN**

Run the Step 2 command again.

Expected: PASS, including the existing ordinary-subtask and governed-report cases.

- [ ] **Step 5: Write the failing chronological-flow tests**

In `stage-work-panel.dom.test.tsx`, update the existing `SubtaskCard` test double to emit only a stable card marker, then add real `StageWorkPanel` assertions for its own composition:

```tsx
it("places each worker's model prose directly after its own card", () => {
  const implementation: Subtask = {
    id: "implementation",
    status: "completed",
    subagent_type: "subagent",
    description: "Implementation specialist",
    prompt: "",
    dbtlStage: "build",
    runId: "run-1",
    displaySummary: "Implemented the exact fixture.",
  };
  const audit: Subtask = {
    id: "audit",
    status: "completed",
    subagent_type: "subagent",
    description: "Independent audit specialist",
    prompt: "",
    dbtlStage: "build",
    runId: "run-1",
    displaySummary: "The independent rerun passed.",
  };

  const { container } = render(
    <SeededTasks tasks={{ implementation, audit }}>
      <StageWorkPanel threadId="thread-1" runId="run-1" isLoading={false} />
    </SeededTasks>,
  );

  expect(container.textContent).toMatch(
    /implementation.*Implemented the exact fixture\..*audit.*The independent rerun passed\./s,
  );
});

it("does not narrate a worker before it finishes", () => {
  const running: Subtask = {
    id: "running-phase",
    status: "in_progress",
    subagent_type: "subagent",
    description: "Implementation specialist",
    prompt: "",
    dbtlStage: "build",
    runId: "run-1",
    displaySummary: "Partial output must not read as a result.",
  };

  render(
    <SeededTasks tasks={{ running }}>
      <StageWorkPanel threadId="thread-1" runId="run-1" isLoading />
    </SeededTasks>,
  );

  expect(screen.queryByText("Partial output must not read as a result.")).toBeNull();
});
```

Add one failed/capped fixture asserting the existing localized capped explanation appears after its card and the partial-success `error` does not.

- [ ] **Step 6: Run the panel tests and verify RED**

Run:

```bash
cd frontend
pnpm test -- tests/unit/components/workspace/messages/stage-work-panel.dom.test.tsx
```

Expected: FAIL because `StageWorkPanel` currently renders only cards and owns no terminal narration.

- [ ] **Step 7: Implement the minimal chronological composition**

In `StageWorkPanel`:

1. Call `useI18n()` and build the same three capped-failure messages already used by `SubtaskCard`.
2. For each task, compute `terminalStageReportForDisplay(task, cappedFailureMessages)`.
3. Render one plain wrapper containing the card, followed by `MarkdownContent` only when terminal prose exists.
4. Pass `showTerminalReport={false}` to the card.

Target shape:

```tsx
{group.tasks.map((task) => {
  const report = terminalStageReportForDisplay(task, cappedFailureMessages);
  return (
    <div key={task.id} className="flex w-full flex-col gap-3">
      <SubtaskCard
        taskId={task.id}
        threadId={threadId}
        runId={task.runId ?? runId}
        isLoading={task.status === "in_progress"}
        flat
        showTerminalReport={false}
      />
      {report ? (
        <div className="text-foreground text-sm leading-6">
          <MarkdownContent content={report} isLoading={false} />
        </div>
      ) : null}
    </div>
  );
})}
```

Retain the existing stage label and group/run selection. Do not add a new card around the sequence.

- [ ] **Step 8: Run the focused card and panel tests and verify GREEN**

Run:

```bash
cd frontend
pnpm test -- \
  tests/unit/components/workspace/messages/subtask-card.dom.test.tsx \
  tests/unit/components/workspace/messages/stage-work-panel.dom.test.tsx
```

Expected: PASS with no snapshots changed.

- [ ] **Step 9: Update the frontend architecture note**

In `frontend/AGENTS.md`, revise the native stage-work paragraph to state that each governed worker renders as its own native activity card and that the safe terminal model summary follows outside the card as ordinary prose. Preserve the existing rules for hydration, task ownership, raw contract suppression, and authenticated review decks.

- [ ] **Step 10: Run regression and static verification**

Run:

```bash
cd frontend
pnpm test
pnpm check
pnpm format
cd ..
git diff --check
```

Expected: all tests pass, ESLint and TypeScript pass, formatting reports no changes needed, and `git diff --check` exits 0.

- [ ] **Step 11: Run the visible manual-profile replay**

Use `dbtl-browser-pilot` with the in-app browser on the local manual profile:

1. Resume the deterministic `y = 2x + 1` cycle from the nearest durable pre-Build checkpoint.
2. Confirm the implementation card appears before its prose.
3. Confirm the audit card appears only after the implementation prose and receives its own prose on completion.
4. Confirm synthesis follows in the same pattern.
5. Confirm the final review files/deck remain unchanged and no human gate is answered automatically.
6. Refresh and verify the same ordering and prose survive durable hydration.

Expected: chronological `card → prose` pairs, no duplicated in-card report, no raw JSON, and unchanged authenticated review behavior.

- [ ] **Step 12: Review and prepare the follow-up commit**

Run OCR review on the exact refactor diff, fixing credible blockers and rerunning affected checks. Do not commit until the user explicitly authorizes the follow-up commit. When authorized:

```bash
git add \
  frontend/AGENTS.md \
  frontend/src/components/workspace/messages/subtask-card.tsx \
  frontend/src/components/workspace/messages/stage-work-panel.tsx \
  frontend/tests/unit/components/workspace/messages/subtask-card.dom.test.tsx \
  frontend/tests/unit/components/workspace/messages/stage-work-panel.dom.test.tsx \
  docs/superpowers/plans/2026-08-07-dbtl-stage-work-chat-flow.md
git commit -m "polish(dbtl): present stage work as native chat flow"
```
