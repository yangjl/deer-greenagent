# Debate Plan: DBTL as a LangGraph Workflow over DeerMem

Date: 2026-07-25
Primary role: Designer
Supporting roles: Builder (phased plan), Tester (independent validation), Learner (knowledge capture)
Status: draft for human review
Branch: `feat/greenagent-dbtl-implementation`

## Classification

- Primary role: Designer.
- Supporting roles: Builder plans and implements after human approval; Tester
  validates independently; Learner proposes knowledge candidates.
- Phase order: Designer debate → human review → Builder phased plan → Builder
  implementation → Tester → Learner.
- Human-review gates: `research-direction`, `methodology`,
  `project-file-mutation`, `knowledge-promotion` (per
  `.greenagent/policies/review-gates.yaml`).
- Next artifact: human decision record, then a schema-valid
  `designer_debate` + `design_package` pair for the cycle.
- Cycle weight: `full`. Cycle class: `computational`.

## User Input Retained

> "Designer, use **Debate Mode** to brainstorm how to implement our redesigned
> **DBTL (Design–Build–Test–Learn)** workflow using DeerFlow's **DeerMem**
> system. […] we have fundamentally shifted DeerFlow from a chat-oriented
> framework to a **project-centric AI research platform**. Our goal is to build
> a long-term, durable AI research harness for plant science and plant
> breeding, where each project maintains persistent memory, structured
> workflows, and continuous knowledge accumulation (which can be shared after
> user promotion).
>
> After receiving user input, the AI should first determine whether the request
> should initiate or continue a **DBTL cycle**. If a DBTL workflow is
> appropriate, the system should interactively clarify the user's intent,
> gather any missing information, and guide the user through the corresponding
> DBTL process.
>
> […] instead of treating Designer, Builder, Tester, and Learner as fixed
> agents, we will implement the DBTL process as a **LangGraph workflow**. Each
> DBTL stage should be represented as a graph node (or subgraph) and may be
> executed by one or more specialized agents depending on the task. The
> workflow should remain modular and extensible […]. Use the existing infra and
> code as much as possible."

Retained without interpretation: the graph is directed; the roles are not fixed
agents; reuse is a constraint, not a preference; knowledge sharing is gated on
user promotion.

## Problem

DeerFlow now owns the project-first substrate — a project is a real folder the
human can open in Finder, the sandbox mounts it, `threads_meta.project_id` is
durable, and `ProjectContextMiddleware` tells the model where it lives. What is
missing is the *research process* on top of it, and the *knowledge that
survives it*.

Concretely, five gaps block the DBTL redesign:

1. **No durable cycle.** `frontend/src/core/workspaces/cycle-planning.ts`
   persists cycles and to-dos to `localStorage` under
   `deer-flow:project-cycle-plan:<projectId>`. `projects.dbtl_phase` exists
   (`persistence/workspaces/model.py:66`) but has no mutator — there is no
   `update_project` in `WorkspaceRepository`. A season-class breeding cycle
   cannot live in a browser.
2. **Stage execution is a stub.** `agents/dbtl/orchestrator.py` is a Phase-1
   skeleton whose `_stub_produce_artifacts` writes `{"stub": true}` placeholders.
   Its own docstring defers real execution to "Phase 2".
3. **Three vocabularies disagree.** greenagent's `lib/dbtl.js` has 27 states
   (19 normal + 8 exception) with two rework loops; `agents/dbtl/state.py`
   mirrors a linear 13-edge subset by hand, omitting `designer-debate`,
   `conditional-pass`, `changes-requested`, `redesign-required` and every
   exception state; the frontend and `projects.dbtl_phase` use four
   (`design|build|test|learn`). Nothing reconciles them, and greenagent exposes
   no `dbtl describe --json`, so the Python tables are an unpinned hand copy —
   exactly the drift `single-source-of-truth-for-constants` warns against.
4. **Knowledge has no promotion path in the product.** The pipeline exists on
   paper (`Learn output → Working Memory → human promotion → Validated
   Knowledge`) and in greenagent's files, but no DeerFlow code implements it.
5. **There are already three memory systems, and none of them is the one the
   redesign describes.** This is the finding that reframes the whole question,
   so it gets its own section below.

