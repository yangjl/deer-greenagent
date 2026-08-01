# Observable and retryable DBTL Build workflow

**Status:** Proposed standalone implementation plan. No implementation is
authorized by this document.

**Date:** 2026-08-01

**Decision:** Replace the monolithic Build worker with a durable,
phased workflow that **reads the approved Design, plans execution, runs
capability-selected specialists, and reports key outcomes**. Each work unit is
observable and independently retryable without discarding valid predecessors.
The live plan is projected read-only into the project rail. Any unit may pause
for human input via existing chatbox replies, `HumanInputCard`s, or Build meetings.

**Frontend Implementation Strictness:** Recycle existing code logic, UI components
(e.g., `TodoList`, `SubtaskCard`, `HumanInputCard`), and layout structures as much
as possible. Do not reinvent the wheel or introduce new visual paradigms if existing
patterns satisfy the requirement.

**Build records; Test checks; a human decides.** Build's contracts bind
*provenance* — which inputs were read, what ran, what it produced — and are
deliberately not a scientific gate. Whether the work followed the rules is
assessed at Test against its validity pack; whether it *means* anything is the
human reviewer's decision. A Build worker must never discard scientifically
useful work because it could not satisfy a presentational requirement, and must
never mark its own result failed for something Test or a person will judge
anyway. This is the same correction that produced `generic:build:v4`, applied to
the whole workflow.

This plan does not change the Design approval boundary and does not let an agent
approve a gate. It complements
[`2026-08-01-runtime-agent-activity-visibility-plan.md`](./2026-08-01-runtime-agent-activity-visibility-plan.md): that plan answers **who is active** in the
Agents rail; this plan answers **what the Build is doing** — in the conversation,
where the controls live, and in the rail, where the phase plan is summarized.
Both land in the same 256px rail, so §6.5 states how they share it.

## 1. Problem

Today, Build executes as one opaque `WorkUnit` without intermediate `task_running` events, hiding sandbox interactions (e.g., Bash execution) from the UI.

Additionally, the final structured answer carries too much responsibility. A failure in rendering or summarization discards scientifically complete sandbox work, forcing full retries.

A person needs to see the workflow breakdown (planning, execution by capability, summarization) and be able to retry specific failed steps (e.g., deck rendering) without re-running valid prior phases.

**Key gaps:**
- **No planning:** Build starts immediately as one opaque stretch.
- **Everything is a generalist:** Build uses `general-purpose` instead of specialized capabilities.
- **Results are file paths, not visual outcomes:** Build outputs paths instead of actual figures or metrics.
- **Uncertainty equals failure:** Build fails instead of pausing for human clarification when encountering implementation ambiguities.

## 2. Product outcome

Build is one governed stage and five operational sub-steps, one of which expands
into a dynamic, durable list of phases:

| Order | Stable key | Visible label | Executor | Typed output |
| --- | --- | --- | --- | --- |
| 1 | `load_design` | Read approved Design | Deterministic server code | `BuildInputBundle` |
| 2 | `plan_build` | Plan the build | Bounded planner worker | `BuildPhasePlan` |
| 3 | `execute_phases` | Run the build | One capability-selected specialist per phase | `BuildExecutionBundle` |
| 4 | `summarize_results` | Summarize results | Bounded summarizer plus deterministic validation | `BuildReviewPackage` |
| 5 | `render_review_deck` | Prepare review slide deck | Deterministic renderer/registrar | Registered Build deck |

`execute_phases` is a container: the plan produced by step 2 decides how many
phases there are, what each one is for, and which capability it needs. A plan
may legitimately contain **one** phase — see §5.2 on feasibility. Phases run in
the recorded order, each as its own attempt with its own worker, task timeline,
and retry boundary.

The ordered block appears in the originating conversation as soon as Build
starts. It updates over time. Expanding any phase uses the existing
`SubtaskCard` timeline without reinventing a custom tracker. Today that card retains tool arguments and output in its
step model but renders only the tool name; extend the native card with a bounded
tool-step disclosure so ordinary subtasks and DBTL workers can both show Bash
commands/output, other tool request/results, token use, and the final result.
Do not create a Build-only debug UI or expose hidden prompts and
chain-of-thought. Reuse existing UI states instead of inventing new ones.

### 2.1 The phase plan is summarized in the project rail

The rail's **Blockers** section is replaced by a live, read-only **Build plan**
projection for the selected cycle. Blockers today lists `openWorkItems(detail)`
— unresolved reconciliation and stage work items — which is genuinely useful and
must not simply disappear; §6.5 says where it goes.

The rail shows what the plan is and how far it has got. It shows no controls:
retry, hold, answering a question, and convening a meeting all stay in the
conversation, per the repo's standing rule that both left rails are navigation,
evidence, summary, and audit surfaces only. Selecting a phase opens the
originating conversation and scrolls to that phase in the workflow block; it
never dispatches anything. §6.5 owns the layout, which has to survive 256px.

The project rail continues to show durable stage state (`Build · In progress`,
`Build · Awaiting review`) in the Cycles section, unchanged. The generic stage
sheet remains an inspection/audit surface.

At any model-backed unit, the workflow block may show **Waiting for you** or
**Build meeting proposed**. These are paused collaboration states, not red
failure states.

The person can answer, request a different interaction format, convene the
proposed meeting, or hold the workflow without losing successful work.

## 3. Authority and evidence boundaries

The workflow decomposes execution without inventing new DBTL gates:
- **Design approval** is prerequisite to `load_design`.
- **No sub-step bypasses human gates**: Sub-steps cannot approve Build or advance Test.
- **Typed strictness**: Outputs must be typed and content-addressed.
- **Atomic evidence chain**: Build is reviewable only when all successful steps form an unbroken input/output digest chain.
- **Canonical deck**: The Build review deck projects the review package; it is not an independent scientific record.
- **Retry Authority**: Guided by server-bound `HumanInputCard`s. Visual states in the rail remain strictly read-only, reusing existing `project-rail` display patterns.

### 3.1 Strict provenance vs. Evaluated science

Contracts enforce *provenance*, leaving *science* to subsequent stages:
- **Build**: Validates paths, execution integrity, and forged evidence. Do not judge scientific adequacy or reject implausible results here.
- **Test**: Checks leakage, folds, holdouts, reproducibility against a validity pack.
- **Human review**: Judges scientific meaning.

A presentational failure (e.g., deck rendering) must never invalidate prior sandbox work. Only the failed presentational step retries.
The digest chain in §4.3 exists mostly to make this guarantee mechanical rather than
a matter of care.

What stays fail-closed is everything a person cannot check by reading: a path
outside the grant, an input mutated mid-run, an output that does not exist or
whose hash does not match, an evidence reference to a file the server cannot
verify, and any attempt to satisfy a gate. Those are provenance failures — the
reviewer's judgement is only as good as the record it is exercised on.

