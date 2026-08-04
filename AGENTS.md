# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

It is the **monorepo orientation layer**: it maps the whole repo and points to the
module guides that own the depth. For anything inside a module, read that module's
guide rather than expecting full detail here:

- **[backend/AGENTS.md](backend/AGENTS.md)** — backend depth: harness/app split, agent &
  middleware chain, sandbox, MCP, skills, memory, IM channels, persistence/migrations,
  config system, test layout.
- **[frontend/AGENTS.md](frontend/AGENTS.md)** — frontend depth: Next.js App Router layout,
  thread/streaming data flow, code style, commands.

## What is DeerFlow

DeerFlow is a LangGraph-based AI super-agent system with a full-stack architecture. The
backend runs a "super agent" with sandboxed execution, persistent memory, subagent
delegation, and extensible tools (built-in, MCP, community), all per-thread isolated. The
frontend is a Next.js chat UI. External IM platforms (Feishu, Slack, Telegram, Discord,
DingTalk) bridge into the same agent through the Gateway.

## Service Topology

A single `make dev` / Docker stack runs four cooperating services:

| Service         | Port   | Role                                                                |
| --------------- | ------ | ------------------------------------------------------------------- |
| **Nginx**       | `2026` | Unified reverse-proxy entry point — open this in the browser        |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime      |
| **Frontend**    | `3000` | Next.js web interface                                               |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Nginx is the single public entry: it serves the frontend and proxies `/api/langgraph/*`
to the Gateway's LangGraph runtime, rewriting it to Gateway's native `/api/*` routes; all
other `/api/*` go straight to the Gateway REST routers. See
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail.

Both compose files publish that entry as `"${BIND_HOST:-127.0.0.1}:${PORT:-2026}:2026"`
— **loopback by default**, matching the README's documented deployment model. A bare
`"${PORT}:2026"` binds `0.0.0.0`, which does not.
Nginx itself listens `default_server` on IPv4+IPv6 and the
Gateway binds `0.0.0.0:8001` inside the container on purpose — both are container-
internal; the published nginx port is the entire external surface, and the Gateway's
`8001` is deliberately not published. Any new published port needs an explicit bind
address; `backend/tests/test_compose_default_bind_host.py` pins this for every service
in both compose files.

## Repository Map

```
deer-flow/
├── Makefile                        # Root orchestration: drives the full stack (dev/start/stop, docker, setup)
├── config.example.yaml             # Template → copy to config.yaml (gitignored) at repo root
├── extensions_config.example.json  # Template → copy to extensions_config.json (gitignored): MCP servers + skills
├── backend/                        # Python backend — see backend/AGENTS.md
│   ├── Makefile                    # Per-module backend commands (dev, gateway, test, lint, migrate-rev)
│   ├── packages/harness/           # deerflow-harness package (import: deerflow.*) — agent framework
│   └── app/                        # FastAPI Gateway + IM channels (import: app.*)
├── frontend/                       # Next.js frontend (pnpm) — see frontend/AGENTS.md
├── docker/                         # docker-compose files, nginx config, provisioner
├── skills/                         # Agent skills: public/ (committed), custom/ (gitignored)
│                                    # Managed integration skill packs are global at .deer-flow/integrations/skills/{provider}/
│                                    # Integration credentials and enabled state remain per-user
├── contracts/                      # Cross-component JSON contracts (e.g. subagent status, skill review)
├── scripts/                        # Root orchestration scripts invoked by the Makefile (check, configure, doctor, support_bundle, serve, nginx, docker, deploy, setup_wizard)
├── tests/                          # Root-level tests (currently tests/skills/ — public skill tests)
└── docs/                           # Cross-cutting docs, plans, and design notes
```

Runtime config lives at the **repo root**: copy `config.example.yaml` → `config.yaml`
(main app config) and `extensions_config.example.json` → `extensions_config.json` (MCP
servers + skills). Both real files are gitignored and may be edited at runtime via the
Gateway API. Config schema and resolution order are documented in
[backend/AGENTS.md](backend/AGENTS.md).

Skill quality review note:

- `skills/public/skill-reviewer/` is the built-in read-only skill quality reviewer.
  It uses the harness-layer `review_skill_package` tool and contracts in
  `contracts/skill_review/`. Model-visible review data is compact and
  tag-neutralized; full raw payloads stay in tool artifacts. See
  [backend/AGENTS.md](backend/AGENTS.md) for the non-activation, SkillScan, and
  `skill-creator` ownership boundaries.

Scheduled-task note:

- The scheduled-task MVP adds a workspace page at `/workspace/scheduled-tasks` plus a background scheduler service gated by `config.yaml -> scheduler.enabled`.
- Scheduled background runs are intentionally non-interactive: they execute through the normal run lifecycle, but the lead-agent toolset excludes `ask_clarification` when `context.non_interactive=true`. The key is honored only for internally-authenticated callers (the scheduler launch path); client-supplied `context.non_interactive` is dropped.

Breeding-workspace note:

- **The project owns the data, in a human-visible folder.** A project's
  workspace is a real directory under `config.yaml -> projects.root` (e.g.
  `~/Documents/projects/G2F`) that the human can browse in Finder; the sandbox
  mounts it for every conversation in the project, the file API lists it
  (`GET /api/projects/{id}/files`), and membership is a durable record
  (`threads_meta.project_id`, written via `PUT /api/projects/{id}/threads/{thread}`
  or validated first-run intent). Conversations are views onto that folder,
  not its owner. See [backend/AGENTS.md](backend/AGENTS.md) for the
  storage/scope/run-context triple that must stay aligned.
  Project creation can use the configured default root, adopt an existing
  folder, accept an absolute existing-or-new path, or create a named folder
  under a selected parent. Custom locations must be under `projects.root` or a
  writable `sandbox.mounts` host root. Project-scoped local sandboxes hide an
  overlapping parent mount so `/mnt/user-data/workspace` remains the canonical
  and unambiguous write target.
- The authenticated product surface is project-first with three rails: sidebar
  projects, a project rail (DBTL cycles, per-cycle to-dos, conversations), and
  chat in the main area. Project workspaces live at `/workspace/<project-slug>`
  and their conversations at `/workspace/<project-slug>/<thread_id>`; opening a
  project lands directly in a conversation. Conversations belong to a project
  through thread metadata `project_id`; legacy projectless conversations stay
  at `/workspace/chats`. Cycles/to-dos are a browser-local review projection —
  DBTL orchestration is deferred per the 2026-07-25 foundation-demo rescope.
- **The chatbox is the primary input and interaction channel.** Both left rails
  are navigation, evidence, summary, and audit surfaces only. The generic stage
  sheet is inspection-only; current Test meeting/outcome decisions return to
  the originating conversation as server-bound Human Input Cards. Specialized
  Reconciliation/Learn controls and the Design-deck migration remain explicit
  exceptions until their chat flows are implemented. See
  [frontend/AGENTS.md](frontend/AGENTS.md) for the `extraTools` / `focusSignal`
  slots that keep this out of the generic composer.
- The project folder tree appears only under the expandable project rows in
  the first sidebar rail. File selection opens the project-scoped content
  inspector and temporarily collapses that rail; the second project rail does
  not duplicate the project header/tree.
- Agent management is an inventory-first workspace surface. Its default
  Built-in tab lists the Lead Agent plus runtime-available built-in subagents;
  Custom lists user-scoped agents plus `config.yaml` custom subagents.
  Built-ins and subagents are read-only in this surface, and delegated
  subagents do not expose a direct-chat action.
