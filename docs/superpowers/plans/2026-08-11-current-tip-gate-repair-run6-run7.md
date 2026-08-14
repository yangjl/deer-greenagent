# Current-Tip Gate Repair and Run 6–7 Implementation Plan

**Status:** Completed 2026-08-11. The current-tip proof gate passed, the
Build → Test canary passed, Run 6 completed through Learn, and Run 7 completed
fail-closed after preserving two `inconclusive` Test attempts. A later explicit
authorization covers consolidating and committing this result; Run 8 remains a
separate paused continuation.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore every required current-tip gate, then execute the governed Build → Test canary, Run 6 replay, and Run 7 without a model-token ceiling.

**Architecture:** Repair only the shared sanitizer classification, stale test expectations, and configured formatting surfaces. Treat the default frontend build as a separate diagnostic boundary. Model runs remain downstream of a completely green proof gate and use only visible product controls plus read-only persistence verification.

**Tech Stack:** Python 3.12, pytest, Ruff, Alembic, Next.js 16, TypeScript, Rstest, ESLint, Prettier, pnpm 10, DeerFlow manual DBTL profile, in-app browser.

## Global Constraints

- Run 6 replay and Run 7 have no model-token ceiling.
- Human gates, durable DBTL authority, evidence validation, and stop controls remain unchanged.
- Do not weaken gates, rewrite migrations, repair the database directly, or use hidden transitions.
- Do not refactor the Build/Test coordinators in this repair.
- Do not commit, push, or create a pull request.
- Runs are sequential; do not start Run 7 until Run 6 is clean.

---

### Task 1: Close the `craft_memory` sanitization gap

**Files:**
- Modify: `backend/tests/test_input_sanitization_middleware.py`
- Modify: `backend/packages/harness/deerflow/agents/middlewares/input_sanitization_middleware.py`

**Interfaces:**
- Consumes: `neutralize_untrusted_tags(text: str) -> str`
- Produces: `<craft_memory>` is escaped on both shared lead and subagent sanitizer paths.

- [x] **Step 1: Add the explicit regression case**

Add `"craft_memory"` to `_FRAMEWORK_STRUCTURED_TAGS`. The existing parametrized tests must then exercise both `_check_user_content` and `neutralize_untrusted_tags` with the literal framework block.

- [x] **Step 2: Prove RED**

Run:

```bash
cd backend
PYTHONPATH=.:packages/harness:packages/extension-api .venv/bin/python -m pytest \
  tests/test_input_sanitization_middleware.py::test_escapes_framework_structured_tags \
  tests/test_input_sanitization_middleware.py::test_neutralize_untrusted_tags_covers_framework_structured_tags \
  -q
```

Expected: the two new `craft_memory` cases fail because the literal tag is unchanged.

- [x] **Step 3: Apply the minimal production fix**

Add `"craft_memory"` to `_BLOCKED_TAG_NAMES`. Do not add a new sanitizer or special-case branch.

- [x] **Step 4: Prove GREEN and anti-drift coverage**

Run:

```bash
cd backend
PYTHONPATH=.:packages/harness:packages/extension-api .venv/bin/python -m pytest \
  tests/test_input_sanitization_middleware.py -q
```

Expected: all sanitizer tests pass and `test_denylist_covers_framework_authority_blocks` no longer reports `craft_memory`. Any other unclassified framework tag remains a separate failing signal and must be classified from its actual source.

---

### Task 2: Align migration proof with the actual head

**Files:**
- Modify only test files returned by `rg -l '0031_dbtl_conversational_discovery' backend/tests -g '*.py'`
- Do not modify: `backend/packages/harness/deerflow/persistence/migrations/versions/`

**Interfaces:**
- Consumes: Alembic `upgrade(..., "head")`
- Produces: tests assert the literal current head `0033_dbtl_evidence_exception_assessment` while preserving every schema/data assertion.

- [x] **Step 1: Preserve the observed RED evidence**

The preflight already recorded two meaningful failures: upgrade reached `0033_dbtl_evidence_exception_assessment` while tests expected `0031_dbtl_conversational_discovery`.

- [x] **Step 2: Update only head expectations**

Mechanically replace the old head literal in the twelve test files identified by `rg`. Do not change migration `down_revision` links or production code.

- [x] **Step 3: Verify the entire affected migration/bootstrap set**

Run all twelve files returned by the same `rg -l` query in one pytest invocation. Expected: every database reaches revision `0033_dbtl_evidence_exception_assessment`, legacy rows survive, and the existing schema/index/constraint assertions pass.

---

### Task 3: Restore configured format and build proof

**Files:**
- Mechanically format: `backend/packages/harness/deerflow/runtime/runs/worker.py`
- Mechanically format only files reported by `cd frontend && pnpm format`
- Do not change dependencies or application behavior.

