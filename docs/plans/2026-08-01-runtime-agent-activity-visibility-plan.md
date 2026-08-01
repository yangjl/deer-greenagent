# Runtime agent activity and dispatch-lineage visibility

**Status:** Proposed standalone implementation plan. No implementation is
authorized by this document.

**Date:** 2026-08-01 · design pass 2026-08-01

**Backend pass:** §3–§5, §7, and the backend half of §8–§9 were re-checked
against the runtime as built. Four claims did not survive: there is **no
run-event retention policy** to inherit; the run worker's own custom-event
persistence is **root-namespace-only**, and the ordinary branch is a subgraph;
a **fenced worker cannot write its own terminal transition**, which is exactly
the multi-worker case the durable projection exists for; and the cross-run read
model needs a **new store method on four implementations**, not a filter on an
existing one. §5.5 adds the contract, limit, and CI-gate obligations the plan
did not name.

**Design pass:** §1, §2, §6, and the frontend half of §8–§11 were revised
against the rail as it is actually built (256px, collapsible to 48px, absent
below `md`, one shared scroll column). The compact surface changed from a
fixed-height auto-following log to a fixed-height presence-and-lineage block,
history moved into an existing Sheet, visible names were separated from internal
identifiers, motion was reduced to a single indicator reusing the codebase's
existing spinner, and the live-region, reduced-motion, collapsed-rail, and
mobile gaps were closed or explicitly scoped out. The backend contract (§3–§5,
§7) is unchanged.

**Decision:** Replace the project rail's static Lead Agent dot with a compact,
conversation-scoped activity window backed by server-authored events. The
window advances over time as actors route, think, compute, dispatch, wait, and
finish. A person can expand it and scroll through the ordered history while
retaining the dispatch tree, without exposing prompts, chain-of-thought, tool
arguments, or worker output.

## 1. Outcome

While a run is active, the **Agents** section should answer four questions —
the first three at a glance, the fourth in one click:

1. who is active now;
2. whether that actor is routing, thinking, computing, coordinating, or
   waiting; and
3. which runtime actor dispatched or selected it; and
4. what happened immediately before the current activity.

The rail is **256px wide** (`w-64`), collapses to **48px**, does not render below
the `md` breakpoint, and scrolls as a single column shared with Cycles,
Blockers, and Conversations. Every mockup below is drawn at the ~224px of
content width that actually remains inside `px-4`. See §6.1 for why those
constraints, not the event model, decide the shape of this surface.

An ordinary conversation looks like this:

```text
AGENTS                              Live

 ⟳  Lead agent                  Thinking

 Activity · 6 steps                    →
```

Once the Lead Agent delegates, the moving row moves down to the actor that is
actually working, and its authority is stated in words above the fold:

```text
AGENTS                              Live

 ⟳  Research subagent          Computing
    via Lead agent

 Activity · 9 steps                    →
```

A governed Build run with two parallel workers stays exactly as tall:

```text
AGENTS                              Live

 ⟳  Build worker 2             Computing
    via Build stage · Cycle 01
    Also running: Build worker 1

 Activity · 14 steps                   →
```

Only the **expanded** surface renders history and the dispatch tree, and it has
the width to do it honestly:

```text
AGENT ACTIVITY — Cycle 01                    [Close]

  Current actors
    Cycle supervisor                     Routing
      └─ Build stage                Coordinating
         ├─ Build worker 1             Thinking
         └─ Build worker 2            Computing
            Dispatched by Build stage

  Earlier                                10:42
    10:42:05  Build worker 1        Started
    10:42:04  Build stage           Preparing Build
    10:42:03  Cycle supervisor       Routing
```

The compact block is deliberately **not a scrolling log**. Questions 1–3 are
answered in place, in three fixed lines: who is working now, what they are
doing, and who authorized them. **Question 4 moves behind the link** — the
count of steps is a door, not a viewport, and history, ordering, pagination, and
the sibling tree all live on the other side of it. That is the one thing this
design gives up relative to the original sketch, and §6.2 records why the trade
is worth taking on a 224px peripheral surface.

Parallel workers stay visible as siblings in the expanded tree, and the compact
block names them on its third line rather than growing. When the run settles,
the single moving indicator stops and the block reads **Lead agent · Completed**
or **Build · Failed** with no animation anywhere. Switching conversations
immediately switches the activity block; work from one thread must never appear
in another.

## 2. Correct the product vocabulary first

Not every runtime component in the requested list is an agent and not every
active component is thinking.

| Runtime actor | What it actually does | Visible name | Honest visible states |
| --- | --- | --- | --- |
| Lead Agent | Converses, invokes a model and tools, and may delegate | Lead agent | Thinking, Computing, Dispatching, Waiting |
| DBTL Supervisor | Deterministically resolves the governed route and invokes one branch | Cycle supervisor | Routing, Dispatching, Waiting |
| Stage adapter (`LiveStageAdapter`) | Validates stage authority, builds work units, dispatches workers, validates results, and records evidence | `<Stage>` stage — e.g. **Build stage** | Preparing, Coordinating, Recording evidence, Waiting |
| Lead-delegated subagent | Performs a bounded delegated task using a model and tools | Its subagent name | Thinking, Computing |
| DBTL stage worker | Executes one `WorkUnit` through `SubagentExecutor` | `<Stage>` worker `<n>` | Thinking, Computing |
| Meeting participant | Same `WorkUnit` path, inside a convened meeting | Its validated seat label | Thinking, Computing |