- Memory follows the same durable project scope: project conversations load
  and learn from a `(user_id, project_id)`-specific bucket, while unfiled chats
  retain user-global memory. Older project conversations replace legacy global
  snapshots on their next run, and current project identity overrides stale
  claims in their existing visible history.
- The shared SQL persistence layer owns `workspaces`, `workspace_members`, and
  `projects`; membership is enforced before project access. PostgreSQL is the
  production authority.
- DBTL Phase 1 adds a durable, project-scoped governance schema and strict
  human-review records, but does not enable cycle execution. Settings → DBTL
  readiness exposes the administrator validation/evidence/cutover checklist;
  SQLite is always cutover-blocked and PostgreSQL approval remains distinct
  from enabling the future Supervisor Graph.
- DBTL Phase 5 adds that Supervisor Graph as a thin, per-run project router,
  fail-closed until `dbtl.mode=graph_enabled`. Interactive threads remain
  durably pinned to `lead_agent`; a runtime-only opt-in selects the supervisor
  for one project request, preserving normal checkpoint access and safe
  rollback. It routes one request to one of four terminal branches — ordinary
  work, clarification, cycle setup/confirmation, existing-cycle continuation —
  and the ordinary branch _is_ the existing lead agent. Stage execution remains
  behind a stub that structurally cannot write results or satisfy gates. A
  per-request DBTL scope selector sits **in the composer's tool row**, beside
  attachments and voice: it is a quiet flask icon for ordinary work and gains a
  label (`Cycle 01 · Design`, `Start a new cycle`) only when the next request is
  scoped, and it applies to the next request only. Cycles are opened by
  describing them in the chatbox under the `Start a new cycle` scope, which routes
  to the supervisor's setup branch — nothing is recorded until the human
  confirms. Internal one-shot model calls and Design-council subagent runs are
  tagged `nostream`; their prompts, raw JSON, reasoning, and tool output must
  never appear in the parent conversation stream or thread history. The
  council's bounded task timeline remains available separately. See
  [backend/AGENTS.md](backend/AGENTS.md) for the reducer-idempotency and
  stream-contract constraints that make delegation safe.
- The supervisor's deterministic Human Input transport, durable card-history
  recovery, continuation handlers, and injected stage ports live under
  `deerflow.agents.dbtl.supervisor_support`. The stable
  `deerflow.agents.dbtl.stage_execution` import is a facade over
  `deerflow.agents.dbtl.live_stage`; replay and Test human-write revalidation
  have separate service owners there. Keep new orchestration behind those
  boundaries instead of growing the graph-factory or compatibility modules.
- DBTL Phase 6 replaces the continuation stub with a production
  `LiveStageAdapter` for Design and Data Reconciliation. It verifies the
  selected cycle belongs to the runtime project, fans bounded work units out
  through `SubagentExecutor`, and atomically records structured worker results
  plus a content-addressed review package in the project workspace. Retries
  replay the durable stage event instead of dispatching workers again. Stages
  are versioned `StageSpec` data, work units declare capabilities rather than
  role names, and workers return a structured `StageWorkerResult` — free-form
  text cannot satisfy a stage contract. Data
  Readiness and Reconciliation is a real gate: every declared input is pinned
  by content hash, raw data must be declared immutable, and Build stays locked
  until every required matrix row is settled. An agent may propose a
  resolution but may never close a _judgement_ row (contradictory sources,
  trait direction, exclusions, leakage, train/test separation) — that stays a
  person's decision, enforced at the write boundary. An approval binds the
  dataset fingerprint, stage-spec version, and policy version it was granted
  against, so a later dataset change invalidates it instead of carrying it
  into Build.
- DBTL Phase 7 added executable Build and Test `StageSpec` contracts while
  leaving Learn unavailable for Phase 8. Build can start only at
  `ready_for_build`; every reviewable Build records the approved dataset
  fingerprint, code/config revisions, environment, versioned outputs,
  deviations, and logs. A reviewable Build uses the canonical Design deck
  shell and keeps its Submit/Approve/Revise/Reject Human gate on the final
  slide; it does not duplicate that gate in chat. If no verified numeric
  outcome or published figure exists, the server emits one recovery Human
  Input Card and creates no deck or feedback surface. Test stores headline metrics separately from a
  versioned validity pack. Its outcome is computed as `supported`,
  `not_supported`, `inconclusive`, or `invalidated`; a generic stage approval
  cannot bypass that computation. Leakage, broken folds, structure artifacts,
  ceiling/direction failures, holdout failure, unreconciled inputs, or
  irreproducible execution dominate strong headline metrics. A server-owned
  human reviewer chooses only an outcome-compatible route to Learn, repeat,
  Design, Reconciliation, Build, or cycle closure. See
  [backend/AGENTS.md](backend/AGENTS.md) for persistence and routing contracts
  and [frontend/AGENTS.md](frontend/AGENTS.md) for the two-column review model.
  A current Test worker must also return a typed metric/check payload; prose or
  arbitrary workspace files cannot become review evidence. The server computes
  the outcome, then chat offers the policy-selected review meeting and a second
  outcome-compatible route card. Card replies recover their cycle from the
  emitted request, so losing the next-request cycle scope cannot strand Test.
- DBTL Phase 8 makes Learn executable and keeps knowledge authority deliberately
  split. Structured Learn workers may synthesize evidence-bound candidates only
  from a human-owned Test outcome; inconclusive and invalidated results can
  close with no candidate. Candidates remain provisional until a human records
  a project-scope promotion. Publication to explicitly selected projects is a
  separate human decision that writes SQL audit records, portable Markdown
  projections, and bounded retrieval pointers. Supersession and retraction
  retain the old claim and event history while removing its active publication
  pointers. No agent, stage approval, or promotion call can implicitly publish.
- New cycles started from the project rail automatically queue the current
  `generic:design:v2` Design council in project chat. Its context includes a
  bounded project-file manifest, declared cycle inputs, prior council turns,
  and the latest human clarification. It always produces at least an
  independent design position and red-team position before a chair synthesis.
  A `needs_input` chair result renders through the existing
  `ask_clarification` human-input card and resumes in the same selected cycle.
  A completed synthesis is emitted through the existing `present_files`
  message shape and thread artifact inspector; it only creates review evidence
  and never submits or approves the human gate. Chat renders one durable,
  run-scoped meeting footprint followed by the round's assistant conclusion or
  `present_files` output, keeping the Design slide deck below the meeting. The
  Design review sheet renders a structured decision map from the package
  referenced by the bound Markdown. Automatic kickoff plumbing is hidden from
  the user-authored transcript. The
  presentation icon beside Cycles opens a client-only Phase 7 three-case demo
  and never mutates durable records.
