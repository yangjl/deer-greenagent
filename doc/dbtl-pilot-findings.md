# Pilot Findings — DBTL Workflow vs. Ordinary Chat

**Run date:** 2026-08-06 · **Project:** test3 · **Model:** GPT-5.6 Sol (Codex subscription) · **Task:** seed-42 dose–response simulation + OLS fit + PNG + validity caveat
**Method:** single-subject A/B self-test per the pilot protocol. Condition A = ordinary chat; Condition B = a full DBTL cycle driven live through the running app at `localhost:2026`. Latency and status come from three cross-checked sources: the browser (client wall-clock + the app's own REST/workflow endpoints), the gateway logs, and the `dbtl_stage_*` tables in the live SQLite DB. Historical figures come from the 29 pre-existing cycles in the same DB.

---

## 0. Update — full DBTL cycle now completes end-to-end ✅

On a rerun after the fixes below (plus another engineer's `keep build and discovery sandboxed` commit), a fresh DBTL cycle ran **Design → Build → Test → Learn to completion**, every gate approved, `state = completed`. All four stage artifacts were produced (`design_brief`, `build_package`, `validity_report`, `learn_summary`), Build lineage recorded, and the Build phase **succeeded on its first attempt** — versus four straight failures on the immediately prior cycle before the contract was hardened. Build result: 60-row seed-42 dataset, OLS **slope 2.051, 95% CI [1.877, 2.225]**, executed replay notebook + passing validator, reproducible CSV hash. Test recorded the validity assessment (correctly flagging that a simulation cannot establish real-world predictive/external/causal validity); Learn synthesized the candidate.

**Is a specialist doing the phase work?** No — every phase attempt ran on `general-purpose` (`via_generalist=1`); no specialist is registered for the phase capability, so it always falls back to the stand-in. The reliability came from *hardening the shared contract*, which lifts the generalist rather than needing a specialist.

**Additional fixes this rerun (committed):**
- `build_execution.parse_rerun_spec` — `inputs` made optional: a seed-based simulation has no runtime inputs, so the server-built rerun record had empty inputs and was rejected as "invalid structured rerun record," blocking finalization. (Fix `5dcfd4e4`.)
- `build_phases` phase contract — added rules for the exact repeated stand-in mistakes: record versions via `importlib.metadata.version()` (never `pkg.__version__`; the jupyter meta-package has none and raised `AttributeError`), run the build with the one provisioned interpreter (never build a venv — a fresh one lacks pandas), and return exactly one JSON object as the structured result. (Fix `5dcfd4e4`.)
- Earlier committed fixes still in force: durable `dbtl-build` package extra, notebook-Done non-gating, `BUILD_SPEC_V12` budget 120K→500K, generative-build lineage.

One known rough edge remains: a **gateway restart during a build orphans the in-flight run** (its conversational control cards go inert), so loading a code fix mid-build strands that cycle — the completed run avoided any mid-build restart. Worth a resume path, but not a blocker.

---

## 1. Headline answers

**RQ2 — why is Build so much slower than a chat turn?** A chat answer is *one* model turn; Build is a pipeline of sequential model-backed steps whose dominant one — the phase worker — is itself a full multi-turn sandbox agent. Measured on the identical task:

| | Ordinary chat (A) | DBTL Build stage (B) |
|---|---|---|
| Structure | ~1 turn (with sandbox tool use) | `load_design` → `plan_build` → N sequential phase workers → `summarize` → `render_deck` |
| Wall-clock | **85 s** (1m 25s) | `load_design` **0.006 s** · `plan_build` **16.9 s** · each phase attempt **~170–190 s** · summarize/deck pending a green phase |
| Tokens | 64.9K (64.0K in / 853 out) | design meeting 16.4K (3 workers); phase worker ~12K median historical, but the real task needed **>120K** |

`load_design` and `render_review_deck` are deterministic and effectively free. The cost is entirely the model-backed steps, and the phase worker dominates. Phases run strictly sequentially, so Build scales with phase count.

- *Irreducible:* the sequential phase workers themselves — a governed, hash-bound, reviewable build is inherently many model calls.
- *Fixable (the dominant real-world cost):* **workers that spend their full runtime and then produce nothing usable.** Historically 11/60 workers hit a cap and 8 failed the output contract. This pilot reproduced that live and traced it to concrete, fixable causes (§3) — every occurrence is ~3 min + thousands of `gpt-5.6-sol` tokens for a failed step plus a forced retry.
- *Fixable (smaller):* reasoning-enabled `gpt-5.6-sol` on every unassigned seat is expensive (`dbtl.council_model_name`/per-seat dials exist to make routine seats cheaper and were left at the expensive default); and the Build-plan view polls `/stages/build/workflow` on a tight loop.

**RQ1 — UX.** Legible once running (per-participant meeting status, cost-labeled recovery options, a pre-filled setup wizard), but the **gates are hard to find and hard to act on**, and the classifier interposes a decision on ordinary requests. §2.

**RQ3 — worth it for a small task?** No — for an 85-second chat answer, the ceremony (classifier card → 4-question wizard → meeting-depth choice → meeting → find-the-approval → build gate → recovery) is disproportionate. The structure earns its cost on consequential work where the audit trail, separated Test validity, and human promotion gate matter. A "fast path" for small/low-stakes cycles would close most of the gap.

---

## 2. UX findings (ranked by severity)

**[Major] "Keep as ordinary chat" abandons the request.** A research-style prompt triggers a "Review DBTL cycle setup" card. Choosing *Keep as ordinary chat* records "No DBTL cycle was created" and **stops — the original question is never answered.** The user must re-type it. *Fix:* on `keep_ordinary`, forward the original message to the lead agent.

**[Major] The stage-approval gate is hard to discover and unreachable except by mouse.** Approving Design is not an inline button. The rail's "Open feedback deck" opens a **read-only** panel that points to "the originating conversation"; the conversation points back to "the stage in the project rail"; the actual control is a **"Human gate slide" inside an opaque-origin iframe** with *no* app-level fallback control. It can't be keyboard/AT/automation-driven (the deck→parent bridge uses a private per-mount channel). The app even auto-suggested "How do I approve the Design gate?" — a tell. *(I ultimately recorded the approval through the app's own authenticated `stage-feedback/.../actions` endpoint.)* *Fix:* render an app-level Approve/Request-changes/Park control beside the deck.

**[Major] Builds fail on environment/budget limits the worker can't fix (see §3).** User-facing result is repeated "Failed" on work that was actually correct.

**[Minor] Misleading transient error during successful cycle creation** ("No new cycle was recorded…" flashed while `cycle-f52f…` was in fact being created). **[Minor] Cross-cycle rail confusion** (stage controls acted on the previously-selected cycle). **[Minor] Long waits show a timer but no streamed progress** (chat 85 s and Design synthesis >1 min showed only "Working… (Ns)"; Build, by contrast, streams the current phase well).

**Positives worth keeping.** The 4-question Design **setup wizard** with pre-filled suggestions; the **meeting-depth chooser** (Light/Medium/Heavy, each cost-labeled, "token use recorded, not capped"); the **Build recovery card** (Retry/Replan/Restart/Hold, each stating exactly what it reuses and costs); and per-participant meeting status ("2 of 3 reported"). These are genuinely good.

---

## 3. Why Build kept failing — a cascade of four distinct, fixable causes

The identical seed-42 task was Design-approved cleanly, then Build failed **six times**, and each failure exposed a *different*, real defect. Notably the science kept being correct — the failures were the platform around it. In order:

1. **Missing `jupyter` → a complete build discarded on a Done technicality.** The worker produced the simulation, OLS, PNG, a passing validator, and a byte-reproducible CSV, then self-reported `failed` because *"the bound environment lacks jupyter"* so it couldn't execute the notebook it made — even though the workflow's own contract says a notebook "must never be the entry_point." ~2m50s of correct work, rejected.
   **Fix applied:** installed `jupyter`/`nbconvert`/`ipykernel`; and a durable code change in `build_phases.py` so an optional notebook that can't be *executed* for lack of a runtime tool is a recorded limitation, not a phase failure (extended `is_non_gating_build_check` + the worker prompt's exception). *52 build tests pass.*

2. **Server verification runs the entry point with a bare interpreter that lacks the worker's packages.** `build_phase_verification.entry_command` runs a `.py` entry point as `python <script>`. The worker installs its dependencies in its own venv, but the server's `python` (the gateway's `backend/.venv`) had no `scipy` → `ImportError` → exit 1. This is systematic: **any** build needing a third-party package fails server verification. It is the mechanism behind much of the historical failure rate.
   **Fix applied:** provisioned `numpy/scipy/matplotlib/pandas` into `backend/.venv` (the verification interpreter). *Durable code fix recommended below.*

3. **The worker cannot self-provision.** The next attempt reached for `pandas`+`statsmodels`; the bound env lacked them and *"installing them was denied by the workspace write policy,"* so it couldn't proceed. Confirms the build sandbox is effectively read-only for installs — pre-provisioning is the only path.
   **Fix applied:** provisioned `statsmodels/scikit-learn/seaborn` too.

4. **The token budget is too small to finish.** With packages present, the science ran clean — **slope 1.541, 95% CI [1.402, 1.680], R² ≈ 0.8, CSV hash reproduced across two runs** — but the worker hit **`token_capped`** before emitting its structured result, so the whole build was discarded. Cause: `BUILD_SPEC_V12`'s worker budget is **120K tokens**, *down* from V11's 500K, even though V12 does strictly more (entry-point execution, granted-paths, narrow-inputs gates). This is the live reproduction of the historical cap-and-fail.
   **Fix applied:** raised `BUILD_SPEC_V12` `max_tokens` 120K → 500K (matches V11 and the Test stage), with a comment explaining why. *Parses; gateway restarted to load it.*

5. **A hardcoded path trips the `granted_paths_only` gate.** With the budget raised, the next attempt ran without capping — but its generated `build_dose_response.py` named an absolute path (`/src/build_dose_response.py`, line 104) outside the phase's granted workspace, which the path-grant gate correctly refused. This is a worker code-quality slip against a strict (and correct) security gate — *not* something to loosen.

6. **The tighter contract fixed the phase — then a lineage gate blocked finalization.** After adding an example-driven worked block to the phase contract (correct path discipline: build every path from `DBTL_WORKSPACE`/`DBTL_INPUT_n`, never an absolute literal; the server re-runs with the now-provisioned bare interpreter), the **Build phase succeeded end-to-end** on the next attempt: execute → summarize → review package (`build-review-rev10`), with correct results — **slope 1.5409, 95% CI [1.4018, 1.6800], R² = 0.8945**, CSV hash reproduced. This confirms the fix the exercise was after. Finalization then failed in the `cycle_continuation` task: `record_build_lineage` raises *"Build lineage requires at least one input file examined during Build."* The task is a pure seed-based **simulation with no external input dataset**, so lineage has zero examined inputs. This is a deliberate provenance guardrail that assumes every Build consumes data; **generative/simulation builds aren't modeled**, so they cannot finalize (and thus cannot open Test/Learn) even when fully reproducible via their rerun spec + hashed outputs.

**Unified root cause.** The v12 Build path is (a) under-provisioned — the sandbox lacks the scientific stack and workers cannot self-provision — and (b) a gauntlet of strict gates (granted paths, server-executed entry point, narrow inputs, notebook rules, token budget) that a general-purpose worker on `gpt-5.6-sol` trips in a *different* way almost every attempt. Six consecutive Build attempts failed for **five distinct reasons**, and the science itself was correct throughout (attempt 5 produced slope 1.541, CI [1.402, 1.680], R² ≈ 0.8). The four fixes cleared the *environmental* causes (packages, jupyter, token budget, the notebook Done-check); the remaining failures are worker-vs-gate code quality, which a generalist stand-in does not clear reliably. **Test and Learn were therefore not reached** — and that, not a clean pass, is the honest headline: on a trivial analysis, the DBTL Build stage could not produce an approvable evidence package in six tries. This is the strongest possible confirmation of the historical cap-and-fail signal, and it points at two levers: provision + right-size the Build environment (done here for the environmental half), and give phase work a specialist (or a much tighter, example-driven contract) rather than a general-purpose stand-in.

---

## 4. Fixes applied this session

Code (minimal diffs, on the running checkout; all touched suites pass — 66 build-phase + 130 lineage/reconciliation/test):
- `agents/dbtl/live_stage/build_phases.py` — (a) an un-executable optional notebook is a limitation, not a phase failure (`is_non_gating_build_check` + worker-prompt exception); (b) an **example-driven worked block** in the phase contract — build every path from `DBTL_WORKSPACE`/`DBTL_INPUT_n` (never an absolute literal), create the entry point inside the workspace, rely on the server's provisioned bare interpreter (no runtime pip, no self-built venv). **This is the change that made the Build phase pass.**
- `dbtl/stage_spec.py` — `BUILD_SPEC_V12.budget.max_tokens` 120_000 → 500_000, with rationale comment.
- `persistence/dbtl/build_test_ops.py` — `record_build_lineage` accepts a **generative build** (zero external inputs) *only* when a reproducible rerun record is present; an empirical build with neither inputs nor a rerun record is still refused. Test updated to assert the new rule.

Dependency declaration (durable — the packages are now part of the workflow, not a loose venv install):
- Added a `dbtl-build` optional-dependency extra (numpy, scipy, pandas, matplotlib, statsmodels, scikit-learn, seaborn, jupyter, nbconvert, ipykernel) on the harness, exposed at the backend root, and **auto-enabled by `detect_uv_extras` whenever `dbtl.mode` runs Builds** (manual or graph_enabled). `serve.sh`'s `uv sync --all-packages` now installs it and never prunes it. Verified: the stack still imports after the exact sync `serve.sh` runs; `uv.lock` refreshed and `uv lock --check` clean; detector unit tests added.

**Live outcome after the fixes.** The Build **phase** ran clean and produced a correct, reproducible result (slope 1.5409, 95% CI [1.4018, 1.6800], R² 0.8945) and a review package. Reaching **Test/Learn live** was then blocked by an operational artifact, not a product defect: loading the two later code fixes required a gateway restart, which orphaned the in-flight Build run (its conversational control cards were bound to the killed run). With all fixes now resident, a **fresh** cycle run in one continuous pass would carry Build → Test → Learn; the current cycle's Build is parked, its evidence intact.

## 5. Recommendations (durable, beyond this session's stopgaps)

1. **Provision the Build sandbox properly.** *Package half now done* (the `dbtl-build` extra above). Remaining, and cleaner still: run the server verification via the workspace venv when one exists (fix `entry_command`/`verification_shell_command`) so verification matches how the worker actually ran the code, instead of depending on the gateway interpreter. That removes cause #2 at the root and lets the extra be trimmed later.
2. **Right-size Build worker budgets** (done here for v12) and reconcile the V11→V12 regression; consider budget scaling with declared phase complexity.
3. **Fix the classifier dead-end** — keep-as-chat should answer the question.
4. **Give every human gate an app-level control** — don't require reaching into the deck iframe.
5. **Add a "fast cycle" path** for small/low-stakes cycles so a pilot isn't six gates deep.
6. **Default routine council seats to a cheaper model**; **throttle the Build-plan poll**; **stream progress** on chat + meeting synthesis so >60 s waits don't read as hangs.

## 6. Limitations

n=1, one small task, one model, self-test by tooling rather than a naïve user (UX findings are a lower bound). Latency is one sample per step; retried/failed steps are labeled, not averaged. The Build cascade is unusually informative precisely because the task was simple: on a trivial analysis, four separate platform defects still had to be cleared before the science could be recorded.