**No visible string may be an internal identifier.** The repo's cross-cutting
convention is plain words, and it has already been applied once here: the
council renders everywhere as a **meeting**. `LiveStageAdapter` is a class name,
and "DBTL supervisor" and "stage adapter" are architecture vocabulary that no
breeder has a reason to learn. The visible-name column above is the contract;
the `actor_kind` enum in §3 keeps the internal vocabulary stable underneath it,
exactly as `council_*` identifiers survive under "meeting" today.

The UI must not label the Supervisor or adapter as **Thinking** merely because
the overall request is still running. If the Supervisor makes a bounded model
call for interpretation, the public label remains **Routing**; the hidden call's
prompt, reply, and reasoning stay out of the parent stream as required by the
DBTL `nostream` contract.

The section title stays **Agents**. Adapter and supervisor rows carry a small
muted **Runtime** role label so the interface does not imply that every row is
an autonomous model — and, because the rail cannot afford a second column, that
label appears only in the expanded surface and in the accessible name, never in
the compact block, where the row is already qualified by "via …".

Replacing the dot does not deliver the per-project agent roster that the current
placeholder promises. Keep one muted line under the activity block — *"Breeding
agents are assigned per project in a later cycle."* — so a deferred feature is
not silently cancelled by a different one shipping in its place.

## 3. Activity model

Introduce one versioned, server-owned `agent_activity` custom-event envelope.
It describes a transition, not a transcript:

```json
{
  "type": "agent_activity",
  "version": 1,
  "transition": "started",
  "activity_id": "act_...",
  "parent_activity_id": null,
  "run_id": "run_...",
  "actor_kind": "dbtl_supervisor",
  "actor_id": "dbtl-supervisor",
  "display_name": "Cycle supervisor",
  "state": "routing",
  "operation": "Selecting the next DBTL action",
  "dispatcher_activity_id": null,
  "scope": {
    "cycle_id": "...",
    "stage": "build",
    "task_id": null
  }
}
```

### 3.1 Required fields

- `activity_id` is unique for one actor invocation, not just for the actor
  type. A retry or later turn receives a new id.
- `parent_activity_id` defines the visual execution tree.
- `dispatcher_activity_id` records authority lineage. It is normally the
  parent, but remains separate because a runtime wrapper may visually group
  work without being the component that authorized it.
- `actor_kind` is one of `lead_agent`, `dbtl_supervisor`, `stage_adapter`,
  `subagent`, or `stage_worker` in version 1.
- `state` is a bounded enum: `routing`, `preparing`, `thinking`, `computing`,
  `dispatching`, `coordinating`, `recording`, `waiting`, `completed`,
  `failed`, `cancelled`, or `interrupted`.
- `operation` is a short server-owned display label selected from bounded
  templates. It is not arbitrary model prose.
- `scope` contains only identifiers needed to reconcile activity with the
  current run, cycle, stage, and existing task ledger.

**One concept, two names, matching the existing precedent.** `agent_activity`
is the live custom-event stream name; `runtime.agent.activity` is the persisted
catalog name. That split already exists — `task_started` persists as
`subagent.start` — and it is deliberate: the stream name is what
`emit_custom_event` dispatches on and what `astream_events` consumers match,
while the catalog name carries the storage category. Both must be introduced
together or the projection and the replay describe different things.

The envelope must fit the persisted limits in `deerflow/constants.py`:
`event_type` ≤ 32 characters (`runtime.agent.activity` is 22) and `category`
≤ 16. Use a dedicated `activity` category, for the same reason `subagent` has
one: `list_messages` — the thread feed — filters by category, so anything
sharing `message` or `trace` would surface in a conversation. No migration is
needed; `run_events` rows are generic over event type.

Transitions are `started`, `updated`, and one terminal transition from
`completed`, `failed`, `cancelled`, or `interrupted`. Each event also receives
the run-event store's monotonic sequence number and server timestamp. Both are
required for chronological history: sequence is the ordering authority and
timestamp is only the human-readable label. The reducer is idempotent: repeated
events do not duplicate rows, older sequences cannot overwrite newer state,
and a terminal activity cannot be reopened.

The frontend derives two projections from the same events:

- **current tree:** the latest state of each active actor, grouped by parent and
  dispatcher; and
- **activity timeline:** one concise row for every meaningful state transition,
  ordered by persisted sequence.

Repeated updates that do not change actor, state, operation, or lineage are
coalesced. This prevents token streaming and polling from turning the compact
window into noise.

### 3.2 Data that must never appear

The envelope must reject or omit:

- model prompts, hidden instructions, or chain-of-thought;
- tool arguments, tool results, shell commands, and file contents;
- raw worker results or Design-meeting payloads;
- credentials, connector metadata, local host paths, or sandbox internals; and
- unbounded user or model text.

The detailed subtask and meeting surfaces can continue showing their already
sanitized evidence. The project rail is presence and lineage, not a second
debug console.

## 4. Dispatch ownership

The backend, not the browser, records who dispatched whom.

| Child actor | Dispatcher / selector | Instrumentation boundary |
| --- | --- | --- |
| DBTL Supervisor | Run runtime after `graph_enabled` selects the supervisor graph | Supervisor run entry |
| Lead Agent on ordinary work | DBTL Supervisor when the supervisor graph routes to `ordinary`; run runtime otherwise | `ordinary` supervisor node or lead run entry |
| Stage adapter | DBTL Supervisor continuation branch | Around `stage_adapter.execute(...)` |
| Lead-delegated subagent | Lead Agent | `task_tool.py` before `SubagentExecutor.execute_async(...)` |
| DBTL stage worker | Stage adapter | `LiveStageAdapter` before executing each `WorkUnit` |
| Design/Test meeting participant | Stage adapter | Same WorkUnit boundary, with the existing validated meeting-seat identity |

