# DBTL Reliability Run 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute one clean, straight-through supported maize DBTL cycle as the feasibility checkpoint for the approved ten-run reliability campaign.

**Architecture:** Use the isolated manual DBTL profile and the in-app browser as the authoritative interaction surface. Prepare two small projects with byte-identical immutable fixtures, run one ordinary-chat control and one governed Design → Build → Test → Learn cycle, then corroborate the browser result with read-only database and filesystem evidence.

**Tech Stack:** DeerFlow manual profile, SQLite, in-app Browser control, Python standard library fixture, authenticated DBTL HTML decks, existing scenario capture tooling.

## Global Constraints

- Hard campaign budget: eight working hours and 3,000,000 model tokens.
- Run 1 hard cap: 45 minutes or 350,000 model tokens.
- Run 1 target: 25 minutes and 240,000 model tokens.
- Freeze model names, reasoning, specialists, prompts, skills, deck behavior, DBTL policy, and stage-spec versions.
- Use `gpt-5.6-sol-codex` as the composer/run model in both ordinary and governed conversations; keep the manual profile's `gpt-5.5-codex` setup and Design-meeting models unchanged.
- Specialist craft memory starts empty and remains unavailable for writes.
- Use visible browser controls for every human decision; API and SQL access are read-only diagnostics.
- Do not patch source code or repair the database during a qualifying attempt.
- If a product bug appears, preserve the failed attempt, diagnose it using systematic debugging and TDD, fix the shared root minimally, restore the baseline, and replay from the start.
- Do not weaken authorization, provenance, evidence hashes, stage ownership, or fail-closed behavior.
- Preserve all unrelated working-tree changes. Do not commit or push experiment results.

---

### Task 1: Record and validate the frozen execution environment

**Files:**
- Read: `AGENTS.md`
- Read: `backend/AGENTS.md`
- Read: `frontend/AGENTS.md`
- Read: `.deer-flow/manual-dbtl/config.yaml`
- Create: `.deer-flow/manual-dbtl/run-ledger/dbtl-reliability-run1-environment.txt`

**Interfaces:**
- Consumes: approved design at `docs/superpowers/specs/2026-08-09-dbtl-10-run-reliability-campaign-design.md`
- Produces: immutable environment record used by the run ledger and final feasibility assessment

- [ ] **Step 1: Confirm repository, branch, protected changes, and service targets**

Run:

```bash
git rev-parse HEAD
git branch --show-current
git status --short
rg -n "dbtl-manual-(init|list|capture|restore|dev)" Makefile
```

Expected: branch `fix/build-bugs`; documented manual targets exist; all pre-existing modified files remain visible.

- [ ] **Step 2: Compute the working-tree patch identity**

Run:

```bash
git diff --binary | shasum -a 256
```

Expected: one SHA-256 recorded alongside `git rev-parse HEAD`; this pair identifies the exact executable source state without committing protected dirty changes.

- [ ] **Step 3: Freeze specialist craft memory for this campaign**

In `.deer-flow/manual-dbtl/config.yaml`, set both custom specialist entries to:

```yaml
craft_memory: false
```

Keep the agent names, prompts, skills, model, turn limits, and timeouts unchanged. Do not change DBTL stage or deck settings.

- [ ] **Step 4: Verify the frozen configuration and empty craft-memory baseline**

Run:

```bash
rg -n "craft_memory|setup_draft_model_name|council_model_name|design_deck_feedback|stage_meetings" .deer-flow/manual-dbtl/config.yaml
find .deer-flow/manual-dbtl -type f -name memory.json -print
```

Expected: both specialists show `craft_memory: false`; no specialist memory file exists; model and DBTL settings are recorded.

- [ ] **Step 5: Confirm the active stack is healthy or restart it from the documented target**

Run:

```bash
curl -fsSI http://localhost:2026/
curl -fsS http://localhost:8001/openapi.json
lsof -nP -iTCP:2026 -sTCP:LISTEN
lsof -nP -iTCP:8001 -sTCP:LISTEN
```

If either HTTP request fails and no stage worker is active, run:

```bash
make dbtl-manual-dev
```

Expected: nginx answers on port 2026 and the Gateway answers through the manual profile.

### Task 2: Prepare two tiny, identical maize projects and capture the clean baseline

