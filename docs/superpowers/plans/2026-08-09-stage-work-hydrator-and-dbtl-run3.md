# StageWorkHydrator Fix and DBTL Run 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the same-thread hydration race, verify the full frontend gate, and complete the campaign's governed Design-clarification Run 3.

**Architecture:** Preserve `StageWorkHydrator`'s current fetch/retry effect and drive it with a small epoch that advances only on the rising edge of `isLoading`, so a run start replaces a stale read while run settlement does not cancel the replacement. After the frontend gate is green, restore the frozen manual profile and use the in-app browser as the only write/decision surface for one Design clarification cycle.

**Tech Stack:** React effects, TypeScript, Rstest, DeerFlow manual DBTL profile, in-app browser, SQLite read-only diagnostics, authenticated HTML review decks.

## Global Constraints

- Work only on branch `fix/build-bugs`; preserve unrelated dirty changes.
- Add no dependency, alternate API call, or database repair.
- Use the existing failing DOM test as the red regression.
- Do not start Run 3 unless the full frontend verification gate is green.
- Use GPT-5.6 Sol in the composer and the frozen manual-profile worker models.
- Keep specialist craft memory disabled and write no memory facts.
- Use visible browser controls for every governed decision and transition.
- Stop Runs 3–5 at 500,000 model tokens each or 45 minutes each. This amends
  the earlier 350,000-token ceiling; the 5.0-million campaign ceiling remains.
- Do not run an ordinary-chat control.
- Runs 4 and 5 are owner-authorized and must execute sequentially after Run 3.
- Do not prepare, start, or authorize Run 6.
- Do not commit or push unless the owner separately asks.

---

### Task 1: Fix the same-thread hydration race

**Files:**
- Modify: `frontend/src/components/workspace/messages/stage-work-panel.tsx`
- Test: `frontend/tests/unit/components/workspace/messages/stage-work-panel.dom.test.tsx`

**Interfaces:**
- Consumes: `StageWorkHydrator({ threadId, isLoading })`
- Produces: one replacement hydration when a live run starts and no third fetch when it settles

- [x] **Step 1: Run the existing same-epoch regression and verify RED.**

Run:

```bash
cd frontend
pnpm exec rstest 'tests/unit/components/workspace/messages/stage-work-panel.dom.test.tsx' --testNamePattern 'StageWorkHydrator durable hydration > rehydrates after a live run cancels an in-flight read in the same epoch'
```

Expected: FAIL because an unintended third `fetchStageWorkers` call returns
`undefined` and `.then` is read from it.

- [x] **Step 2: Apply the minimal shared-boundary fix.**

Track the previous loading value and advance a `loadingEpoch` only when
`isLoading` changes from false to true. Replace `isLoading` with `loadingEpoch`
in the hydration effect dependency list:

```ts
const previousIsLoadingRef = useRef(isLoading);
const [loadingEpoch, setLoadingEpoch] = useState(0);
useEffect(() => {
  if (isLoading && !previousIsLoadingRef.current) {
    setLoadingEpoch((value) => value + 1);
  }
  previousIsLoadingRef.current = isLoading;
}, [isLoading]);
```

- [x] **Step 3: Run the focused file and verify GREEN.**

Run:

```bash
cd frontend
pnpm exec rstest 'tests/unit/components/workspace/messages/stage-work-panel.dom.test.tsx'
```

Expected: all five tests pass.

- [x] **Step 4: Run the complete frontend gate.**

Run:

```bash
cd frontend
pnpm test
pnpm typecheck
pnpm exec eslint src/components/workspace/messages/stage-work-panel.tsx tests/unit/components/workspace/messages/stage-work-panel.dom.test.tsx
pnpm exec prettier --check src/components/workspace/messages/stage-work-panel.tsx tests/unit/components/workspace/messages/stage-work-panel.dom.test.tsx
cd ..
git diff --check
```

Expected: all commands exit zero. If any command fails, stop before Run 3.

### Task 2: Restore and freeze the Run 3 baseline

**Files:**
- Read: `.deer-flow/manual-dbtl/scenarios/dbtl-reliability-run1-start/manifest.json`
- Restore: `.deer-flow/manual-dbtl/scenarios/dbtl-reliability-run1-start/`
- Create: `.deer-flow/manual-dbtl/run-ledger/dbtl-reliability-run3-environment.txt`