- A registered Design feedback deck is the normal post-meeting input surface.
  The persisted HTML is inert until the authenticated artifact parent verifies
  its exact SHA-256, project, cycle, originating conversation, evidence
  revision, and current server state. It can answer a paused chair, explicitly
  submit Design for review, and then record Approve, Request changes, or Reject
  as a second transition. The iframe emits bounded intents only; the parent
  owns API calls, and the backend revalidates scope and records a single-use
  action ledger plus review provenance. A deck-backed clarification remains in
  durable thread history but is not rendered as a duplicate card and does not
  lock ordinary chat. Request changes starts a focused refinement in the
  originating conversation. The parent follows a chair-resume run through the
  authenticated surface read model: a successor deck refreshes durable thread
  history, while a terminal run with no successor marks the same action failed,
  reports the latest chair-worker rejection detail, and re-enables its
  choice/comment as an editable draft. Because recording the failed worker
  advances `db_revision`, the retry rebinds the optimistic revision under the
  same client submission id and deck; the failed payload remains in bounded
  attempt history, while the replacement answer may differ. Successful answers
  and review/handoff actions remain immutable. On reload, the authenticated
  parent restores the last failed answer as the starting draft without forcing
  the person to replay it byte-for-byte.
  An approval that opens another stage also starts a short hidden supervisor
  run in the originating conversation. That run emits a deterministic,
  checkpointed Human Input Card with **Start &lt;next stage&gt;** and **Hold here**;
  it never dispatches stage work while rendering the card. The reply recovers
  its cycle from the server-emitted card, and only the Start option reaches the
  live stage adapter. Both the supervisor and adapter validate the card's bound
  cycle revision and intended next stage; stale cards dispatch nothing. Review
  success and chat delivery are separate durable states: a failed handoff keeps
  the approval, reopens the exact deck action, and retries only the prompt under
  the original payload/id. This keeps the owner in the loop after leaving the
  deck without relying on the composer's one-request cycle selector.
  `dbtl.design_deck_feedback=false` restores the
  legacy Design card/sheet during the rollback window; non-Design review sheets
  are unchanged. Legacy, downloaded, wrong-thread, stale, superseded, or
  hash-mismatched decks remain read-only.
- **A control nobody can see is a control that does not exist, and an
  unanswered one is not an invitation to the lead agent.** A hidden handoff run
  used to complete successfully, hold its Start/Hold card in its final graph
  state, and persist no message at all — the card was absent live and after
  refresh, and the owner's next words, "go ahead with build", reached ordinary
  chat. Four layers each failed independently.
  **Rendering**: the card carries `design_feedback_surface_id`, and the web UI
  returned `null` for _any_ request carrying that field — a rule written for the
  Design decision card, where the deck genuinely is the input surface. On a
  handoff the surface id is only an audit binding (which approval opened this
  stage) and the deck holds no control that could answer it, so the card
  rendered as nothing even once it reached history.
  `isDeckOwnedHumanInputRequest` keys the suppression on the clarification type
  instead, as an allowlist: an unrecognized surface-bound card stays suppressed,
  because being invisible is recoverable and being wrongly interactive is not.
  **Delivery**: the journal recognizes a run's own output by finding the run's
  input message, which requires an id nothing mints, so graph-authored cards
  were silently never reconciled; the worker now hands the journal the
  thread's pre-run messages as the boundary (an empty set for a thread's first
  run, which is a _known_ boundary — only a failed snapshot stays unknown),
  deck-started runs stamp an explicit input id as well, and a deterministic
  supervisor reply marked `deerflow_graph_receipt` (server-owned, stripped from
  client input) is reconciled too — otherwise "Holding here" is spoken into a
  void.
  **Verification**: run success is not delivery, so the handoff watcher asks
  the thread's own message projection whether the card arrived and treats a
  successful-but-empty run exactly like a dead one, reopening the deck action.
  A read error propagates rather than being swallowed, so the watcher's own
  retry loop covers it; swallowing it ended the watch and reported delivered
  exactly when a struggling store was the likely reason the card was missing.
  **Routing**: while a Start/Hold card the server emitted is still unanswered,
  a request that would otherwise become ordinary work is answering _that card_
  — the supervisor re-presents it and dispatches nothing. Every earlier guard
  missed this because each fires only on a card _answer_ or an explicitly
  scoped request.
  `pending_stage_handoff_control` is the single predicate the route fence and
  the handler share, because a fence that intercepts a request the handler then
  declines would fall through to stage execution. Three things make a card _not_
  pending: it was answered (**Hold** is a decision, and re-presenting it would
  argue with the person who made it); its refusal was already recorded, so a
  stale card states its reason once and hands the conversation back rather than
  answering every later message with the same refusal; or the request names a
  different cycle, since a project runs several at once and the card now names
  the cycle it would start. **Start** validates against the rebuilt marker, not
  the raw card — the card names its cycle `dbtl_cycle_id` while the validator
  reads `cycle_id`, so passing the request through sent an empty cycle id and
  every Start was refused as "no project-owned cycle".
- **A card in checkpoint state must be one the graph emitted.** `ToolMessage.
artifact` is server-owned in full and is now stripped from external run input:
  `convert_to_messages` will build a `ToolMessage` with any caller-supplied
  artifact, and the supervisor resolves replies against the card it finds
  there — so an unstripped artifact let a client place a fabricated control in
  a thread, hijack its routing, and (by forging an _answered_ card) suppress
  the fence while a real one waited. No legitimate client sends one; the
  composer and every IM channel send human messages only. `cycle_revision` is
  validated where the card is read rather than coerced where the marker is
  rebuilt, because the fence forces every later request through that rebuild
  and a non-integer value would take the whole conversation down instead of one
  card.
- **The lead agent is told what governed work surrounds it, and that knowing
  is not permission.** An ordinary project run receives a read-only
  `dbtl_status_snapshot` in request-only context — the live cycles, their stage
  statuses, and any waiting control — rendered by
  `build_dbtl_status_reminder` as a `<dbtl_status>` block that states plainly
  that the lead agent may discuss, read, and prepare but may never start, run,
  advance, approve, reject, or record a stage, nor describe its own work as a
  stage result. The takeover began with the lead agent not knowing a cycle
  existed; this is why it now explains the boundary instead of building past
  it. The block is orientation, not enforcement — the routing fence and the
  stage-owned output paths are what actually stop it.
- The Design council surfaces in the product as a **design meeting** — every
  user-facing string (card titles, debate panel, decision map, review
  Markdown) says "meeting"/"participants", while internal identifiers
  (`council_preflight`, `dbtl-council__`, `council_plan`, module names) keep
  the council vocabulary so the protocol and history stay stable.
- **An unscoped request in the conversation that opened a cycle can still
  reach it.** The composer's cycle scope is next-request-only by design, so the
  _second_ consecutive cycle request arrives unscoped and the routing ladder had
  nothing to continue: it fell through to the classifier, which read "run the
  meeting again" as ordinary chat. The lead agent then read the trial data
  itself, wrote a file named like a design package into `outputs/`, and answered
  — no meeting, no worker rows, no artifact, and a Design stage that looked
  answered while its gate had not moved. Nothing was _recorded_, but the person
  received a stage deliverable from the one actor that must never produce one.
  A conversation's own live cycle is now recovered server-side from
  `dbtl_cycles.originating_thread_id` when the request deterministically names
  another round. It stays narrow rather than becoming a sticky scope: only the
  re-run phrases, only in the conversation that opened the cycle, only while
  that cycle is live, and an explicit `ordinary` choice still wins. See
  [backend/AGENTS.md](backend/AGENTS.md) for the rung and its failure mode.
- **A meeting is convened when a person asks for one, and only then.** Three
  rules in `deerflow.agents.dbtl.stage_execution` replace the old "any Design
  request runs the whole council". (1) _Hold_: a Design stage stays
  `in_progress` until someone submits it for review, so a package already on
  the table and no `changes_requested` review means the next cycle-scoped
  request convenes nobody and points at the review sheet instead;
  `_wants_new_debate` is a deterministic phrase check ("run the meeting again",
  "restart/retry/relaunch the meeting", plus enumerated one-slip typos like
  "restat") that overrides it. What the phrases miss, an injected fail-soft
  intent interpreter (`make_llm_intent_interpreter`, one nostream call on
  `dbtl.setup_draft_model_name`) may still read as a re-run — the owner's words
  stay verbatim in the record while interpretation absorbs typos and
  paraphrases; only an explicit CONVENE verdict convenes, and an absent model,
  provider failure, or ambiguous reply holds. A `changes_requested` review
  still re-opens the debate without being asked, because that verdict _is_ the
  request to argue again.
  (2) _Resume_: answering the chair's `needs_input` question dispatches the
  chair alone (`_resumed_chair_unit`) over the positions already recorded, with
  the question and the owner's words carried verbatim — re-running the full
  council spent a second meeting's budget re-arguing what nobody questioned and
  read to the owner as being ignored. The supervisor passes the answer as an
  explicit `clarification_answer`, since the answer and an ordinary request are
  the same string. (3) _Model_: `dbtl.council_model_name` sets the default model
  for seats that do not name one; inheriting the composer's meant a meeting
  convened from an expensive chat quietly ran four workers on that model. An
  unconfigured name warns and falls back rather than failing the meeting.
  Known limitation, narrowed by the progressive gate's Park route: a held,
  unparked Design still answers any cycle-scoped message with the same
  pointer, so ordinary questions about the design are not routed to the lead
  agent. Parking the cycle from the deck inverts this — parked cycles send
  ordinary cycle-scoped requests to the lead agent with the design brief
  carried hash-bound and explicitly marked unapproved.
