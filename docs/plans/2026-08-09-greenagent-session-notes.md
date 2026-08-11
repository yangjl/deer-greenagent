# GreenAgent / DBTL — session notes, 9 Aug 2026

Working notes from a long session on the DBTL layer over DeerFlow 2.0.
Everything below was verified against the code or measured, not recalled.

---

## 1. What shipped

**Cap salvage** (`a8fa9a88`). Token exhaustion was the single biggest Build
failure mode — 8 of 34 recorded failures, all `token_capped` — and it was also the
most expensive one, because a capped result was thrown away *before* anything
verified it. It now degrades to partial-but-admissible instead of a total loss.

The safety conjunct is the whole change: salvage is allowed only when
`server_executed_entry_point` is in the spec's validity gates **and**
`enforce_server_execution` is on. If nothing actually ran the entry point, there is
nothing to verify against and the cap stays fatal. Two explicit `salvaged_cap`
terms keep automatic corrections closed, because clearing `stop_reason` would
otherwise flip `was_capped` to False and make a capped build cost *more* than
before.

**House plot style** (`45f40d0a`, plus the `dbtl-plot-style` skill). The matplotlib
style block now lives in the phase prompt as `DBTL_PLOT_STYLE_BLOCK`, and a test
pins the skill's copy byte-for-byte to the constant so the two cannot drift.

**PNG-only figures** (`1a5e51f4`). Build now rejects a rerun spec that expects
`.pdf`, `.svg`, `.ps`, or `.eps` outputs.

**Subagent naming** (`96aa1ceb`). `SubagentExecutor` now sets
`context["agent_name"]`, which is what any per-agent state — DeerMem included —
needs to scope itself correctly.

**Test verdict ordering** (`a3504e14`). See §3.

**Deck readability** (`9631622f`). Published-artifact hash prefixes stripped from
displayed names, numbers formatted for humans, duplicate entries deduped.

---

## 2. The measurement that changed a design

The plan assumed image output embeds a timestamp, so the plot style had to live in
a config file. One ten-line probe inverted that: **PNG is byte-stable** across
rebuilt font caches, different config dirs, and different working directories —
but **PDF and SVG are not**, because they embed a creation date.

That flipped two decisions at once. The style has to live in the *script*, not a
matplotlibrc, because Test re-runs in a fresh workspace that has no config. And
non-PNG figure formats can never pass the reproducibility gate, which turned out
to be a real latent bug nobody had hit yet.

The general lesson, now written into the `verified-fix` skill: when a design rests
on how something behaves, write the probe. Do not reason about it.

---

## 3. The Test verdict bug

Symptom: every route from the Test gate returned 409. Cause: two workers each
returned a complete `validity_assessment` and they disagreed. The code took the
first one it saw, so the deck's menu and the gate's validator were reading
different verdicts — the user picked a route the server did not believe was legal.

Fix is one `sorted()` call in `test_review.py` putting the validity assessment
first deterministically. Verified live: 409 → 200.

This is the shape worth watching for — a defect where nothing fails loudly, it
just depends on which producer answered first.

---

## 4. Pilot 2 (plant breeding question, end to end)

Build succeeded on the first attempt. The Test re-run **passed** — every expected
output matched its approved Build hash, which proves the styled PNGs regenerate
byte-for-byte in a clean workspace. That was the real thing being tested.

The verdict came back `invalidated`, and correctly so: the predeclared
diminishing-returns direction failed (late gain 0.085 > early gain 0.047). A
genuine negative result, which is the system working.

Learn never dispatched — orphaned by a gateway bounce, not by a code defect.

---

## 5. Honest assessment of the harness

**Size.** DBTL is 44,834 lines against 140,238 for the harness, roughly 24%. Of
that, 29,049 is actual code, 8,086 is prose, 4,277 blank. Only two files exceed
1,500 lines — but `adapter.py` alone is **8,762 lines**, about 30% of all DBTL
code. That file is the one real structural problem.

**Fork discipline is good.** Diff against merge-base `99c926b7` is 319 files added
and 130 modified — about 92% additive. Only three harness files are genuinely hot
(`sandbox/tools.py` 457 lines changed, `gateway/services.py` 332,
`runtime/journal.py` 328), and 33 of the modified files changed 20 lines or less.

**Is 45k lines reasonable?** Yes, for what it is. It is not 45k lines of
reimplementation — it is a governance contract with its own specs, gates, personas,
and durable records, plus the tests that pin them. The number to watch is not the
total, it is `adapter.py`.