**Interfaces:**
- Consumes: green frontend gate and frozen campaign fixture
- Produces: idle manual profile with no cycle and unchanged inputs

- [ ] **Step 1: Record branch, commit, dirty files, model configuration, service state, and the frontend verification result.**

- [ ] **Step 2: Verify no run, stage worker, or deck action is active, then use the documented hot-restore target for `dbtl-reliability-run1-start`.**

- [ ] **Step 3: Verify the governed project has zero cycles and these hashes:**

```text
train.csv     24f4d576fd64b61020c437e91725a5594b4481868be51453d558d2b3b4e43e4b
holdout.csv   f6d0640bbcdb1d4ba6182136360790b0e9637311692f658ddff0d9f26dab461c
EXPERIMENT.md 7c7790f31bca10a70c0b72d5edfc29386ebcb3e949cf177e48a45f6d5cfba65b
```

### Task 3: Execute the governed Design clarification route

**Files:**
- Expected Build outputs: `run_calibration.py`, `model.json`, `predictions.csv`, `metrics.json`, `RUNBOOK.md`, plus every Design-approved deliverable

**Interfaces:**
- Consumes: restored baseline
- Produces: one browser-created cycle with a durable chair clarification and terminal stage evidence

- [ ] **Step 1: Open a fresh governed-project conversation, select GPT-5.6 Sol, and submit:**

```text
Start a new governed DBTL cycle for EXPERIMENT.md. Infer the deterministic synthetic maize calibration from data/train.csv and verify it on data/holdout.csv using Python's standard library only. Keep Design light and Build to exactly one small phase. Required deliverables are run_calibration.py, model.json, predictions.csv, metrics.json, and RUNBOOK.md plus a precise structured Test rerun specification. Before completing Design, preserve one project-owner decision: after the independent and red-team positions, the chair must ask exactly one focused question—Which artifact should be the canonical human replay surface: human_replay.ipynb or REPLAY.md? Do not infer the answer. Pause for my decision, then resume only the chair over the recorded positions. Success requires slope 2, intercept 1, holdout MAE 0, unchanged input hashes, and byte-identical outputs after a clean rerun. This is synthetic software calibration only and must not support a real breeding claim.
```

- [ ] **Step 2: Verify discovery creates no cycle, start exactly one cycle, choose `Light debate`, and start the meeting.**

- [ ] **Step 3: Verify independent and red-team results precede a chair `needs_input` result with the exact declared question.**

- [ ] **Step 4: Answer through the authenticated Design deck:**

```text
Use human_replay.ipynb as the canonical human replay surface. Keep RUNBOOK.md and the structured Test rerun specification as supporting reproducibility evidence.
```

- [ ] **Step 5: Verify only the chair resumes, the earlier positions are reused, and a successor Design deck is registered.**

- [ ] **Step 6: Approve Design, use the visible `Start Build` handoff, accept a one-phase plan that covers every approved deliverable, and run Build.**

- [ ] **Step 7: Verify and approve the Build deck, use `Start Test`, accept only server-computed `supported` → Learn, then approve provisional Learn evidence without promotion/publication.**

### Task 4: Verify, capture, and report Run 3

**Files:**
- Create: `.deer-flow/manual-dbtl/scenarios/dbtl-reliability-run3-final/manifest.json`
- Create: `.deer-flow/manual-dbtl/run-ledger/dbtl-reliability-run3.md`
- Modify: `docs/superpowers/specs/2026-08-09-dbtl-10-run-reliability-campaign-design.md`

**Interfaces:**
- Consumes: terminal Run 3 cycle
- Produces: durable evidence, campaign classification, and explicit Run 4 stop

- [ ] **Step 1: Refresh and verify the completed cycle, both chair attempts, four approved stages, and zero active runs.**

- [ ] **Step 2: Execute the persisted Build rerun in a disposable clean directory and compare machine-readable hashes.**

- [ ] **Step 3: Verify Test `supported` / `advance_to_learn`, provisional-only candidates, zero promotions/publications, and zero craft-memory writes.**

- [ ] **Step 4: Review the focused fix with OCR, capture `dbtl-reliability-run3-final`, write the Run 3 ledger and campaign checkpoint, and mark every checklist item complete.**

- [ ] **Step 5: Checkpoint Run 3, execute the declared Run 4 Design-revision
  route, then execute revised Run 5 as Build request changes. The removed
  reconciliation scenario is not a gate or a pilot route. Stop before revised
  Run 6.**