No frontend inference from display names, card types, or `dbtl_stage` is
authoritative. In particular, a `task_started` event only proves that a worker
started; the new parent linkage says whether it came from the Lead Agent or the
stage adapter.

## 5. Backend implementation

### 5.1 A small activity API

Add a harness-level activity helper beside
`deerflow.utils.custom_events`. It should:

- allocate an activity id;
- emit sanitized start/update/terminal events;
- carry the current activity id through `RunnableConfig` runtime context;
- provide an async context manager that guarantees a terminal event on normal
  return, exception, or cancellation; and
- derive only bounded display labels from actor kind and operation enum.

Activity context is runtime-only, and the mechanism matters: carry it in
`context` (runtime) and **never** in `configurable`, which is checkpointed —
the same rule `dbtl_selected_cycle_id` follows, for the same reason. A value
accepted from `configurable` would survive into later turns and keep asserting
a lineage that has ended. `build_run_config` already strips `__`-prefixed
context keys from client input; activity keys ride that convention or get an
explicit strip beside `apply_project_scope_context`.

Emission must go through `emit_custom_event` / `aemit_custom_event`, not a bare
`StreamWriter` — the writer alone is the documented invariant violation, and an
async graph hook must await the async form rather than dispatching synchronously
on a running loop.

**The `nostream` guarantee is not inherited here.** `RunJournal` honours
`TAG_NOSTREAM` for prompts, responses, and tool results; custom events do not
pass through it. The Supervisor's bounded interpretation call, the Design
council's seat prompts, and every one-shot in `run_oneshot_llm` are invisible to
the journal but would be perfectly visible to a naive activity emitter attached
to model callbacks. The activity layer therefore enforces its own rule, and §9
tests it as a new property rather than a regression.

**Subagent context crosses an isolated event loop.** `_copy_isolated_subagent_
context()` copies ambient ContextVars into the persistent subagent loop but
strips handlers marked `deerflow_loop_bound` (that marker exists because
`RunJournal` owns parent-loop tasks and a SQL pool). If the activity id travels
as a ContextVar it survives; if any part of it travels as a callback handler
that touches the store, it must carry the loop-bound marker and the child must
report through the parent instead — which is what `SubagentTokenCollector`
already does.

### 5.2 Supervisor and Lead Agent

Instrument `deerflow.agents.dbtl.supervisor` at graph boundaries:

- open **DBTL Supervisor · Routing** before `route` resolves a branch;
- update it to **Dispatching** once the branch is selected;
- create **Lead agent** under it for `ordinary`; or
- create **Stage adapter** under it for stage continuation.

For direct, non-DBTL Lead Agent runs, the run worker creates the root Lead
activity. Avoid creating both a runtime Lead row and a supervisor-child Lead row
for the same invocation.

Use model and tool lifecycle callbacks to switch a model-backed actor between
**Thinking** and **Computing**. Emit only state changes, not token chunks or
every tool progress frame. When the Lead Agent delegates, set it to
**Dispatching** and then **Waiting** while the delegated child is active.

### 5.3 Stage adapter and workers

Wrap `LiveStageAdapter.execute` as one **Stage adapter** activity. Use bounded
adapter phases already present in its control flow:

- Preparing;
- Coordinating `<stage>`;
- Recording evidence; and
- Waiting for worker or human input.

Do not manufacture a stage worker from the adapter itself. Each real
`WorkUnit` gets one child `stage_worker` activity and reuses its `unit_id` as the
existing task linkage. Parallel `asyncio` dispatch naturally produces sibling
activities.

Extend existing `task_started`, `task_running`, and terminal task events with
`activity_id`, `parent_activity_id`, `dispatcher_activity_id`, and a validated
worker kind. Keep these additions optional during rollout so older clients
continue to parse the task ledger. The activity event is the rail contract; the
task events remain the detailed worker-progress contract.

### 5.4 Durable projection and reload

Live custom events alone are insufficient: refresh, reconnect, hidden
post-approval runs, and stream replay gaps would leave a false green dot.

Add `runtime.agent.activity` to the canonical run-event catalog and persist its
bounded payload through the existing `RunEventStore`. Batch the writes the way
`_SubagentEventBuffer` does — `put_batch`, flushing on a terminal transition, at
a threshold, and in the worker's `finally`. This is not an optimization: `put()`
is a documented low-frequency path that takes a per-thread advisory lock per
call, and activity is the highest-frequency event type this system has proposed.

Three things below were assumed and are not true.