**Files:**
- Create: `.deer-flow/manual-dbtl/live/projects/DBTL Reliability Maize/data/train.csv`
- Create: `.deer-flow/manual-dbtl/live/projects/DBTL Reliability Maize/data/holdout.csv`
- Create: `.deer-flow/manual-dbtl/live/projects/DBTL Reliability Maize/EXPERIMENT.md`
- Create: `.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/data/train.csv`
- Create: `.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/data/holdout.csv`
- Create: `.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/EXPERIMENT.md`
- Create: `.deer-flow/manual-dbtl/scenarios/dbtl-reliability-run1-start/manifest.json`

**Interfaces:**
- Consumes: healthy manual profile from Task 1
- Produces: two project-owned immutable fixtures plus scenario `dbtl-reliability-run1-start`

- [ ] **Step 1: Create the governed and ordinary projects through the visible workspace UI**

Using the in-app browser, create projects named exactly:

```text
DBTL Reliability Maize
Ordinary Reliability Maize
```

Expected: both projects appear in the project sidebar and own separate human-visible folders under the manual-profile project root.

- [ ] **Step 2: Write the immutable training fixture to both projects**

Use this exact `train.csv` content in each project's `data/` directory:

```csv
marker_score,phenotype
0,1
1,3
2,5
3,7
4,9
5,11
6,13
7,15
8,17
9,19
10,21
11,23
```

- [ ] **Step 3: Write the immutable holdout fixture to both projects**

Use this exact `holdout.csv` content in each project's `data/` directory:

```csv
marker_score,phenotype
12,25
13,27
14,29
15,31
```

- [ ] **Step 4: Write the same experiment contract to both projects**

Use this exact `EXPERIMENT.md` content:

```markdown
# Synthetic maize marker calibration

Infer `phenotype = slope * marker_score + intercept` from `data/train.csv` and
verify it independently on `data/holdout.csv` using Python's standard library.

Required Build deliverables are `run_calibration.py`, `model.json`,
`predictions.csv`, `metrics.json`, and `RUNBOOK.md`. The recorded Test rerun
command must regenerate all machine-readable outputs from the immutable inputs
in a clean workspace.

Success requires slope `2`, intercept `1`, holdout MAE `0`, unchanged input
hashes, and byte-identical generated outputs after a second clean rerun. This is
a synthetic software-calibration check and supports no real breeding claim.
```

- [ ] **Step 5: Verify fixture equality and record hashes**

Run:

```bash
shasum -a 256 '.deer-flow/manual-dbtl/live/projects/DBTL Reliability Maize/EXPERIMENT.md' '.deer-flow/manual-dbtl/live/projects/DBTL Reliability Maize/data/train.csv' '.deer-flow/manual-dbtl/live/projects/DBTL Reliability Maize/data/holdout.csv'
cmp '.deer-flow/manual-dbtl/live/projects/DBTL Reliability Maize/EXPERIMENT.md' '.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/EXPERIMENT.md'
cmp '.deer-flow/manual-dbtl/live/projects/DBTL Reliability Maize/data/train.csv' '.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/data/train.csv'
cmp '.deer-flow/manual-dbtl/live/projects/DBTL Reliability Maize/data/holdout.csv' '.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/data/holdout.csv'
```

Expected: all `cmp` commands exit zero; the three governed hashes are recorded as campaign fixture hashes.

- [ ] **Step 6: Capture the pre-run baseline**

Run:

```bash
make dbtl-manual-capture SCENARIO=dbtl-reliability-run1-start
```

Expected: a new manifest exists and records the database and project-tree hashes before either conversation begins.

### Task 3: Run the ordinary-chat control

**Files:**
- Expected project outputs: `.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/run_calibration.py`
- Expected project outputs: `.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/model.json`
- Expected project outputs: `.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/predictions.csv`
- Expected project outputs: `.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/metrics.json`
- Expected project outputs: `.deer-flow/manual-dbtl/live/projects/Ordinary Reliability Maize/RUNBOOK.md`

**Interfaces:**
- Consumes: ordinary project fixture from Task 2
- Produces: ordinary thread/run identifiers, deterministic outputs, duration, tokens, and proof of zero governed records

- [ ] **Step 1: Verify the fresh ordinary project owns no cycle**

Run this read-only query before submitting the prompt:

```sql
SELECT COUNT(*) AS governed_cycles
FROM dbtl_cycles AS c
JOIN projects AS p ON p.id = c.project_id
WHERE p.name = 'Ordinary Reliability Maize';
```

Expected: `0`.

- [ ] **Step 2: Select GPT-5.6 Sol and submit the control prompt in a fresh ordinary conversation**

Select **GPT-5.6 Sol (Codex Subscription)** in the composer model menu before submitting.

