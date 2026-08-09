# Better Build decks, and memory for DBTL specialists — a plan

**Status:** Plan. Not started.

**Date:** 2026-08-09

**Goal:** Build decks that are worth reading — fewer words, figures first, to the
point — and that keep getting better as reviewers comment on them. Give the
existing specialists (`statistician`, `build-engineer`) a craft-memory layer on
the way, and pin plot appearance in a shared skill.

**No new agent.** An earlier draft added a dedicated `slide-deck` specialist. It
was cut: once plot style became a shared skill (§4b), the only thing left arguing
for a separate agent identity was a private memory bucket, which recent reviewer
comments already cover as ordinary context. Deck composition folds into the
existing summarizer seat (§3), with a documented trigger for splitting it out
later if that prompt gets muddy.

---

## 0. The boundary this plan must not cross

Two different things are called "memory" and conflating them would break a
deliberate design decision.

**Craft memory (in scope).** What a specialist learns about *doing its job*:
"this reviewer skims — one line per figure", "`pkg.__version__` raises on the
jupyter meta-package". Scoped to one agent, one user, one project. It changes
*how* work is done and can never become a scientific claim.

**Stage knowledge memory (out of scope).** What a *cycle* concluded about maize
or sorghum. `MemoryWritePolicy` (`dbtl/stage_spec.py:63-72`) already exists for
this, with every Design/Build/Test spec set to `NONE` and only Learn at
`CANDIDATE_ONLY`, and its docstring says why: *"a stage worker cannot manufacture
durable knowledge as a side effect of doing its job."*

**This plan does not read, write, or change `MemoryWritePolicy`.** That enum stays
reserved for the stage-knowledge question. Craft memory is a property of the
*agent*, not of the cycle, and is keyed by agent name rather than by cycle.

The practical rule enforced in prompts and reviewed in verification: a specialist
may remember how to work, never what is true about a crop.

---

## 1. Fix subagent memory scoping (prerequisite)

Per-agent memory is fully implemented in DeerMem — facts live at
`{storage}/users/{uid}/agents/{agent_name}/facts/**`, and `agent_name` is
required for every fact write (`deermem/core/storage.py:729-730`). It works.
Subagents just cannot reach it correctly.

**The bug:** in tool mode `_resolve_scope` (`agents/memory/tools.py:33-44`) reads
`agent_name` and the project scope from `runtime.context`. `SubagentExecutor`
holds `project_id` and `project_root` (`subagents/executor.py:456-457`) but
`_build_initial_state` (`:691-829`) **publishes no runtime context at all**. So
every subagent write would silently land in the `__default__` bucket and in
user-global rather than project scope. Wiring memory before fixing this produces
one shared, wrong pile.

**Change:** publish `agent_name`, `project_id` and `project_root` into the
subagent's runtime context.

**Naming constraint:** `validate_agent_name` (`deermem/core/paths.py:56-62`)
enforces `^[A-Za-z0-9-]+$`. `statistician` and `build-engineer` pass. **Underscores
do not** — the new specialist must be named `slide-deck`, not `slide_deck`.

**Verify:** two subagents with different names, same project, write one fact each;
assert two directories under `agents/`, and that a second project produces a
different `--project--` bucket.

---

## 2. Give subagents memory — explicit writes only

Subagents today have none of it: no `MemoryMiddleware`, no memory tools, and no
injection (`DynamicContextMiddleware`, the only injector, is lead-only at
`lead_agent/agent.py:438`).

**Decision: tool mode for specialists, not middleware mode.** In middleware mode
a separate extraction LLM reads the whole conversation and decides what to keep,
with the agent having no say and no visibility. For a Build worker that
conversation is full of scientific content, and passively distilling it into
durable facts is exactly the side-effect knowledge creation §0 forbids. Tool mode
means one deliberate `memory_add` call per fact, auditable in the transcript.

**Changes:**