**The worker's custom-event persistence is root-namespace-only, and the
ordinary branch is a subgraph.** `_publish_stream_item` returns early for any
frame carrying a namespace — it never reaches `subagent_events.add(chunk)`.
Today that is harmless because the web frontend streams with
`stream_subgraphs=False`, which flattens everything to the root (the ordinary
branch's namespace survives only in frame *metadata*). But the worker's own
persistence rides the same loop as the client's stream, so **a run whose client
requested subgraph streaming would persist no activity at all** — and §6.7's
"absence of data is unavailable, not active" would be reporting a gap caused by
an unrelated client option. Persistence must not depend on what a client asked
to stream: either capture activity before the namespace branch, or persist it
through a path that does not read `namespace`. Subagent step persistence has
the same shape today; the difference is that this plan promises a durable
projection specifically to remove false spinners, so it cannot inherit the hole.

**A fenced worker cannot write its own terminal transition.** When a lease
expires, `ownership_lost` fences the worker and it performs no further journal,
completion, status, or checkpoint writes — the peer recovery path owns the
terminal receipt. That is precisely the multi-worker case a permanent spinner
would come from. Terminal activity must therefore also be emitted from the
recovery path, through `RunManager.on_orphans_recovered` (the existing generic
callback, kept generic to avoid a harness→app dependency), alongside the
`stop_reason=orphan_recovered` terminalization. The worker's `finally` covers
the ordinary case; it is not sufficient on its own.

**There is no run-event retention policy to follow.** `RunEventsConfig` has
`backend`, `max_trace_content`, and `track_token_usage` — no TTL, no pruning,
no expiry. §6.7's "retention follows the run-event policy" and its "earlier
activity is no longer retained" boundary have nothing behind them, so today the
honest reading is that activity rows accumulate indefinitely for the highest-
volume event type in the system. Resolve it explicitly: either bound the rows
(a per-thread cap or an age-based prune, which is new work and belongs in Phase
5), or state plainly that activity inherits the same unbounded growth as every
other run event and that the UI's expiry boundary is dead code until a policy
exists. Do not ship the sentence that implies a policy is enforcing something.

**The cross-run read model is a new store method, not a filter.** `list_events`
is run-scoped and already takes `event_types`, `task_id`, and `after_seq`; there
is no thread-scoped-across-runs events read. Adding one means the abstract base
plus all three implementations (`memory`, `jsonl`, `db`) — a `db`-only signature
raises `TypeError` on the other two at runtime, not at import. The existing
authenticated `GET /api/threads/{thread_id}/runs/{run_id}/events` serves one run
and is enough for Phase 2; the paginated cross-run endpoint is app-side (the
harness owns the store method, `app/gateway/routers/` owns the route) and uses an
opaque cursor over the persisted sequence.

### 5.5 Contracts, limits, and gates this must satisfy

Adding a run event is not a one-file change. The repo enforces cross-layer
agreement in CI, and the plan named only one of the five artifacts:

| Artifact | What it owns |
| --- | --- |
| `deerflow/constants.py` | Persisted envelope limits (`event_type` 32, `category` 16) |
| `runtime/events/catalog.py` | Validated runtime definition and category |
| `contracts/run_event_stream_contract.json` | Payload schema, storage semantics, compatibility rules |
| `backend/docs/RUN_EVENT_STREAM.md` | The human-readable contract |
| `tests/test_run_event_stream_contract.py` | Conformance — requires **both** views and every producer group to agree |

The conformance test is the gate: producer code and the JSON contract changing
independently fails CI, which is the intended behaviour and the reason to write
the contract entry first rather than last.

Two more gates apply. **Blocking IO**: `tests/blocking_io/` runs a strict
Blockbuster context over `app.*` and `deerflow.*`, and
`test_jsonl_run_event_store.py` already anchors that store's file IO behind
`asyncio.to_thread`. A new activity write path that reaches the JSONL or SQLite
store from the event loop fails that gate — correctly. Add an anchor rather than
discovering it in CI. **Harness boundary**: `tests/test_harness_boundary.py`
forbids `deerflow.*` importing `app.*`, which is why the terminal-transition
recovery hook above must stay a generic `RunManager` callback the Gateway
registers, not a direct call into Gateway code.

Two smaller notes. `run.end.content` is documented as opaque and represented
differently by memory versus JSONL/db stores for nested values; keep the
activity payload flat and JSON-native so no consumer has to know which backend
wrote it. And `RunEventsConfig.backend` defaults to `memory`, so in a default
development install the durable projection does not survive a restart — the UI
must render that as **Status unavailable** (§6.7), never as idle.

## 6. Frontend implementation

### 6.1 Design direction, and the constraints that decide it

**Purpose.** Make an apparently-idle run legible without leaving the
conversation. A breeder waiting on a governed Build needs to know that something
is working and under whose authority — not to audit it.

**Audience and placement.** The reader is reading *chat*. The rail is
peripheral, and the repo already says so: both left rails are "navigation,
evidence, summary, and audit surfaces only." An auto-scrolling live log is a
*monitoring* surface, and putting one in a person's peripheral vision while they
read is the single easiest way to make this feature actively unpleasant.

**Tone.** Instrument panel, not console. Dense, quiet, scannable, and boring
when nothing is happening.

**The design idea: exactly one thing moves.** One spinner, on the deepest
actor that is genuinely working. Every ancestor is stated as static text on the
line beneath it. Nothing else on the surface animates, ever. This is what makes
the block read as an instrument rather than a `tail -f`, and it is the rule that
§6.5 enforces.

**The constraints are not negotiable and they are severe:**

| Constraint | Source | Consequence |
| --- | --- | --- |
| 256px wide, ~224px of content | `ProjectRailFrame` `w-64` + `px-4` | Three columns of timestamp + name + state do not fit. Absolute `10:42:03` timestamps cost roughly a third of the row for very little signal. |
| Collapses to 48px | `ProjectRailFrame` `w-12` | Activity needs a collapsed representation, or it vanishes exactly when a person collapses the rail to concentrate on chat. §6.4. |
| `hidden … md:flex` | `ProjectRailFrame` | **The rail does not exist below `md`.** There is no mobile project rail to put a sheet in. §6.4. |
| One scrolling column | `ProjectRailFrame`'s `overflow-y-auto` | A fixed-height auto-following viewport nested in it is a nested-scroll trap, and growing the section in place pushes Cycles, Blockers, and Conversations out of view. |

**Reuse the rail's existing vocabulary rather than inventing one.** `StageRow`
in `project-rail.tsx` is already the exact three-part shape an activity row
needs — a `size-3.5` icon, a truncated name, and a right-aligned
`text-[11px] text-muted-foreground` status word — and it already carries this
plan's own rule in its source comment: *"an icon, a name, and its status in
words, never colour alone."* Take `SectionLabel`'s
`text-[11px] font-semibold tracking-widest uppercase` header treatment and
`STATUS_MARK`'s icon+tone pairs (emerald / amber / destructive, each with its
dark-mode variant) as given. Add no new colour family, no new radius, and no new
shadow. Display names come from the server registry in §8 Phase 1; the frontend
keeps a `STATE_LABELS` map beside `STAGE_LABELS` / `STATUS_LABELS` in
`core/dbtl/cycle-view.ts` for the state words only, so an unknown server state
degrades to its raw value instead of blanking the row.