### The three-memory-systems problem

| System | Location | Shape | Authority today |
| --- | --- | --- | --- |
| DeerMem facts | `.deer-flow/users/{bucket}/agents/{agent}/facts/**.md` | atomic content + category + confidence + staleness window | agent prompt context |
| greenagent knowledge | `~/.greenagent/knowledge/methods/*.md` + `index.yaml` | full method documents with `evidence_strength`, `provenance`, `contradicts`, `approved_by` | human-approved, promotion-logged |
| greenagent project memory | `MEMORY.md`, `doc/{PROJECT_STATUS,DECISIONS,DAILY_LOG,DATA_PROVENANCE,REVIEW_QUEUE}.md` | human-written markdown in the repo | project-owned, never overwritten |

`MEMORY.md` states the goal explicitly: *"project context stays portable
instead of being trapped inside one AI tool."* Any design that makes DeerMem
the authority for validated findings violates a contract the project already
published. **The risk in this cycle is not choosing the wrong graph — it is
shipping a fourth memory system.**

DeerMem is also project-scoped *already*, which most of the design docs predate:
`agents/memory/scope.py` derives a bucket
`{user_id}--project--{sha256(project_id)[:24]}`, and
`DynamicContextMiddleware._project_memory_update` swaps the injected snapshot
when the project changes. So the question is not "can DeerMem be
project-scoped" but "what is it allowed to be authoritative about."

One further fact is load-bearing enough to state on its own, because it decides
the answer rather than informing it:

> **The DeerMem project bucket is per-(user, project), deliberately.** Its
> docstring says so — *"two users opening the same project id cannot share
> memory."* Promotion into DeerMem would therefore share a validated finding
> with nobody. **"Knowledge that can be shared after user promotion" is not
> implementable in DeerMem as it stands**, whatever else is decided.

## Debate

Five perspectives were developed independently against the live codebase.

### Position 1: Memory & Knowledge Architect

Perspective: evidence / storage

Position: DeerMem can carry the DBTL knowledge lifecycle if the three scopes
map onto *separate buckets* rather than separate labels — personal, project
working, and project validated — with the validated bucket rooted inside the
human-visible project folder and its LLM-driven passes disabled.

Assumptions:

- Unknown fact keys round-trip losslessly. Verified: `_render_fact_markdown`
  writes every key except `content`/`title` into YAML frontmatter and
  `_parse_fact_markdown` reads it back, so provenance needs no storage change —
  only a `metadata` passthrough at the `MemoryManager` boundary.
- Project members should share one project bucket; membership is enforced
  upstream by `WorkspaceRepository`, not by path opacity.
- `projects.id` is path-safe, so the bucket hash can become reversible.

Concerns:

- `category` is a **closed set**. `CORE_CATEGORIES` = `{preference, correction,
  context, goal, behavior, identity, constraint, decision, other}`; anything
  else is coerced to `other` with the original preserved in
  `categoryExtension`. A `validated` category is therefore not implementable,
  which forces bucket separation rather than label separation.
- `storage_path` and every staleness/consolidation setting are **process-global**
  (`get_memory_manager()` is a singleton over one `DeerMemConfig`). Per-scope
  settings are not expressible without a wrapper backend.
- `max_facts` is schema-bounded `ge=10, le=500` with confidence-ranked silent
  eviction. Beyond 500 claims, validated knowledge must move behind a
  `retrieval_adapter`.
- Shipping a second markdown knowledge store next to
  `~/.greenagent/knowledge/` gives three sources of truth. Dissent recorded:
  validated knowledge should go to Postgres `knowledge_claims`, and DeerMem
  should carry Working Memory only.

### Position 2: Graph / Orchestration Architect

Perspective: methods / runtime

Position: The lead agent stays the entry point and the router; a thin cycle
graph with four node *kinds* (`resolve` / `run_stage` / `gate` / `commit`)
replaces the 13-state mirror; stages execute through the existing
`SubagentExecutor`; roles are YAML data, not Python classes.

Assumptions:

- greenagent stays the transition authority; Python never decides legality.
- The web frontend will not adopt subgraph streaming, so root-namespace
  `task_*` events remain the only working progress channel.
- One active cycle per project, enforced by a partial unique index.

Concerns:

- `assistant_id` is **durably pinned per thread** (`threads_meta`, indexed,
  read back by `resolve_thread_assistant_id`, and graphs are cached per
  `(assistant_id, mode)`). `DBTLState` carries only `messages` + `dbtl_*` — no
  `sandbox`, `thread_data`, `todos`, `artifacts`, `uploaded_files`,
  `skill_context`, `summary_text`. **Pinning a project conversation to
  `dbtl_orchestrator` silently deletes its sandbox, file browser, uploads and
  summarization.** This kills the "separate assistant_id" option outright.
- Nesting `make_lead_agent` as a stage subgraph inherits named harms:
  `TitleMiddleware` rewrites the thread title from inside a stage;
  `ClarificationMiddleware` returns `Command(goto=END)`, which from a nested
  position terminates the *orchestrator*; `MemoryMiddleware` writes project
  memory from a stage's internal chatter; the goal loop races the cycle loop.
- A multi-day gate modelled as a live `interrupt()` leaves a zombie
  `interrupted` run row and a pending checkpoint task blocking the thread.
- Two graph factories both calling `freeze_checkpoint_channel_mode` share a
  process-global singleton; the interaction is untested.

### Position 3: Skeptical Reviewer / Minimalist

Perspective: skeptical-reviewer

Position: Ship DBTL as four skills plus a router skill over the existing goal
system, `write_todos`, `ask_clarification`, and a bash call to
`greenagent dbtl check-transition`. Write no graph code until a real cycle has
failed in a way prompts cannot fix.

Assumptions:

- Single researcher. Verified: `database.backend: sqlite`,
  `checkpointer.type: sqlite`, `strict_user_scope: false`, no PostgreSQL, no
  populated `workspace_members`. The shared-record argument has no user yet.
- The DBTL hypothesis is untested — no cycle has ever run through
  `orchestrator.py`.

Concerns (the strongest findings in the debate, and all verified):

- **`interrupt()` renders nothing today.** `interrupt` appears in the entire
  harness only in `agents/dbtl/orchestrator.py:126`, and
  `frontend/src/core/threads/hooks.ts:1102` and `:1115` hardcode
  `interrupts: {}`. A `dbtl_review` interrupt will fire, freeze the run, and
  display nothing. The load-bearing "human review" feature is currently a dead
  end.
- Scheduled runs strip `ask_clarification` via `context.non_interactive=true`,
  but a graph that `interrupt()`s has no equivalent escape — a scheduled cycle
  hangs at `approved-for-build` forever.
- `SubprocessGreenAgentGate` makes an external Node CLI on `$PATH` a hard
  runtime dependency of a core run target, with a 30 s blocking
  `subprocess.run` inside async graph nodes, against the blocking-IO gates.
- A 13-state hard-gated machine **cannot express `light` or `retroactive`
  cycles at all**, contradicting approved user-global knowledge
  (`lightweight-cycle-classification`).
- The human rescoped away from DBTL once already and spent eight review rounds
  asking for simpler and more concrete. Re-proposing the maximal version is
  itself a disagreement that must route to the human.
- Conceded: an unenforced gate is not a gate. The answer offered is that
  `greenagent dbtl check-transition` is *already* the enforcement layer and
  works from bash.

### Position 4: Evidence & Governance

Perspective: evidence

Position: The audit envelope, not the graph, is the deliverable. Every stage
execution and every transition needs an immutable record with actor, authority
reference, artifact hashes and run id; approval needs its own persisted row,
not a checkpoint interrupt; promotion is an immutable human decision.

Assumptions:

- greenagent's artifact validators are the schema contract and should not be
  re-implemented in Python.
- `.greenagent` files are a projection; Postgres is the intended authority.

Concerns (four are live defects in the committed skeleton, not future risks):

- **The gate accepts anything truthy.** `_parse_human_decision`
  (`orchestrator.py:69-75`) takes a bare truthy string as *both* `approved` and
  `authorization`. Nothing verifies the reference exists, is single-use, or came
  from a human.