- **A re-run is a new meeting, so it is set up like one.** The preflight card
  asks how much debate, which seats, and each participant's model, reasoning
  strength, and instructions — and is deliberately once-only per cycle, which
  is right for every turn belonging to the meeting it opened. A deliberate
  re-run arrives with that guard already tripped, so a second council's budget
  was spent with nobody asked; and because the readers for depth, roster, and
  participant dials are all scoped to the turn that _answers_ a card, a re-run
  answered nothing and the server re-derived all three. Asking again in the
  same words therefore produced a different number of participants, on
  different agents, on different models, with the owner's per-seat instructions
  gone. A re-run now raises the card again — no worker may be dispatched until
  a person answers it — opened on the setup last confirmed, since "again" means
  again. The approved roster is reused rather than rewritten, which is what
  changed the seats. It is still a proposal: anything on it can be changed, and
  "Adjust the roster first" deliberately recovers nothing, because asking for a
  different roster is the opposite of reusing the old one. The guard is
  positional rather than phrase-only, so the card cannot re-raise forever —
  including when a stale client answers with a depth the server cannot read.
- **Every round with a real chair outcome ends with a slide deck.**
  `deerflow.dbtl.council_deck` renders a trustworthy completed chair result or
  an uncapped `needs_input` result as one self-contained HTML deck (inline
  CSS/JS, no network, print-friendly) written beside the review package as
  `design-slides-rev<N>-<hash>.html`. Slide order is background → objectives →
  agreements → contested → needs-your-decision → conclusions → limitations →
  next → the human gate, preserving the "disagreement before synthesis" rule
  the review Markdown follows. It is a **renderer, not a worker**: it has
  no sentence of its own, so it cannot smooth a contested point away. It is
  deliberately not registered as a durable artifact and is presented _after_
  the review Markdown in `present_files`, because an approval must bind to the
  reviewed document and a deck listed first is the one a reader reviews. A
  paused meeting gets a deck too, shown ahead of the `ask_clarification` card:
  the round that asks for a decision is the one that most needs it. A
  failed/blocked/capped chair result remains recorded for audit but creates no
  deck or feedback surface; otherwise a provider outage is presented as a
  concluded meeting. A chair result is also insufficient by itself: the round
  must contain both a validated independent-position report and a validated
  red-team report. Light may retain completed-but-capped reports as an
  explicitly limited pilot, but provider failures and contract rejections are
  not debate input and cannot be converted into a conclusion.
- **The deck is paced for a reader, and the gate is the last thing they reach.**
  Background and Objectives are quoted verbatim from the cycle's own
  `research_question`, `objective`, and `success_criteria` — the renderer still
  authors nothing, and a cycle with none recorded simply omits those slides
  rather than showing an empty one. Every section runs onto **as many screens
  as it needs** (`BULLETS_PER_SLIDE`, `CARDS_PER_SLIDE`,
  `PARAGRAPHS_PER_SLIDE`, bounded by `MAX_SLIDES_PER_SECTION`), with
  continuation slides marked `cont.`; a section collects up to
  `MAX_SECTION_ITEMS` before that bound bites, deliberately above the display
  size, because collecting only what fits makes the overflow count zero and the
  deck could never say it was holding something back. Consensus sections are
  already capped upstream at `MAX_CONSENSUS_ITEMS`, so the deck is never what
  drops an agreement. The human gate moves to the **end**, after everything it
  is a verdict on; the sentence that the deck approves nothing merely by being
  opened moves with it onto the last content slide, since losing it would let a
  reader infer that reaching the end is itself an approval.
  Each content slide carries its own **note box**, shipped `disabled` like every
  other control and enabled individually by the authenticated parent — an
  enabled `fieldset` does not clear a control's own `disabled`. Notes are
  drafts, not a second record: `withNotes` folds them, each labelled by
  `data-note-label`, into the comment of whatever decision is actually taken,
  so a note can never be stored as a verdict nobody gave and the record keeps
  one comment. Validation runs against that folded text, because a reviewer who
  wrote their reasons on the slide the reasons are about has written them down.
  A restored comment (a retry after a failed submission) **clears** the boxes,
  since it already contains them and re-folding would duplicate the note.
  The gate slide has no box of its own — it owns the comment they fold into.
- **A paused chair may offer a structured choice, and the deck renders it.**
  `deerflow.dbtl.decision_request` adds an optional `decision_request` beside
  `clarification_question`: two to five options, each with a stable slug id, a
  label, and a value stating what choosing it means. Prose is enough to resume
  a chair and not enough to audit — "family holdout" and "family holdout, but
  only if sample size allows" are the same free-text field — so the structured
  answer is what a record can bind to. Three rules hold it together. The
  **question is not the payload's to state**: parsing takes the recorded
  `clarification_question` and overwrites whatever the payload repeated, so the
  card and the audit record cannot describe different questions. A **refusal
  costs the cards, never the question**: an unknown option id, a single option,
  or a recommendation naming nothing returns a reason and the deck falls back to
  free text, rather than failing a whole meeting over a misshapen sub-object or
  dropping it silently. And **only a paused chair may offer options** — a
  `completed` result carrying them is describing a decision already taken.
  The deck renders them as a real `fieldset`/radio group with the question as
  its legend, and **nothing is preselected, including the recommendation**: a
  default that becomes the answer is a decision nobody made. The recommendation
  is labelled as the chair's recorded view, with a bordered badge rather than
  colour alone. Every control ships **disabled** and the deck says "Open this
  deck in DeerFlow to respond." — the persisted file must be inert wherever it
  is opened from, and activation is the authenticated parent's job. Deck
  navigation now yields to a focused control (space selects a radio) while the
  deck's own arrow buttons keep working. See
  [docs/plans/2026-07-28-design-deck-feedback-plan.md](docs/plans/2026-07-28-design-deck-feedback-plan.md)
  for the bridge and review bindings this is an early phase of; **no deck can
  yet record anything**.
- **A rendered deck is registered, so a page can later prove it is one.**
  Nothing about an HTML file distinguishes the deck DeerFlow rendered from any
  other page an agent wrote, so `dbtl_design_feedback_surfaces` records which
  cycle, stage attempt, round, evidence, and originating conversation a
  particular deck's exact bytes belong to. It is **not** a review and grants no
  authority by itself. Its bindings are server-owned — a deck that could assert
  what it was rendered against could assert that a stale one is current — and
  regeneration **supersedes rather than mutates**, because each old row is the
  record of what somebody was actually shown. A review deck must name the
  evidence it projects, matched by content hash rather than attachment order; a
  paused meeting's deck must not, because there is no package yet. An
  authenticated, project-scoped read endpoint reports those bindings and, in
  this phase, always reports that nothing is actionable. Registration is
  fail-visible: evidence already committed remains retryable, but an
  unregistered deck cannot be presented as an answerable review surface. Cycle
  row locking serializes surface revision allocation and supersession.