The state vocabulary maps onto the tones that already exist — **no new hue
family**, and the icon carries the meaning so the mapping survives greyscale:

| Activity state | Icon | Tone |
| --- | --- | --- |
| `thinking`, `computing`, `routing`, `coordinating`, `preparing`, `recording` | `Loader2Icon` + `animate-spin` | default foreground |
| `dispatching` | static `Loader2Icon` (no spin) | `text-muted-foreground` |
| `waiting` | `CircleDashed` | `text-muted-foreground` |
| `completed` | `CheckCircle2` | emerald pair |
| `failed` | `AlertTriangle` | `text-destructive` |
| `cancelled`, `interrupted` | `AlertTriangle` | amber pair |
| unknown / unavailable | `CircleDashed` | `text-muted-foreground/50` |

Emerald, amber, destructive, and the muted ramp are lifted from `STATUS_MARK`
with their `dark:` variants intact. Note that `STATUS_MARK`'s keys are DBTL
*stage statuses* (`approved`, `changes_requested`) and these are *activity
states* — reuse the tone pairs, not the key names, or the two vocabularies will
drift into each other.

### 6.2 The compact block

Replace `PLACEHOLDER_AGENTS` in
`frontend/src/components/workspace/project-rail/project-rail.tsx` with an
`AgentActivityBlock` of **fixed height and no internal scrolling**:

- **Line 1** — the active leaf: state icon, actor name (truncated), and the
  state word right-aligned, exactly `StageRow`'s layout.
- **Line 2** — lineage in words: `via <dispatcher>`, and `· Cycle 01` when the
  activity carries a cycle scope. Muted, `text-[11px]`, truncated.
- **Line 3, conditional** — `Also running: <name>` for one sibling, or
  `+ 3 more running` beyond that. Absent when there is no sibling.
- **Footer** — `Activity · <n> steps` as a button opening §6.3, with
  `aria-expanded` and `aria-controls`.
- **Header right** — a `Live` / `Waiting` / settled word, matched to the
  §6.6 states.

The leaf is the deepest non-terminal activity; ties break by highest sequence.

**Every line truncates; none wraps.** At ~224px a wrapped actor name changes
the block's height, and a block whose height changes as work moves is the
flicker this design exists to avoid. Line 1 is
`min-w-0 flex-1 truncate` on the name with the state word `shrink-0`, exactly
`StageRow`'s arrangement; lines 2 and 3 truncate as single lines. The full text
lives in the accessible name (§6.5) and in the expanded sheet, both of which
have room for it.

**Banned outright, because each one reintroduces a constraint from §6.1:** an
internal scroll container of any kind; `Collapsible` / `Accordion` / any
grow-in-place disclosure (§6.3); a virtualized list; and a height that varies
with sibling count. If the block needs more room, that is the signal to put the
content in the sheet, not to make the block taller.

**This replaces the plan's original fixed-height auto-following viewport, and
the deletion is deliberate.** In 224px, a five-row log with timestamps is
unreadable; nested inside the rail's own scroll container it fights the parent;
and auto-follow plus a "3 new activities ↓" pill plus scroll anchoring is a
substantial amount of machinery whose entire purpose is to stop a surface from
stealing attention that it did not need to take in the first place. What the
compact block gives up is *recent history at a glance* — a reader who wants to
know what happened ten seconds ago must open §6.3. That is the right trade for
a peripheral surface, and §1's four questions are all still answered in place.

### 6.3 The expanded activity surface

Expansion opens a shadcn `<Sheet>`, following the structure
`cycle-stage-sheet.tsx` already established in this rail — non-blocking,
dismissible, and wide enough for a tree — rather than growing inside the rail.
Growing in place would push the rest of the rail out of view and nest a second
scroll region inside the first; both are worse than a surface that has room to
be honest. Concretely: no `Collapsible`, no `Accordion`, no conditional
in-rail block that changes the rail's own scroll height. Take the sheet's
open-state ownership, header, and dismissal wiring from the existing component
rather than inventing a second convention beside it.

It provides two views over the same conversation-scoped projection:

- **Current actors** — the dispatch tree. Indentation is capped at three visual
  levels; a fourth-level DBTL chain renders flat with an explicit
  `Dispatched by <name>` line rather than indenting off the edge. Parallel
  workers are siblings.
- **Earlier** — the ordered timeline, newest first, grouped by run with a
  separator carrying the run's start time and terminal result. Absolute
  timestamps belong here, where there is width for them.

Scrolling behaviour lives entirely in this surface, which is where the original
plan's careful rules belong and where they are cheap: auto-follow only while
pinned to the live edge, **Jump to live** when away from it, preserved position
when events arrive or an older page is prepended, and lazy loading at the top.
Closing the sheet and reopening it restores the reader's position for the
lifetime of the conversation view.

The surface is read-only. Starting, cancelling, approving, or retrying work is
outside this feature.

