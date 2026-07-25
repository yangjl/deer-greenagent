# Consolidated Design: DBTL as a Project Workflow over DeerMem

Date: 2026-07-25
Primary role: Designer
Status: draft for human review
Branch: `feat/greenagent-dbtl-implementation`

Consolidates two independently authored designs:

- `2026-07-25-dbtl-langgraph-deermem-debate.md` — five-perspective council debate,
  code-verified constraints, plant-science critique. Referred to below as **[D]**.
- `2026-07-25-project-supervisor-dbtl-deermem-design.md` — supervisor-graph
  architecture, stage specs, capability-based agent selection, durable data
  model. Referred to below as **[S]**.

Where the two disagree, this document decides and says why. Both source
documents are retained as reference material; neither is authoritative on its
own.

## 1. Comparison

### What [S] has that [D] lacks

| Contribution | Why it wins |
| --- | --- |
| **Deterministic routing precedence** (explicit selection → pending clarification → pinned cycle → sole active cycle → model classification → default ordinary) | [D] left triage to "a model decision with a deterministic prior". [S]'s objection is correct and decisive: tool use is model-directed, so the model can forget, act late, or start ordinary work first — and the routing policy becomes unauditable. The user's requirement is that the AI decide *first*. |
| **Structured classifier contract** (`route`, `cycle_id`, `proposed_stage`, `confidence`, `reason_codes`, `missing_fields`, `risk_level`) | Makes the routing decision inspectable and testable rather than implicit in prose. |
| **`StageSpec` as versioned data** (`required_inputs`, `required_artifact_types`, `output_schemas`, `required_capabilities`, `allowed_tools`, `memory_read_policy`, `memory_write_policy`, `transition_policy`, `human_gate_policy`) | [D] said "roles are YAML"; [S] says *stages* are specs and *capabilities* are the matching dimension. That is a strictly better decomposition. |
| **Capability-based `AgentSelector`** | This is the sharpest answer to "not fixed Designer/Builder/Tester/Learner". Stage and specialization are orthogonal: a Design stage may need a breeder, a quantitative geneticist and a literature specialist; a Test stage may need a statistical evaluator, a data-quality specialist and a pipeline runner. [D]'s role-YAML mapping quietly preserved the one-role-per-stage assumption. |
| **`StageWorkerResult` contract** — free-form subagent text may be kept as an artifact but cannot by itself satisfy a stage output contract | Closes the gap where a stage "succeeds" because an agent wrote prose. |
| **Four memory layers**, separating *validated* from *shared* | The user said knowledge "can be shared after user promotion". [S] correctly makes validation and sharing two distinct decisions with two gates. [D] collapsed them into one. |
| **Explicit scope object** (`kind`, `workspace_id`, `project_id`, `actor_user_id`, `agent_name`) with a compatibility adapter | Cleaner than [D]'s "drop `user_id` from the bucket key", and it stops encoding new semantics inside `user_id` — which is how the current opaque hash bucket happened. |
| **Fresh per-run/per-stage memory retrieval** | Genuinely new. `DynamicContextMiddleware` freezes the memory block into the first `HumanMessage` for prefix-cache reuse. That is right for one user's stable preferences and wrong for continuously changing shared project memory. [D] missed this. |
| **Stage-submitted memory candidates** instead of passive conversation extraction | [D] hand-waved at `create_fact`. [S] specifies the candidate payload and puts the submission at the stage aggregation node. |
| **`dbtl_stage_runs` with `attempt`**, and transitions as policy verbs (advance / request changes / return to Design / return to Build / repeat Test / pause / abort / supersede / complete) | Supports rework, which [D]'s four node kinds handled only implicitly. |
| **Harness/App boundary section** | Enforced in CI by `tests/test_harness_boundary.py`; [D] omitted it. |
| **A concrete first vertical slice** | Matches the human's repeated direction to keep cycles short and reviewable. |

### What [D] has that [S] lacks