Prompt:

```text
Complete EXPERIMENT.md now as ordinary chat, not as a DBTL cycle. Read the two immutable CSV inputs. Use Python's standard library only. Create run_calibration.py, model.json, predictions.csv, metrics.json, and RUNBOOK.md in the project root. Run the script twice from a clean output state, verify byte-identical generated outputs and unchanged input hashes, then report slope, intercept, holdout MAE, exact rerun command, and file hashes. Do not create governed DBTL evidence.
```

If discovery offers a cycle, click **Keep as ordinary chat**.

- [ ] **Step 3: Wait for native chat completion and record browser evidence**

Record the thread ID, run ID, start/end timestamps, displayed token usage, visible tool progress, and final assistant summary.

Expected: slope `2`, intercept `1`, MAE `0`, five deliverables, and no DBTL stage/deck controls.

- [ ] **Step 4: Verify ordinary outputs and clean rerun**

Run from the ordinary project root:

```bash
python3 run_calibration.py
shasum -a 256 run_calibration.py model.json predictions.csv metrics.json RUNBOOK.md data/train.csv data/holdout.csv
python3 run_calibration.py
shasum -a 256 run_calibration.py model.json predictions.csv metrics.json RUNBOOK.md data/train.csv data/holdout.csv
```

Expected: both hash sets are identical and `metrics.json` records slope `2`, intercept `1`, and MAE `0`.

- [ ] **Step 5: Verify the ordinary project still owns no governed cycle**

Use a read-only SQLite query against the active manual database:

```sql
SELECT c.id, c.state, c.originating_thread_id
FROM dbtl_cycles AS c
JOIN projects AS p ON p.id = c.project_id
WHERE p.name = 'Ordinary Reliability Maize';
```

Expected: zero rows.

### Task 4: Run the straight-through governed DBTL cycle

**Files:**
- Expected governed evidence under `.deer-flow/manual-dbtl/live/projects/DBTL Reliability Maize/outputs/`
- Expected DBTL rows in `dbtl_cycles`, `dbtl_stage_runs`, `dbtl_stage_worker_runs`, `dbtl_artifacts`, `dbtl_reviews`, and `dbtl_design_feedback_surfaces`

**Interfaces:**
- Consumes: governed project fixture from Task 2 and the frozen profile from Task 1
- Produces: one completed governed cycle with supported Test outcome and provisional-only Learn state

- [ ] **Step 1: Explicitly start a new cycle through the composer scope**

Select **GPT-5.6 Sol (Codex Subscription)** in the composer model menu before submitting.

Prompt:

```text
Start a new governed DBTL cycle for EXPERIMENT.md. Infer the deterministic synthetic maize calibration from data/train.csv and verify it on data/holdout.csv. Use Python's standard library only. Keep Design light and Build to exactly one small phase. Required deliverables are run_calibration.py, model.json, predictions.csv, metrics.json, and RUNBOOK.md plus a precise structured Test rerun specification. Success requires slope 2, intercept 1, holdout MAE 0, unchanged input hashes, and byte-identical outputs after a clean rerun. This is synthetic software calibration only and must not support a real breeding claim.
```

Expected: setup/discovery writes nothing before confirmation; clicking **Create this DBTL cycle** creates exactly one cycle.

- [ ] **Step 2: Complete the Design meeting and authenticated deck gate**

Choose a **Light** meeting with the frozen roster. Verify independent, red-team, and chair positions are visible before synthesis. If clarification is requested unexpectedly, classify run 1 as a failed route attempt rather than silently changing its scenario.

In the final Design deck:

1. Verify the five deliverables and clean-rerun contract.
2. Add the comment `Run 1: keep the calibration deterministic, synthetic-only, and limited to one Build phase.`
3. Submit Design for review.
4. Approve Design through the deck.

Expected: one approved Design surface and a visible, cycle-bound **Start Build / Hold here** handoff card.

- [ ] **Step 3: Start and approve the one-phase Build**

Click **Start Build**. If the planner proposes more than one phase, use the visible plan-change control with:

```text
Use exactly one small phase. Produce run_calibration.py, model.json, predictions.csv, metrics.json, and RUNBOOK.md. Run the entry point twice from clean output state, verify slope 2, intercept 1, MAE 0, unchanged input hashes, and byte-identical generated outputs. Return one precise structured rerun specification. Test owns independent verification.
```

Approve only after the Build deck shows all five promised deliverables, exact inputs and hashes, environment, command, outputs, limitations, and successful rerun evidence.