### 6.4 Collapsed rail, and the missing mobile surface

**Collapsed rail (48px).** `ProjectRailFrame` already takes a
`collapsedItems: readonly ProjectRailCollapsedItem[]` prop — `{ icon, label,
onSelect }` — and renders each as a 32px button carrying `aria-label` and
`title`. Activity contributes exactly one item:

- `icon` is the deepest active state's icon from §6.1's table, so one glyph
  distinguishes running from waiting from failed from idle;
- `label` is the same full sentence used as the row's accessible name —
  *"Build worker 2, computing, dispatched by Build stage"* — which the frame
  places on both `aria-label` and `title`, giving a hover tooltip for free; and
- `onSelect` runs `scrollToRailSection(RAIL_SECTION_IDS.agents)`. The frame
  already calls `setCollapsed(false)` before `onSelect`, so the item only needs
  the scroll, and the existing `setTimeout(…, 0)` in that helper is what lets
  the anchor exist before it is scrolled to.

The icon obeys the same one-moving-thing rule: it spins only when the leaf is
genuinely working, and never under reduced motion.

**Below `md` there is no rail at all.** The original plan's "on narrow layouts,
expansion uses a non-blocking sheet" describes a surface with no host. This is a
real gap and the plan resolves it by **scoping the feature to `md` and above for
this phase**, and saying so: mobile users see today's behaviour, unchanged.
Building a mobile activity entry point means adding a project-rail affordance to
the mobile workspace shell, which is a larger change than this feature should
carry. Record it as follow-up work rather than pretending the sheet has
somewhere to open.

### 6.5 Motion, colour, and assistive technology

**Motion.** The established in-flight indicator across this codebase is
`Loader2Icon` with `animate-spin` — the debate panel, subtask card, meeting
progress card, and human input card all use it. Use it here rather than
introducing the "restrained pulse" the original plan proposed; a second idiom
for the same meaning is a cost with no benefit. Exactly one spinner is on screen
in the compact block, on the active leaf. In the expanded tree, ancestors of a
spinning leaf are static. Nothing else transitions, and no text marquees.

Every animated indicator carries `motion-reduce:animate-none`, following
`chat-box.tsx`. Under reduced motion the leaf's state word alone carries the
meaning, which it already does for every other reader.

**Colour.** Reuse `STATUS_MARK`'s existing tone pairs. Colour is never the only
signal — the state word is always present in text, and each state has a distinct
icon shape, so the surface survives greyscale and the common colour-vision
deficiencies. Both themes are already handled by the `dark:` variants in those
pairs; do not add a new hue family for activity.

**Assistive technology.** This is where a live activity surface most easily
becomes hostile. The rule:

- The compact block is **not** an `aria-live` region, and no ancestor of it may
  be one either. Its DOM churns on every transition; announcing that would flood
  a screen reader during a governed run, for a surface the reader did not ask to
  monitor.
- Announcements come instead from one sibling
  `<div className="sr-only" role="status" aria-live="polite">` that the provider
  writes to directly — kept **outside** the block's own subtree so re-renders of
  the visual rows cannot trigger it. It carries only **actor-level** changes —
  who is working now and their state — coalesced to at most one announcement
  every few seconds: *"Build worker 2, computing, via Build stage."* A state
  change on the same actor that does not cross an actor boundary updates the
  visual row silently.
- The expanded timeline is `role="log"` with `aria-live="off"`, matching
  `ai-elements/conversation.tsx`. A reader who opened it is reading it, not
  being read to.
- Each row's accessible name is the full sentence — *"Build worker 2,
  computing, dispatched by Build stage"* — because the visual row splits that
  across three truncated lines.
- The expand button carries `aria-expanded` and `aria-controls`; the collapsed
  rail item carries the same sentence as its `aria-label`.

### 6.6 Conversation-scoped reducer

Add `ThreadScopedActivityProvider`, parallel to `ThreadScopedSubtasksProvider`
and mounted the same way. Two different keys, and conflating them is a bug:
the **provider** is scoped to `(thread_id, run_id)` — that is what gets torn
down and rebuilt — while an individual activity's **identity** is
`(thread_id, run_id, activity_id)`, which is what dedupes and orders rows
within it. It should:

- reduce live `agent_activity` events immediately;
- attach children only when the parent id matches within the same run;
- retain concurrent active siblings;
- reconcile live state with persisted events after reconnect or reload;
- append transitions to an ordered, deduplicated timeline;
- expose the **active leaf** and its dispatcher chain as a derived value, so the
  compact block reads one selector rather than re-walking the tree;
- prepend older pages without changing the reader's scroll position;
- close stale activities when the authoritative run is terminal; and
- clear completely when navigation changes to another conversation.

**Coalescing belongs here, not in the view.** The reducer drops any update that
changes neither actor, state, operation, nor lineage, and returns the *same
state object* when it drops one so React bails out of the render entirely.
Without that, a token-rate stream re-renders the block many times a second to
paint an identical spinner — the classic DOM-thrashing failure, and here it is
also a *visual* failure, since a remounted `animate-spin` restarts its rotation
and the one moving thing on screen stutters. Two rules follow: the leaf
selector and the sibling summary are memoized on that state object, and the
spinner element is keyed by `activity_id` alone so a state change on the same
actor updates the row without remounting the icon.

The current subtask provider remains responsible for rich worker cards and
meeting transcripts, and `design-meeting-progress-card.tsx` remains the detailed
per-participant surface for a convened meeting. The rail is the cheap projection
of the same underlying work, not a competitor to it; shared task ids link the
two without either reducer owning the other.