## 4. Durable workflow model

### 4.1 Declarative step specification

Add a Build workflow specification beside the versioned `StageSpec` registry.
It declares stable order, input contract, output contract, executor kind, and
retry policy. Do not encode the order in frontend conditionals.

Version 1 contains the five keys above. The owning Build stage run records the
workflow-spec key, so a later deployment can still reconstruct which workflow
was reviewed.

**The spec is fixed; the plan is data.** The five step keys never vary — a
deployment always reads the Design, plans, executes, summarizes, and renders.
What varies is the `BuildPhasePlan` that step 2 produces, and that plan is a
typed, content-addressed artifact with its own digest, not a runtime shape the
frontend infers. Phases are therefore auditable in exactly the way steps are: a
reviewer can ask which plan an attempt ran under, and a changed plan invalidates
the phases beneath it rather than silently rebinding them.

Phases are **sequential**. Concurrency inside a Build is out of scope for this
release (§14): one sandbox writer at a time is what makes the workspace grant, the
input snapshot, and the mutation checks tractable.

Step states are a closed vocabulary:

- `queued`
- `running`
- `succeeded`
- `needs_input`
- `failed`
- `invalidated`
- `cancelled`

`waiting` is a UI projection for a queued step whose predecessor is unfinished.
`needs_input` is a terminal, immutable attempt outcome: it records the exact
question and interaction request, releases worker/runtime ownership, and leaves
the stage safely paused. A human answer creates a new attempt of the same step
linked to the paused attempt and its bound response. It never mutates the old
attempt back to running.

### 4.2 Append-only step attempts

Add `dbtl_stage_step_runs` rather than overloading
`dbtl_stage_worker_runs`. Worker rows describe model-backed work units; the
first and fourth Build steps are deterministic and need the same audit and
retry semantics without pretending to be agents.

Each row binds at least:

- project, cycle, stage-run, workflow-spec, and step key;
- for a phase, its `phase_index`, stable `phase_key`, and the plan digest it
  belongs to — a phase attempt whose plan changed is a different phase, not a
  retry of this one;
- the capability requested and the agent that covered it, plus whether a
  generalist stood in (§5.3);
- step attempt number and immutable status;
- parent run id and native task id;
- input digest and, on success, output digest;
- selected predecessor step-attempt ids;
- start/completion timestamps;
- bounded server-owned error code and display summary;
- optional server-owned human-input request id or Build work-meeting id;
- retry relationship (`supersedes_step_run_id`); and
- execution metadata needed for reconstruction, never raw prompts, secrets,
  unbounded shell output, or model reasoning.

Only one attempt for a step may be `running`. Multiple terminal attempts are
retained. The workflow projection selects the newest valid success for each
step whose input digest still matches its predecessors.

### 4.3 Digest chain and invalidation

Every step computes its input digest from server-owned material:

1. `load_design`: approved Design artifact id, exact content hash, Design
   review id, policy version, Build `StageSpec`, and relevant project/cycle
   revision.
2. `plan_build`: `BuildInputBundle` digest, planner-contract version, and the
   capability registry snapshot the plan was drawn against.
3. each phase of `execute_phases`: `BuildPhasePlan` digest, this phase's index
   and key, the digests of every **preceding** phase in the plan, the selected
   immutable workspace inputs, worker configuration, code/environment revision,
   and the isolated per-attempt workspace grant.
4. `summarize_results`: the ordered digests of every successful phase plus the
   current Build result-contract version.
5. `render_review_deck`: canonical Build-review-package digest plus renderer
   version.

If an upstream digest changes, downstream successes become `invalidated`; they
are never silently rebound. The next resume starts at the earliest dirty step.
Changing the approved Design restarts at `load_design`. A malformed summary
restarts only at `summarize_results`. A failed deck registration restarts only
at `render_review_deck`.

**Phase 3's chain is what makes a partly-built Build resumable.** Because each
phase binds its predecessors' digests, retrying phase 2 of four leaves phase 1
selected and untouched, while phases 3 and 4 — if they had somehow run — are
invalidated rather than accepted against a changed upstream. This is also the
rule that governs replanning: a new `BuildPhasePlan` invalidates **every** phase
beneath it, because a phase's meaning comes from the plan it sat in. Preserving
completed work across a replan is deliberately not attempted; a plan that
changed is a different account of the work, and quietly carrying old phase
outputs into it would produce evidence nobody planned.

## 5. Step contracts

### 5.1 Read approved Design

This step is deterministic. It must:

- resolve the exact approved Design attempt and canonical artifact by id and
  hash;
- verify project/cycle membership and current approval;
- read the artifact through the project/sandbox mapping without asking a model
  to rediscover the path;
- collect declared datasets and the bounded project manifest required for
  Build; and
- write a compact, typed `BuildInputBundle` whose provenance distinguishes
  approved artifact, workspace input, dataset, and server policy.

A missing, stale, unreadable, or hash-mismatched Design fails here with a
specific error. No Build worker is dispatched.

### 5.2 Plan the build

A bounded planner worker reads the `BuildInputBundle` — the approved Design, the
declared datasets, and the project manifest — and answers one question: **what
are the pieces of work this Design implies, in what order, and what kind of
specialist should do each one?**

It returns a typed `BuildPhasePlan`:

- an ordered list of phases, each with a stable `phase_key`, a short visible
  title, an objective in one or two sentences, the **capability** it requires,
  the inputs it expects, the outputs it should produce (including figures), and
  a done-condition a later reader can check;
- a plan-level rationale explaining the decomposition; and
- explicit `assumptions` and `open_questions`.

**Feasibility is a real answer, not a formality.** The planner returns one of:

- `planned` — a decomposition it can defend;
- `single_phase` — the work does not usefully decompose, so the plan is one
  phase covering it. This is a legitimate outcome and must not read as a
  failure; a short simulation script is not four phases pretending to be a
  project; or
- `needs_input` — the Design leaves something the planner cannot resolve, or
  the work as specified cannot be built at all. The §7 collaboration flow takes
  over, including the governed route back to Design when the Design itself must
  change.

The planner writes nothing to the workspace, runs no Bash, and dispatches no
worker. It is cheap and re-runnable by construction. A plan is bounded:
`MAX_BUILD_PHASES` (start at 8) caps the list, and a planner that wants more
must say so as an open question rather than emitting a project plan. An
unparseable or over-long plan degrades to `single_phase` with a recorded note —
the same fail-soft posture the Design roster writer takes — because losing the
decomposition costs structure, while failing here costs the whole Build.