- The preflight card is also the meeting's setup form.
  `deerflow.dbtl.council_settings` renders one **editable participant card per
  seat** (`council_participants` on the artifact), prefilled with the roster
  writer's suggestions: model (from the configured model list), reasoning
  strength (`standard`/`extended` → the subagent's
  `thinking_enabled`), and owner instructions (prefilled with the seat's
  brief), plus an explicit **metered, no cap** token policy. Provider-reported
  input/output/total usage is shown per seat and in aggregate, reported to the
  parent run journal, and persisted in the review package. Edits ride back on
  the depth reply's `participants` key, are
  validated server-side field by field, recovered from the card the server
  emitted (forged request ids match nothing, and like the depth they apply
  only to the meeting that reply convenes), applied to the recorded
  `CouncilPlan` so the review package reports the dials that actually ran,
  and carried onto the dispatched `WorkUnit`s (per-seat model/thinking;
  instructions are quoted **verbatim** into that seat's
  prompt — an instructions box echoed back byte-identical to its prefill is
  the writer's text, not the owner's, and is never attributed to them).
  The dispatcher pins that effective per-seat model onto the
  `SubagentConfig` handed to the executor; applying it only to tools and UI
  labels while leaving the executor at `inherit` would silently call the
  composer's provider instead.
  Legacy replies can still carry a token value, but every Design depth has
  `token_limit_enforced=false`, so that value is recorded without restoring a
  guardrail that can discard the chair's synthesis.
- The council's composition is inspectable before it convenes.
  `deerflow.dbtl.council` computes the roster as values — one seat per worker,
  naming its agent, role (independent position, red team, chair), model, tools,
  and whether a generalist is standing in for a missing specialist. The first
  Design request of a cycle raises a `council_preflight` `ask_clarification`
  card carrying that roster plus a four-way depth choice; **no worker is
  dispatched until a person answers it**. Depth sets how many independent
  positions are heard and what each may spend, and `medium` reproduces the
  pre-depth budget exactly; no depth can drop the red team or the chair.
  `human_input` ("Write it myself") is the exception and a different kind of
  setting: it seats nobody, and instead raises a `design_authoring` card whose
  answer is recorded verbatim as the Design review package, with **zero** worker
  runs and an explicit human attribution on the document — synthesizing a worker
  entry would put a person's words behind an agent's name in the audit record.
  Because it also produces an empty roster, callers must ask
  `CouncilPlan.human_authored` **before** `dispatchable`: both are false here,
  and the other one means the council cannot answer the question. Nothing
  recommends this depth — declining to consult anyone is a statement about who
  owns the answer, not a judgement a rule table should make. The authoring card
  carries its own `council_depth` and the supervisor reads it back from the card
  the server emitted, because the client sends a scope with every request and
  falls back to `ordinary` for a card it does not special-case; without that the
  answer turn would convene the council the owner just declined.
  `recommend_depth` suggests one from named phrases in the request rather than
  a model's opinion, and stakes beat brevity. Wording those phrases do not
  match gets one more reading: `interpret_depth` passes the request verbatim to
  a fail-soft interpreter (`make_llm_depth_interpreter`), so "jsut a qiuck
  pilto" still opens the card on Light instead of quietly recommending several
  times the debate that was asked for. It may only name Light/Medium/Heavy —
  never `human_input`, which is a statement about who owns the answer — and a
  provider failure or unrecognized reply keeps the deterministic default, since
  the card must always open on a usable setting. The confirmed depth travels in
  the request's own context (`dbtl_council_depth`, next-request-only like the
  selected cycle) and the executor scopes the stage spec by it.
  **The server also recovers that depth from the card it emitted**
  (`_confirmed_council_depth`), because a client reply that loses the echoed
  value is indistinguishable from one that never carried a choice — and the
  fallback is the server's own recommendation, so someone who chose "Light
  debate" silently got a medium council and nothing said so. The client's value
  still wins when it sends one; recovery only fills the gap, and is scoped to
  the answering turn, since a preflight answer sits in history forever and must
  not pin the whole cycle to one depth.
  **The preview is the proposal.** `preview_council` runs the roster proposal
  rather than showing capability selection's roster while dispatch used a
  proposed one: in a generalist-only deployment selection yields one
  undifferentiated seat, so the card described a thin council and four
  differentiated seats then ran. `plan_from_proposal` re-describes the plan from
  the proposal while keeping depth, budget, and stage spec — the human's setting
  and the attempt's contract — fixed. Approving a roster you were not shown is
  not a choice.
  A fifth preflight option, `adjust`, is the only one that starts nothing: it
  raises a free-text `council_adjustment` card, carries the reviewer's words
  **verbatim** into `build_proposal_prompt`, redraws the roster, and shows it
  again before anyone runs. It is deliberately not a `CouncilDepth` — a value in
  that enum is something the council can be _run at_. The note is read back from
  the emitted card at dispatch (`_council_adjustment`), scanning back rather than
  reading only the newest message, because by then the depth answer is the newest
  thing said and the roster that runs must be the one that was approved.
  Being _registered_ is not the same as being able to debate: `bash` is a real
  subagent, so the fail-closed agent check accepted it and a live council seated
  it as an independent position, where it spent its whole budget running commands
  and returned no argument. `NON_DELIBERATIVE_AGENTS` keeps built-in execution
  specialists out of both the prompt's agent list and the parser. Depth and effective budget are recorded in the review
  package, because the budget a reviewer would reconstruct from `stage_spec_key`
  is the spec's, not the one those workers had. An agent that declares no
  `subagents.custom_agents.<name>.dbtl_capabilities` covers nothing, so a
  deployment with none declared runs every council role as `general-purpose` —
  `config.example.yaml` ships a worked `experimental-design` specialist.
  The roster itself is **proposed for the question, then validated**.
  `deerflow.dbtl.council_proposal` parses a one-shot `nostream` reply into seats
  that carry a _focus_ and a _brief_ — what each seat argues from — which is
  what makes three seats disagree even when all three resolve to
  `general-purpose`, the case every deployment without registered specialists
  lands in. Parsing is fail-closed **per seat**: an unregistered agent, an
  unconfigured model, an unknown capability, or a seat with no brief is refused
  with a reason and never swapped for the generalist, because that silent swap
  is the original bug arriving through the feature meant to fix it. One bad seat
  does not discard the good ones, duplicate briefs collapse (two seats arguing
  the same thing read as corroboration rather than repetition), and positions are
  capped by the chosen **depth**, not by how many seats capability selection
  happened to fill — inheriting selection's limit would cap a heavy council at
  one position in exactly the deployment this exists for. A proposal _replaces_
  selection's units rather than sitting beside them, so the package cannot
  describe a council that did not run; the refusals ride in `selection.notes`,
  since a seat that was asked for and refused is otherwise indistinguishable
  from one never considered. Every failure — no drafting model, a provider
  outage, an unparseable reply — degrades to capability selection, which is what
  ran before proposals existed: a worse council, not a failed one. `WorkUnit`
  gained `model`, so a seat may name its own; before this every seat ran on
  whatever the composer was set to.
  The chair reports a **structured consensus** beside its prose
  (`deerflow.dbtl.consensus`): agreements, disagreements that keep _both_
  positions and how each was settled, and the questions only the project owner
  can answer. The chair is told not to average incompatible positions, and prose
  is exactly where a reviewer cannot check that — a real convergence and a
  smoothed-away disagreement read identically. An unsettled disagreement keeps
  an empty resolution and renders as "Not resolved" rather than being dropped;
  `unanimous` flags the opposite shape (agreement on everything with no argument
  recorded) and requires at least one agreement, so a chair that reported
  nothing cannot be rendered as one reporting total accord. The block renders
  **before** the positions, because where the council disagreed is what tells a
  reader whether the synthesis is a conclusion or an average, and it is
  worthless once they have read the synthesis as settled. It is read off the
  recorded chair result so the document cannot describe a consensus the chair
  never reported, and parsing is permissive — a malformed consensus costs the
  structured view, not the Design attempt.

  **"Request changes" opens a focused refinement round, not a fresh debate.**
  `_change_request` reads the latest Design review from the activity feed and
  carries the reviewer's objection **verbatim** into the seats' prompts, because
  a paraphrase is the failure this exists to fix. Only a `changes_requested`
  verdict counts: an approval clears a previous objection (a cycle re-opened for
  an unrelated reason must not keep arguing a settled point) and a rejection
  ends the attempt rather than meaning "try again addressing this". Rounds are
  numbered (`_design_round`, capped at `MAX_DESIGN_ROUNDS`) and every wave of a
  round shares its number so the debate panel groups it rather than splitting
  the chair off. A refinement seats fewer positions than a first pass but never
  fewer than two — re-opening the full debate spends a second council's budget
  re-litigating the parts the reviewer accepted, while a single voice with no
  red team is not a debate at all.

  **"Request changes" chooses a route before it spends a meeting.**
  `deerflow.dbtl.revision_intent` reads the reviewer's objection — verbatim,
  alongside the positions already argued — and returns `chair_only` or
  `reconvene`. `chair_only` dispatches the chair alone over the recorded
  positions; `reconvene` runs the existing refinement round and contributes the
  verdict's roster note as the roster writer's adjustment, while the reviewer's
  own words still travel separately. **Every failure takes the cheap route** —
  no configured reader, a provider outage, an unparseable reply — because an
  unavailable reader must never be the reason four workers run. The round's
  note states which route it took and why: the failure this replaces was
  silence, where four workers ran, three died on an expired credential, and the
  only visible symptom was a review card that never came back.
  **A roster somebody confirmed outranks that route.** `chair_only` is the right
  default for an unattended refinement and the wrong answer to a person who has
  just read a roster of three and pressed **Start meeting** — the card described
  a council and one chair ran, which is the failure the roster proposal exists
  to prevent arriving by the route that avoids its cost. An approved proposal
  therefore suppresses the chair-only branch; the objection is not lost with the
  route, since it still travels into the round as the change request, so the
  seats argue the reviewer's point rather than the original question.

  **Build, Test, and Learn receive the approved design.** `_approved_design_brief`
  puts the human-approved Design package into `stage_context` with its content
  hash, since the approval bound a specific document and a stage naming only the
  path could silently work from a later revision. Only an _approved_ design
  travels; its absence is meaningful (work happening before the gate, not merely
  without context), and the read never raises because it runs on every
  Build/Test/Learn request.
  `council.request_context` is the single reader of per-request context: a
  run request carries `context` at the top level, but LangGraph relocates it to
  `configurable["context"]` before a node sees it, so code running on both sides
  must look in both places or silently read nothing on one of them.