Expected: one approved Build surface and a visible, cycle-bound **Start Test / Hold here** card.

- [ ] **Step 4: Start Test and require a server-computed supported outcome**

Click **Start Test**. Verify Test independently stages the persisted contract in a clean workspace, runs the recorded command, audits all five deliverables, verifies unchanged inputs and byte-identical outputs, and returns typed metrics/checks.

Expected: the server computes `supported`; the Test deck offers only outcome-compatible routes. Select the route to Learn inside the authenticated Test deck.

- [ ] **Step 5: Start and approve Learn without promotion or publication**

Use the visible **Start Learn** control. Verify the Learn synthesis is restricted to the synthetic fixture, cites the supported Test evidence, and makes no real breeding claim.

In the Learn deck, comment:

```text
Retain only the bounded synthetic calibration lesson. Leave every candidate provisional; do not promote or publish.
```

Approve Learn through the deck.

Expected: the cycle reaches `completed`; any candidates remain proposed; promotions, publications, and project knowledge claims remain zero.

- [ ] **Step 6: Refresh and reopen the terminal evidence**

Hard-refresh the project conversation, expand the project cycle rail, and reopen each stage surface.

Expected: the completed state, approved Design/Build/Learn surfaces, supported Test outcome, comments, artifacts, and decisions remain visible and unchanged.

### Task 5: Capture evidence and evaluate feasibility

**Files:**
- Create: `.deer-flow/manual-dbtl/scenarios/dbtl-reliability-run1-final/manifest.json`
- Create: `.deer-flow/manual-dbtl/run-ledger/dbtl-reliability-run1.md`

**Interfaces:**
- Consumes: ordinary and governed results from Tasks 3 and 4
- Produces: scenario capture, attempt ledger, budget comparison, failure classification, and recommendation for runs 2–10

- [ ] **Step 1: Read the authoritative cycle, stage, worker, review, artifact, and knowledge rows**

Use read-only queries scoped to the newest cycle in the fresh governed project:

```sql
SELECT c.id, c.state, c.db_revision, c.originating_thread_id,
       c.created_at, c.updated_at
FROM dbtl_cycles AS c
JOIN projects AS p ON p.id = c.project_id
WHERE p.name = 'DBTL Reliability Maize'
ORDER BY c.created_at DESC
LIMIT 1;

SELECT stage, attempt_number, status, stage_spec_key, created_at, updated_at
FROM dbtl_stage_runs
WHERE cycle_id = (
  SELECT c.id FROM dbtl_cycles AS c
  JOIN projects AS p ON p.id = c.project_id
  WHERE p.name = 'DBTL Reliability Maize'
  ORDER BY c.created_at DESC LIMIT 1
)
ORDER BY created_at;

SELECT unit_id, capability, agent_name, via_generalist, status, stop_reason, created_at
FROM dbtl_stage_worker_runs
WHERE cycle_id = (
  SELECT c.id FROM dbtl_cycles AS c
  JOIN projects AS p ON p.id = c.project_id
  WHERE p.name = 'DBTL Reliability Maize'
  ORDER BY c.created_at DESC LIMIT 1
)
ORDER BY created_at;

SELECT COUNT(*) AS cycle_candidates
FROM memory_candidates
WHERE cycle_id = (
  SELECT c.id FROM dbtl_cycles AS c
  JOIN projects AS p ON p.id = c.project_id
  WHERE p.name = 'DBTL Reliability Maize'
  ORDER BY c.created_at DESC LIMIT 1
);

SELECT COUNT(*) AS cycle_promotions
FROM knowledge_promotions AS kp
JOIN memory_candidates AS mc ON mc.id = kp.memory_candidate_id
WHERE mc.cycle_id = (
  SELECT c.id FROM dbtl_cycles AS c
  JOIN projects AS p ON p.id = c.project_id
  WHERE p.name = 'DBTL Reliability Maize'
  ORDER BY c.created_at DESC LIMIT 1
);

SELECT COUNT(*) AS project_publications
FROM knowledge_publications AS pub
JOIN knowledge_claims AS claim ON claim.id = pub.claim_id
JOIN projects AS p ON p.id = claim.project_id
WHERE p.name = 'DBTL Reliability Maize';
```

Expected: one completed cycle, complete stage/worker provenance, only provisional candidates if any, and zero promotion/publication.

- [ ] **Step 2: Verify governed deliverables and the persisted rerun contract**

