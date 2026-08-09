# Slide-deck specialist, and memory for DBTL specialists — a plan

**Status:** Plan. Not started. Phases 1–2 are prerequisites for everything else.

**Date:** 2026-08-09

**Goal:** Build decks that are worth reading — fewer words, figures first, to the
point — by a specialist that keeps getting better as reviewers comment on its
work. Give the existing specialists (`statistician`, `build-engineer`) the same
memory layer on the way.

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

## 3. The slide-deck specialist

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

### Shape

**A new read-only seat, dispatched inside the existing `render_review_deck`
step.** No new step key, no `BUILD_WORKFLOW_V1` change, no migration.

- Agent `slide-deck` declared in `config.yaml` under `subagents.agents`, with its
  own `system_prompt` and its own `skills:` whitelist.
- New role `deck`, added to `_READ_ONLY_ROLES` (`adapter.py:331`) so it gets no
  writable workspace — same posture as `summarizer`.
- Its own memory bucket follows automatically from its agent name, once §1 lands.

**Why its own seat rather than folding into the summarizer:** the summarizer
writes the durable Markdown record and is explicitly forbidden from judging
("Do not say whether the result is good, acceptable, or sufficient",
`build_review.py:62-64`). A deck is a different job for a different reader, it
needs its own memory bucket to learn from deck comments specifically, and a bad
deck must not be able to invalidate the write-up.

### Contract

Input: the `BuildReviewPackage`, the execution bundle's figure list with hashes,
recent reviewer comments (§4), and its injected craft memory.

Output: a **slide plan**, not HTML — ordered slides, each naming a kind, a title,
at most one figure path, one line on what that figure shows, and trimmed body
text. Rules mirroring the summarizer's: cite only figure paths present in the
bundle; never assert whether a result is good; drop a slide rather than fill it
with "none recorded".

`render_build_deck` consumes the plan instead of laying out the package itself.
**Style stays entirely in the renderer** — the specialist decides *what is on a
slide*, never how it looks.

**Fallback is mandatory.** If the composer fails, is capped, or returns an
unusable plan, fall back to today's deterministic layout and let the step
succeed. `write_build_deck` already wraps rendering in try/except with the
comment "a presentation must not break the record"
(`build_review.py:266-268`); keep that property.

### The skill

One `SKILL.md` — the house deck style: figures first, one line per figure, no
slide without content, limitations ranked and deduplicated, target slide count.
It must be an enabled skill in the same user storage the Build phase gate uses
(`adapter.py:1564-1577`), since that is the only registry `_load_skills` reads.

**Skill vs memory:** the skill is the standing house style, edited by a human.
Memory is what this agent has learned about *this* reviewer and project. Keep
them separate — if a lesson generalizes, promote it into the skill by hand.

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
- Letting the deck specialist judge whether results are good. It composes; the
  human reads and decides.
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
3. **Deck composer becomes a new failure mode in Build.** Mitigated by the
   mandatory deterministic fallback — the step must never fail because of it.
4. **Passive writes may be forced by the backend** even in tool mode. Verify
   `backend_requires_passive_writes_in_tool_mode` before §2, not after.
5. **Scoping regression.** If §1 is wrong, facts land in `__default__` and leak
   across projects. It is the one change worth testing hardest.

---

## Verification

- Two named specialists in one project write distinct facts; assert two agent
  directories and no cross-read. Second project → different bucket.
- A specialist run with memory disabled behaves exactly as today.
- Deck composer failure, cap, and malformed plan each fall back to the current
  layout with the step still succeeding.
- The composed deck for the existing genomic-selection package: no empty slides,
  every figure carries a one-line reading, limitations deduplicated.
- No path in this change reads or writes `MemoryWritePolicy`.

---

## Order

§1 → §2 → §5 (memory, cheap, independently useful) → §4 → §3 (the deck).

§5 before §3 deliberately: it exercises the memory layer on two agents that
already exist and whose output we can compare against known-good runs, before a
brand-new specialist depends on it.