- **Test can be a required gate or a decision the reviewer takes.**
  `dbtl.conditional_test` (default **false**) keeps today's rule: an approved
  Build always opens Test, and Learn waits for a human-owned validity
  assessment. Turn it on and an approved Build offers a second route —
  _Learn from this exploration_ — which records Test as explicitly **skipped**
  and opens Learn directly. The switch exists because one predictive check
  pack is pinned to the Test _stage_ rather than to the claim a Build made, so
  the ordinary deliverable here — a population-structure PCA, a QC
  distribution, a trait correlation plot — has no folds, no holdout, and no
  predictive ceiling, scores four of seven required checks as not-applicable,
  and lands on `inconclusive`, which cannot reach Learn at all. An exploratory
  pilot should not have to fail a confirmatory contract it never claimed to
  satisfy.
  Skipping is never silent success, and four rules make that true rather than
  aspirational. `skipped` is **its own** `StageStatus`, distinct from `locked`
  (still blocked) and `approved` (a review happened), because collapsing it
  into either is how "we chose not to validate this" becomes
  indistinguishable from "this passed". Only a human choice may skip: the
  typed `BuildDisposition` is accepted only on an approved Build, refused on
  any other stage or verdict rather than ignored, and refused outright once
  Test has started — skipping in-flight work would discard what nobody agreed
  to discard. The deployment rule is enforced at the **write boundary**, not
  only in the UI, since a frontend that can skip Test while persistence still
  treats the result as validated is the one failure this path exists to
  prevent. And an exploratory Learn may synthesize but **cannot create a
  knowledge candidate**, which makes the promotion block structural: no
  candidate means no claim, so promotion and publication have nothing to act
  on rather than relying on a downstream check somebody must remember. The
  skip is deliberately **not** a new table — the decision is already durable
  as the review row plus an append-only `learn_exploratory` transition row
  bound to the Build evidence hash and the reviewer, and a third record of the
  same act could only drift from those two.
- **Data Reconciliation can be a required gate or an optional stage.**
  `dbtl.reconciliation_required` (default **true**) keeps today's rule: an
  approved Design opens Reconciliation, and Build waits for a settled matrix.
  Set it false and an approved Design opens Build directly, while the stage,
  its endpoints, and its matrix stay available — it is _skipped_, never
  deleted, and a cycle already working the matrix stays advanceable in either
  direction so flipping the switch cannot strand one. The rule lives in
  `deerflow.dbtl.reconciliation_policy`, read by the state machine, the route
  menu, and the Build lineage writer so they cannot disagree; an unreadable
  config keeps the gate, because a deployment that cannot state its rule has
  not asked for the looser one. **The data guarantees move into Build/Test
  without becoming a pre-Build form**: Build workers name the exact workspace
  files they actually examined, the server computes and records their SHA-256
  bindings automatically, and Build refuses only when no real input was used or
  an input changed during execution. Test owns leakage, split, and validity
  checks against that lineage. People do not declare a dataset or paste a
  digest before Build can start; what optional mode gives up is the
  human-settled judgement matrix.
  In optional mode, later workers receive a server-owned provenance policy
  instead of the skipped gate's unsettled projection: the compatibility
  validity key `reconciled_inputs` means "bound input provenance" and is judged
  from Build lineage, so absent declarations or matrix rows cannot invalidate
  Test. One-click Build approval also normalizes `ready_for_build` to the Build
  checkpoint before advancing to Test. `LiveStageAdapter` prefers the single
  active stage row over a stale cycle checkpoint, allowing captures written by
  older code (`state=build`, Build approved, Test in progress) to recover at
  Test instead of rerunning Build.
  `generic-predictive:v2` is the current generic Test pack. It retains the
  cross-cutting predictive checks but does not make population structure,
  within-group analysis, or duplicates/relatedness mandatory unless a later
  explicitly selected pack says so; a worker may report those as limitations
  but cannot invent them as gates. In optional mode the backend replaces any
  client/worker `reconciled_inputs` verdict with the server-owned Build-lineage
  pass, and still refuses assessment when no lineage exists.
  `generic:build:v3` gives executable Build work twelve bounded model calls
  (143 LangGraph super-steps) rather than the four-call effective default that
  could only read the Design and one input before finalizing.
  `generic:test:v3` keeps the v2 bounded allowance and pins the authoritative
  `generic-predictive:v2` check list into the worker contract. Ordinary
  Build/Test/Learn worker events do not carry `council_seat`; only actual
  meetings do, and meeting seat identity carries its DBTL stage so the frontend
  cannot label Build work as a Design meeting.
  The rule is published through `/api/features` because the UI cannot infer
  it: a stage locked because it was skipped and one locked because it has
  not been reached are the same status, so `stageBlockReason` takes it as an
  argument rather than naming Reconciliation unconditionally.