### 6.7 Idle, retention, and historical behavior

The static green dot currently looks like proof that the Lead Agent is active.
Replace it with explicit states:

- no run: **Lead agent · Idle**, no spinner, no lineage line;
- waiting on a Human Input Card: **Waiting for you**, no animation, and a second
  line naming what is waiting — *"Start Build? — answer in chat"* — because a
  control the person cannot see is one they will not answer;
- recently completed: the block holds the terminal actor and result until the
  next run starts; and
- unavailable activity data: **Status unavailable**, never a guessed **Active**.

Each of these is a distinct icon and a distinct word, so the four are
distinguishable without colour and without motion — which matters most here,
since three of the four have no motion by definition.

The compact block holds only the active leaf and its lineage. The expanded
surface pages through durable conversation activity. **Retention is undecided —
see §5.4 and Phase 5.** No run-event retention policy exists today, so the
**Earlier activity is no longer retained** boundary is specified but must not be
built until a policy exists to trigger it; building it first produces a control
that can never fire and a claim the backend cannot keep. Ship the boundary in
the same change as the policy, or not at all.
This is operational history, not a permanent scientific audit record; durable
DBTL decisions and evidence remain in their existing audit surfaces.

## 7. Failure and concurrency rules

- **Several workers:** show all active siblings; the adapter stays
  **Coordinating** until all settle, while their transitions interleave by
  server sequence in the timeline.
- **Worker fails:** mark only that worker failed while the adapter continues or
  records the terminal stage result.
- **Run cancelled:** cancel the root and all non-terminal descendants. Cancel
  has several outcomes (`cancelled`, `taken_over`, `lease_valid_elsewhere`,
  `not_active_locally`, `not_cancellable`, `unknown`); only the first two end
  the run, so terminal activity must key on the outcome rather than on the
  cancel call returning.
- **Owner lost its lease:** the fenced worker writes nothing further (§5.4);
  the recovery path emits the terminal transition. A run marked `error` with
  `stop_reason=orphan_recovered` must leave no open activity behind it.
- **Browser disconnect:** stop local animation after a short stale threshold,
  fetch persisted activity, and resume only from authoritative state. Replayed
  rows merge without jumping a reader who is inspecting older history.
- **Stream replay gap:** clear optimistic activity before backfill, matching the
  existing task-ledger reset behavior.
- **Hidden deck-triggered run:** associate activity with the originating thread
  and recover it from persisted run events even if that thread had no live SSE
  subscriber.
- **Missing/old client:** additive task-event fields are ignored safely; the
  conversation continues normally.
- **Forged metadata:** gateway request normalization strips all activity and
  dispatcher fields. Only server instrumentation can create the projection.

## 8. Delivery phases

### Phase 1 — Contract and reducer

Define the event schema, actor/state vocabulary, transition reducer, security
allowlist, and **all five** run-event artifacts in §5.5 together — constants,
catalog, JSON contract, `RUN_EVENT_STREAM.md`, and the conformance test — since
CI fails when they disagree and the contract is cheapest to write first. Decide display names in
one server-owned registry rather than scattering strings across producers, and
populate it from §2's visible-name column so no internal identifier can reach a
screen. A contract test asserting that no `display_name` matches an
`actor_kind`, a class name, or the word "adapter" is cheap and permanent.

### Phase 2 — Root orchestration

Instrument direct Lead runs, Supervisor routing, the ordinary branch, and the
stage-adapter boundary. Persist their events and close them on every run
terminal path. This phase makes the top-level owner truthful before showing
individual workers.

### Phase 3 — Delegation lineage

Enrich Lead `task_tool` and `LiveStageAdapter` worker events with activity and
dispatcher ids. Cover parallel stage workers and Design/Test meeting seats.

### Phase 4 — Project rail

Add the thread-scoped provider, live reducer with its active-leaf selector, the
fixed-height compact block, the collapsed-rail item, the expanded sheet with its
timeline and current-tree views and scroll anchoring, the state/label maps, the
motion and reduced-motion rules, the live-region policy, and explicit
idle/waiting/unavailable states — behind an `agent_activity_visibility` rollout
flag. Scoped to `md` and above; a mobile entry point is follow-up work (§6.4).

### Phase 5 — Reload and hardening

Backfill persisted activity on reload/reconnect, add the cross-run store method
and its cursor-paginated endpoint, handle hidden runs and replay gaps, close the
namespace and fenced-worker gaps from §5.4, add telemetry for orphaned/stale
activities, and remove the static placeholder after rollout evidence is clean.

**Decide retention here, explicitly.** There is no run-event retention policy to
apply (§5.4). Either add one — a per-thread cap or age-based prune, new work
that belongs in this phase — or record that activity grows unbounded like every
other run event and remove §6.7's expiry boundary rather than shipping a control
that never fires.

## 9. Verification

### Backend tests

- Activity schema rejects unknown actors/states, unbounded labels, and private
  fields.
- Context managers emit one terminal transition on success, exception, and
  cancellation.
- Supervisor → Lead and Supervisor → Stage adapter lineage is correct.
- Lead → subagent and Stage adapter → worker lineage is correct.
- Parallel workers have distinct ids and one adapter parent.
- Persisted events retain order and replay idempotently.
- Client-supplied activity/dispatcher metadata is stripped, and an activity id
  placed in `configurable` is never honoured as lineage.
- `nostream` model prompts and results never enter activity events — including
  the Supervisor's interpretation call, Design council seats, and
  `run_oneshot_llm`, none of which the journal's own `TAG_NOSTREAM` handling
  covers here.