- Append `get_memory_tools()` (`agents/memory/tools.py:242-252` —
  `memory_search`, `memory_add`, `memory_update`, `memory_delete`) to the tool
  list DBTL builds at `adapter.py:4862-4878`, for specialist seats only.
- **Do not** add `MemoryMiddleware` to `extra_middlewares` — that is the passive
  writer. The seam exists (`adapter.py:4915-4921`, appended at
  `executor.py:620-621`) and is where it *would* go if we ever wanted passive
  writes; leave it unused.
- **Verify first:** `backend_requires_passive_writes_in_tool_mode`
  (`agents/memory/manager.py:605-611`) — if DeerMem returns true, tool mode still
  writes passively and this decision needs revisiting before proceeding.
- Inject the agent's own facts into the prompt rather than relying on it to
  search: add a bounded memory section to `system_parts` in `_build_initial_state`
  (`executor.py:770-793`), beside the skills section. Bound it with the existing
  `max_injection_tokens` (2000). Note DeerMem sets `injection_agent = None` in
  tool mode (`deer_mem.py:315`), so this section must call `get_context` with the
  agent name explicitly.

**Category discipline is prompt-enforced, not config-enforced.** Categories are
free-form strings with no allowlist anywhere (`updater.py:189-190`, `:928`;
`tools.py:91`). "Only style, never claims" cannot be expressed in config. Use a
convention — `craft`, `style`, `failure-mode` — instruct it in the system prompt,
and check it in review. Record this as a known soft edge, not a guarantee.

---

## 3. Deck composition — folded into the existing summarizer seat

### Why the current deck is thin