- **A cycle awaiting changes does not argue with every message.** The hold
  rule used to skip `changes_requested` entirely, on the grounds that the
  verdict _is_ the request to argue again — true of the verdict, false of
  every message after it, so a cycle in that status convened a meeting for
  the word "hello". `_unreviewed_design_package` now holds for that status
  too, and the review endpoint's own refinement kickoff is recognised
  deterministically (`_is_refinement_kickoff`) so an unavailable interpreter
  cannot cost a reviewer the round their verdict asked for. Recording a
  verdict through the deck also invalidates the `dbtl-cycles` queries: the
  rail refreshed for its own controls but not for a decision taken on the
  deck, so an approved Design left Build reading "Locked".
- **Progressive-gate Phases 0-1** record every gate decision as an append-only
  edge on a Design/Build/Test/Learn stage graph (`dbtl_stage_transitions`);
  reconciliation is a Build-edge precondition, not a path node, so data work
  never writes an edge. The production Test-validity router consults that graph
  and refuses the legacy `return_to_reconciliation` destination before any
  state change; transition rows also refuse ORM update/delete.
  `dbtl.progressive_gate` (default off) gates the path read model and the
  progressive Design gate. A one-shot assessor labels remaining work
  `routine`, `standard`, or `high_stakes`; null configuration, outages, and
  malformed output fall back to standard. The gate itself is deliberately one
  question with three answers — **Approve**, **Revise**, **Park** — as a single
  radio group with a comment box and one button. Any depth records its verdict
  in that one action; depth changes how much justification is required, not how
  many clicks, so a high-stakes approval needs the reviewer's written rationale
  rather than a depth override. The deck uses one built-in editorial
  presentation style. There is no theme
  setting or skill-loading fallback, so every newly rendered review surface has
  the same canonical appearance. Its exact bytes remain hash-registered as the
  surface a person answers the gate through. Park keeps the Design
  open and routes ordinary cycle-scoped work to the lead agent with the exact
  evidence hash explicitly marked unapproved; any later gate decision clears
  the marker. Records accumulate with the flag off. Manual checkpoint manifests
  may record the expected head, assessment, offered routes, and next action. See
  [docs/plans/2026-07-29-progressive-dbtl-gate-plan.md](docs/plans/2026-07-29-progressive-dbtl-gate-plan.md)
  and [backend/AGENTS.md](backend/AGENTS.md) for the route-legality and
  transition-write contracts.
- **A paused Build is a decision waiting to be made, and somebody has to be
  able to make it.** Build stops for four different reasons — the plan is drawn
  and nobody has agreed to it, a phase declared a boundary, a step failed, or a
  worker cannot continue without one answer — and each raises one bound control
  in chat. Every option states what choosing it costs (Replan says plainly that
  finished phases are discarded; Retry says they are kept), nothing is
  preselected including the recommendation, and Hold is a decision rather than
  the absence of one. The exchange is durable: one control may be open per
  Build, a response is idempotent by request plus submission id, and a
  _different_ answer under that id conflicts instead of overwriting the one
  already recorded. Those records are also what make Restart and Replan mean
  anything — both move the digest chain, so a restart discards the committed
  work it exists to discard rather than replaying it. "Change the plan" is a
  second exchange whose words travel verbatim into the replan. Answers resolve
  against the card the server emitted, and while a control is unanswered a
  request that would otherwise be ordinary work is answering _that control_.
  A **Build work meeting** (`dbtl.build_work_meetings`) is the escalation for a
  question one exchange cannot settle: three seats, convened only when a person
  chooses it, advisory to the end — it returns options and a recommendation, and
  the same question comes back with that briefing above it. The plan
  confirmation card is behind `dbtl.build_plan_confirmation`; both default off
  beside `dbtl.build_workflow_steps`, and all three are on in the isolated
  manual DBTL profile. A phase that asks for input surfaces its exact question
  through the same durable control and resumes with the recorded answer. Phase
  replay binds and re-hashes the workspace inputs it actually read, while any
  failed step/control write stops dispatch; an incomplete durable workflow or
  unregistered review deck cannot be submitted for review. Migration `0028`
  forward-repairs stamped legacy step tables that lack `phase_slot`, preserving
  their audit rows while restoring the phase/attempt uniqueness guarantees. The project rail's
  **Build plan** section replaces
  Blockers with a read-only projection of the same server view the transcript
  uses; open work items keep their signal as a count on the stage they belong
  to. See [backend/AGENTS.md](backend/AGENTS.md) and
  [frontend/AGENTS.md](frontend/AGENTS.md) for the control transport and the
  rail's one-moving-indicator rule.

## Commands: Root vs. Module

**Root `make` targets drive the whole stack** (run from the repo root):

```bash
make setup       # Interactive setup wizard (recommended for new users)
make doctor      # Check configuration and system requirements
make support-bundle  # Generate redacted troubleshooting summary, AI issue draft, and optional zip
make config      # Generate local config files from the examples
make check       # Check that required tools are installed
make install     # Install all dependencies (frontend + backend + pre-commit hooks)
make dbtl-manual-init                          # Create the isolated DBTL manual-test profile
make dbtl-manual-dev                           # Run the full stack against that profile
make dbtl-manual-capture SCENARIO=chair-choice # Capture its quiet DB + project state
make dbtl-manual-restore SCENARIO=chair-choice # Restore it after `make stop`
make dbtl-manual-restore-hot SCENARIO=chair-choice # Swap it under the running stack
make dev         # Start all services with hot-reload (Gateway + Frontend + Nginx)
make start       # Start all services in production mode (local, optimized)
make stop        # Stop all running services
make up / down   # Build/stop the production Docker stack (browser at localhost:2026)
make docker-start / docker-stop / docker-logs   # Docker development environment
```

Docker log and restart commands resolve `DEER_FLOW_ROOT` from the current
checkout before invoking Compose, matching the start and stop commands.

Run `make help` for the full list.

**Per-module commands drive a single module** (run inside that module):

```bash
# Backend (see backend/AGENTS.md for the full set)
cd backend && make dev        # Gateway API with reload (port 8001)
cd backend && make test       # Backend test suite
cd backend && make test-core  # Faster local subset (skips live-LLM + unused-subsystem tests)
cd backend && make lint       # ruff check
cd backend && make format     # ruff format

# Frontend (see frontend/AGENTS.md for the full set)
cd frontend && pnpm dev       # Dev server with Turbopack (port 3000)
cd frontend && pnpm check     # Lint + type check (run before committing)
cd frontend && pnpm test      # Unit tests
```

Rule of thumb: **root `make` = the full application**; **`backend/Makefile` and `frontend/`
(`pnpm`) = per-module work.**

Gateway development launchers exclude `backend/tests/` from Uvicorn's reload
watcher. Test edits must not restart the live Gateway; runtime source and
configuration changes remain hot-reloaded.