- Activity is persisted for a run streamed with `stream_subgraphs=True` exactly
  as it is for `False`; a client's stream option cannot change what is durable.
- A fenced/orphan-recovered run leaves no open activity: the recovery path
  emits the terminal transition the worker could not write.
- The contract conformance test agrees with the catalog, constants, and
  producers; the payload stays flat and JSON-native across all three stores.
- The new cross-run store read exists on the base plus `memory`, `jsonl`, and
  `db`, and the blocking-IO gate has an anchor for its write path.

### Frontend tests

- Reducer tolerates duplicate, delayed, missing-parent, and out-of-order events.
- Terminal activity cannot be reopened by an older update.
- The active-leaf selector picks the deepest non-terminal activity, breaks ties
  by sequence, and returns idle rather than a stale leaf when the run is
  terminal.
- Coalescing drops updates that change neither actor, state, operation, nor
  lineage, and a dropped update returns the **same state object** so React
  bails out of the render.
- A state change on the same actor does not remount the spinner element (its
  rotation must not restart), and does not fire an announcement.
- The polite status element lives outside the compact block's subtree, so
  re-rendering the rows cannot trigger it.
- The compact block renders state in text, lineage as `via <name>`, and a full
  accessible sentence, without relying on colour.
- The compact block's height does not change between one worker, two workers,
  and four; long actor names truncate rather than wrap or overflow 224px.
- No visible string is an internal identifier: the supervisor renders as
  **Cycle supervisor** and the adapter as **`<Stage>` stage**.
- Exactly one animated indicator exists on screen at a time, and none exists in
  idle, waiting, failed, cancelled, or unavailable states.
- Every animated indicator carries `motion-reduce:animate-none`.
- The compact block is not an `aria-live` region; the polite status element
  announces actor-level changes only and coalesces repeats.
- The collapsed rail item reports run state and its full sentence, and selecting
  it expands the rail and reveals the Agents section.
- In the expanded sheet: auto-follow stays pinned at the live edge but pauses
  when the reader scrolls upward, and **Jump to live** restores it.
- Opening, closing, and reopening the sheet preserve the reader's position.
- Prepending a paginated history page does not move the visible row.
- Tree indentation caps at three levels; a fourth-level chain renders flat with
  an explicit `Dispatched by <name>` line.
- Conversation navigation clears the prior thread's tree.
- Reload and replay-gap recovery converge on the persisted projection.
- Multiple stage workers render as siblings in the tree and as
  `Also running:` / `+ n more running` in the compact block.
- An unknown server-supplied state degrades to its raw value rather than
  blanking the row.

### Manual scenarios

1. Ordinary prompt: Lead Agent moves **Thinking → Computing → Thinking →
   Completed**.
2. Lead delegation: a subagent appears under Lead Agent and names Lead as its
   dispatcher.
3. DBTL continuation: the rail reads **Cycle supervisor** routing, then
   **Build stage** coordinating — never a class name — and each
   Build/Test worker appears under the adapter.
4. Design meeting: independent positions, red team, and chair retain their
   visible seat labels while sharing the adapter parent.
5. Two parallel workers: both remain visible until each settles.
6. Open the sheet and scroll upward during an active run: new events accumulate
   behind **Jump to live** without stealing the reader's position.
7. Open the sheet, load an older page, close it, and reopen: history and scroll
   position remain coherent.
10. Collapse the rail mid-run: the collapsed item still reports that work is in
    flight, and selecting it returns to the Agents section.
11. Watch the rail through a four-worker Build with the reduced-motion system
    setting on and off: at most one indicator moves, and none under reduced
    motion.
12. Read the rail with a screen reader through a governed run: actor changes are
    announced, individual transitions are not, and the sheet reads on demand.
8. Refresh mid-run and after completion: the same lineage, ordered history, and
   terminal state return without a permanent spinner.
9. Switch between two project conversations during a run: neither rail leaks
   the other's activity.

## 10. Acceptance criteria

The feature is ready when:

- every active row names an actor and a truthful state in text;
- every delegated actor has server-authored dispatch lineage;
- Supervisor and adapter work are not mislabeled as model thinking;
- ordinary Lead work and governed DBTL work are visually distinguishable;
- parallel workers, failures, cancellation, reload, and thread switching
  converge to authoritative state;
- the compact block keeps a fixed height, never scrolls internally, and never
  takes over the rail, while the expanded sheet can scroll through paginated
  history;
- at most one indicator is animating at any moment, and none animates under
  `prefers-reduced-motion`;
- no visible string is a class name, an `actor_kind`, or other internal
  vocabulary;
- the surface is legible in greyscale, at 224px, and to a screen reader that is
  not being flooded;
- incoming events never steal scroll position from a person reading earlier
  activity;
- the activity window contains no prompt, reasoning, tool payload, secret, or
  raw worker output;
- the current task and meeting progress surfaces keep working unchanged; and
- absence of activity data is rendered as unknown/unavailable, not a green
  active indicator.

## 11. Non-goals

- exposing chain-of-thought or a token-by-token model trace;
- a mobile or sub-`md` activity surface; the project rail does not render there
  at all, and giving it one is separate work (§6.4);
- recent history inside the compact block; the block is presence and lineage,
  and history is one click away (§6.2);
- exposing a full debug log or observability console; the history contains only
  bounded, human-facing actor-state transitions;
- changing DBTL stage authority, gate behavior, or worker selection;
- assigning durable breeding agents to a project;
- adding cancel, retry, approve, or stage-start controls to the Agents section;
- replacing detailed subtask cards, meeting transcripts, or audit history; or
- inferring activity from assistant prose, card presence, or filesystem writes.
