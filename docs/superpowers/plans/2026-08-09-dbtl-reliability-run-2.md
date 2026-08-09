# DBTL Reliability Run 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete one exact governed replay of Run 1's supported synthetic-maize cycle, capture durable evidence, report the comparison, and stop before Run 3.

**Architecture:** Restore the captured Run 1 clean baseline, use the in-app browser as the only write/decision surface, and corroborate the terminal state with read-only SQLite and filesystem checks. Do not run an ordinary-chat arm. Accept every Design-approved deliverable; a Build-plan change cannot remove one.

**Tech Stack:** DeerFlow manual DBTL profile, in-app browser, SQLite read-only diagnostics, Python standard library, authenticated HTML review decks.

## Global Constraints

- Work only on branch `fix/build-bugs`; preserve all unrelated dirty changes.
- Use `gpt-5.6-sol-codex` in the composer and the frozen manual-profile Design models.
- Keep specialist craft memory disabled and write no memory facts.
- Use visible browser controls for discovery, meetings, decks, and stage handoffs.
- Never advance state through an API write, SQL write, fabricated artifact, or hidden transition.
- Stop the attempt at 350,000 model tokens or 45 minutes.
- Do not run an ordinary-chat control.
- Do not start or authorize Run 3.
- Do not commit or push unless the owner separately asks.

---

### Task 1: Freeze and restore the Run 2 baseline

**Files:**
- Read: `.deer-flow/manual-dbtl/scenarios/dbtl-reliability-run1-final/manifest.json`
- Restore: `.deer-flow/manual-dbtl/scenarios/dbtl-reliability-run1-start/`
- Record: `.deer-flow/manual-dbtl/run-ledger/dbtl-reliability-run2-environment.txt`

**Interfaces:**
- Consumes: completed Run 1 capture and the clean pre-Run-1 baseline
- Produces: an idle, clean manual profile with identical governed fixtures

- [x] **Step 1: Record branch, commit, dirty files, model configuration, and current service state.**

- [x] **Step 2: Verify that no run, stage worker, or deck action is active.**

- [x] **Step 3: Restore `dbtl-reliability-run1-start` through the documented hot-restore target.**

- [x] **Step 4: Verify the governed project has zero cycles and the fixture hashes are:**

```text
train.csv   24f4d576fd64b61020c437e91725a5594b4481868be51453d558d2b3b4e43e4b
holdout.csv f6d0640bbcdb1d4ba6182136360790b0e9637311692f658ddff0d9f26dab461c
EXPERIMENT.md 7c7790f31bca10a70c0b72d5edfc29386ebcb3e949cf177e48a45f6d5cfba65b
```

### Task 2: Run the exact governed supported replay

**Files:**
- Expected Build outputs: `run_calibration.py`, `model.json`, `predictions.csv`, `metrics.json`, `RUNBOOK.md`, plus every additional Design-approved deliverable

**Interfaces:**
- Consumes: restored governed project and frozen configuration
- Produces: one browser-created DBTL cycle with durable stage evidence

- [x] **Step 1: Open a fresh conversation in `DBTL Reliability Maize`, select GPT-5.6 Sol, and submit:**

```text
Start a new governed DBTL cycle for EXPERIMENT.md. Infer the deterministic synthetic maize calibration from data/train.csv and verify it on data/holdout.csv. Use Python's standard library only. Keep Design light and Build to exactly one small phase. Required deliverables are run_calibration.py, model.json, predictions.csv, metrics.json, and RUNBOOK.md plus a precise structured Test rerun specification. Success requires slope 2, intercept 1, holdout MAE 0, unchanged input hashes, and byte-identical outputs after a clean rerun. This is synthetic software calibration only and must not support a real breeding claim.
```

- [x] **Step 2: Verify discovery creates no cycle, then click `Start DBTL cycle` and verify exactly one cycle exists.**

- [x] **Step 3: Choose `Light debate`, start the meeting, and verify independent, red-team, and chair results precede synthesis.**

- [x] **Step 4: Review the Design deck, record an approval, and use the visible `Start Build` handoff.**

- [x] **Step 5: Accept the one-phase Build plan when it covers every approved manifest deliverable. Do not remove an approved deliverable through `Change the plan`.**

- [x] **Step 6: Run Build, verify its package and clean-rerun contract, then submit and approve the Build deck.**

- [x] **Step 7: Use `Start Test`, let Test rerun independently, accept only a server-computed `supported` route to Learn, and record any failed deliverable audit.**

- [x] **Step 8: Use `Start Learn`, approve only provisional synthetic-scoped evidence, and perform no promotion or publication.**

### Task 3: Verify durability and reproducibility

**Files:**
- Read: governed project outputs, stage packages, deck files, and SQLite projections

**Interfaces:**
- Consumes: terminal Run 2 cycle
- Produces: independent proof of terminal state, exact hashes, and no unauthorized knowledge write

- [x] **Step 1: Refresh the originating conversation and verify the cycle remains completed with all four stage decisions durable.**

- [x] **Step 2: Execute the persisted Build rerun command in a disposable clean directory and compare every expected machine-readable output hash.**

- [x] **Step 3: Verify Test outcome `supported`, route `advance_to_learn`, zero promotions, zero publications, and no craft-memory file.**

- [x] **Step 4: Record total and per-stage tokens, model runtime, wall time, worker identities, deliverable audit, and differences from Run 1.**

### Task 4: Capture and report Run 2

**Files:**
- Create: `.deer-flow/manual-dbtl/scenarios/dbtl-reliability-run2-final/manifest.json`
- Create: `.deer-flow/manual-dbtl/run-ledger/dbtl-reliability-run2.md`
- Modify: `docs/superpowers/specs/2026-08-09-dbtl-10-run-reliability-campaign-design.md`

**Interfaces:**
- Consumes: verified Run 2 evidence
- Produces: qualifying/failed classification, campaign totals, and a stop before Run 3

- [x] **Step 1: Capture scenario `dbtl-reliability-run2-final`.**

- [x] **Step 2: Classify Run 2 against the campaign success definition without hiding worker/package defects.**

- [x] **Step 3: Append the Run 2 checkpoint result and write the complete run ledger.**

- [x] **Step 4: Report Run 2 and stop. Do not prepare, start, or authorize Run 3.**