- **The actor label is derived from the target state, not the actor.**
  `transition()` sets `actor = "human" if target in HUMAN_GATED_TARGETS` — so
  the CLI is told "human" regardless of who actually resumed the run. This
  defeats the separation-of-powers requirement at its only enforcement point.
- **A scheduled run parks forever.** `services.py` stamps
  `context={"non_interactive": True}` for scheduler launches, but only the lead
  agent reads it (to drop `ask_clarification`). `build_dbtl_graph` never reads
  it, so a scheduled DBTL run reaches `interrupt()` and holds
  `uq_runs_thread_active` indefinitely.
- **Replay re-satisfies a spent gate.** Branch/regenerate replays a checkpoint
  verbatim, so the same approval payload passes the same gate twice. Checkpoint
  state is not an audit record.
- `GreenAgentGate` exposes no tool version, so persisting a gate result without
  one is theatre — the evaluation is not reproducible.
- `run_events` cannot be the ledger: its record schema is keyed
  `thread_id, run_id, seq` with a closed `category` set and carries no
  `project_id`, `actor_type`, or `authorization_ref`. It is a stream projection
  subject to compaction — observability only.
- DeerMem cannot express retraction: `_normalize_fact` raises
  `fact.status must be 'active'; deletion is physical`, and there is no version
  chain. Provenance must also sit at the fact's top level, not under `source` —
  `_legacy_source_value` collapses `source` to a string on every public read.
- The live project already violates its own contracts: the cycle's `state` is
  `awaiting-human-review`, which is **not in `STATES`** — `greenagent dbtl
  validate` would fail on this project's own cycle record today. Handoffs use
  `to_role: "human-reviewer"`, which `createHandoff` would reject. Project agent
  contracts were widened with `execute` permissions despite the documented
  `project-narrows-only` merge rule, and `policies/tools.yaml` still declares
  `horizon: dbtl-phase-1-contract-only` with execution disabled.
- Separation of powers is schema-enforced and must not regress:
  `test_result.implementation_edits` must be `false`;
  `designer_debate.minority_positions_preserved` must be `true`;
  `knowledge_candidate.promotion.automatic` must be `false`; a failed test
  cannot become a fact candidate.

### Position 5: Plant Science / Breeding Practitioner

Perspective: biology / statistics

Position: The 13-state machine is a software-delivery pipeline and fails at the
two points where real cycles actually break — data reconciliation before
modelling, and validity assessment after results. Replace it with a
research-native state set, and make cycle *class* (computational / season /
program) orthogonal to cycle *weight*.

Assumptions:

- Raw data in `data/` is read-only and sacred.
- `~/.greenagent/knowledge/methods/*.md` is representative of what accumulates.

Concerns:

- **No data-reconciliation state.** The two highest-value knowledge entries are
  about inputs before modelling: `triangulate-data-sources` (four sources
  encoded N treatment for BGEM 2023 Havelock, none fully correct) and
  `biological-plausibility-qc-gate` (a field book reversed N for hybrids in
  BK3/BK4, producing HN < LN at p=3.5e-9 and −18.6 g when the truth was
  +18.6 g). The graph goes `designing → approved-for-build` with nothing in
  between.
- **`pass` is a Boolean.** Pooled BGEM accuracy of 0.92 would have been a
  `pass`; the within-population truth was 0.20–0.32. High accuracy must *raise*
  the review bar, not lower it.
- **No inconclusive terminal.** One terminal state plus a required
  `knowledge_candidate` artifact structurally pressures the agent to
  manufacture a finding to reach `completed`.
- DeerMem's fact model fits 2 of 5 real knowledge kinds. Format specs
  (`gensel4r-v4-input-format`, 3.1 KB with error→cause→fix tables and a
  supersession note) are not atomic. Negative results
  (`bgem-hybrid-tkw-unpredictable-1k-markers`: 10/10 folds r ≈ −0.11, method
  agreement r = 0.86–0.99 ruling out implementation artifacts, three competing
  causes, an explicit review trigger) cannot be compressed into a scalar
  confidence. Scoped claims retrieved out of scope are actively harmful.