The local DBTL manual pipeline lives in `scripts/dbtl_manual.py` and stores all
generated state under gitignored `.deer-flow/manual-dbtl/`. Its generated
profile forces unified SQLite plus an isolated `projects.root`, enables the
graph, Design-deck feedback, and the progressive-gate cycle timeline, and disables
background memory/scheduler/channel writers. A scenario is a matched SQLite backup + project tree + integrity
manifest captured only at a quiescent human-decision boundary. Restore refuses
while Gateway port 8001 is listening, validates both hashes, backs up the prior
isolated live pair, and never touches the normal configured database or project
root. `restore --hot` (`make dbtl-manual-restore-hot`) inverts only the
running-stack rule and **requires** the stack to be up: it keeps every other
safety behavior, refuses unless the live database is quiescent (the same
active-run/action guard capture applies, re-checked immediately before the
swap), then touches one watched backend source file so the Gateway's
`uvicorn --reload` watcher recycles that process alone, and polls the Gateway
until it answers. The frontend and nginx keep running, so the developer only
hard-refreshes the browser. This is a developer acceleration tool, not a
production stage bypass; it adds no Gateway route or config flag; see
`docs/dbtl-manual-test-pipeline.md`.

Host-side pnpm consumers, including the root/frontend Makefiles and local diagnostic scripts, must run through `scripts/pnpm.py`. The runner preserves direct `pnpm`/`pnpm.cmd` priority, falls back to `corepack pnpm`, and is invoked from `frontend/` so Corepack honors the package-manager version pinned by that project.

## Where to Go Next

- Backend work → **[backend/AGENTS.md](backend/AGENTS.md)**
- Frontend work → **[frontend/AGENTS.md](frontend/AGENTS.md)**
- Setup & install → **[Install.md](Install.md)**, **[CONTRIBUTING.md](CONTRIBUTING.md)**
- Project overview & usage → **[README.md](README.md)** (translations: `README_zh.md`,
  `README_ja.md`, `README_fr.md`, `README_ru.md`)
- Security policy → **[SECURITY.md](SECURITY.md)**
- Changes → **[CHANGELOG.md](CHANGELOG.md)**
- Cutting a release → **[RELEASING.md](RELEASING.md)**

## Progressive DBTL feedback surfaces

Progressive-gate Phase 2 keeps the physical
`dbtl_design_feedback_{surfaces,actions}` names for a one-release compatibility
window, while migration `0025` adds server-owned `stage` and
`surface_revision` fields. New code uses the `/stage-feedback/` API aliases and
generalized repository methods; captured Design decks continue through the
legacy aliases. The artifact parent renders the stage, lifecycle, and revision.
Phase 3 review-meeting contracts are pinned as
`generic:{build,test,learn}-review:v1`; rollout flags live under
`dbtl.stage_meetings` and default off independently. The convening decision now
reaches the read model: `surface_meeting_gate` derives a stage's gate from the
assessment its own deck was rendered against (falling back to `standard`, never
`routine`, when that is missing), and `apply_meeting_gate` adds
`convene_review_meeting` when one may be convened and withholds the transition
intents while one is required — never the chair or park intents, or convening
the meeting that unlocks the gate would be unreachable. Design surfaces get a
null gate and are untouched.

Phase 3A registered a review page for Test; Phases 3B and 3C extend the same
page to Build and Learn, so **every `REVIEW_MEETING_STAGES` stage now registers
a pre-meeting review page of its own**, rendered from that stage's evidence
rather than from a chair result, because no meeting has happened when it is
written. Each carries the transition assessment (so the meeting gate has a
difficulty to read) and **deliberately no route menu** — the stage's verdict is
taken at review time against this evidence (Test's outcome is computed there
from the validity pack; Build's verdict and Learn's promotion are decisions a
human takes then), and a menu rendered beforehand would pre-empt the decision
it exists to record. Learn's page can never promote or publish — no deck
intent may stand in for those separate human acts. The page is registered
whether or not `progressive_gate` is on; only the gate rides on that flag.

**Convening now runs a real meeting, and a meeting is a reader.** The
`convene_review_meeting` intent starts a run in the originating conversation
carrying a **server-owned** `dbtl_review_meeting_stage` — taken from the stage
the server registered the deck against, never from the request, or a deck could
convene a meeting over evidence it was never rendered from. The supervisor
routes that request straight to `LiveStageAdapter._execute_review_meeting` and
skips the Design preflight entirely: a review meeting seats its own roster over
recorded evidence, so raising the Design participant card would ask about the
wrong meeting. The adapter runs the pinned `generic:<stage>-review:v1` contract
with the same position/red-team/chair shape the Design council uses — one
reviewer is an opinion, not a meeting — and deliberately **skips the
`awaiting_review` refusal** that ordinary execution applies, because that status
is precisely when a meeting is legal: it reads the stage's evidence and never
re-runs it, so the pack a person is reading cannot move underneath them. What it
records goes through `sanitize_meeting_attachment` on the way into the row, not
merely as an available helper, so a chair cannot restate a computed Test outcome.
It writes a `<stage>_review_meeting` artifact beside the core result and
registers its own `stage_review` deck.

`review_meeting_recorded` closes the gate, derived from that artifact on that
stage attempt rather than a stored flag — the same reason `is_current` is
derived on a surface: a boolean column could disagree with the evidence a
reviewer opens. Scoping to the attempt is load-bearing, since a revised attempt
receives a new assessment and must not inherit the previous attempt's meeting.
Before this, `meeting_completed` was a parameter nothing passed as true, so a
_required_ meeting could never be satisfied.

**A deck-started round that dies mid-flight says so in chat.** A revision
round ("Request changes") and a convened review meeting both run in the
originating conversation, and their explanation rides on their own reply — so
a run that ends in error used to say nothing. `app.gateway.dbtl_round_watch`
polls the run's durable status on a detached task and, on a terminal failure
(`error`/`timeout`/`interrupted`), appends one server-owned visible
`llm.ai.response` to the thread feed naming what stopped and how to retry. It
is deliberately a visibility aid: it never touches cycle state, writes nothing
for a successful run (the round's own reply is the explanation), is
process-local (a Gateway restart loses the watcher), and its message identity
binds the surface and run so replays dedupe through `put_if_absent`. Tests:
`tests/test_dbtl_round_failure_watcher.py`.

**The timeline can open the deck that decided an edge.**
`list_stage_transitions` joins `decided_in_thread_id` onto each transition row
from the deciding surface's `originating_thread_id` — the transition names
only the surface, and the surface alone knows which conversation it may
answer; a client cannot resolve that itself because the surface read endpoint
requires a viewer thread the project rail does not have. Off-deck decisions
carry null.

The Build (3B) and Learn (3C) meetings ride the same generic
`_execute_review_meeting` path and remain gated behind their own
`dbtl.stage_meetings.*` flags. Tests:
`tests/test_dbtl_test_review_surface.py`, `tests/test_dbtl_stage_review_pages.py`,
`tests/test_dbtl_meeting_gate_surface.py`,
`tests/test_dbtl_review_meeting.py`, `tests/test_dbtl_review_meeting_dispatch.py`.

## Cross-Cutting Conventions

These apply repo-wide; module guides own the module-specific detail.

- **Answer short, in plain words** — chat replies are for a busy person, not a log
  file. Lead with what happened or what you found, keep it to a few sentences, and
  use ordinary language instead of internal identifiers, code names, or jargon
  when a plain phrase says the same thing. Spell a term out the first time it is
  needed. Skip the reasoning, the options you did not take, and the caveats unless
  they change what the reader would do next — or unless they ask. This governs
  replies only; commit messages, code comments, and these guides stay as detailed
  as they are.
- **Documentation update policy** — keep docs in sync with code: update `README.md` for
  user-facing changes and the relevant `AGENTS.md` for development/architecture changes in
  the same change set.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/` (TDD is mandatory there; see [backend/AGENTS.md](backend/AGENTS.md));
  frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` (backend) / `pnpm check` (frontend). Backend
  CI enforces `ruff format --check`, so formatting must be clean before a push.