From the Build lineage and artifact records, identify the exact clean rerun command, declared inputs, expected outputs, and hashes. Execute the recorded command only in a disposable clean directory populated from the published package.

Expected: exit zero; all five deliverables are accounted for; machine-readable outputs reproduce; immutable input hashes match.

- [ ] **Step 3: Record token and elapsed-time totals**

Correlate browser-displayed usage with `run_events`, stage timestamps, and worker results. Record totals for supervisor/routing, Design, Build, Test, Learn, deck/review overhead, ordinary control, and full governed run.

Expected: the ledger distinguishes model runtime, worker runtime, and operator wait time.

- [ ] **Step 4: Verify craft-memory writes remain zero**

Run:

```bash
find .deer-flow/manual-dbtl -type f -name memory.json -print
```

Expected: no specialist craft-memory file or fact write attributable to the new cycle.

- [ ] **Step 5: Capture the final scenario**

Run:

```bash
make dbtl-manual-capture SCENARIO=dbtl-reliability-run1-final
```

Expected: final manifest records database and project hashes and the new resumable cycle URL.

- [x] **Step 6: Evaluate run 1 against the feasibility checkpoint**

The written evaluation must answer:

1. Did run 1 qualify as a clean product success?
2. Did the expected route equal the actual route?
3. Did it stay under 45 minutes and 350,000 model tokens?
4. Which stage dominated time and tokens?
5. Was first-pass Build-to-Test successful?
6. Did refresh preserve every control, surface, and decision?
7. Were failures product bugs, worker/package defects, scientific outcomes, or operator errors?
8. Can the remaining nine scenarios fit within the remaining campaign budget?
9. Which explicit, versioned changes to the campaign plan are recommended before run 2?

Expected: a go, modify, or stop recommendation grounded in captured evidence; no retroactive redefinition of success.

**Result:** Go with modifications. Run 1 completed as qualifying product success
1 of 10 at 308,813 governed tokens and 16 minutes 16 seconds wall time. Test
recorded `supported` on the first attempt and routed to Learn. The missing
Design-added notebook was detected as a non-gating worker/package defect; no
product fix or replay was required. The final checkpoint is
`.deer-flow/manual-dbtl/scenarios/dbtl-reliability-run1-final/manifest.json`.
The campaign design contains the versioned budget and protocol amendments. The
owner subsequently raised the hard campaign ceiling to 5.0 million tokens and
retired the ordinary-control arm after this one historical comparison.

### Task 6: Verify any observed product-fix batch before resuming

**Files:**
- Modify: only the smallest shared-boundary source files identified by root-cause analysis
- Test: focused regression file beside the owning backend or frontend boundary

**Interfaces:**
- Consumes: a reproducible product bug from Tasks 3–5
- Produces: focused red/green proof, affected-suite verification, OCR disposition, and a clean restored replay

This task runs only if run 1 encounters a product bug. The failed attempt remains evidence and cannot qualify.

- [ ] **Step 1: Preserve the failing browser and durable state**

Record URL, thread/cycle/run IDs, screenshot or semantic snapshot, active surface, evidence revision, deck hash, logs, and database projection without writing state.

- [ ] **Step 2: Prove the shared root cause before editing**

Use `superpowers:systematic-debugging` to trace the failing boundary, callers, state ownership, and nearest durable replay point.

- [ ] **Step 3: Write one focused regression and observe the intended failure**

Use `superpowers:test-driven-development`. Run the exact focused test and record the failure caused by the observed defect, not a setup error.

- [ ] **Step 4: Apply the smallest governance-preserving fix**

Use `ponytail:ponytail` at full intensity. Add no dependency, harness, alternate gate, or speculative abstraction.

- [ ] **Step 5: Run focused, adjacent, lint, formatting, and diff verification**

Run the owning focused test, affected suite, Ruff or ESLint/TypeScript as applicable, formatting check, and:

```bash
git diff --check
```

- [ ] **Step 6: Review the fix with OCR**

Run:

```bash
ocr llm test
ocr review --audience agent --background "DBTL reliability run 1; preserve authenticated deck gates, stage ownership, hash-bound evidence, and sandbox isolation" uncommitted
```

If the OCR endpoint is unavailable, use delegate preview/rules and perform the host review under the resolved rules.

- [ ] **Step 7: Restore and replay from the clean baseline**

When no stage worker or deck action is active, restore `dbtl-reliability-run1-start`, hard-refresh the browser, and repeat Tasks 3–5. Only the uninterrupted post-fix replay may qualify.
