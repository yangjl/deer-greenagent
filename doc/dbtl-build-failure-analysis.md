# DBTL Build — Failure History, Contracts, Patterns, and the Path Forward

*A decision document. Everything here is from the live runs this session, cross-checked against the gateway logs and the `dbtl_stage_*` tables. Companion to `doc/dbtl-pilot-findings.md`.*

---

## 1. Reliability at a glance

Across every DBTL cycle run this session:

- **14 Build phase attempts → 11 failed, 3 succeeded (~21% per-attempt success).** Every failure is `execution_contract_rejected`.
- **5 cycles reached Build; 3 eventually got a passing phase; 1 completed the whole Design→Build→Test→Learn cycle.**
- Worker stop reasons on the phase: 8 plain failures, 2 `token_capped`, 2 clean completions.

The single completed cycle got there only after **eleven** distinct fixes. The phase now *can* pass first-try (the completed cycle did), but the per-attempt rate is still dominated by a general-purpose worker improvising against a strict contract. That is the crux of the "way forward" decision.

---

## 2. Complete failure → fix ledger (what we tried, what worked, what didn't)

Each row is a *distinct root cause* observed live, in the order we hit them.

| # | Failure (as reported) | Root cause | Fix tried | Result |
|---|---|---|---|---|
| 1 | Complete build marked **Failed** — "lacks jupyter, notebook not executed → Done condition not met" | Sandbox had no `jupyter`; worker treated an un-runnable notebook as a Done failure | (a) install jupyter/nbconvert/ipykernel; (b) make an un-executable optional notebook a *limitation*, not a phase failure (`is_non_gating_build_check` + prompt) | ✅ Worked — removed this mode |
| 2 | "server executed the declared Build entry point and it **exited with status 1**" | Server verification runs a `.py` entry point with the **bare gateway `python`**, which lacked scipy/pandas → ImportError | Provision numpy/scipy/matplotlib/pandas into the gateway venv | ✅ Worked |
| 3 | Worker: "lacks pandas + statsmodels; **installing them was denied by the workspace write policy**" | Build sandbox is read-only for installs; the worker cannot self-provision | Provision statsmodels/scikit-learn/seaborn too | ✅ Worked |
| 3b | A normal `make dev` runs `uv sync`, which **prunes** manually-installed packages | The stack was never *declared* as a dependency | Declare a `dbtl-build` optional extra + auto-enable it in `detect_uv_extras` whenever `dbtl.mode` runs builds | ✅ Worked (verified the stack survives the real `uv sync`) |
| 4 | **`token_capped`** *after* the science succeeded; whole build discarded | `BUILD_SPEC_V12` worker budget was cut to **120K** (down from v11's 500K) while doing *more* | Raise `max_tokens` 120K → 500K | ✅ Worked |
| 5 | Phase refused — generated source names an absolute path `/src/...` (the `granted_paths_only` gate) | Worker hardcoded an absolute path in its code | Tighten the phase contract: worked example, **workspace-relative paths only**, entry point created inside the workspace | ✅ Worked — phase then reached the review package |
| 6 | `record_build_lineage`: **"requires at least one input file examined"** | Lineage required ≥1 input; a seed simulation consumes none | Allow zero inputs **when a reproducible rerun record exists** (generative build); empirical builds still require inputs | ✅ Worked (unit-tested) |
| 7 | `record_build_lineage`: **"invalid structured rerun record"** | `parse_rerun_spec` required a non-empty `inputs`; a simulation's rerun record has empty inputs | Make `inputs` optional in `parse_rerun_spec` | ✅ Worked — lineage recorded on the completed cycle |
| 8 | Phase failed while **recording `jupyter.__version__`** (AttributeError) | The `jupyter` meta-package has **no `__version__`** | Contract rule: read versions via `importlib.metadata.version()`, never `pkg.__version__`; wrap in try/except → `'unknown'` | ✅ Worked |
| 9 | "structured result is **not valid JSON: Extra data**" | Worker printed extra text / a second object after the JSON | Contract rule: return **exactly one JSON object**, nothing before/after | ✅ Worked |
| 10 | Worker built **its own venv** where pandas was missing, then fell back | Worker created a venv instead of using the provisioned interpreter | Contract rule: use the **one interpreter already on PATH**; never `python -m venv`/pip | ✅ Worked |

### What did *not* work (equally important)

- **Blind retries.** 11 failed attempts, most a *different* failure each time. Retrying without changing the environment or contract does not converge — for env gaps it repeats the same wall, and for worker-quality it rolls a new mistake.
- **Restarting the gateway mid-build to load a fix.** This **orphans the in-flight run**: its conversational control cards (Retry / Start Build) go inert because they were bound to the killed run, stranding that cycle. It happened ~3×. The completed cycle only worked because *all* fixes were pre-loaded and no restart occurred during its build. → Load every fix first, then run; or add a build-resume path.
- **Loosening the strict gates (deliberately not done).** `granted_paths_only`, `server_executed_entry_point`, and the exit-status check are *correct* security/reproducibility gates — loosening them would let genuinely broken builds pass. The only "loosening" we did was the **generative-build** allowances (#6, #7), which are correct: a seed simulation legitimately has no inputs.

**Tally:** every *systematic* cause (environment, budget, generative-build assumptions, the specific recurring worker mistakes) is now fixed and committed. What remains is **non-systematic worker-output variance** (see §4/§5).

---

## 3. The Build contracts (what a phase must satisfy)

Three layers of contract sit under every Build. Understanding them explains why the generalist trips.

### 3a. Stage spec — `BUILD_SPEC_V12`
- **Runs as a versioned 5-step workflow:** `load_design` (deterministic) → `plan_build` (worker) → `execute_phases` (N sequential phase workers) → `summarize_results` (worker) → `render_review_deck` (renderer). A digest chain invalidates only descendants on change.
- **Budget:** `max_workers=3, max_turns=450, max_tokens=500K` (was 120K), `timeout=900s`, token limit enforced.
- **Required capabilities:** `SOFTWARE_ENGINEERING` (optional: `STATISTICAL_ANALYSIS`, `QUANTITATIVE_GENETICS`, `FIELD_TRIAL_QC`).
- **Validity gates (all enforced):** `server_bound_input_lineage`, `versioned_derived_outputs`, `structured_rerun_spec`, `server_verified_phase_manifest`, `phase_declared_skills`, `narrow_implementation_inputs`, `granted_paths_only`, `server_executed_entry_point`.

### 3b. Phase worker contract (what each phase must produce)
- **Path discipline:** every path built from `DBTL_WORKSPACE` / `DBTL_INPUT_n`; **no absolute literals**; entry point created *inside* the workspace. (Gate: `granted_paths_only`.)
- **Executable entry point:** a runnable script (`.py`/`.sh`/`.R`/…), never a notebook; the **server re-executes it** with the provisioned interpreter and it must exit 0. (Gate: `server_executed_entry_point`.)
- **Phase manifest (v3):** `entry_point`, `declared_outputs` (== published files exactly), `declared_inputs`, `execution_inputs ⊆ declared_inputs`, `completion_condition` copied verbatim. (Gate: `server_verified_phase_manifest`, `narrow_implementation_inputs`.)
- **Exactly one `phase_done_condition` quality check**, true only when done + outputs exist.
- **Structured result** (a single JSON object): status, summary, `artifact_refs`, `claims`, `limitations`, `provenance` (incl. `phase_manifest`, `rerun_spec`), `key_outcomes`, `figures`, quality checks.

### 3c. Lineage / rerun contract (`record_build_lineage` + `parse_rerun_spec`)
- Requires: non-empty `environment`, ≥1 versioned `output_artifact` (URI + sha256 + revision), and either ≥1 examined `input_artifact` **or** a valid rerun record.
- Rerun record must parse: `entry_point`, `command`, non-empty `environment`, non-empty `expected_outputs`; `inputs` **now optional** (generative builds). (Gate: `structured_rerun_spec`, `server_bound_input_lineage`.)

The contract is *deliberately* strict — it is what makes a Build auditable, reproducible, and safe to promote. It is not the problem. The problem is asking a **generalist** to satisfy all of it, first try, by inference.

---

## 4. Failure patterns (the taxonomy)

Every failure this session falls into one of five buckets:

1. **Environment / provisioning** (#1–3b) — the sandbox/verification interpreter lacked what generated code needs, and the worker can't self-install. → *Systematic; now fixed durably* (the `dbtl-build` extra).
2. **Budget** (#4) — token cap too small for the work. → *Systematic; fixed* (500K).
3. **Generative-build assumptions** (#6, #7) — lineage/rerun code assumed every build consumes input data. → *Systematic; fixed* (inputs optional with a rerun record).
4. **Contract-mismatch / worker-output quality** (#5, #8, #9, #10) — the generalist improvises and violates a gate a *different* way each run: absolute path, `jupyter.__version__`, extra JSON, self-built venv. → *Partially* addressed by hardening the shared contract (raises the rate), but **inherently variance-prone** because it depends on one-shot LLM output against a large rule surface.
5. **Operational** (mid-build restart orphans runs) — tooling/lifecycle, not the build itself. → *Not fixed*; needs a resume path.

Buckets 1–3 are **closed**. Bucket 5 is a known rough edge. **Bucket 4 is the residual reliability risk** — and it is the one a specialist targets.

---

## 5. Why a specialist resolves the residual risk

The residual failures (bucket 4) are not bugs in the platform — they are a **general-purpose agent inferring a specialised, high-constraint contract from scratch on every run.** The `assign_phase` machinery already expects this: it selects a *specialist* by capability and only falls back to `general-purpose` as a recorded **stand-in** when none is registered. This session's phases *all* ran on the stand-in (`via_generalist=1`). A registered specialist helps for concrete, structural reasons:

1. **The contract lives in the agent, not in a wall of per-run prompt text.** A specialist's system prompt *is* the DBTL build discipline — workspace-relative paths, `importlib.metadata` versioning, one-JSON-object output, provisioned interpreter, v3 manifest shape. The generalist must re-derive all of that from the (now-hardened, and necessarily long) phase prompt every time; one-shot inference over a big rule set is exactly what produces the "different mistake each run" pattern.
2. **Curated few-shot examples pin the output shape.** A specialist can carry a worked, end-to-end example (correct `run.py`, correct manifest, correct result JSON, correct version capture). Few-shot exemplars are the most reliable way to remove structured-output variance — far more reliable than prose rules, which the generalist paraphrases.
3. **Narrowed task surface → convergent outputs.** "general-purpose does anything" has a huge output distribution; "this agent builds a reproducible analysis that satisfies the v12 gates" is a narrow, repeatable task. Narrower scope = lower variance = higher first-attempt success.
4. **Tunable independently.** A specialist seat can pin model, reasoning effort, tools, and skills for *this* task without changing global chat behaviour — e.g. higher reasoning effort on the phase where it pays, or a `STATISTICAL_ANALYSIS` specialist for the analysis phase and a `SOFTWARE_ENGINEERING` one for the implementation phase.
5. **The record stays honest.** Today the system correctly labels every phase "general-purpose (stand-in)" — a reviewer reading that knows no specialist covered it. Registering one makes the label true *and* better.

**Honest caveat.** A specialist runs the *same* model (gpt-5.6-sol); its edge is a tighter, persistent, example-driven system prompt and a narrower task — essentially the contract-hardening we did, but *owned by the agent and reusable*, rather than re-inferred each run. The contract hardening already captured a chunk of this gain (first-attempt success on the completed cycle). A specialist makes that gain **durable and repeatable** instead of luck-of-the-draw, and is the architecture the selection machinery was built for.

---

## 6. Options for the way forward (for you to decide)

1. **Register a phase specialist** (recommended primary). Create a `SOFTWARE_ENGINEERING` (and optionally `STATISTICAL_ANALYSIS`) specialist agent whose system prompt encodes the build contract + a curated worked example, and let `assign_phase` select it. Highest, most durable lift on bucket 4. *Effort: moderate — author the agent, register it, run one validation cycle.*
2. **Keep hardening the shared contract** (complementary, cheap). Continue folding each new observed mistake into the phase prompt. Helps the generalist and any specialist, but has diminishing returns and can't fully remove one-shot variance. *We've already done the high-value ones.*
3. **Add a build-resume path** (orthogonal, worthwhile). Let a stranded/orphaned build (e.g. after a restart, or a continuation error) be re-driven without a full re-run. Removes the operational rough edge and makes iterating on the build far cheaper. *Effort: moderate.*
4. **Selective gate softening** (use sparingly). Only for genuinely generative-workflow assumptions (as with lineage/rerun inputs). Do **not** soften `granted_paths_only` / `server_executed_entry_point` / exit-status — those catch real defects.
5. **A verify-and-repair micro-loop for the phase** (nice-to-have). A cheap deterministic pre-check of the worker's output (paths absolute? one JSON object? versions via metadata?) that bounces obvious violations back once before spending a full server verification. Cuts wasted ~5-min attempts.

My recommendation: **1 + 3** — register the specialist to fix the residual worker-variance, and add the resume path so iterating never strands a cycle again. 2 continues opportunistically; 5 is a good cheap add if you want to squeeze the per-attempt rate further.

---

## 7. Registered specialists (done this session)

Two specialists are now **live** in `config.yaml` under `subagents.custom_agents` (config.yaml is git-ignored local config — this is where live registration takes effect). Both are recognised by the selector: `build-engineer` covers `software_and_workflow_engineering` (the *required* Build capability, so it is selected for the main build phase instead of the general-purpose stand-in), and `statistician` covers `statistical_analysis` (optional in Build; also usable in Design/Test).

| | `build-engineer` | `statistician` |
|---|---|---|
| DBTL capability | `software_and_workflow_engineering` (required in Build) | `statistical_analysis` (optional in Build; Design/Test) |
| System prompt | The build contract, agent-owned: workspace-relative paths, one provisioned interpreter (no venv), `importlib.metadata` versions (never `pkg.__version__`), notebook-as-output, exactly one JSON result, v3 manifest + rerun spec, one `phase_done_condition`, honest reporting | Statistical rigor: state model/estimator, interval by a named method, diagnostics, separate performance from validity (a simulation can't establish real-world/causal validity), explicit limitations; plus the same reproducibility discipline when implementing in a Build phase |
| Skills attached | `data-analysis`, `chart-visualization`, `code-documentation` | `data-analysis`, `chart-visualization` |
| Model | `inherit` (gpt-5.6-sol) | `inherit` |
| max_turns / timeout | 300 / 1800s | 200 / 1800s |
| Tools | inherit all sandbox tools (read/write/str_replace/bash/ls) | inherit all |

**What "skills" means here:** two layers. (1) **DBTL capabilities** — the load-bearing field that makes the selector pick the agent for a phase. (2) **Skills** — discoverable/activatable packages (from `skills/public/…`) the agent may load at runtime: `data-analysis` (DuckDB SQL exploration/summary/export), `chart-visualization` (chart image generation), `code-documentation` (READMEs/API docs/inline docs). The *system prompt* is the primary reliability lever; skills are secondary helpers.

### How to improve the specialists over time
1. **Iterate the prompt from real failures.** Fold every new observed mistake into the system prompt (as we did for `jupyter.__version__`, extra-JSON, self-built venv). Highest leverage, cheapest.
2. **Add curated few-shot examples.** Embed a complete correct `run.py` + phase_manifest + result JSON (in the prompt or as a dedicated skill). Exemplars cut structured-output variance more than prose rules.
3. **Curate skills.** Build a purpose-made `dbtl-reproducible-build` skill (the exact scaffolding), and prune skills that don't help. Use the `skill-creator` skill.
4. **Tune the dials per seat.** `model`, reasoning effort, `max_turns`, `timeout_seconds`, token budget — independently, without changing global chat behaviour.
5. **Split capabilities into finer phases.** Let the planner emit a software-engineering phase *and* a statistics phase, each drawing its specialist, so each does what it is best at.
6. **Promote Learn outputs back into prompts.** When a cycle's Learn stage surfaces a durable lesson, encode it in the specialist prompt/skill.
7. **Measure + A/B.** Track per-attempt phase success by agent; compare specialist vs generalist; iterate on the failures.
8. **Grow the roster.** Add `experimental_design`, `quantitative_genetics`, `validity_assessment`, `scientific_reporting` specialists so more of the pipeline runs on specialists rather than the stand-in.
9. **Version the template.** The live agents are in git-ignored `config.yaml`; add a documented copy to `config.example.yaml` if you want new setups to ship with them.