**Should DBTL be a plugin?** No. The diff is already 92% additive, so it is already
plugin-shaped in the way that matters. A formal plugin API would mean designing
graph registration, schema and migration ownership, route mounting, and UI
contribution — all speculatively, from a single example. The better move is to keep
the diff additive and upstream the generic parts: durable graph replies in
`journal.py`, and the grants/isolation work in `sandbox/tools.py`.

---

## 6. Deployment and HPC

**Deploy** is `make up` — five containers, nginx on **:2026** as the sole ingress.
Dev is `make dev` or `make dbtl-manual-dev`. The gateway is deliberately
single-worker with run state in-process, so scaling means gateway replicas behind
nginx with shared redis and Postgres, which is untested on this fork. One box is
fine for a lab.

**HPC: not supported today.** Grepped the whole repo for slurm/sbatch/qsub/
singularity/apptainer — zero code hits, one passing mention in a design doc.

Three obstacles, worst last:

Build's server execution is hard-capped at 300s (`adapter.py:5583`) and Test's
re-run at 600s (`:7710`) — both `min()` clamps that the 900s stage budget cannot
raise. `Sandbox.execute_command()` is synchronous, so an `sbatch --wait` would pin
the single gateway worker for the whole job. And the one that actually decides it:
Test demands byte-identical output hashes, which a cluster job will not deliver —
different node, different BLAS threads, numbers move in the last decimal.

**Recommended shape.** Run the harness on a Slurm login node and mount scratch via
the existing `config.sandbox.mounts` (no code needed). Short analyses run in the
sandbox as they do now. Long jobs move *outside* the evidence contract: Build
submits with `sbatch` (returns instantly), and results return as **inputs to the
next cycle**, declared as external evidence the same way a field trial would be. A
`SlurmSandboxProvider` is only ~150 lines, but it would also require raising both
clamps and deciding what "reproducible" means on a cluster — a governance question,
not a coding one.

---

## 7. Operational gotchas (cost real time this session)

The manual DBTL profile needs `DEER_FLOW_AUTH_DISABLED=1` plus
`DEER_FLOW_AUTH_DISABLED_USER_ID` / `_EMAIL` **and** a sourced `.env`. Without all
three, the gateway returns `401 token_invalid / invalid_signature`. Cost ~10
minutes. Now documented in the pilot skill.

Repeated gateway bounces took down nginx and the frontend; both had to be restarted
by hand (`pnpm run dev`, `nginx -c docker/nginx/nginx.local.conf`).

Known pre-existing test failures — verified against a stashed tree, **not** ours:
`test_app_config_reload.py::test_config_example_registers_two_dbtl_specialists`,
migrations 0015 / 0024 / 0025 / 0028, and
`test_subagent_executor.py::TestAgentConstruction::test_create_agent_threads_explicit_app_config_to_model_and_middlewares`.

---

## 8. Two times the suite was right and I was wrong

I deleted the deck's empty slides as dead weight. Two existing tests caught it. Each
empty section is a **comment anchor**, and each absence is real information — a
withheld figure that nobody listed is a withheld figure nobody notices. Reverted,
and the reasoning is now in the new test class docstring so the next person does not
repeat it.

I also mis-scored 3 of 24 assertion cells in a skill eval because my grading regexes
were too strict. Fixed by reading the actual REPORT.md files rather than trusting
the pattern.

---

## 9. Still open

**Deck LLM composition.** Extend the summarizer contract to return a slide plan;
renderer consumes it; the plan is stored with the summarizer's output so
`TestADeckRetryDoesNotReRunTheSummarizer` still holds. Needs a mandatory
deterministic fallback and a deck-style `SKILL.md`.

**Memory layer, §2 and §5.** Add `get_memory_tools()` to the DBTL tool list for
specialist seats and bounded memory injection into `_build_initial_state`
`system_parts`. Check `backend_requires_passive_writes_in_tool_mode` first, then
enable for `statistician` and `build-engineer`. §4 needs one repo method for
cross-cycle deck comments.

**The blocker from the debate doc.** `evaluate_validity` forces `INCONCLUSIVE` when
`metrics` is empty, so a figure-first cycle can never reach a verdict. This is a
real design hole, not a bug.

**`verified-fix` skill** — awaiting feedback from the eval viewer, then revise and
package as a `.skill` file.

**`adapter.py` at 8,762 lines** — the single highest-value refactor available.