**The plan is proposed, not imposed.** When the workflow is configured to
confirm plans (`dbtl.build_plan_confirmation`, default on for `full`-weight
cycles), the plan is presented in chat as a structured card before any phase is
dispatched: **Start the build**, **Change the plan** (free text, replanned with
the person's words carried verbatim), or **Hold here**. Nothing is preselected.
With confirmation off, the plan is recorded and execution begins immediately —
the plan is still durable, visible in the rail, and inspectable.

### 5.3 Run the build, one phase at a time

Each phase in the recorded plan becomes its own step attempt, with its own
worker, task timeline, retry boundary, and isolated per-attempt workspace path.

**Each phase asks for a capability and gets the best available agent.** The
phase declares a `Capability`; `select_agents` resolves it against the
registered agents exactly as the Design council does, records the chosen agent,
and records `via_generalist` when nothing more specific covers it. This is the
machinery that already exists — Build simply asks it once per phase instead of
once per stage. Three properties follow, and all three matter more as
specialists are added:

- a deployment with **no** registered specialists still works: every phase runs
  as `general-purpose`, honestly recorded as a generalist stand-in, which is
  exactly today's behaviour and not a regression;
- registering a specialist later changes **who runs which phase and nothing
  else** — no workflow, contract, or UI change, because the phase asked for a
  capability rather than an agent name; and
- the review record names the capability requested *and* the agent that covered
  it, so a reviewer reading "quantitative genetics: general-purpose" knows what
  they are looking at, and a reviewer reading nothing does not.

Capabilities a phase may request are the registered `Capability` values; an
unknown capability is a plan-contract rejection, not a silent fallback to the
generalist. That refusal is the one place selection fails closed, and it is the
same rule the council's roster proposal follows: a silent swap is the bug the
feature exists to prevent.

A phase worker may inspect allowed inputs, write implementation and derived
output only inside its isolated grant, run tools/Bash, diagnose failures, and
perform the validation its phase objective asks for. It may read the outputs of
**preceding** phases in the same plan — that is what makes phase 3 able to fit a
model phase 1 simulated — but it may not modify them; an earlier phase's outputs
are inputs, hash-bound like any other.

Each phase returns a narrow `BuildPhaseResult`, and the server folds the
ordered successful phases into one `BuildExecutionBundle`:

- implementation and configuration paths;
- exact generated output paths, with **figures declared as figures** — each
  carrying its path, a caption, what it is meant to show, and the phase that
  produced it;
- key numeric outcomes as typed `{name, value, unit}` entries rather than prose;
- execution/log paths;
- commands/tools used in bounded form;
- environment and source revisions;
- declared input paths examined, including preceding phases' outputs;
- the rerun procedure — exact command, seed, interpreter/environment — per
  `generic:build:v4`'s `recorded_rerun_procedure`;
- checks the phase ran and their typed results; and
- limitations and deviations.

The server verifies every path, computes hashes itself, rejects escapes or
mutated inputs, and snapshots the successful phase before marking it succeeded.
Workers do not write into `outputs/dbtl` and do not author the final Build deck.

**A phase reports; it does not grade itself.** Per §3.1, a failed check the
phase ran is recorded as a failed check with its detail, not a reason to
withhold the phase. `status=failed` means the work could not be done — the
script would not run, an input was missing, the tool errored — not that the
result was disappointing. A phase that produced outputs it cannot vouch for
returns them with the caveat; Test assesses compliance and the reviewer assesses
meaning.

If an unresolved choice would materially alter the implementation, evidence,
cost, or scientific interpretation, the worker returns `needs_input` before
guessing. It proposes the narrowest answer schema and may recommend a Build work
meeting for a contested/high-impact decision. The server discards any partial
claim of phase success, preserves bounded diagnostic work, and enters the
collaboration flow in §7.

**Between phases is a natural place to stop.** A phase boundary is a committed,
resumable state with no worker lease held, which makes it the cheapest possible
pause. The workflow therefore honours a pending **Hold** or a person's
mid-execution meeting request at the next phase boundary rather than mid-write
(§7.4), and a phase may declare `pause_after: true` in the plan when its result
is one a person should see before the next phase consumes it.

A retry always receives a fresh isolated subdirectory. Failed directories are
retained for bounded audit/diagnosis and never selected as review evidence.

### 5.4 Summarize results

This step cannot invoke Bash or modify the execution bundle. It reads the
server-verified bundle and produces the canonical Build review package. The
model's role is bounded synthesis; deterministic code owns schema validation,
path/hash checks, lineage, and gate-relevant fields.

**Its job is to answer "what did we get", not "what files exist".** The package
leads with:

- **key outcomes** — the typed numbers the phases reported, each named, with its
  unit and the phase and figure that support it;
- **figures worth looking at** — selected from the figures the phases declared,
  each with the summarizer's one-line reading of what it shows, ordered by what
  a reviewer needs first;
- **what the build did**, phase by phase, in a few lines each; and
- **deviations and limitations**, including anything a phase could not verify.

Selection is bounded (start at `MAX_SUMMARY_FIGURES = 8`) and the package
records every declared figure, so choosing a subset for the deck never hides
one from the audit record. The summarizer may only select figures the server
verified; it cannot cite a plot that does not exist, and it cannot describe a
figure it did not receive. It states what a figure shows — it does not state
whether the result is good, which is the reviewer's call.

The result contract uses the canonical evidence kinds
`artifact | workspace_file | dataset | external`. The prompt explicitly names
the approved Design as `artifact`. Narrow, semantics-preserving aliases may be
canonicalized, but arbitrary evidence types still fail closed.

If the summarizer produces invalid JSON, an unsupported field, or incomplete
evidence, only this step fails. The successful phases stay pinned and reusable.
Two deterministic parse/normalization passes are allowed before the step fails,
and a summary that parses but selects **no** figures is a valid summary — some
builds legitimately produce none. Do not silently relaunch an expensive phase to
recover a presentational failure.

The summarizer may also return `needs_input` when the bundle is valid but a
human-owned interpretation is required to describe a deviation or decide whether
the package should be submitted as-is. It may not use human input to rewrite
failed checks as passed.

### 5.5 Prepare review slide deck

This step is a renderer, not a worker. It converts the validated Build review
package into a self-contained, registered HTML slide deck and a bounded chat
summary. It may not invent claims, change outcomes, smooth away deviations, or
select evidence that is absent from the canonical package. It has no sentence of
its own — the same rule the Design council deck follows, and the reason a
renderer cannot quietly improve a result.

**The deck leads with the figures and the numbers.** Slide order is key
outcomes → figures → what the build did → deviations and limitations → how to
re-run it. Deviations come *before* the rerun notes for the same reason the
Design deck puts disagreement before synthesis: a reader who has already
accepted the result will not go looking for the caveat.

Figures are **embedded**, not linked. Registered decks are self-contained HTML
under a strict no-network rule, so each selected figure is inlined as a `data:`
URI with its caption and the summarizer's reading beneath it. Two consequences
to design for rather than discover: a raster figure has to be size-bounded
(cap per figure and per deck; downscale or fall back to a labelled "figure too
large to embed — open it from the workspace" placeholder), and a figure format
the renderer cannot embed is named and skipped rather than silently dropped. A
build with no figures renders its key outcomes and says so plainly; it does not
render an empty gallery.

Registration binds the exact deck bytes to project, cycle, Build stage attempt,
core evidence artifact, evidence revision, originating conversation, renderer
version, and SHA-256. A write or registration failure leaves the review package
intact and retries only this step. Identical input plus renderer version is
idempotent and must reproduce or reuse the same content-addressed output.

Only after registration succeeds does the adapter mark the **workflow** ready
for human review, present the package and deck in chat, and offer the existing
human review path. The Build stage itself stays `in_progress` until the person
submits that evidence through the registered deck; that human action moves the
stage to its existing `awaiting_review` status. The worker and renderer never
submit or approve the stage.

## 6. Native runtime progress

### 6.1 Reuse the existing task event contract

Do not invent Build-only progress messages. Every sub-step emits the existing
native task lifecycle:

- `task_started`
- `task_running`
- `task_completed`
- `task_failed`

Add optional bounded metadata:

```json
{
  "dbtl_stage": "build",
  "dbtl_step": "execute_phases",
  "dbtl_phase_index": 2,
  "dbtl_phase_key": "derive_marker_matrix",
  "dbtl_capability": "software_and_workflow_engineering",
  "stage_run_id": "...",
  "step_run_id": "...",
  "workflow_spec_key": "generic:build-workflow:v1",
  "plan_digest": "..."
}
```

Task ids are stable per step attempt — and a phase attempt is a step attempt —
so retries create an honest new timeline. Display labels come from the server
workflow spec and the recorded plan, never from a frontend string table: a phase
title is the planner's, and a client that invented its own would describe work
that did not happen.

Phase events carry the capability requested and the agent that covered it, for
the same reason the record does. They must not carry `council_seat`: ordinary
Build work is not a meeting, and the frontend keys its meeting copy on that
field.

Deterministic steps normally emit start and terminal events. The planner,
phases, and summarizer additionally emit `task_running` messages for every newly
captured assistant/tool step. These are persisted through the existing
`subagent.step` run-event path so reload and expansion use the same history as
ordinary delegated tasks.

### 6.2 Share the normal subagent streaming adapter

The normal `task` tool already runs `SubagentExecutor` asynchronously, polls
its thread-safe `SubagentResult.ai_messages`, emits each new step, and reports
cumulative usage. `LiveStageAdapter` currently calls `execute` in a thread and
waits for the terminal result.

Extract that polling/step-emission behavior into a shared harness helper used
by both paths. The helper owns:

- background execution and cooperative cancellation;
- incremental message cursor/deduplication;
- sanitized `task_running` events;
- cumulative token usage;
- timeout and terminal mapping; and
- cleanup on success, failure, cancellation, and parent interruption.

Do not duplicate the five-second polling loop in the adapter. A later
condition/queue-based executor improvement should benefit ordinary subtasks and
DBTL steps together.

### 6.3 Give direct DBTL tasks a transcript anchor

The current `SubtaskCard` is mounted only when an assistant message contains a
`task` tool call. Direct DBTL worker events enter the task provider but have no
such message, so they have nowhere to render.

Add one run-scoped **Build workflow block** to `MessageList`:

- while active, place it at the bottom of the transcript;
- once settled, place it immediately above the deterministic Build receipt;
- render the five ordered rows from the server workflow spec, with
  `execute_phases` expanding into its recorded phases as indented child rows —
  each showing its title, its capability, and its state;
- name the agent that covered each phase's capability, marking a generalist
  stand-in explicitly rather than leaving it to be inferred;
- use the existing `SubtaskCard` for phase content and tool/Bash history;
- show queued/waiting deterministic rows without fabricating a subagent;
- render the plan-confirmation and per-phase pause states as collaboration, not
  failure (§7);
- retain a failed phase and its prior attempts after a retry; and
- backfill from the durable workflow read model plus the existing paginated
  run-events endpoint.

Phase rows come from the recorded plan, so the block renders whatever the
planner produced — one phase or eight — without a client-side assumption about
how many there are. A plan that was replaced shows its phases as invalidated
rather than deleting them; the record of what was attempted is the point.

This is composition around the native task UI, not a second step renderer.
Design meeting tasks remain in `DebatePanel`; the two surfaces must not render
the same task twice.

The native card needs one shared improvement before it can satisfy this plan:
pair each tool request with its following tool result and let the tool row open
a bounded request/output detail. Reuse or extract the pairing logic already
used by `meeting-transcript.ts`; do not implement a Build-specific Bash parser.
The same disclosure should work for every ordinary delegated task. A pending
call renders as pending, a result whose request was compacted remains visible,
and truncation is labelled rather than hidden.

### 6.4 What is visible

The expanded sandbox timeline and its tool-step disclosure may show the
already-sanitized native data:

- concise assistant progress text;
- tool name;
- bounded tool arguments already allowed by the native card;
- bounded tool output, including Bash stdout/stderr;
- model label and cumulative token use; and
- terminal structured summary or error.

It must not show system prompts, hidden instructions, chain-of-thought, secrets,
unbounded file bodies, authentication material, host-only paths, or raw
provider payloads. Existing tool-output budgets and sanitizers remain the
boundary; Build must not create a looser copy.

### 6.5 The Build plan in the project rail

Replace the rail's **Blockers** section with a **Build plan** section for the
selected cycle, rendered from the same workflow read model (§9) the conversation
block uses. One source, two projections — a rail that derived its own view of
progress would eventually disagree with the transcript, and the person would
have no way to tell which was right.

**The constraints are the rail's, not this feature's.** `ProjectRailFrame` is
`w-64` — about 224px inside `px-4` — collapses to 48px, does not render below
`md`, and scrolls as one column shared with Cycles, Agents, and Conversations.
A phase row is therefore `StageRow`'s existing shape and nothing more: a
`size-3.5` state icon, a truncated title, and a right-aligned
`text-[11px] text-muted-foreground` state word.

```text
BUILD PLAN · CYCLE 01                    2 of 4

 ✓  Simulate founder population       Done
 ⟳  Derive marker matrix           Running
 ○  Fit and evaluate model          Queued
 ○  Produce figures                 Queued
```

Rules:

- **Read-only.** No retry, no hold, no answer, no confirm. Selecting a phase
  navigates to the originating conversation and scrolls to that phase in the
  workflow block. That is the whole interaction.
- **Truncate, never wrap.** A wrapping title changes the rail's height as work
  moves; the full title lives in `title`/`aria-label` and in the transcript.
- **State in words, never colour alone**, reusing `STATUS_MARK`'s existing tone
  pairs with their `dark:` variants. No new hue family.
- **Capability is not shown here.** There is no room for a third column, and the
  transcript already names it. It rides in the row's accessible name.
- **Bounded.** `MAX_BUILD_PHASES` caps the plan at 8, which is what makes an
  uncollapsed list safe in a shared scroll column. A cycle with no Build plan
  yet shows one muted line rather than an empty heading.
- **One moving indicator**, on the running phase only, carrying
  `motion-reduce:animate-none` — the same rule the agent-activity plan applies
  in the Agents section directly below. Two independently spinning sections in
  a 256px rail is the failure both plans are trying to avoid.

**Blockers do not disappear.** Open work items are how a person learns that
Reconciliation is unsettled, and deleting that surface to make room for build
progress trades one real signal for another. Fold the count into the Cycles
section as a badge on the affected stage row (`Data reconciliation · 2 open`),
which is where the stage it belongs to already is, and keep the full list in the
generic stage sheet that already renders it. If that badge cannot be made to
work, keep a collapsed **Blockers** line beneath the Build plan rather than
dropping the information.

The two rail features land together and must be sequenced deliberately: this
section replaces Blockers, and the agent-activity plan replaces the
`PLACEHOLDER_AGENTS` dot in Agents. Whichever ships second inherits a rail with
one live section already in it, and the "exactly one thing moves" rule has to
hold across both — it is a property of the rail, not of either feature.

## 7. Human collaboration during Build

### 7.0 The plan is the first and cheapest place to intervene

Redirecting a build before it runs costs a sentence; redirecting it afterwards
costs the run. The phase plan therefore creates two interaction points the
current monolithic worker cannot offer.

**At the plan.** With `dbtl.build_plan_confirmation` on, the recorded plan is
presented as a structured card before dispatch — **Start the build**, **Change
the plan**, **Hold here** — showing each phase's title, objective, capability,
and the agent that would cover it, plus the planner's assumptions and open
questions. **Change the plan** carries the person's words verbatim into a
replan; it never paraphrases them into a planner-owned decision. Nothing is
preselected.

**At a phase boundary.** A phase declared `pause_after: true` raises a bounded
card when it completes: **Continue**, **Change the remaining plan**, **Hold
here**. Only phases *after* the current one may be changed this way — rewriting
a phase that already produced bound outputs is a replan, with the invalidation
in §4.3, not an edit.

Both are ordinary Human Input Cards under §7.3's rules, and both leave every
completed phase pinned. Neither is a gate: they change what Build does next,
never whether Build is approved.

### 7.1 One typed request, several presentation formats

A worker does not emit arbitrary UI instructions. When it cannot safely
continue, it returns `status=needs_input` plus one typed
`BuildHumanInputRequest`:

- a focused question;
- why the answer changes Build;
- the exact workflow, step attempt, and input digest it belongs to;
- the requested answer schema: free text, one choice, several choices, or a
  bounded form;
- optional server-validated options/fields;
- whether a Build work meeting is recommended and why; and
- safe fallback actions: choose another format, hold, restart Build, or return
  to Design when the approved Design itself must change.

The server validates and records that object before rendering anything. A
worker cannot invent an endpoint, grant itself a new tool, or mark its own
question answered. Invalid or over-broad input requests fail the step contract
with a bounded diagnostic; they do not render an untrusted form.

The supervisor selects the presentation from the validated schema and the
person's explicit request. The same scientific question may be re-presented in
another format without becoming a different question or losing its audit
binding.

### 7.2 Direct chatbox reply

Use the chatbox for one open-ended correction, explanation, example, or missing
piece of context that does not benefit from enumerated choices.

The conversation shows the focused question and arms the composer with a
dismissible **Replying to Build · `<step>`** chip. The next send carries a
server-recognized response envelope bound to the pending request while the
visible message remains the person's ordinary words. Dismissing the chip sends
nothing and leaves the workflow on hold; it must not reinterpret the person's
next unrelated message as a Build answer.

This is stricter than treating every visible human message after a question as
an answer. A pending Build question must not permanently capture ordinary chat
or make the thread silent. The person may reopen the reply chip from the
workflow block or choose **Hold here**.

The answer is stored verbatim with the input-request id, responder identity,
cycle revision, and predecessor digests. On acceptance, the supervisor creates
a new attempt of the paused step and injects the answer as human-owned context.
It is never paraphrased into an agent-owned decision.

The armed composer keeps its normal input capabilities: text, file
attachments, quoted project artifacts, voice-to-text, and links. Any attached
file is resolved under normal project authorization and bound by server-computed
hash; the response record stores the bounded reference, not an untrusted claim
that a filename is evidence.

### 7.3 Structured Human Input Card

Use the existing Human Input Card when the answer is safer or faster as:

- `single_choice` / `choice_with_other` for one bounded decision;
- `multi_select` for an explicit subset;
- `form` for several related typed values; or
- `free_text` when a visible card is preferable to the armed-composer chip.

Options are never preselected, including recommendations. The card explains
the consequence of each choice, names the Build step it will resume, and keeps
the ordinary composer available. A direct chat answer is accepted only when
the composer is explicitly armed for this request; it does not silently bypass
required typed fields.

Card responses recover project, cycle, workflow, step, request schema, and
digests from the server-emitted request. Client-supplied scope is not authority.
A stale or already-answered card returns an explicit receipt and dispatches
nothing.

### 7.4 Build work meeting for complex decisions

A **Build work meeting** is available when the uncertainty is too complex for
one worker/person exchange, for example:

- several implementation approaches trade scientific fidelity against runtime
  or maintainability;
- the approved Design leaves a technically consequential ambiguity;
- tool/sandbox failures have more than one plausible root cause;
- evidence conflicts across project files or specialists; or
- the owner explicitly asks for a multidisciplinary review before continuing.

This meeting is distinct from the optional **Build review meeting** held after
the Build package exists. The work meeting helps decide how to perform the
current step. The later review meeting annotates completed evidence before the
human gate.

No model starts a work meeting merely by labelling a task complex. A
server-bound card offers **Start Build meeting**, **Answer directly**,
**Use a structured card**, and **Hold here**, with estimated participants,
models, and metered token use. The owner may adjust the roster through the
existing meeting-preflight pattern before confirming dispatch.

The owner may also request a Build work meeting directly from the chatbox while
Build is active. The supervisor records the request immediately but never runs
deliberation concurrently with a sandbox writer. It asks the active step to
pause at its next safe boundary; if the worker cannot checkpoint safely, that
attempt settles as cancelled with its diagnostic output retained but
unselected. Only after the worker lease and write grant are released may the
meeting start. The eventual human decision resumes in a new step attempt.

The meeting receives a bounded, content-addressed context package:

- approved Design and `BuildInputBundle`;
- current workflow/step identity and exact question;
- selected successful predecessor outputs;
- bounded relevant tool failures/log excerpts;
- project-file manifest and declared inputs;
- prior human answers and prior meeting outcomes for this issue; and
- explicit statements of what the meeting may not decide.

Participants may use different configured models, skills, and tools according
to their validated capabilities. During a work meeting, project/stage outputs
are read-only: participants analyze and propose; they do not concurrently edit
the sandbox implementation. The later phase attempt remains the
single writer.

At minimum the meeting contains an implementation position, an independent
challenge/red team, and a chair. It uses the native meeting/task timeline and
participant inspector. The chair returns one of:

- `recommendation_ready` with explicit alternatives, trade-offs, and evidence;
- `needs_input` with one focused human decision; or
- `return_to_design` when continuing would change the approved scientific
  Design rather than merely implement it.

The meeting is advisory. Its recommendation cannot resume Build by itself.
The owner records the chosen option/comment in the meeting deck or answers the
chair through the armed chatbox/card path. That human-owned decision becomes a
`BuildDecisionRecord` bound to the meeting outcome and paused step. A
`return_to_design` outcome offers the governed route back to Design and never
quietly widens Build scope.

One issue/revision may have only one active meeting. A completed meeting is not
reconvened automatically; **Run meeting again** is an explicit human action.
Provider failure or an invalid chair contract leaves the step paused and makes
the same meeting action retryable without rerunning successful participants
where durable results remain valid.

### 7.5 Durable input and decision records

Add a server-owned collaboration record (or extend the step-attempt model with
a normalized child table) so every pause can reconstruct:

- request id, format, schema, question, rationale, and recommendation;
- workflow/step attempt plus bound input/predecessor digests;
- originating thread and parent run;
- response id, exact human response, responder, and timestamp;
- optional meeting, chair outcome, selected option, and comment;
- lifecycle: `open`, `answered`, `held`, `superseded`, `stale`, or `cancelled`;
  and
- the resumed step attempt, if any.

Only one collaboration request may be open for one step attempt. Responses are
idempotent by request plus client submission id. A changed answer under the
same id is a conflict, not a silent overwrite.

### 7.6 Choosing the smallest useful interaction

The default escalation ladder is:

1. continue autonomously when the approved Design and evidence settle the
   implementation;
2. shape the work at the plan or the next phase boundary (§7.0), which is the
   cheapest intervention available and the only one that costs nothing already
   run;
3. direct chat for one open contextual answer;
4. a structured card for bounded or multi-field input; and
5. a human-confirmed Build work meeting for contested/high-impact questions.

This is guidance, not a restriction on the person. The owner can ask for a
meeting immediately, switch a proposed meeting to a direct answer, or request
a card instead. The worker may recommend a format but cannot deny another safe
format supported by the answer schema.

## 8. Failure and retry interaction

### 8.1 Failure taxonomy

Use bounded server-owned error codes so UI and telemetry can distinguish:

- `design_missing_or_stale`
- `design_unreadable`
- `plan_contract_rejected`
- `plan_capability_unknown`
- `plan_not_feasible`
- `sandbox_setup_failed`
- `sandbox_execution_failed`
- `worker_timed_out`
- `execution_contract_rejected`
- `execution_output_missing`
- `input_changed_during_execution`
- `summary_contract_rejected`
- `review_package_write_failed`
- `deck_render_failed`
- `deck_registration_failed`
- `cancelled`
- `internal_error`

The display summary names the failed step and next safe action. Full bounded
diagnostics stay in the step record/run events, not in the final assistant
paragraph alone.

`needs_input` is deliberately absent from this taxonomy. It renders as
**Waiting for you**, carries its own collaboration request, and does not count
against worker failure telemetry.

So is **a check the work did not pass.** Per §3.1 that is a recorded result, not
a step failure: it travels in the phase's checks and the package's limitations,
reaches Test as evidence, and reaches the reviewer as something to weigh. There
is deliberately no error code for "the result looked wrong", because no code
here should be able to stop a Build on a judgement that belongs to a person.

### 8.2 Server-bound retry card

After a failure the supervisor appends a native Human Input Card:

- **Retry `<failed phase or step>`** — reuse valid predecessors, including every
  earlier successful phase, and create a new attempt for the failed one;
- **Replan the build** — keep the loaded Design, discard the current plan and
  its phases (§4.3), and return to `plan_build`;
- **Restart Build** — invalidate the selected Build workflow and begin again at
  `load_design`; and
- **Hold here** — leave the Build in progress with no dispatch.

**Replan** exists because the honest answer to a repeatedly failing phase is
often that the decomposition was wrong, and the alternative — restarting the
whole Build to change one phase — is expensive enough that nobody does it. It
states plainly that completed phases will be discarded, since that is the cost.

The request binds project, cycle, stage-run, workflow spec, failed step-run,
cycle revision, and predecessor digests. The backend recovers those fields from
the card it emitted; client context is not authority. A stale card expires with
an explicit receipt and no dispatch.

The retry card, not the project rail or workflow block, owns mutation. This
keeps the chatbox/card interaction model and makes the decision durable in the
conversation.

### 8.3 Resume algorithm

On Start, Retry, process restart, or duplicate delivery:

1. load the stage run and workflow spec under project/cycle scope;
2. select the latest valid success for each step;
3. recompute input digests and invalidate stale downstream selections;
4. find the earliest step without a valid success;
5. replay an already-committed idempotency key without dispatch;
6. acquire the one-running-attempt constraint for that step;
7. execute and commit one step at a time;
8. stop on `needs_input`, commit the collaboration request, and wait without
   holding a worker lease;
9. resume the same step in a new attempt only after a bound human response; and
10. stop immediately on failure, emitting the retry card.

Successful predecessors are never rerun merely because a later step failed.
Retries append attempts; they never overwrite the failed record.

## 9. API and read model

Add one authenticated stage-workflow read model scoped by project and cycle. It
returns:

- workflow and Build stage-run identity;
- ordered step definitions;
- the recorded `BuildPhasePlan` — its digest, feasibility verdict, rationale,
  assumptions, open questions, and ordered phases with title, objective,
  capability, resolved agent, and `via_generalist`;
- selected attempt and prior terminal attempts per step and per phase;
- the key outcomes and selected figures once `summarize_results` succeeds, as
  bounded metadata plus artifact references — never image bytes, which the
  artifact endpoint already serves under project authorization;
- status, bounded error, timestamps, digests, run id, and task id;
- an open collaboration request, its allowed response formats, and any bound
  Build work-meeting state;
- whether Retry and Restart are currently legal; and
- the final review package/deck bindings when complete.

The endpoint does not return raw prompts, secrets, full shell logs, or arbitrary
model output. Detailed task steps continue to come from the existing
authenticated run-events endpoint by `(thread_id, run_id, task_id)`.

Mutation stays in the supervisor/card continuation path for the first release.
Do not add a generic unaudited `POST /retry` button endpoint merely because the
read model exists.

## 10. Run and stage semantics

A controlled Build-step failure is not necessarily an infrastructure failure:
the parent LangGraph run may finish normally after recording the failed step
and retry card. The UI must therefore show two distinct facts:

- **Run completed** — orchestration returned a durable response; and
- **Build paused at Sandbox execution** — the governed workflow did not
  complete.

Never present the first as Build success. The Build stage remains `in_progress`
until every step succeeds and the review package/deck are registered. Test
stays locked until the later human Build decision opens it.

A paused input request similarly leaves Build `in_progress`. The parent run may
complete after posting the question, while the workflow reads **Waiting for
you**. Answering starts a new run/step attempt; it does not resume an expired
process or depend on an in-memory worker.

## 11. Delivery phases

### Phase 0 — Current-worker observability

Before changing persistence or contracts, route the existing monolithic Build
worker through the shared native subtask streaming helper and give direct DBTL
tasks a transcript anchor. Add the shared native tool-step request/output
disclosure to `SubtaskCard`. This immediately exposes reads, tools, Bash,
output, and the exact terminal contract failure for DBTL and ordinary delegated
tasks. It does not yet provide step-level retry.

### Phase 1 — Workflow contract and persistence

Add the versioned workflow spec, typed step outputs, append-only step-attempt
table (including phase identity and capability fields), repository projection,
digest/invalidation rules, and authenticated read model. Keep the existing Build
path behind the rollout switch.

### Phase 2 — Split Design loading and sandbox execution

Move approved-Design resolution into `load_design`; narrow the Build worker to
implementation/execution; snapshot and verify its result. Stop discarding
successful sandbox work when later presentation fails. Still one execution unit
at this point — the plan arrives next.

### Phase 3 — Split summary and deck publication

Introduce the read-only summarizer and deterministic Build deck renderer,
including declared figures, key outcomes, and embedded-figure rendering with its
size bounds. Mark the workflow ready for human review only after both succeed;
keep the Build stage in progress until the owner submits the registered
evidence.

### Phase 4 — Phased plan and specialist phases

Add `plan_build`, the typed `BuildPhasePlan`, feasibility verdicts including
`single_phase`, plan-digest invalidation, per-phase attempts, and capability
selection per phase through the existing `select_agents`. Add the
plan-confirmation card and `pause_after` boundaries. This phase is where a Build
stops being one opaque worker; sequencing it after 2–3 means the retry and
presentation boundaries already work before phases multiply the units they
apply to.

### Phase 5 — Build plan in the project rail

Replace the Blockers section with the read-only Build plan projection, relocate
the open-work-item signal into the Cycles section, and reconcile the
one-moving-indicator rule with the agent-activity plan's Agents section.

### Phase 6 — Human collaboration and Build work meetings

Add typed `needs_input`, the explicitly armed composer reply, structured-card
selection, durable collaboration records, and the human-confirmed Build work
meeting. Reuse the native meeting roster/task/deck patterns while keeping the
meeting read-only over execution outputs.

### Phase 7 — Failure cards and targeted retry

Add the server-bound Retry/Replan/Restart/Hold card, resume algorithm,
stale-card handling, attempt history, cancellation, and process-restart
recovery.

### Phase 8 — Manual-profile cutover and hardening

Enable `dbtl.build_workflow_steps` first in the isolated manual DBTL profile.
Exercise every failure boundary, capture a new `design-approved` fixture pinned
to the new schema, and compare telemetry before enabling it generally. Retain
the monolithic fallback for one rollback window, then remove it and its
characterization tests.

## 12. Verification

### Backend contract and repository tests

- Workflow step order and keys are versioned and deterministic.
- Step attempts are append-only; one running attempt per step is enforced at
  the database boundary.
- Every output digest binds the exact selected predecessor attempts.
- An upstream digest change invalidates all and only its descendants.
- A duplicate execution key returns the committed step without redispatch.
- A failed summary can reuse a successful execution bundle.
- A failed deck registration can reuse the exact review package.
- Missing or changed sandbox outputs invalidate execution rather than letting a
  stale summary proceed.
- `needs_input` commits no successful output, releases execution ownership, and
  resumes only through a new attempt bound to an authenticated response.
- Human responses cannot be replayed against another cycle, step, question,
  input digest, or superseded request.
- A meeting recommendation alone cannot resume Build; only its bound human
  decision can.
- A meeting conclusion that changes the approved scientific Design offers the
  governed return-to-Design route and cannot be folded into Build context.
- Unknown evidence kinds still fail closed; the explicit Design-artifact
  canonicalization does not accept arbitrary `*_document` values.
- No successful combination can offer Build submission/review without every
  selected step success, every planned phase succeeded, and a registered deck.

### Plan, specialist, and figure tests

- A plan is bounded, ordered, and stable-keyed; an over-long or unparseable plan
  degrades to `single_phase` with a recorded note rather than failing Build.
- `single_phase` is a success path and is never rendered or recorded as a
  failure.
- An unknown capability is refused at the plan contract and never silently
  becomes `general-purpose`.
- With no registered specialists, every phase runs as `general-purpose` with
  `via_generalist` recorded; registering one changes which agent runs a phase
  and changes nothing else about the workflow, contract, or read model.
- A phase may read a preceding phase's outputs and cannot modify them; a
  modified predecessor output invalidates rather than being accepted.
- Retrying phase *n* leaves phases before it selected and untouched.
- A new plan invalidates every phase beneath it and never rebinds an old phase
  output into a new plan.
- Figures are declared with captions, verified server-side, and cannot be cited
  by the summarizer unless they exist; an unverifiable figure fails closed.
- A build with no figures produces a valid package and a deck that says so.
- Figure selection is bounded, and every declared figure survives in the package
  even when the deck shows a subset.
- A figure too large or in an unembeddable format is labelled and skipped, and
  the deck stays self-contained with no network reference.

### Guardrail-posture tests

- A phase whose own check failed still returns its outputs, with the failed
  check and its detail recorded; it does not report `status=failed`.
- A worker cannot mark a Build unreviewable on a judgement Test or a human owns:
  there is no error code for an implausible result.
- An invalid summary, an empty figure selection, or a failed deck render leaves
  every phase pinned and reusable.
- Provenance failures still fail closed: path escape, mutated input, missing or
  hash-mismatched output, unverifiable evidence reference, or any attempt to
  satisfy a gate.

### Runtime/event tests

- Direct DBTL workers emit incremental `task_running` assistant and tool steps,
  not only start/terminal.
- Bash output is bounded/sanitized through the same path as an ordinary
  delegated task.
- Cancellation and timeout emit one terminal task event and one terminal step
  attempt.
- Live and persisted task timelines converge after reload, event replay, and a
  replay gap.
- Meeting tasks and Build workflow tasks are not rendered twice.
- Hidden prompts, reasoning, secrets, and host paths never enter task events or
  the workflow read model.
- A paused step survives process restart with no live worker or permanent
  activity spinner.
- Work-meeting participants appear through the existing meeting lifecycle and
  cannot write into the active sandbox execution directory.

### Frontend tests

- The five step rows and their phase children render in server order and always
  carry text status, not colour
  alone.
- The active row updates without remounting prior completed rows.
- Expanding the sandbox row shows interleaved assistant/tool steps and the
  paired output; opening a Bash tool row shows its bounded command and
  stdout/stderr through the shared native task disclosure.
- A failed summary leaves sandbox execution visibly successful.
- Retry appends a new attempt beneath the failed step and preserves the failed
  attempt for audit.
- The armed chatbox reply chip binds only the next explicit Build response and
  can be dismissed without capturing later ordinary chat.
- Free text, choice, multi-select, and form requests render through the native
  Human Input protocol and restore correctly after reload.
- A proposed Build meeting shows roster/cost before dispatch, and its result is
  visibly advisory until the owner records a decision.
- **Waiting for you** is distinct from failed and carries no failure styling.
- Active placement is at the transcript bottom; settled placement is above the
  final Build receipt.
- Reload reconstructs the workflow from the read model and lazily backfills
  task details from run events.
- Phase rows render from the recorded plan, so one phase and eight phases both
  render correctly with no client-side assumption about the count.
- The plan-confirmation card preselects nothing and carries the person's own
  words verbatim into a replan.
- The rail's Build plan section renders at 224px without wrapping, states each
  phase in words, is read-only, and navigates to the transcript on select.
- Exactly one indicator animates across the whole rail, counting both the Build
  plan and the Agents sections, and none under `prefers-reduced-motion`.
- The open-work-item signal survives the Blockers replacement and is still
  reachable.
- Key outcomes and figures render in the settled workflow block and the deck,
  and a build with no figures renders without an empty gallery.
- Long Bash output, narrow viewports, reduced motion, and keyboard/screen-reader
  navigation remain usable.

### Manual acceptance scenarios

1. Happy path: the plan card appears with its phases and their capabilities; on
   Start, each phase advances in order in both the transcript and the rail;
   expansion shows tool and Bash activity; the package and deck lead with key
   outcomes and figures; the workflow offers human submission while Build
   remains in progress until the owner submits it.
2. Bad workspace path: phase 2 fails with its command/output; Retry creates a
   fresh sandbox attempt for that phase alone and phase 1 stays green.
3. Invalid structured summary: every phase stays green, `summarize_results`
   fails, and Retry does not execute any phase again.
4. Deck write/registration failure: steps 1–4 stay green and only step 5 reruns.
5. Design changes after a failed Build: the old workflow is invalidated and
   resume starts at step 1.
6. Refresh during execution: the same task remains active and its prior steps
   backfill without duplicate rows.
7. Stop/cancel: the current step settles as cancelled, predecessors remain
   reusable, and the owner receives a bounded recovery choice.
8. Duplicate Retry click or network replay: one new attempt runs.
9. Open question: the worker pauses, the composer is explicitly armed, a chat
   answer resumes the same step in a new attempt, and an unrelated message after
   dismissing the chip does not answer it.
10. Bounded choice/form: the card binds the exact request; reload preserves it;
    a stale response dispatches nothing.
11. Complex implementation decision: the owner confirms a Build work meeting,
    participants inspect the bound context without writing, the chair presents
    alternatives, and only the owner's selected decision resumes execution.
12. Meeting finds a Design change: Build remains paused and offers Return to
    Design rather than treating the recommendation as implementation input.
13. Indivisible work: the planner returns `single_phase`; the plan card, the
    transcript, and the rail all present it as a normal Build rather than as a
    degraded one.
14. Change the plan: the owner rejects the proposed decomposition in their own
    words, the replan carries those words verbatim, and the new plan replaces
    the old one in both the transcript and the rail.
15. Replan mid-build: after a repeatedly failing phase the owner replans; the
    card states that completed phases will be discarded, and the discarded
    phases remain visible as invalidated rather than vanishing.
16. Specialist arrives: register a specialist covering one phase's capability
    and re-run; that phase names the specialist instead of a generalist
    stand-in, and nothing else about the workflow changes.
17. Disappointing result: a phase's own check fails; the phase still returns its
    outputs, Build still becomes reviewable, the failed check is visible in the
    package and the deck, and the human gate — not the worker — decides.
18. No figures: a build that plots nothing produces a valid package and a deck
    that says so, with its key outcomes intact.

## 13. Rollout signals

Track, without raw scientific content:

- failure count and duration by step/error code;
- retry success rate by step;
- fraction of retries that incorrectly reran an earlier successful step
  (target: zero);
- time from Build start to first visible task step;
- workflows stranded with a running step after lease recovery;
- summary-contract rejection rate;
- deck-render/registration failure rate; and
- collaboration requests by format, answer latency, and stale/superseded rate;
- phase-count distribution, `single_phase` rate, and plan-degradation rate;
- plan confirmation outcomes: started, changed, held;
- share of phases covered by a real specialist versus a generalist stand-in,
  which is the number that should move as specialists are registered;
- replan rate and how many phases each replan discarded;
- figures declared, figures selected, and figures skipped as unembeddable;
- Build meeting offer, acceptance, completion, and retry rates;
- percentage of meetings whose recommendation still required return to Design;
  and
- discrepancy between parent-run terminal status and Build workflow status.

## 14. Non-goals

- Altering the Design, Build, or Test human authority model.
- Turning the project rail into a control surface or log console (it remains read-only).
- Running phases concurrently (DAG orchestration goes beyond this scope).
- Allowing Build workers to mandate their own scientific adequacy or overrule human judgement.
- Inventing new custom components on the frontend where existing UI (`TodoList`, `SubtaskCard`, `HumanInputCard`) satisfies the need.

## 15. Completion criteria

Complete when:
- Users can review the phased plan before it runs.
- Execution is observable via the native `SubtaskCard` and project rail summary.
- Users can retry specific phases without losing valid prior work.
- Build meetings can be convened for complex resolutions without mutating sandboxes automatically.
- Outputs prioritize visual figures / metrics over file paths.
- Execution uses existing UI models, retaining uniformity.
- The worker **always** submits completed (or partially limited) work for human/Test judgement instead of hiding it behind synthetic validation failures.