| Contribution | Why it matters |
| --- | --- |
| **`interrupt()` renders nothing today.** `interrupt` appears in the harness only in `agents/dbtl/orchestrator.py`; `frontend/src/core/threads/hooks.ts:1102` and `:1115` hardcode `interrupts: {}` | [S] says "LangGraph may pause while a decision is pending". It would pause and display nothing. This is a hard blocker [S] walks into. |
| **Four live defects in the committed skeleton**: `_parse_human_decision` accepts a bare truthy string as both approval and authorization; `transition()` derives `actor="human"` from the *target state* rather than from who approved; `build_dbtl_graph` never reads `non_interactive`, so a scheduled run parks on a gate holding `uq_runs_thread_active`; a replayed checkpoint re-satisfies a spent gate | [S] correctly says the interrupt is not the approval record but does not name what is already broken. |
| **`assistant_id` is durably pinned per thread**, and `DBTLState` carries no `sandbox`, `thread_data`, `todos`, `artifacts`, `uploaded_files`, `skill_context` or `summary_text` | [S] makes a supervisor graph the entry point for project runs without addressing what that does to the thread's channel table. Unaddressed, it silently removes the sandbox and file browser from every project conversation. |
| **Nested-middleware harms**: `TitleMiddleware` rewriting the thread title from inside a stage; `ClarificationMiddleware`'s `Command(goto=END)` terminating the parent; `MemoryMiddleware` writing project memory from a stage's internal chatter; the goal loop racing the cycle loop | Determines *how* stages may invoke agents. |
| **DeerMem's hard limits**, all code-verified: `CORE_CATEGORIES` is closed (anything else coerced to `other` + `categoryExtension`); `max_facts` bounded `ge=10, le=500` with silent confidence-ranked eviction; `storage_path` and every staleness/consolidation setting are process-global singletons; `fact.status must be 'active'; deletion is physical` so there is **no retraction or version chain**; `search()` is substring-only over `content`; `_legacy_source_value` collapses `source` to a string on every public read; unknown top-level keys *do* round-trip losslessly through frontmatter | [S] assigns DeerMem responsibilities the implementation cannot carry, and misses the one affordance that makes provenance cheap. |
| **The three-memory-systems problem.** `~/.greenagent/knowledge/methods/*.md` + `index.yaml` and greenagent's project-memory protocol (`MEMORY.md`, `doc/DECISIONS.md`, `doc/DATA_PROVENANCE.md`, `doc/REVIEW_QUEUE.md`) already exist | [S] proposes `knowledge_claims` in PostgreSQL as though the field were empty. It would be the **fourth** store. `MEMORY.md` states the governing goal: *"project context stays portable instead of being trapped inside one AI tool."* |
| **Plant-science critique**: the state machine is a software-delivery pipeline; there is no data-reconciliation state, which is where the two highest-value knowledge entries came from; `pass` is a Boolean when pooled BGEM accuracy 0.92 vs within-population 0.20–0.32 was a `pass`; there is no inconclusive terminal, so a required `knowledge_candidate` structurally pressures the agent to manufacture a finding | [S]'s stage contracts are domain-neutral in a way that would accept a confident wrong answer. |
| **Five hard validity gates** (structure null, asserted fold composition, heritability ceiling, direction check, tester-only holdout) and the twelve GS intake questions | Turns [S]'s abstract "success criteria" into checks that actually catch the failure that matters. |
| **Cycle class × cycle weight** — computational / season / program, orthogonal to `full` / `light` / `retroactive` | A season cycle cannot be a live graph run, and a 40-minute sanity check must not pay a 13-state ceremony tax. `lightweight-cycle-classification` is approved user-global knowledge that the current machine cannot express. |
| **greenagent is the gate and its table cannot be fetched.** `lib/dbtl.js` has 27 states with two rework loops; `state.py` is a hand-copied 13-edge subset; there is no `dbtl describe --json` | [S] says `HAPPY_PATH` "becomes a policy-driven transition system" without noting that greenagent, not DeerFlow, decides legality. |
| **The skeptic's falsification test**, and that the human already rescoped away from DBTL once over eight review rounds | Scope discipline. |
| **Template compliance** | [S] contains a seven-increment implementation sequence — precisely the Designer-boundary violation `designer-debate-plan-template-required` documents (6 of 14 plans drifted despite six explicit prohibitions). |

### The one real architectural conflict

**[S]: a supervisor graph is the entry point for project runs. [D]: the lead
agent stays the entry point, with a middleware and tools.**

Both objections are valid, and they are about different things. [S] is right
that routing must be deterministic and evaluated before the model answers — a
tool the model may forget is not a router. [D] is right that a graph swap
threatens the thread's channel table and that nesting the lead agent inside
another graph imports named harms.

The harms [D] catalogued, though, apply to invoking a full lead agent **as a
stage worker** — where `TitleMiddleware` would rewrite the thread title from
inside a stage and `Command(goto=END)` would terminate the orchestrator rather
than the stage. They do **not** apply to invoking it as the **terminal branch**
of a supervisor: there, retitling the thread is correct, and a clarification
ending the turn is correct.

That distinction resolves the conflict, and §2.1 records the decision.