From a real deck in the manual workspace
(`build-slides-rev6-4d09cc.html`, 11 slides, 5 figures): every figure slide shows
the word "Figure", a title-cased filename, and the full raw path — no reading,
because the captions were recovered from filenames by `execution_bundle`
(`build_review.py:112-133`) rather than written. Three slides say nothing ("This
build reported no numeric outcomes", "The build did not record a rerun
procedure"). Nine limitations are dumped on one slide, each prefixed
"Limitation:", with linkage disequilibrium repeated three ways. Figures appear in
discovery order.

`render_build_deck` (`dbtl/build_deck.py:141-238`) is pure template logic: it drops
whatever the package holds into fixed slide shapes. **Nobody decides what belongs
on a slide.** That is the gap — not the styling, which is fine.

### No new specialist

An earlier draft of this plan added a dedicated `slide-deck` seat. Once figure
style moved to a shared skill (§4b), that seat stopped earning its keep: the only
thing left requiring a separate agent identity was a private memory bucket, and
that argument is weak because recent reviewer comments are fed in as context on
every run (§4) — the model can distill them in place without remembering
anything.

**Use the existing summarizer seat** (`SUMMARIZER_CAPABILITY =
"build_result_synthesis"`, role `summarizer`, already in `_READ_ONLY_ROLES` at
`adapter.py:331`, already a recorded step). It already selects figures, already
writes readings, and its output already feeds the renderer. Give it the deck
skill and extend its contract. **No new seat, role, agent, or config block.**

Note what the thin deck actually proves: those filename-derived captions mean the
summarizer never cited those figures at all — `execution_bundle` recovered them
from the published PNGs. Part of this work is not new capability but making that
seat do the job it already has.

### Contract

Extend the summarizer's existing output with a **slide plan** — ordered slides,
each naming a kind, a title, at most one figure path, one line on what that figure
shows, and trimmed body text. Its current rules already carry over: cite only
figure paths present in the bundle (`build_review.py:69-71`); never say whether a
result is good (`:72-74`). Add: drop a slide rather than fill it with "none
recorded", and rank and deduplicate limitations rather than listing all of them.

`render_build_deck` (`dbtl/build_deck.py:141-238`) consumes the plan instead of
laying out the package itself. **Style stays entirely in the renderer** — the
seat decides *what is on a slide*, never how it looks.

**The plan is stored with the summarizer's output, not recomputed at render
time.** This preserves the existing guarantee that a failed deck can be retried
without re-running the summarizer
(`test_dbtl_build_workflow_execution.py::TestADeckRetryDoesNotReRunTheSummarizer`)
— the retry re-renders the stored plan.

**Fallback is mandatory.** A missing, malformed, or capped plan falls back to
today's deterministic layout and the step still succeeds. `write_build_deck`
already wraps rendering in try/except with the comment "a presentation must not
break the record" (`build_review.py:266-268`); keep that property.

### The skill

One `SKILL.md` for deck style — figures first, one line per figure, no slide
without content, limitations ranked and deduplicated, target slide count. Separate
file from the plot-style skill (§4b): slide layout and plot appearance are
different concerns. It must be an enabled skill in the storage `_load_skills`
reads (`adapter.py:1564-1577`).

**When to split this back out.** If the summarizer's prompt gets muddy holding two
contracts — the durable Markdown record for the audit chain, and a deck a person
skims to decide — split the deck into its own seat. That costs a config block and
a role name, and nothing in this design forecloses it. Do not pay for it up front.

---

## 4. Feed it past comments

Per-slide reviewer comments are already durable: `slide_comments` (JSON, not null)
and `active_slide_id` on `dbtl_design_feedback_actions`
(`persistence/dbtl/model.py:824-826`, migration `0032_dbtl_slide_comments`), with
slide ids validated against the surface's registered `commentable_slides` on write
(`design_feedback_ops.py:110-111`).

**Nothing can read them across cycles.** Every accessor is narrow —
`stage_feedback_actions` needs a `surface_id` (`design_feedback_ops.py:871-883`).
But `project_id` is a first-class indexed column (`model.py:801-806`), so:

**Change:** one new repository method on `DesignFeedbackOpsMixin` — recent
feedback actions for a project, newest first, bounded. Join the surface for slide
*titles*, which live in `decision_request["commentable_slides"]` rather than on the
action row (the join is already demonstrated at `dbtl_cycles.py:167-174`).
**No schema change, no migration.**

**Comments are evidence; memory is the distillation.** The composer reads recent
raw comments each run, and separately records durable style lessons via
`memory_add`. Do not try to make memory a substitute for reading the comments.

---

## 4b. Figure style is a shared skill, never a memory

Font size, axis labels, tick sizes and DPI are **not** the deck's to set — they are
baked into the PNG by the Build worker's Python. The deck only embeds the finished
image. Today there is **no figure-style guidance anywhere in the repo**: zero hits
for `mplstyle`, `rcParams`, `plt.style`, `dpi`, `figsize` or axis-label wording in
`build_phases.py` or `config.yaml`. Every plot inherits raw matplotlib defaults.

**This must be pinned, not learned.** The Test rerun requires every expected output
to match its approved Build hash byte-for-byte
(`test_rerun.py:519-527`). A style that drifts as an agent learns would re-render
the same script differently, change the hash, and invalidate the cycle over a font
size. Deterministic appearance is a precondition of the reproducibility gate.

**Shape: one shared `SKILL.md` carrying the house plot style** (an `.mplstyle` block
or explicit `rcParams`), declared by any seat that draws — `build-engineer`,
`statistician`, and any future analysis specialist.

Two properties make a skill strictly better than a loose config file here:

- **It is already hash-pinned into the Build record.** `_declared_skill_bindings`
  (`adapter.py:1553-1586`) SHA-256s the skill's `SKILL.md` bytes into
  `skill:<name>:sha256:<digest>`, recorded per phase (`adapter.py:5246,5255`) and
  re-verified on replay (`:5342-5346`). So *which style produced this figure* is
  provably part of the record, and editing the style shows up as a different
  binding rather than as a silent change.
- **It is genuinely shared.** Base skills resolve from `skills.path` /
  `$DEER_FLOW_SKILLS_PATH` / `<project_root>/skills`
  (`config/skills_config.py:37-57`); only *custom* skills redirect per user. One
  house style, one place, every agent.

A reviewer comment like "axis labels are too small" becomes a one-line edit to that
skill — applying to every figure in every later cycle at once, versioned and
revertible — rather than a fact one agent learned and the others did not.

Slide layout gets its own **separate** skill (§3). Plot appearance is produced by
the seats that draw; slide layout by the seat that presents. Different files,
different declarers.

**Consequence for §2:** craft memory keeps only wording and density — "one line per
figure, not three", "this reviewer skims". Nothing that changes a pixel.

---

## 5. Turn memory on for the existing specialists

Once §1 and §2 land, `statistician` and `build-engineer` get memory by
configuration alone — both names already satisfy the agent-name pattern.

The value is that hard-won failures accumulate without a human editing YAML.
`build-engineer`'s prompt already carries a hand-maintained list of failures that
have discarded whole phases (`config.yaml:343-346`); memory is where the next one
lands by itself.

**Bounded by:** `max_facts` 100, `fact_confidence_threshold` 0.7, staleness review
at 90 days, and consolidation off by default (`deermem/config.py:87-213`).

---

## 6. Explicitly not in this plan

- Any DBTL **stage** writing memory. Reserved for `MemoryWritePolicy`, per §0.
- Changing the global `memory.mode` — the lead agent keeps middleware mode.
- A dedicated deck agent. Cut deliberately; §3 records the trigger for adding one
  later.
- Letting the summarizer judge whether results are good. It composes; the human
  reads and decides. Its existing prohibition stays.
- Learning *which figures matter* from reviewer taste. Figure selection follows
  the Design's deliverable manifest and the bundle; only presentation is learned.
  A deck that learns what a reviewer likes will start hiding what they do not.

---

## Risks

1. **Memory learns a wrong lesson and repeats it forever.** Mitigated by tool-mode
   explicit writes (visible in the transcript), `max_facts`, staleness review, and
   the fact that craft memory cannot reach scientific content. Add a way to
   inspect one agent's facts before this ships.
2. **Category discipline is prompt-only.** A specialist could file a scientific
   claim as `craft`. Not preventable in config today; needs review, and a
   write-side allowlist if it happens.
3. **Deck composition becomes a new failure mode in Build.** Mitigated by the
   mandatory deterministic fallback — the step must never fail because of it.
4. **Passive writes may be forced by the backend** even in tool mode. Verify
   `backend_requires_passive_writes_in_tool_mode` before §2, not after.
5. **Scoping regression.** If §1 is wrong, facts land in `__default__` and leak
   across projects. It is the one change worth testing hardest.
6. **Two contracts in one seat.** The summarizer now owes both the durable
   Markdown record and a slide plan, and a prompt serving two readers can serve
   both worse. Watch write-up quality against known-good runs; §3 names the split
   as the remedy.

---

## Verification

- Two named specialists in one project write distinct facts; assert two agent
  directories and no cross-read. Second project → different bucket.
- A specialist run with memory disabled behaves exactly as today.
- Missing, malformed, and capped slide plans each fall back to the current layout
  with the step still succeeding.
- `TestADeckRetryDoesNotReRunTheSummarizer` still passes — a deck retry must
  re-render the stored plan, not re-synthesize it.
- The composed deck for the existing genomic-selection package: no empty slides,
  every figure carries a one-line reading, limitations deduplicated.
- The summarizer's Markdown write-up is no worse than a known-good run after
  taking on the second contract.
- No path in this change reads or writes `MemoryWritePolicy`.

---

## Order

§4b (the plot-style skill — standalone, no dependencies, visible immediately) →
§1 → §2 → §5 (memory, cheap, independently useful) → §4 → §3 (the deck).

§4b first because it is one file, depends on nothing else here, and improves every
figure in the next cycle.

§5 before §3 deliberately: it exercises the memory layer on two agents whose
output can be compared against known-good runs, before the summarizer's contract
changes underneath it.
