# DBTL Specialist Memory and Deck Composer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the approved August 9 plan so configured DBTL specialists retain project-scoped craft memory and the Build summarizer produces feedback-informed, persisted slide plans with deterministic fallback.

**Architecture:** Add one opt-in `craft_memory` bit to the existing subagent configuration and reuse the current memory tools, scope resolver, and prompt assembly. Add one bounded repository query for project feedback, then extend the existing `BuildReviewPackage` with a small slide-plan value object consumed by the existing renderer. Keep DBTL stage authority, `MemoryWritePolicy`, persistence schema, and agent roster unchanged.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy async repositories, pytest, existing DeerMem and DBTL stage contracts.

## Global Constraints

- No new agent, database migration, dependency, or stage transition.
- Craft memory may contain working style, preferences, and failure modes; never scientific claims.
- Global `memory.mode` remains `middleware`; configured specialists receive explicit memory tools without `MemoryMiddleware`.
- Reviewer comments are bounded evidence loaded every summarizer run; memory is not their replacement.
- Missing, malformed, or over-limit slide plans fall back to the current deterministic deck.
- The plot style remains byte-reproducible and its exact skill revision is bound to phases that declare figure outputs.
- Existing dirty files outside this plan are protected and must not be edited.

---

### Task 1: Prove subagent memory isolation

**Files:**
- Modify: `backend/tests/test_memory_tools.py`
- Existing implementation: `backend/packages/harness/deerflow/subagents/executor.py`

**Interfaces:**
- Consumes: `memory_add_tool`, `memory_search_tool`, `scoped_memory_user_id`, and subagent runtime keys `agent_name`, `project_id`, `project_root`.
- Produces: integration proof that two agents in one project and one agent in a second project use distinct fact buckets.

- [x] Add a real-manager test that writes one fact per scope and proves no cross-read.
- [x] Run the characterization test; it passes with all scope keys and directly asserts every forbidden cross-read.
- [x] Make no production change because the existing propagation is correct.
- [x] Run the focused memory and subagent-context tests green (`2 passed`).

### Task 2: Opt configured specialists into explicit craft memory

**Files:**
- Modify: `backend/packages/harness/deerflow/config/subagents_config.py`
- Modify: `backend/packages/harness/deerflow/subagents/config.py`
- Modify: `backend/packages/harness/deerflow/subagents/registry.py`
- Modify: `backend/packages/harness/deerflow/subagents/executor.py`
- Modify: `backend/packages/harness/deerflow/agents/dbtl/live_stage/adapter.py`
- Modify: `config.example.yaml`
- Modify: `config.yaml`
- Test: `backend/tests/test_subagent_skills_config.py`
- Test: `backend/tests/test_subagent_executor.py`
- Test: `backend/tests/test_dbtl_live_stage_execution.py`

**Interfaces:**
- Produces: `SubagentConfig.craft_memory: bool`; `CustomSubagentConfig.craft_memory: bool`; bounded `<craft_memory>` prompt context; the four existing `memory_*` tools for opted-in DBTL workers.

- [x] Write failing config-propagation, tool-availability, read-only summarizer, bounded-injection, and disabled-behavior tests.
- [x] Run each red check and confirm the missing `craft_memory` behavior is the cause.
- [x] Add the boolean field and registry propagation.
- [x] Reuse `get_memory_tools()` without adding `MemoryMiddleware`; refuse a backend that requires passive tool-mode writes.
- [x] Reuse `scoped_memory_user_id()` and the manager token bound to inject only the current specialist/project facts.
- [x] Add the craft/style/failure-mode prompt boundary and enable the two existing specialists in example/local config.
- [x] Run focused tests green (`156 passed`).

### Task 3: Load recent project slide feedback

**Files:**
- Modify: `backend/packages/harness/deerflow/persistence/dbtl/design_feedback_ops.py`
- Modify: `backend/packages/harness/deerflow/agents/dbtl/live_stage/build_review.py`
- Modify: `backend/packages/harness/deerflow/agents/dbtl/live_stage/adapter.py`
- Test: `backend/tests/test_dbtl_design_feedback_surface.py`
- Test: `backend/tests/test_dbtl_build_summary_and_deck.py`

**Interfaces:**
- Produces: `recent_project_stage_feedback(project_id, limit)` returning newest-first action summaries with server-owned slide titles; optional `reviewer_feedback` input to `summarizer_unit()`.

- [x] Write a failing repository test covering project isolation, ordering, limit, and title resolution.
- [x] Implement one joined, bounded query with no schema change.
- [x] Write a failing summarizer-prompt test proving recent comments are present and labelled as evidence.
- [x] Load feedback fail-soft from the repository and pass it to the summarizer.
- [x] Run focused repository and summarizer tests green (`88 passed`).

### Task 4: Persist and render a bounded slide plan

**Files:**
- Modify: `backend/packages/harness/deerflow/dbtl/build_summary.py`
- Modify: `backend/packages/harness/deerflow/agents/dbtl/live_stage/build_review.py`
- Modify: `backend/packages/harness/deerflow/dbtl/build_deck.py`
- Create: `skills/public/dbtl-build-deck-style/SKILL.md`
- Test: `backend/tests/test_dbtl_build_summary_and_deck.py`
- Test: `backend/tests/test_dbtl_build_workflow_execution.py`

**Interfaces:**
- Produces: bounded `BuildSlide` records in `BuildReviewPackage.slide_plan`; renderer consumes valid plans and otherwise uses the existing fixed layout.

- [x] Write failing parser tests for valid, missing, malformed, unknown-figure, and over-limit plans.
- [x] Add the minimal slide-plan dataclass/parser and preserve it in `as_dict()`.
- [x] Write failing renderer tests for ordered plan consumption, one figure per slide, no empty plan slides, and deterministic fallback.
- [x] Make `render_build_deck()` select plan rendering only when the parsed plan is valid.
- [x] Extend the summarizer contract and add the deck-composition skill without a new seat.
- [x] Prove deck retry reuses the stored summarizer output (`3 passed`).

### Task 5: Guarantee plot-style lineage for drawing phases

**Files:**
- Modify: `backend/packages/harness/deerflow/dbtl/build_plan.py`
- Test: `backend/tests/test_dbtl_build_plan.py`
- Test: `backend/tests/test_dbtl_plot_style.py`

**Interfaces:**
- Produces: phases whose declared outputs are figures include `dbtl-plot-style` in their exact skill bindings.

- [x] Write a failing plan-parser test for a drawing phase and a non-drawing control.
- [x] Add the smallest deterministic figure-output predicate and skill insertion.
- [x] Run plan, skill-binding, and reproducibility tests green (`32 passed`, plus exact-binding tests in the 371-test regression set).

### Task 6: Regression proof and review graph

**Files:**
- Review all files changed by Tasks 1-5; do not alter protected dirty files.

- [x] Run focused memory, subagent, persistence, Build summary/deck, Build workflow, and plot-style suites.
- [x] Run backend formatting/type/static checks appropriate to the touched files.
- [x] Run OCR delegate mode on all 18 reviewable task files and apply only correctness findings that survive verification.
- [x] Run the senior code-review skill, classify remaining findings, and fix blockers through new red-green cycles.
- [x] Re-run the focused suite (`374 passed`) and report remaining gaps without shipping or committing.