## 2. Reconciled decisions

### 2.1 A thin supervisor, with the lead agent as its terminal ordinary-work branch

Adopt [S]'s supervisor with [D]'s constraints attached:

- The supervisor's `ordinary_work` branch delegates to the **same compiled lead
  agent**, as the terminal node of the turn. It is not a stage worker, so the
  nested-middleware harms do not arise.
- DBTL **stage workers** never invoke the lead agent. They go through
  `SubagentExecutor`, which is the only mechanism that already emits `task_*`
  root-namespace events (the web frontend does not request subgraph streaming,
  so this is the only progress channel that works), accounts tokens, and
  enforces turn/token/loop caps with `stop_reason`.
- The supervisor's state **subclasses `ThreadState`**. `DBTLState` as written
  drops `sandbox`, `thread_data`, `todos`, `artifacts`, `uploaded_files`,
  `skill_context` and `summary_text`; a project conversation cannot lose those.
  `dbtl_*` channels are added by subclass, never merged into base `ThreadState`,
  which would add them to every plain chat thread's delta channel table.
- Retain `dbtl_orchestrator` as the headless/scheduled entry point only.

### 2.2 Routing is deterministic first, model-classified last

Take [S]'s precedence list, with one addition from [D]'s cost concern: rules 1–4
are pure lookups against durable state and require **no LLM call**. Only rule 5
invokes the classifier, and only when no deterministic rule fired. Insufficient
evidence defaults to ordinary work — never to starting a cycle.

A cycle is never created silently. When the classifier proposes `start_cycle`,
the supervisor gathers intent using the structure already shipped in
`.greenagent/skills/designer/templates/clarification-intake.md`, rendered through
the existing `ask_clarification` → `artifact.human_input` → Human Input Card
path. For a genomic-selection cycle, the twelve intake questions from [D] are the
domain instance of that template.

### 2.3 Memory: four layers, three stores, one authority per fact

[S]'s four layers are right. [D]'s three-store finding is also right. Reconciled:

| Layer | Authority | Human-readable rendering | DeerMem's part |
| --- | --- | --- | --- |
| Personal | DeerMem | — | owns it (unchanged) |
| Project working | DeerMem, project scope | — | owns it; always labeled provisional |
| Validated project knowledge | SQL `knowledge_claims` + `knowledge_promotions` | `<project>/knowledge/*.md` in the human's folder, in the shape `~/.greenagent/knowledge/methods/*.md` already uses | a **pointer fact** only |
| Published / shared | SQL, second promotion | same, with widened scope | pointer fact with widened retrieval scope |

Rationale: the SQL row carries what DeerMem provably cannot — an immutable
promotion record, a supersession chain, and retraction (DeerMem raises
`fact.status must be 'active'; deletion is physical`). The markdown rendering
carries what SQL is bad at — a portable, diffable, greppable document that
survives this tool, per `MEMORY.md`'s stated goal, and that already has a
field vocabulary (`evidence_strength`, `provenance`, `contradicts`,
`review_trigger`) richer than a DeerMem fact. The DeerMem pointer carries what
only DeerMem does well — token-budgeted injection at the right moment. Because
facts are pointers, the 500-fact cap and its silent confidence-ranked eviction
stop being a data-loss risk.

Three consequences follow immediately:

- **`consolidation_enabled` must be `false` for project buckets**, and the
  pointer category must be added to `staleness_protected_categories` and
  `guaranteed_categories`. Per-scope settings are not expressible today because
  `DeerMemConfig` is a process-global singleton, so this is a real constraint on
  how scopes are separated.
- **Provenance rides at the fact's top level**, never nested under `source`,
  which `_legacy_source_value` collapses to a string on every public read.
  Unknown top-level keys round-trip losslessly through YAML frontmatter — this
  is verified and should be pinned by a test, because it is load-bearing.
- **Project working memory is retrieved fresh per run and per stage** ([S]),
  while personal memory keeps the existing frozen first-message injection for
  prefix-cache reuse.

### 2.4 The bucket key must change before any of this is shareable

`agents/memory/scope.py` derives `{user_id}--project--{sha256(project_id)[:24]}`
and its docstring states the intent: *"two users opening the same project id
cannot share memory."* Project working memory is therefore **per-user today**,
and no promotion path can make it shared. [S]'s scope object is the right fix;
adopting it is a prerequisite for layer 2, not a refinement of it.

### 2.5 Gates are records; interrupts are at most transport

Adopt [S]'s ten-step transition contract, with [D]'s blockers attached:

- Because the frontend renders nothing for interrupts, the review surface must
  use the shipped Human Input Card path plus a durable review row. An interrupt
  may later become transport; it is never the authority.
- Never model a gate that can outlive the sitting as a live interrupt — a
  week-old interrupt is a zombie run holding the thread.
- The gate node must read `non_interactive` and terminate with an error
  *before* interrupting.
- The committed `actor_type` is the **reviewer's** principal, never a constant
  derived from the target state.
- A review whose principal is `is_internal=True` is rejected — this is how an
  agent is prevented from approving its own gate, reusing the server-owned trust
  rule that already gates `non_interactive` and `channel_user_id`.
- A review row is single-use and bound to a gate evaluation and prior revision,
  so a replayed checkpoint cannot re-satisfy it.
- `GreenAgentGate` must expose a tool version; without it a persisted evaluation
  is not reproducible.
- The gate runs against a **throwaway projection** materialized from the durable
  record at a known revision, not against the human's `.greenagent/` folder —
  which today is both the CLI's input and the stub writer's output.

### 2.6 Stage contracts carry domain validity gates

[S]'s stage outputs are domain-neutral and would accept a confident wrong
answer. Add [D]'s hard gates as structural conditions on leaving the Test stage,
expressed in `StageSpec.output_schemas` so they are contract, not prompt: a
structure-null comparison reported beside every accuracy; asserted (not logged)
fold composition and train/test counts; a heritability ceiling that makes an
implausibly high accuracy *block* promotion rather than accelerate it; a
direction check on any known-direction contrast; and a tester-only holdout the
builder never saw. Model family, marker density, chain length and plotting stay
suggestions.

Add an **inconclusive** terminal, and make `knowledge_candidate` optional at
completion. A required candidate plus a single terminal is structural pressure
to manufacture a finding.

Per `convention-enforcement-requires-layered-structure`, each gate needs all
three layers — an AGENTS.md rule, a template scaffold that makes the correct
shape the path of least resistance, and an advisory audit check.

### 2.7 Cycle class and weight are first-class fields

Neither source carried these. A cycle record must declare `cycle_class`
(`computational` | `season` | `program`) and `cycle_weight` (`full` | `light` |
`retroactive`). Class drives gate weight, because ceremony must scale with
irreversibility, not formality — planting is irreversible, an rrBLUP re-run is
not. Weight keeps `light` and `retroactive` cycles legal and measurable, as
approved user-global knowledge requires. Whether cycles nest (a season cycle
containing N computational cycles) is left open in §6.

### 2.8 greenagent stays the gate, and the drift gets pinned

[S] speaks of replacing `HAPPY_PATH` with a policy-driven transition system.
That is right about the *graph*, but legality remains greenagent's: `lib/dbtl.js`
holds 27 states and two rework loops, `state.py` is a hand-copied 13-edge subset,
and there is no `dbtl describe --json` to fetch the table from. The transition
verbs in [S] are the policy layer; `check_transition` remains the authority; and
the duplicated tables need a contract test or an upstream `describe` command —
this is exactly what `single-source-of-truth-for-constants` warns about, and the
tables have already drifted.

## 3. Design direction

```text
project-scoped run
   │
   ▼
hydrate (durable, no LLM)
   project identity + membership · active/pinned cycles · pending review
   · fresh project working memory · validated-knowledge pointers
   │
   ▼
route  ── rules 1-4: deterministic lookup ──┐
   └───── rule 5: structured classifier ────┤
                                            │
   ┌────────────────────────────────────────┴──────────────────┐
   ▼                  ▼                    ▼                   ▼
ordinary_work      clarify            start_cycle         continue_cycle
   │                  │                    │                   │
   ▼                  ▼                    ▼                   ▼
LEAD AGENT      Human Input Card      intake → confirm     resolve stage
(terminal,          (existing)        → create cycle            │
 full chain,                                                    ▼
 unchanged)                                            ┌── STAGE SUBGRAPH ──┐
                                                       │ load ctx           │
                                                       │ validate inputs    │
                                                       │ plan work units    │
                                                       │ AgentSelector      │
                                                       │ fan out ─ Send ──▶ │ SubagentExecutor
                                                       │ aggregate          │  (task_* events,
                                                       │ validate contracts │   budgets, caps)
                                                       │ persist evidence   │
                                                       │ submit mem cands   │
                                                       │ propose transition │
                                                       └─────────┬──────────┘
                                                                 ▼
                                                    policy eval (greenagent,
                                                    against throwaway projection)
                                                                 │
                                                    ┌────────────┴────────────┐
                                                automatic              human review
                                                    └────────────┬────────────┘
                                                       commit transition (atomic)
                                                                 │
                                              advance · rework · pause · abort
                                              · supersede · complete · inconclusive
```