**Interfaces:**
- Produces: `ruff format --check .` and `pnpm format` exit zero.

- [x] **Step 1: Apply configured formatters to their reported files**

Use Ruff on the single backend file and Prettier on exactly the reported frontend files. Review the diff to confirm whitespace/layout-only changes.

- [x] **Step 2: Re-run format and static gates**

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd frontend && pnpm check && pnpm format
git diff --check
```

- [x] **Step 3: Diagnose the default build in an uncontended checkout**

Run `pnpm build` with a private `node_modules` tree and no dev server sharing that checkout. If it fails or stalls, capture process state and logs, compare `pnpm exec next build --webpack`, and change product code only when the repository—not sandbox/worktree/process isolation—is reproducibly causal.

---

### Task 4: Run the complete proof gate

**Files:** none

**Interfaces:**
- Produces: fresh current-tip evidence before any model run.

- [x] **Step 1: Backend DBTL gate**

```bash
cd backend
CI=1 PYTHONPATH=.:packages/harness:packages/extension-api \
  .venv/bin/python -m pytest tests -m 'not live' -k dbtl -q
```

- [x] **Step 2: Frontend gates**

```bash
cd frontend
pnpm test
pnpm check
pnpm format
pnpm build
```

- [x] **Step 3: Stop on any red result**

Append exact results to `.deer-flow/manual-dbtl/run-ledger/dbtl-reliability-run6.md`. Do not start the canary or browser replay until every required command exits zero.

---

### Task 5: Execute the Build → Test canary

**Files:**
- Update: `.deer-flow/manual-dbtl/run-ledger/dbtl-reliability-run6.md`
- Capture through: `scripts/dbtl_manual.py` / `.deer-flow/manual-dbtl/`

**Interfaces:**
- Produces: one governed canary proving an input-consuming rerun contract and one self-contained rerun contract reach Test unchanged.

- [x] **Step 1: Verify and start the isolated manual profile**

Use `make dbtl-manual-list`, verify the profile config and frozen inputs, then start `make dbtl-manual-dev` only if the profile is not already healthy.

- [x] **Step 2: Execute through visible browser controls**

Create or use the designated canary project/cycle. Approve only evidence that proves Build rejects non-portable contracts and Test executes the exact accepted input mapping. Refresh each deck before acting.

- [x] **Step 3: Verify persistence read-only**

Compare stage attempts, artifacts, feedback surfaces, worker results, rerun specifications, hashes, and lineage before/after refresh. Record identifiers and outcomes without database writes.

---

### Task 6: Replay Run 6 and execute Run 7

**Files:**
- Update: `.deer-flow/manual-dbtl/run-ledger/dbtl-reliability-run6.md`
- Create/update: `.deer-flow/manual-dbtl/run-ledger/dbtl-reliability-run7.md`
- Update: `docs/superpowers/specs/2026-08-09-dbtl-10-run-reliability-campaign-design.md`

**Interfaces:**
- Run 6: valid `not_supported` Test outcome routes to Learn from durable Build recovery.
- Run 7: Test records an `inconclusive` attempt, repeats once while preserving both attempts, then follows the second server-computed outcome without manufacturing missing evidence.

- [x] **Step 1: Record the owner amendment**

Amend the campaign ledger/spec so Run 6 replay and Run 7 explicitly have no model-token ceiling, superseding both the later-run per-attempt cap and aggregate campaign token ceiling for these two runs only. Keep elapsed-time reporting and all governance stops.

- [x] **Step 2: Replay Run 6**

Resume cycle `cycle-fa067703445443339179a87256178d57` in thread `44317b36-678b-4db5-a7e0-7827a22a8fb0` from its durable Build `changes_requested` recovery. Use only visible controls; verify the corrected portable Build, exact Test rerun, server-computed `not_supported` outcome, allowed Learn route, terminal state, refresh durability, zero craft-memory writes, zero promotions/publications, and no hidden transitions.

- [x] **Step 3: Review the Run 6 evidence**

If any product defect appears, stop, classify it, preserve the failed attempt, and return to a test-first repair. Start Run 7 only when Run 6 is clean.

- [x] **Step 4: Execute Run 7 sequentially**

Restore the declared clean baseline and immutable fixture hashes. Start exactly one governed cycle whose immutable conditions produce `inconclusive`; repeat Test once through the offered control and preserve both Test attempts. If the frozen inputs still cannot establish authoritative row/entity identity, accept the second `inconclusive` outcome and close the cycle rather than weakening the validity check or entering Learn. Capture all IDs, hashes, tokens, elapsed time, transitions, refresh checks, and zero-write controls.

- [x] **Step 5: Final proof and review**

Re-run affected gates if source changed during diagnosis, update both ledgers, review the complete working diff against the approved spec, and stop without committing or shipping.
