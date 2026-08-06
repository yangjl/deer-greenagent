# Pilot Study — Self-Test of the DBTL Workflow vs. Ordinary Chat

**System:** GreenAgent (DeerFlow fork), `dbtl.mode = graph_enabled`, models pinned to `gpt-5.6-sol`
**Design:** single-subject (n=1) self-test, within-subject A/B on one matched task
**Primary focus:** UI/UX friction, and a decomposition of *why the Build worker is much slower than an ordinary chat turn*
**Entry point under test:** `http://localhost:2026` → chat composer, DBTL scope selector in the composer tool row

---

## 1. Why run this

DBTL (Design → Build → Test → Learn) is a heavyweight, multi-agent, human-gated workflow layered on top of the same runtime that serves ordinary chat. It promises legibility and rigor (council design meetings, hash-bound build phases, separated Test validity, human-owned Learn promotion) at the cost of latency and interaction complexity. Ordinary chat is a single conversational agent turn: fast, familiar, but unstructured and unaudited.

The open questions this pilot answers:

- **RQ1 (UX).** Where does the DBTL flow cost the operator attention, clicks, or confidence that chat does not — from discovering how to start, through each stage gate, to reading the final evidence?
- **RQ2 (Latency).** Build routinely takes far longer than a chat answer to the "same" request. *How much* longer, and *where* does the time go across the five Build steps (`load_design → plan_build → execute_phases → summarize_results → render_review_deck`)?
- **RQ3 (Value trade).** For a small, well-scoped task, does the structure DBTL adds pay for its latency and interaction cost, or does it feel like ceremony?

This is a pilot: the goal is to surface the top handful of high-severity issues and produce reliable latency numbers on **one** representative task, not to reach statistical significance.

---

## 2. The matched task

One task, small enough to finish a full D→B→T→L cycle on `gpt-5.6-sol` at modest cost, but real enough that Build must actually *write and run code in the sandbox* (so the latency measurement is representative, not a toy).

> **Task T:** "From a small synthetic dataset of `(dose, response)` pairs, estimate the dose–response relationship. Fit a model, report the key parameter(s) with an uncertainty estimate and goodness-of-fit, and produce one diagnostic figure. State one caveat about the estimate's validity."

Properties that make T a good probe:

- **Buildable & executable** — a short Python script (numpy/scipy/matplotlib) that fits a curve and writes a figure. Exercises the sandbox path, entry-point execution, and the phase manifest verification that dominates real Build time.
- **Testable** — a natural validity question (hold-out / repeat-run reproducibility) that the Test stage separates from the headline fit.
- **Learnable** — a promotable finding ("slope ≈ X under these conditions") that Learn can turn into a provisional candidate.
- **Chat-doable** — the identical request can be handed to ordinary chat, so the two conditions are genuinely comparable.
- **Deterministic-ish** — with a fixed random seed the answer is stable, so re-runs are comparable and cost is bounded.

A fixed input dataset (same bytes, same seed) is used for **both** conditions so the comparison is apples-to-apples. The dataset is generated once and saved into the project folder before the run.

---

## 3. Conditions

| | Condition A — Ordinary chat | Condition B — DBTL cycle |
|---|---|---|
| Scope selector | Chat (default) | DBTL cycle scope |
| Orchestration | One `lead_agent` turn (may call tools) | Supervisor graph: Design council → Build workflow → Test → Learn |
| Human gates | none | Design review, Build approval, Test review, Learn promotion |
| Expected output | inline answer + maybe an artifact | approved design deck, hash-bound build evidence, Test validity verdict, Learn candidate |

Run **A first, then B** (chat establishes the baseline expectation and the "obvious" answer; then we see what structure B adds). Both on `gpt-5.6-sol`.

---

## 4. What we measure

### 4.1 Quantitative — latency & cost (the RQ2 core)

Wall-clock, captured per condition and, for B, **per stage and per Build step**:

- **TTFT** — time from submit to first streamed token/visible activity.
- **TTFA** — time to first durable artifact (chat: first file/answer block; DBTL: first approved design deck).
- **Total wall-clock to a usable result** — chat: answer complete; DBTL: Learn candidate recorded (and, separately, time to *end of Build* since that's the RQ2 subject).
- **Build-step decomposition** — elapsed for each of `load_design`, `plan_build`, `execute_phases` (and per phase within it), `summarize_results`, `render_review_deck`. This is the money metric: it tells us whether Build is slow because of (a) many sequential model calls, (b) sandbox setup/exec, (c) manifest/verification/publish overhead, (d) correction-loop retries, or (e) deck rendering.
- **Model-call count & token cost** — number of distinct model invocations and input/output tokens per condition (chat ≈ 1 turn; Build ≈ `2 + N_phases` worker calls minimum). Cost is the honest denominator for "is the structure worth it".
- **Gate latency (human-in-the-loop idle)** — measured but reported separately from compute latency, since operator think-time is not the system's fault.

### 4.2 Qualitative — UX (the RQ1 core)

Scored against a compact Nielsen-heuristic checklist, one severity rating per issue (§6):

- **Discoverability** — could the operator find how to start a DBTL cycle vs. a chat without prior knowledge? Is the scope selector legible?
- **System status visibility** — during long Build waits, does the UI show *what* is running now (current phase), progress, and an ETA or at least motion? Or does it look hung?
- **Legibility of structure** — are the four stages and their gates understandable? Does the operator know what each gate is asking them to approve?
- **Control & recovery** — can the operator pause, redirect, retry, replan, or abandon? Are destructive actions guarded? Does "Waiting for you" clearly say what it wants?
- **Match to expectation** — does what happens match what the buttons/labels promised? (e.g., "Build work" not mislabeled as "Design meeting"; a capped phase not shown as a failure.)
- **Evidence readability** — is the final review deck / Test verdict / Learn candidate something the operator can actually read and trust, or a wall of JSON?
- **Effort** — count of clicks, context-switches, and reads-required to move the cycle one stage forward.

### 4.3 Instrumentation — where the numbers come from

1. **Browser network panel** (`read_network_requests`) — submit→first-byte and SSE stream timings for `/api/langgraph/*` and `/api/*`; client-observed TTFT/TTFA.
2. **Gateway log** (`logs/gateway.log`) — server-side stage/step boundaries and timestamps.
3. **run_events / step-runs** — the backend records per-stage token usage and step runs (`run_events.track_token_usage=true`, `dbtl_stage_step_runs`); these give authoritative per-step start/end + tokens. Query the sqlite DB (`backend/.deer-flow/deerflow.db`) after the run.
4. **Wall-clock stopwatch** on the browser side as a sanity cross-check.
5. **Screenshots + one GIF** of the DBTL run for the qualitative record and the report.

All timings are cross-checked between client (network) and server (log/DB); the report notes any divergence rather than trusting one source.

---

## 5. Procedure

**Setup (once).**
1. Stack running at `:2026` (Gateway `:8001`, Frontend `:3000`, Nginx `:2026`).
2. Generate the fixed `dose_response.csv` (seeded) into the project folder.
3. Confirm `dbtl.mode=graph_enabled` and the run model is `gpt-5.6-sol`.
4. Open browser dev instrumentation; clear console + network.

**Condition A — chat.**
1. In the composer (scope = Chat), submit Task T verbatim, pointing at the dataset.
2. Start stopwatch on submit; record TTFT, TTFA, completion.
3. Save the answer + any artifact. Note UX observations against §4.2.

**Condition B — DBTL.**
1. In the composer tool row, switch the scope selector to a new DBTL cycle. Record how discoverable this was.
2. Submit Task T verbatim. Proceed through **Design** (council meeting → design deck → review/approve), noting each gate's clarity and latency.
3. On Design approval, observe **Build**: capture the per-step decomposition (§4.1) — this is the primary latency measurement. Note the "Waiting for you" and control affordances (Retry/Replan/Restart/Hold) if they appear.
4. Approve Build → **Test**: record the validity verdict and whether headline vs. validity are clearly separated.
5. **Learn**: record the provisional candidate and the human promotion step.
6. Throughout: record stage timings, screenshots, and one GIF spanning Design→Build.

**Teardown.** Export DB step timings; reconcile with client timings; leave the stack running or stop per operator preference.

---

## 6. UX issue severity rubric

Each finding gets one rating (Nielsen 0–4, adapted):

- **4 — Blocker:** operator cannot complete the stage, or is misled into a wrong action.
- **3 — Major:** completes but with significant confusion, wasted effort, or lost trust; would frustrate a real user.
- **2 — Minor:** noticeable friction, easy to recover from.
- **1 — Cosmetic:** polish; does not impede.
- **0 — Not an issue / working as intended.**

Each finding records: where (stage + component), what happened, expected vs. actual, severity, and a proposed minimal fix.

---

## 7. Build-worker latency — the specific hypotheses to confirm/refute

From the Build workflow's structure, the a-priori latency model is:

```
T_build ≈ T_load_design (deterministic, ~0)
        + T_plan          (1 model worker)
        + Σ_phases ( T_phase_model  +  T_sandbox_setup  +  T_entry_point_exec
                     +  T_publish+hash+manifest_verify  [+ T_correction if a check fails] )
        + T_summarize      (1 model worker)
        + T_render_deck    (deterministic projection, ~0)
```

Chat, by contrast, is ≈ `T_one_model_turn`. So the expected explanation is **structural**: Build makes at least `2 + N_phases` sequential model calls plus sandbox and verification overhead, where chat makes ~1. The pilot's job is to **attribute** the total to these buckets and identify which are *removable overhead* (e.g., redundant re-reads, oversized prompts, avoidable correction retries, sandbox spin-up per phase, model warm-up) vs. *irreducible structure* (the sequential phases themselves).

Concrete things to watch that would be *fixable* latency rather than inherent cost:
- Per-phase sandbox/container setup repeated when it could be reused.
- Planner or phase prompts carrying the full Design when the objective already summarizes it (the code claims it shouldn't, but verify).
- Correction-loop retries triggered by avoidable manifest/contract rejections.
- Summarizer re-reading large evidence it could receive as a digest.
- Deck render or DB writes blocking the event loop (there's a `detect-blocking-io` tool in-repo — a signal this has bitten before).

---

## 8. Analysis & deliverables

- **UX findings table** — ranked by severity, with proposed minimal fixes.
- **Latency table** — chat vs. DBTL total, plus the Build-step decomposition with the fixable/irreducible split.
- **Verdict on RQ3** — for a small task, is the structure worth the cost, and what would move that trade (e.g., a "fast Build" path, better wait-state UI, phase parallelism where safe).
- **Fixes applied** — the clear UI/UX and safe latency wins landed during the pilot (minimal diffs), with anything larger flagged.

## 9. Limitations

n=1, single task, single model, self-test by the system's own developer (expert bias — a naïve user would likely find *more* discoverability issues, so UX findings here are a lower bound). Latency numbers are one sample per step; where a step is retried or variable, the report says so rather than implying precision. The point is direction and magnitude, not significance.