Durable entities are [S]'s list, plus `cycle_class` / `cycle_weight` on the
cycle and `attempt` on the stage run. LangGraph state holds identifiers and
temporary orchestration data only; nodes reload authoritative state from the
database, so rebuilding or discarding a checkpoint never changes a cycle's
accepted state. `projects.dbtl_phase` remains a denormalized display field
updated transactionally — it currently has no mutator at all, since
`WorkspaceRepository` has no `update_project`.

Component boundaries follow [S]: the harness owns graph builders, `StageSpec`,
worker-result contracts and neutral protocols; the app owns authenticated
resolution, routes, review commands and repository composition. The harness must
not import `app.gateway` — pinned by `tests/test_harness_boundary.py`.

## 4. Minimum reviewable slice

Scope for the first reviewable delivery, honoring the human's standing direction
to keep cycles short. Decomposition within this scope remains Builder's.

```text
classify a project request → clarify missing intent → create one durable
Design cycle → dispatch one Design work unit to a selected agent → persist and
display its artifact → evaluate the Design gate → request human approval →
transition durably to Build
```

This exercises routing, persistence, capability selection, artifact contracts,
review authority and transition atomicity without first building a breeding
ontology. Explicitly out of scope: the full four-stage loop, published/shared
knowledge, multi-cycle projects, and any change to `lib/dbtl.js`.

## 5. Tester requirements

[S]'s twenty requirements are adopted in full. Add, from [D]'s verified findings
— and per `synthetic-failure-testing-for-audit-checks`, each must assert the
guard *fires* on a synthetic failure, not merely that a clean run is quiet:

21. A scheduled / `non_interactive` run terminates with an error at a human gate
    and holds no active-run slot.
22. A replayed branch or regenerate of an approved gate does not re-satisfy it.
23. A resume payload from an internal principal (`is_internal=true`) is rejected.
24. The committed `actor_type` equals the reviewer's principal, not a value
    derived from the target state.
25. A validated claim survives a full staleness pass and a full consolidation
    pass unchanged.
26. A project conversation routed to `ordinary_work` retains its sandbox,
    uploads, file browser and summarization — no channel is lost to the
    supervisor.
27. Two authorized project members both read the same project working memory.
28. A Test stage cannot leave with a reported accuracy and no structure-null
    comparison; asserted fold composition mismatches block; an accuracy above
    the heritability ceiling blocks promotion.
29. A cycle can complete as `inconclusive` with no knowledge candidate.
30. The Python state tables agree with greenagent's, or the test fails.

## 6. Human input needed

Carried from both sources, deduplicated, unresolved. Per
`unresolved-design-disagreements-must-route-to-human`, Builder must not settle
these by implementation choice.

1. Authorize the supervisor architecture, or run the falsification test first
   (three research questions with and without staged execution). The human has
   rescoped away from DBTL once already.
2. One active cycle per project in the first release, or several?
3. Do cycles nest — a season cycle containing computational children?
4. Which transitions require human review beyond greenagent's three
   (`approved-for-build`, `learning`, `completed`)?
5. Which project roles may submit working-memory candidates, and which may
   promote? Does promotion validate within the project only, with publication a
   separate action?
6. Confirm §2.3: validated knowledge is SQL-authoritative with a markdown
   rendering in the project folder and a DeerMem pointer — and confirm §2.4,
   changing the bucket key so project memory is shareable at all.
7. Keep greenagent's state vocabulary, or adopt a research-native set adding
   data-reconciliation and validity states? If the latter, who changes
   `lib/dbtl.js`?
8. PostgreSQL now or later? The design requires it; the deployment is SQLite
   with one user.
9. Repair the invalid live cycle record (its `state` is `awaiting-human-review`,
   not in `STATES`, so `greenagent dbtl validate` would fail today), the
   handoffs that bypassed the CLI, and the agent contracts widened against
   `project-narrows-only` — or supersede them with a new cycle.
10. Which maize workflow and artifact schemas form the first reference domain
    pack, and confirm the twelve GS intake questions.
11. Any bootstrap-exception knowledge seeding must carry
    `re_evaluate_after_projects` per `bootstrap-exception-governance`.

Cross-check reminder: please cross-check this consolidated plan before any
Builder implementation begins. It does not authorize implementation, execution,
or scope expansion.