- Abandonment triggers, stated as product requirements: writing an unapproved
  fact and citing it back; hiding the actual `sbatch`/`.inp`/R code; ceremony
  tax on a 40-minute check; silent ID mangling; re-running a 6-hour array
  because the harness lost state; writing to `data/`; reporting 0.92 with
  enthusiasm.

## Agreements

These converge across all five positions and become design constraints.

1. **The lead agent remains the entry point and the triage surface.** No new
   `assistant_id` for interactive project work — pinning a thread to
   `dbtl_orchestrator` would strip its sandbox, uploads, files and
   summarization.
2. **greenagent stays the deterministic gate.** The graph proposes; `greenagent
   dbtl check-transition` permits. Legality is never re-implemented in Python.
3. **Cycle state must be durable and project-owned**, outliving both the
   conversation and the checkpoint. `localStorage` is not a research record.
4. **Do not use live `interrupt()` for gates that outlive a sitting.** The
   frontend renders nothing for interrupts today, scheduled runs cannot answer
   them, and a week-long interrupt is a zombie run. Reuse the shipped
   `ask_clarification` → `artifact.human_input` → Human Input Card path, plus a
   persisted gate-request row.
5. **Stages execute through `SubagentExecutor`**, not nested `make_lead_agent`
   and not bare `.ainvoke` — it is the only option that already emits
   `task_*` run events, accounts tokens, enforces `max_turns`/token/loop caps
   with `stop_reason`, and preserves checkpoint lineage.
6. **Roles are data, not code.** `.greenagent/agents/*.yaml` +
   `.greenagent/skills/{role}/` map structurally onto `SubagentConfig`. Adding
   a documentation or knowledge-synthesis role must be a YAML file.
7. **DeerMem is a prompt-context projection, never the authority for validated
   findings.** It has no joins, no transactions, no referential integrity,
   substring-only search, a 500-fact cap with silent confidence-ranked
   eviction, LLM-driven staleness pruning and lossy consolidation, and an LLM
   with write access to its own contents.
8. **Knowledge promotion is human-gated, immutable, and cannot land in
   DeerMem.** No run, schedule, or agent may promote by changing a label; and
   because the DeerMem project bucket is per-(user, project), a promoted claim
   must live somewhere a second member can read it. DeerMem holds at most a
   pointer.
9. **Negative, contradictory and inconclusive results are first-class.** The
   state set must have an inconclusive terminal and must not require a
   knowledge candidate to close.
10. **Ceremony must scale with irreversibility.** `full|light|retroactive`
    weight is approved user-global knowledge and the current machine cannot
    express it.
11. **Any duplicated state table needs a contract test.** greenagent exposes no
    `dbtl describe --json`, so `state.py` is an unpinned hand copy that has
    already drifted.

## Disagreements To Resolve

Per `unresolved-design-disagreements-must-route-to-human`, Builder must not
resolve these through implementation choices.

