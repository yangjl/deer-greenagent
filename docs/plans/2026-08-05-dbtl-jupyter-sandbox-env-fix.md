# DBTL Jupyter Sandbox Environment Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and superpowers:verification-before-completion. This replay is being repaired inline in the current feature workspace so the running manual-profile stack can hot-reload the change.

**Goal:** Let governed Build verification run installed Jupyter tools without writing outside the phase grant, and teach Build workers the cheapest valid notebook check.

**Architecture:** The server-owned Build execution boundary will override Jupyter/IPython state directories with paths beneath the already writable phase workspace. Worker guidance will show `python -m json.tool` for structural notebook validation and reserve `nbconvert` for cases that actually require execution. No dependency or broad `HOME` change is needed.

**Tech Stack:** Python 3.12, pytest, DeerFlow local/remote sandbox abstraction.

## Global Constraints

- Preserve the sandbox's read/write grant; do not add host-home access.
- Apply the environment fix to every governed Build verifier, independent of the selected specialist.
- Add no dependency and do not commit this round.
- Preserve the unrelated existing `frontend/tsconfig.json` modification.

---

### Task 1: Inject writable notebook runtime directories

**Files:**
- Modify: `backend/tests/test_dbtl_build_phase_verification.py`
- Modify: `backend/packages/harness/deerflow/agents/dbtl/live_stage/adapter.py`
- Modify: `backend/packages/harness/deerflow/agents/dbtl/live_stage/build_phases.py`
- Modify: `README.md`
- Modify: `backend/AGENTS.md`

**Interfaces:**
- Consumes: `_execute_server_build_command(command, env, timeout_seconds, ..., writable_workspace)`.
- Produces: the same execution result, with `JUPYTER_CONFIG_DIR`, `JUPYTER_DATA_DIR`, `JUPYTER_RUNTIME_DIR`, and `IPYTHONDIR` forced beneath `writable_workspace`.

- [x] Add a focused executor test whose fake sandbox captures `env` and asserts the four literal phase-scoped paths.
- [x] Run that test and confirm it fails because the variables are absent.
- [x] Merge the four server-owned variables into the environment immediately before sandbox execution; do not mutate the caller's dictionary.
- [x] Run the focused test and the Build phase-verification suite.
- [x] Add a concise Build-worker example: use `python -m json.tool artifact.ipynb` for structure-only validation; use `python -m jupyter nbconvert --execute` only when executed-cell evidence is required. State that the server supplies writable Jupyter directories.
- [x] Update README and backend agent guidance with the execution-boundary contract.
- [x] Run Ruff formatting/checks and the focused DBTL Build tests.
- [x] Restart the manual profile, then replay the browser flow through Build, Test, and Learn.

### Task 2: Keep explicit discovery read-only

The clean replay exposed a second defect before cycle creation: the supervisor copied
the discovery marker into `config`, but did not pass it through LangGraph's runtime
`context=` channel. The lead agent therefore saw the discovery prompt without the
read-only middleware seeing the marker, and wrote Build-like deliverables before a
cycle existed.

- [x] Add a regression assertion that the server-owned discovery context reaches the
  lead graph as runtime context.
- [x] Confirm the assertion fails with `runtime_contexts == [None]`.
- [x] Pass the same server-owned context explicitly to `lead_agent.ainvoke`.
- [x] Run the complete discovery Phase 1 test file (27 passed).
- [x] Restore the fixture and verify the proposal leaves only `EXPERIMENT.md`,
  `data/train.csv`, and `data/holdout.csv` in the project before confirmation.

## Replay Evidence

- Thread: `608e11a8-c18c-4d66-bec7-48e78b264f4a`
- Cycle: `cycle-48a17f8631e74783bf93a52469607f8d`
- Design: light meeting completed with independent, red-team, and chair positions;
  approved from `design-slides-rev1-8a735c.html`.
- Build: one five-deliverable phase completed; `a=2`, `b=1`, holdout MAE `0`,
  deterministic output validation passed; approved from
  `build-slides-rev4-558781.html`.
- Notebook: the worker correctly used `python -m json.tool` for the replay playbook;
  no host `~/.jupyter` access or Jupyter `PermissionError` occurred.
- Test: independent rerun and validity assessment computed `supported`; the route to
  Learn was selected from `test-slides-rev8-52ed0e.html`.
- Learn: the narrow, file-bounded update was approved from
  `learn-slides-rev11-43f10e.html`; the cycle is `completed`.
- Knowledge authority: five candidates remain `proposed`; SQL shows zero promotions,
  zero knowledge claims, and zero publications.