1. **Build the graph now, or run skills-first?** The human's direction in this
   cycle is explicit ("we will implement the DBTL process as a LangGraph
   workflow"). The skeptic's falsifiable counter-proposal — run three research
   questions with and without staged skills before writing graph code — is
   preserved as a minority position and is cheap enough to run in parallel.
2. **Whose state vocabulary wins?** greenagent's 27 software-delivery states,
   or the practitioner's research-native set (`requested → scoped →
   data-reconciled → analysis-designed → executing → results-in → validated →
   interpreted → knowledge-proposed → closed`, with `abandoned`)? Changing it
   means changing `lib/dbtl.js`, which lives in a separate repo. This is the
   deepest fork in the debate.
3. **Where does validated knowledge live, given it cannot live in DeerMem?**
   Durable markdown in the project folder (the shape
   `~/.greenagent/knowledge/methods/*.md` already uses), SQL `knowledge_claims`,
   or both with one designated authoritative. Note the architect's primary
   position — a separate DeerMem validated bucket — is only viable if the
   per-(user, project) bucket key is *also* changed to drop `user_id`; that
   change is itself a decision, not an implementation detail.
4. **Is a `greenmem` wrapper backend justified**, or should per-scope settings
   wait until a second scope actually needs different staleness rules?
5. **Postgres now or later?** The design docs require it; the deployment is
   SQLite with one user. Durable cycles do not strictly need Postgres; the
   promotion ledger's immutability guarantees are weaker without it.
6. **Do cycles nest?** A season cycle containing N computational cycles is a
   parent/child relation the current model has no field for.
7. **Repair or supersede the existing violations?** The live cycle record has
   an invalid `state`, handoffs bypassed the CLI, and agent contracts were
   widened against `project-narrows-only`.

## Proposed Design Direction

The recommendation is a **thin graph over durable records, with a fat
role-and-skill layer** — the graph supplies orchestration and enforcement; the
roles and skills supply the behaviour; the files and database supply the truth.

### Direction 1 — Triage lives in the lead agent, driven by injected context

The "is this a DBTL cycle?" decision should be a model decision with a
deterministic prior, not a classifier node. A `DbtlContextMiddleware` placed
beside `ProjectContextMiddleware` in the lead chain injects a request-only
`<dbtl_context>` block — active cycle, state, blocking gate, missing artifacts,
whose move it is — read from the durable cycle record via the already-stamped
`context["project_id"]`. A small tool family (`dbtl_status`, `dbtl_open_cycle`,
`dbtl_run_stage`, `dbtl_submit_gate`) lets the model act on it.

This satisfies the interactive-clarification requirement with zero new
primitives: with no active cycle the model sees `<dbtl_context>none</>` and uses
the existing `ask_clarification` to gather intent before opening one. The
clarification content should come from the GreenAgent templates that already
exist — `.greenagent/skills/designer/templates/clarification-intake.md` for the
structure, and the practitioner's twelve-question list for a genomic-selection
cycle as the domain instance. Ordinary chat is untouched, and the project-first
UX (`/workspace/<slug>/<thread_id>`) needs no assistant picker.

### Direction 2 — Four node kinds, not thirteen; roles as data

The cycle graph should collapse the state machine into node *kinds* rather than
mirroring it:

```
START ─▶ resolve ─┬─▶ run_stage ─▶ verify ─┬─▶ commit ─┬─▶ resolve   (≤1 stage/turn)
                  │       │ Send(...)      │           └─▶ END  closed
                  │       ├ role: designer │
                  │       ├ role: tester   └─▶ END  blocked (missing artifacts)
                  │       └ role: doc-writer
                  └─▶ gate ─┬─ human present ─▶ Human Input Card ─▶ END
                            └─ async        ─▶ persist gate request ─▶ END
```

The six work states map to `run_stage`, the four review states to `gate`, the
three marker states to `commit`. `Send` (available in the pinned langgraph
1.2.9) fans one stage out to several role agents when a stage needs more than
one specialist, which is precisely the "one or more specialized agents"
requirement. Keep `HAPPY_PATH` / `ARTIFACT_REQUIREMENTS` / `HUMAN_GATED_TARGETS`
as **data** and keep the CLI as the authority, so a greenagent schema change
does not require a graph edit.

Each `run_stage` invocation resolves a `SubagentConfig` from
`.greenagent/agents/<role>.yaml` plus that role's skill pack. `DBTLState` should
*subclass* `ThreadState` rather than replace it, so role agents keep a sandbox
and the existing schema-adaptation helpers keep working; `dbtl_*` channels must
not be merged into the base `ThreadState`, which would add them to every plain
chat thread's delta channel table.

### Direction 3 — Three memory lanes, and DeerMem is the index

This is the core of the answer to "using DeerMem", and it deliberately avoids a
fourth store:

| Lane | Store | Holds | Authority |
| --- | --- | --- | --- |
| Working memory | DeerMem project bucket (already exists) | provisional operational context, revisable, unvalidated | none — prompt projection |
| Durable record | project folder: `.greenagent/`, `doc/*.md`, `knowledge/` | artifacts, decisions, provenance, promoted methods | human-visible, git-friendly, portable |
| Control & audit | SQL | cycle state, transitions, gate requests, promotion ledger | authoritative |

The Learn stage writes a schema-valid `knowledge_candidate` — greenagent's
existing artifact, with `sources`, `project_evidence`, `evidence_grade`,
`scope`, `limitations`, `contradictions`, `status: "candidate"` and
`promotion.automatic: false`. It never writes validated knowledge. On human
approval, promotion writes the durable markdown entry (the shape
`~/.greenagent/knowledge/methods/*.md` already uses, which carries the
`evidence_strength`, `provenance`, `contradicts` and `review_trigger` fields the
practitioner requires and DeerMem lacks), registers it in `index.yaml`, appends
to the promotion log, **and** writes a short DeerMem fact that *points at* the
file.

DeerMem then does what it is genuinely good at — token-budgeted injection of
the right context at the right moment — while the claim itself lives in a file
the human can read, diff, and carry to another tool. The 500-fact cap stops
being a data-loss risk because facts are pointers, not payloads. Provenance
rides in fact frontmatter (verified to round-trip losslessly), `topics` carries
the cycle id, and retrieval scoping is a hard filter on species/panel/trait, not
a similarity score.

Two configuration consequences follow immediately and cheaply:
`consolidation_enabled` must stay `false` for any project bucket, and the
promotion pointer category must be added to `staleness_protected_categories` and
`guaranteed_categories`.

### Direction 4 — Gates are records, and the human answers where they already are

A gate writes a durable gate-request row and returns a normal terminal message
carrying the existing `artifact.human_input` payload, so the run ends cleanly.
Resumption is a **new run**, never `Command(resume=...)`. The approval record
must carry actor, a documented authorization reference (greenagent rejects
`human-directed`), the artifact hashes evaluated, the gate input and output, the
gate tool version, and the run id. The queue surfaces in `doc/REVIEW_QUEUE.md`,
which greenagent's project-memory protocol already defines for exactly this
purpose.

The graph must then *read* that record rather than trust a resume payload, and
require a matching idempotency key, a matching gate evaluation, an unchanged
prior revision, and an unconsumed review row. Two rules follow directly from the
defects above: the committed `actor_type` is the **reviewer's** principal, never
a constant derived from the target state; and a review whose principal is
`is_internal=True` is rejected outright, which is how an agent is prevented from
approving its own gate — reusing the server-owned trust rule that already gates
`non_interactive` and `channel_user_id`.

Retain the `dbtl_orchestrator` assistant_id strictly as the **headless /
scheduled** entry point — and make its gate node read `non_interactive` and
terminate with an error *before* interrupting, so a scheduled cycle can never
park on an unanswerable gate while holding the thread's active-run slot.

### Direction 5 — The gate runs against a rendered projection, not the human's folder

Today the file tree is both input and output: `_stub_produce_artifacts` writes
into `.greenagent/dbtl-cycles/{id}/artifacts/` and the CLI reads
`--project <path>`, so files are the authority. The resolution is a projection
adapter that materializes a **throwaway** projection from the durable record
into a temp directory at a known revision, runs the CLI against that, and
persists the revision, projection hash, tool version, input and output. The
human-visible `.greenagent/` tree becomes a best-effort post-commit render.
Migration is an explicit, human-reviewed one-time import of the existing cycle,
work-item and handoff files — the design forbids automatic file-to-database
repair — followed by dual-write with hash comparison before the projection is
retired.

### Direction 6 — Validity gates belong in the graph, not in the prompt

The practitioner's hard gates should be structural conditions on leaving the
validation stage, because a suggestion the model can talk itself past is not a
gate: a structure-null comparison reported beside every accuracy; asserted (not
logged) fold composition and train/test counts; a heritability ceiling that
makes an implausibly high accuracy *block* promotion; a direction check on any
known-direction contrast; and a tester-only holdout the builder never saw.
Model family, marker density, chain length and plotting remain suggestions.

Per `convention-enforcement-requires-layered-structure`, each of these needs all
three layers — an AGENTS.md rule, a template scaffold that makes the correct
shape the path of least resistance, and an advisory audit check — because no
single layer has ever held.

### What this direction explicitly does not do

It does not replace the chat surface, introduce a second streaming protocol,
make `.greenagent` authoritative, promote knowledge automatically, require
PostgreSQL to start, or ask the model to decide transition legality.

## Builder Handoff Summary

After human approval, this design package hands off to Builder for phased
implementation planning. Designer does not prescribe implementation phases;
Builder owns that decomposition within the approved scope.

Builder should plan for the following design deliverables:

- A durable cycle record (state, class, weight, artifacts, transitions, gate
  requests) with a project-scoped read path and a mutator for
  `projects.dbtl_phase`, which currently has none.
- `DbtlContextMiddleware` and the `dbtl_*` tool family on the lead agent.
- A cycle graph of four node kinds with the greenagent CLI as the injected gate
  port, and `DBTLState` subclassing `ThreadState`.
- Role resolution from `.greenagent/agents/*.yaml` + role skill packs into
  `SubagentConfig`, so a new specialist is a YAML file.
- A knowledge-promotion path producing a durable markdown entry plus a DeerMem
  pointer fact, with an immutable promotion record.
- A contract test pinning the Python state tables against greenagent's, or a
  `dbtl describe --json` request upstream so the tables are fetched, not copied.
- Validity gates as structural conditions, with the three enforcement layers.
- A projection adapter that renders a throwaway gate input at a known revision,
  plus a gate tool version so evaluations are reproducible.
- Repairs to the four live skeleton defects: the truthy-string authorization,
  the target-state-derived actor label, the unread `non_interactive` flag, and
  the replayable approval.

Builder's implementation plan should respect:

- No new `assistant_id` on the interactive path; the lead agent's ~34-middleware
  chain and its invariants are not to be re-derived.
- No live `interrupt()` for gates that can outlive a sitting.
- DeerMem is never the authority for a validated finding; `consolidation_enabled`
  stays `false` for project buckets.
- greenagent decides legality; Python never does.
- The blocking-IO gates apply — the 30 s `subprocess.run` in
  `SubprocessGreenAgentGate` must be offloaded and its result cached within a
  turn.
- Existing chat, run-event, streaming, checkpoint-mode and delta-channel
  contracts are unchanged.
- The stub artifact writer is removed, not extended; its skip-if-present
  idempotency contract is kept.
- Tests must assert that each guard *fires* on a synthetic failure, not merely
  that a clean run is quiet — per `synthetic-failure-testing-for-audit-checks`.
  At minimum: a scheduled run cannot reach a gate; a replayed approval does not
  re-satisfy one; an internal principal cannot approve; a validated claim
  survives a full staleness and consolidation pass unchanged; a retraction flags
  every transition that cited the claim; two contradicting claims both persist;
  and deleting a thread removes no project, cycle, decision, or promotion.

<!-- Do not add implementation phases, phase numbering, or recommended
     scope sequencing below this line. That is Builder's job. -->

## Human Input Needed

1. Decide disagreement 1: authorize the graph now, run the skeptic's
   three-question falsification test first, or run both in parallel.
2. Decide disagreement 2: keep greenagent's state vocabulary, or adopt a
   research-native state set — and if the latter, who changes `lib/dbtl.js`.
3. Decide disagreement 3: where validated knowledge lives, and whether the
   DeerMem project bucket key drops `user_id` so members can share at all.
4. Decide disagreements 4–6: `greenmem` wrapper yes/no; Postgres now or later;
   whether cycles nest.
5. Decide disagreement 7: repair the invalid live cycle record and the widened
   agent contracts, or supersede them with a new cycle.
6. Resolve the three questions the approved system design left open and that
   Builder must not settle by implementation choice: which transitions beyond
   greenagent's three require human approval; what evidence grade and reviewer
   role are required to promote; and what reconciliation retry limit, alerting
   policy and recovery role apply before a cycle is administratively blocked.
7. Confirm the first reference workflow for validation — a real
   genomic-selection question, so the validity gates are tested against a case
   with a known answer.
8. Confirm that the twelve clarifying questions are the required intake for a
   GS cycle, or amend them.
9. If any initial knowledge is seeded by bootstrap exception, confirm its
   `re_evaluate_after_projects` trigger per `bootstrap-exception-governance`.

Cross-check reminder: please cross-check this saved plan before any Builder
implementation begins. This plan does not authorize implementation, execution,
or scope expansion.
