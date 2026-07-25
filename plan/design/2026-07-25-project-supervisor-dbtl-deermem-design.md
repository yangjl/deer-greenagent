# Project-Supervisor DBTL Workflow with DeerMem

**Status:** proposed for human review  
**Role:** GreenAgent Designer  
**Date:** 2026-07-25  
**Scope:** project-centric DBTL orchestration, dynamic stage agents, DeerMem
working memory, and human-gated knowledge promotion

## Design summary

Introduce a project-scoped **Supervisor LangGraph** that evaluates every user
turn, routes ordinary work to DeerFlow's existing lead-agent graph, and routes
research work into a durable Design-Build-Test-Learn workflow.

DBTL stages are graph nodes or subgraphs, not fixed Designer, Builder, Tester,
and Learner agents. Each stage may dynamically assign one or more specialized
agents according to the work item's capabilities, project assignments, tools,
skills, data scope, budget, and approval policy.

DeerMem supplies evolving project working context. PostgreSQL remains
authoritative for projects, DBTL cycles, transitions, artifacts, evidence,
reviews, decisions, and promoted knowledge. Learn-stage output enters
provisional working memory and cannot become validated or broadly shared
knowledge without an explicit authorized human promotion.

## GreenAgent principles preserved

This proposal follows the approved GreenAgent DBTL design principles:

1. The durable project is the primary unit of ownership, authorization, files,
   memory, activity, and scientific continuity.
2. Conversations are project working sessions, not the owner of DBTL state or
   scientific evidence.
3. PostgreSQL is the production source of truth.
4. `.greenagent` is a temporary, replaceable policy projection. It never
   overrides newer database state.
5. LangGraph checkpoints preserve execution continuity but do not own durable
   business state.
6. Human-gated transitions cannot be approved by agents, schedulers, service
   tokens, or other non-interactive runs.
7. Project working memory remains distinct from reviewed, versioned knowledge.
8. Learn-stage output is provisional until an authorized human promotion.
9. Existing run, streaming, artifact, sandbox, event-ordering, and
   cancellation behavior should be reused rather than rewritten.
10. Plant-breeding capabilities are provided through project assignments,
    skills, schemas, and domain packs rather than hard-coded into the platform
    core.

## Current-state assessment

The current implementation provides useful foundations but is not yet the
target workflow.

### Existing DBTL skeleton

`backend/packages/harness/deerflow/agents/dbtl/orchestrator.py` is an opt-in
`dbtl_orchestrator` assistant target. It:

- follows a fixed happy path;
- creates placeholder artifacts;
- invokes the GreenAgent CLI;
- interrupts at three human gates;
- does not execute real stage work;
- does not dynamically select specialized agents.

`backend/packages/harness/deerflow/agents/dbtl/state.py` encodes the fixed
transition path and artifact requirements. This is appropriate as a Phase-one
prototype, but the final system must support revision, pause, rework,
supersession, and stage loops.

### Existing project shell

The project-first shell and project-owned workspace are already in place.
Project conversations share the same human-visible folder and receive a
server-derived project context.

However:

- the project rail's DBTL cycles and to-dos are still a browser-local
  `localStorage` projection;
- `projects.dbtl_phase` is only a summary field;
- durable cycles, stage runs, evidence, reviews, and transitions do not yet
  exist.

### Existing DeerMem behavior

DeerMem already provides:

- asynchronous conversation extraction;
- correction and reinforcement detection;
- persistent facts;
- revision checks;
- consolidation and staleness review;
- prompt injection;
- memory tools and retrieval;
- project isolation through a derived memory bucket.

The current project bucket is derived from `(user_id, project_id)`. This
prevents cross-project leakage, but it also means two authorized users in the
same project do not receive one shared project working memory.

The existing passive memory middleware learns from ordinary user and assistant
turns. That remains valuable for conversational continuity, but unreviewed
conversation extraction must not become authoritative scientific knowledge.

## Debate Mode

### Position A: keep DBTL as a separate assistant

**Argument:** This is the smallest change because the existing
`dbtl_orchestrator` run target already works with the Gateway lifecycle.

**Objection:** Users must recognize when DBTL applies and manually switch
assistants. The requested behavior requires the system to evaluate every
project request before responding.

**Decision:** Reject as the primary user experience. Retain the assistant ID as
a compatibility and diagnostic entry point during migration.

### Position B: add a `start_dbtl` tool to the lead agent

**Argument:** The existing lead agent already has tools, memory, sandbox,
skills, artifacts, and subagent delegation.

**Objection:** Tool use is model-directed. The model may forget to invoke the
tool, invoke it too late, or continue ordinary work before the DBTL decision.
It also makes durable routing policy harder to audit.

**Decision:** Retain an explicit DBTL command/tool as a user override, but do
not make it the primary router.

### Position C: force every project request through DBTL

**Argument:** This gives every project action structure, evidence, and
traceability.

**Objection:** Simple file inspection, README edits, explanations, exports, and
small maintenance tasks do not justify a complete scientific workflow.
Over-application would make the project cumbersome and train users to bypass
DBTL.

**Decision:** Reject. The system must distinguish DBTL work from ordinary
project work.

### Position D: make DeerMem authoritative for DBTL and knowledge

**Argument:** One memory system would reduce duplicated storage and retrieval
logic.

**Objection:** Mutable LLM-extracted facts cannot safely represent human
approvals, transition revisions, immutable artifact versions, scientific
provenance, or retraction history.

**Decision:** Reject. DeerMem owns working context; PostgreSQL owns durable
workflow state and validated knowledge.

### Position E: fixed agents for fixed stages

**Argument:** One Designer, Builder, Tester, and Learner agent is easy to
understand.

**Objection:** A breeding Design stage may require a breeder, quantitative
geneticist, statistician, literature researcher, or data steward. A Test stage
may require statistical evaluation, field-data quality control, and pipeline
validation. Stage and specialization are different dimensions.

**Decision:** Reject fixed role agents. Keep four stable workflow stages and
dynamically orchestrate capability-scoped agents inside each stage.

### Synthesis

Adopt a **project-scoped Supervisor Graph**. It becomes the entry point for
project conversations, evaluates whether DBTL applies, and routes to either the
existing lead-agent graph or a durable DBTL workflow.

## Target graph

```text
User turn
   │
   ▼
Hydrate project context
   ├── project identity and permissions
   ├── active or pinned DBTL cycles
   ├── pending clarification or review
   ├── latest DeerMem project context
   └── validated knowledge references
   │
   ▼
Classify project intent
   ├── ordinary_work ───────────────▶ Existing lead-agent graph
   ├── clarify_dbtl_intent ─────────▶ Human Input Card
   ├── start_cycle ─────────────────▶ Intake → confirmation → create cycle
   └── continue_cycle ──────────────▶ Resolve cycle and current stage
                                             │
                                             ▼
                                      Stage router
                           ┌─────────────────┼─────────────────┐
                        Design             Build              Test
                           └─────────────────┼─────────────────┘
                                           Learn
                                             │
                                   each stage is a subgraph
                                             │
                          plan → dispatch workers → aggregate
                               → validate → persist evidence
                                             │
                                     policy evaluation
                                  ┌──────────┴──────────┐
                             automatic              human review
                                  └──────────┬──────────┘
                                      commit transition
                                             │
                               next stage / rework / pause / end
```

## Project Supervisor Graph

### Scope

The Supervisor Graph is the default entry point only for project-scoped runs.
Projectless Inbox conversations continue directly through the existing
lead-agent graph.

The Gateway must supply server-derived project identity before graph
construction. A client-supplied project or cycle identifier is only intent. It
must be membership-checked, associated with the project, and restamped from
durable state before the graph can use it.

### Intent contract

The classifier returns a structured result:

```text
route: ordinary_work | start_cycle | continue_cycle | clarify
cycle_id: optional
proposed_stage: design | build | test | learn
confidence: 0..1
reason_codes: list
missing_fields: list
risk_level: low | medium | high
```

Routing precedence:

1. Explicit user selection.
2. A response to a pending clarification or review.
3. A conversation pinned to an active cycle.
4. An applicable active project cycle.
5. Structured model classification.
6. Ordinary work when evidence is insufficient.

The classifier proposes workflow intent. It does not create a durable cycle or
approve a transition.

### When DBTL applies

DBTL is normally appropriate when a request:

- introduces or changes a research objective or hypothesis;
- proposes a new breeding, trial, analysis, data-processing, or evaluation
  activity;
- continues work already associated with an active cycle;
- asks to implement a previously reviewed Design artifact;
- asks to evaluate results against explicit success criteria;
- asks to synthesize findings, limitations, decisions, and next-cycle
  recommendations.

Ordinary project work normally includes:

- viewing or summarizing an existing file;
- creating a simple README or other low-risk support file;
- answering a conceptual question;
- navigating, exporting, or inspecting project state;
- small administrative edits that do not change research methodology,
  evidence, or decisions.

### Starting a cycle

A new cycle must never be created silently. The supervisor gathers:

- objective or research question;
- hypothesis;
- success criteria;
- population, material, environment, or season when applicable;
- available datasets and evidence;
- constraints and risks;
- accountable human;
- required review authority.

The user can choose:

- Start the proposed cycle.
- Continue an existing cycle.
- Handle the request as one-off work.

An explicit user command to start a cycle may skip confirmation when all
required fields are present.

### Continuing a cycle

Projects may eventually contain multiple open cycles. A conversation carries
an optional durable cycle association, and every run records the resolved
cycle.

Resolution rules:

- use a valid pinned cycle when one exists;
- use the only applicable active cycle when unambiguous;
- ask the user when several cycles match;
- reject any cycle that does not belong to the selected project.

## Stage subgraphs

Each DBTL stage is a versioned `StageSpec`, not an agent identity:

```text
stage
required_inputs
required_artifact_types
output_schemas
required_capabilities
allowed_tools
memory_read_policy
memory_write_policy
transition_policy
human_gate_policy
```

All four stages follow a common internal structure:

```text
load stage context
  → validate required inputs
  → create stage plan and work units
  → select eligible agents
  → fan out work
  → collect and normalize results
  → validate artifact and evidence contracts
  → persist stage outcome
  → propose transition
```

### Dynamic agent selection

Every work unit declares required capabilities. An `AgentSelector` chooses
eligible agents from the project team and available subagent registry using:

- capability match;
- project assignment;
- allowed skills and tools;
- data scope;
- writable object types;
- model and budget policy;
- autonomy and human-approval limits;
- current availability.

Examples of capabilities include:

- breeding strategy;
- experimental design;
- quantitative genetics;
- statistical analysis;
- literature review;
- software and pipeline implementation;
- data stewardship and lineage;
- field-trial quality control;
- evidence review;
- scientific reporting.

A Design stage might use a breeder, statistician, and literature specialist.
A Test stage might use a statistical evaluator, data-quality specialist, and
pipeline runner. Their participation is attached to work items, not hard-coded
to the stage.

### Reusing subagent infrastructure

Reuse:

- `SubagentExecutor`;
- existing custom and built-in agent registries;
- project sandbox and thread data;
- skill and tool authorization;
- token, turn, loop, and time budgets;
- `task_started`, `task_running`, and `task_completed` events;
- Langfuse tracing.

Add a structured `StageWorkerResult` adapter containing:

```text
status
summary
artifact_refs
evidence_refs
claims
limitations
provenance
recommended_next_actions
```

Free-form subagent text may be preserved as an artifact, but it cannot alone
satisfy a stage output contract.

### Stage outputs

Design typically produces:

- objective and hypotheses;
- assumptions and constraints;
- experimental or implementation plan;
- success and failure criteria;
- data and evidence requirements;
- risks and human decisions.

Build typically produces:

- executable implementation, analysis pipeline, or trial protocol;
- configuration and environment records;
- artifact manifest;
- data lineage;
- implementation deviations and handoff.

Test typically produces:

- versioned test or evaluation plan;
- observations and results;
- comparison with success criteria;
- failure analysis;
- limitations and uncertainty;
- pass, rework, or escalation recommendation.

Learn typically produces:

- synthesized findings;
- evidence-linked claims;
- deviations and causal hypotheses;
- next-cycle recommendations;
- project working-memory candidates;
- validated-knowledge promotion candidates.

## Transitions and human review

The fixed `HAPPY_PATH` becomes a policy-driven transition system supporting:

- advance;
- request changes;
- return to Design;
- return to Build;
- repeat Test;
- pause;
- abort;
- supersede;
- complete.

For every proposed transition:

1. Load the exact project, cycle, database revision, and stage attempt.
2. Resolve required versioned artifacts and evidence.
3. Create an idempotent transition intent at the expected revision.
4. Materialize the temporary GreenAgent projection from that revision.
5. Run `DBTLPolicyEvaluator`.
6. Persist complete evaluator input, output, tool version, and hashes.
7. Present consequences, missing evidence, and validation errors.
8. Collect an authorized human decision where required.
9. Commit the accepted transition, review, activity event, and next revision
   atomically.
10. Refresh the `.greenagent` projection best-effort.

LangGraph may pause while a decision is pending, but the checkpoint interrupt
is not the approval record. The authoritative review is a PostgreSQL row with
actor, authorization reference, evidence versions, decision, and timestamp.

The existing Human Input Card protocol should handle conversational
clarification. Durable transition approvals should use a project Review
surface. A card may link to or summarize that review, but it is not the sole
record.

## DeerMem design

### Memory and knowledge layers

Maintain four explicit layers:

1. **Personal memory**  
   Private user preferences and continuity.

2. **Project working memory**  
   Revisable objectives, operational decisions, methods, constraints,
   unresolved questions, and provisional findings. Available only to
   authorized project members and assigned agents and always labeled
   provisional.

3. **Validated project knowledge**  
   Reviewed, sourced, versioned claims created by authorized human promotion.

4. **Published or shared knowledge**  
   An optional second promotion that widens retrieval scope to a workspace,
   selected projects, or an approved knowledge library.

Project working memory may be shared inside its project while still remaining
provisional. Validation and broader sharing are separate decisions.

### Explicit memory scope

Stop encoding new scope semantics directly into `user_id`. Introduce a
framework-neutral scope object:

```text
kind: personal | project_working
workspace_id
project_id
actor_user_id
agent_name
```

A compatibility adapter may initially translate this scope into DeerMem's
existing `(user_id, agent_name)` storage keys. Existing call sites can continue
using the old interface during migration, while new DBTL code uses the
explicit scope.

The Gateway mints project-working-memory scope only after authorization.
Client input must never select a memory bucket directly.

### Reading memory

At the start of each project turn and DBTL stage:

1. Load relevant personal preferences.
2. Retrieve latest project working memory.
3. Retrieve validated project knowledge and authoritative structured records.
4. Label provisional and validated material separately.
5. Preserve source, version, project scope, producing run, and retrieval time.

Project working memory should be retrieved fresh per run or stage. A frozen
first-message snapshot is too stale for continuously changing shared project
memory. Retrieval results should remain request-scoped and should not become
the authoritative checkpoint state.

### Writing working memory

Ordinary project conversations may continue feeding DeerMem for operational
continuity, but the resulting facts remain provisional.

DBTL stage agents do not passively write their whole conversations into shared
memory. Instead, the stage aggregation node submits explicit memory candidates
with:

- project and cycle;
- stage and stage attempt;
- producing run and agent;
- source artifact and version;
- evidence references;
- content and category;
- confidence;
- limitations;
- data-use constraints;
- supersession links.

Learn consolidates those candidates and proposes promotion. It cannot perform
promotion.

### Human promotion

Promotion is a dedicated application command, not a mutable DeerMem label.

An approved promotion creates an immutable PostgreSQL knowledge snapshot
containing:

- candidate and source versions;
- reviewer identity and project role;
- authorization reference;
- evidence and claim links;
- validation scope;
- limitations;
- intended retrieval and sharing scope;
- supersession or retraction relationships.

Validated retrieval reads PostgreSQL knowledge plus authoritative structured
records. DeerMem remains the working-memory and candidate engine.

DBTL completion and knowledge promotion are separate. A cycle can complete
with unpromoted Learn candidates still awaiting review.

## Durable data model

Add the following database-owned entities:

- `dbtl_cycles`
- `dbtl_stage_runs`
- `dbtl_transitions`
- `dbtl_gate_evaluations`
- `dbtl_reviews`
- `work_items`
- `dbtl_artifacts`
- `memory_candidates`
- `knowledge_claims`
- `knowledge_promotions`
- `knowledge_links`
- `activity_events`

### DBTL cycle

Recommended core fields:

```text
id
project_id
name
objective
hypothesis
status
current_stage
db_revision
reconciliation_status
created_by
created_at
updated_at
```

### Stage run

Recommended core fields:

```text
id
cycle_id
stage
attempt
status
triggering_run_id
input_snapshot
stage_spec_version
started_at
completed_at
```

### Artifacts and evidence

Every artifact records:

```text
project_id
cycle_id
stage_run_id
artifact_type
schema_version
path or object reference
content hash
version
producing actor and run
source data versions
created_at
supersedes
```

LangGraph state stores IDs and temporary orchestration data. Nodes reload
authoritative cycle and artifact state from PostgreSQL. Removing or rebuilding
a checkpoint must not change a cycle's accepted state.

`projects.dbtl_phase` may remain temporarily as a denormalized display field,
updated transactionally from the active cycle. It is not the cycle authority.

## Existing infrastructure to preserve

### Backend

- Gateway run lifecycle, streaming, cancellation, journaling, and checkpoints.
- Project-derived run context and first-run project filing.
- Human-visible project folders and sandbox mappings.
- Existing lead-agent graph for ordinary project work.
- Subagent execution, skills, tools, authorization, and budgets.
- Structured task lifecycle events.
- Human Input Cards.
- Project files, artifacts, and workspace-change diffs.
- Langfuse callbacks and run metadata.
- Scheduled-task and channel delivery paths for notifications.
- `SubprocessGreenAgentGate`, behind `DBTLPolicyEvaluator` and
  `DBTLProjectionAdapter`.

### Frontend

- Existing project-first routes and three-rail shell.
- Project rail cycle location and conversations.
- Main chat format and streaming message rendering.
- Artifact and workspace-change inspector.
- Human Input Card rendering.
- Existing file tree and file preview.

The browser-local cycle plan is replaced by database-backed queries while
retaining the current rail composition.

## Component boundaries

Preserve the Harness/App dependency direction.

The harness owns:

- DBTL graph and subgraph builders;
- stage schemas and `StageSpec`;
- worker-result contracts;
- neutral repository, policy, memory, and agent-selection protocols;
- compatibility adapters that do not depend on Gateway code.

The application owns:

- authenticated project and cycle resolution;
- API routes;
- durable review commands;
- concrete repository composition;
- notifications;
- project UI response models.

The application may import the harness. The harness must not import
`app.gateway`.

## Implementation sequence

### Increment 0: contracts and failing tests

- Define DBTL cycle, stage, transition, review, artifact, and memory-candidate
  contracts.
- Define `StageSpec`, structured classifier output, and worker-result schemas.
- Pin authorization, idempotency, revision, and project-isolation behavior with
  failing tests.

### Increment 1: durable DBTL foundation

- Add migrations and repositories.
- Add project cycle, stage, work-item, artifact, and review APIs.
- Treat PostgreSQL as authoritative.
- Replace the rail's `localStorage` cycle source with server data.
- Keep the existing local plan only as an explicit one-time migration input if
  needed.

### Increment 2: project supervisor and intake

- Add the project-scoped Supervisor Graph.
- Route ordinary work into the existing lead-agent graph.
- Implement deterministic routing precedence and structured classification.
- Add cycle selection, intake drafts, clarification cards, and cycle creation.
- Record resolved cycle and stage identifiers in run metadata and tracing.

### Increment 3: dynamic stage execution

- Implement generic stage-subgraph machinery.
- Implement capability-based agent selection.
- Reuse `SubagentExecutor` and task events.
- Normalize structured worker results.
- Persist stage plans, artifacts, evidence, deviations, and work-item status.

### Increment 4: gates and reconciliation

- Put GreenAgent behind evaluator and projection interfaces.
- Add durable transition intents, gate evaluations, and reviews.
- Implement optimistic database revisions and idempotency keys.
- Add reconciliation-blocked behavior and database-led rebuild.
- Prevent agents and non-interactive runs from satisfying human gates.

### Increment 5: DeerMem governance

- Add explicit memory-scope adapter.
- Add fresh project-memory retrieval for project turns and DBTL stages.
- Add explicit stage memory candidates and Learn consolidation.
- Add human promotion, validated retrieval, supersession, retraction, and
  optional publication scope.

### Increment 6: project DBTL experience

- Make the existing cycle rail authoritative.
- Display stage owners, work items, blockers, evidence, and current activity.
- Add Review and knowledge-promotion surfaces.
- Preserve the main chat presentation and existing artifact inspector.

## First vertical slice

The first end-to-end increment should implement:

```text
classify a project request
  → clarify missing intent
  → create one durable Design cycle
  → create and dispatch one Design work item
  → persist and display its artifact
  → evaluate the Design gate
  → request human approval
  → transition durably to Build
```

This slice validates routing, persistence, agent dispatch, artifacts, review,
and transition authority without first implementing the entire breeding
ontology.

## Tester requirements

1. A simple file or documentation request must not create a DBTL cycle.
2. An explicit research objective must produce a DBTL proposal before work
   begins.
3. A cycle must not be created without required intent and human confirmation,
   unless the user explicitly supplied both.
4. A request in a conversation pinned to a cycle must continue that cycle.
5. Ambiguous requests across multiple cycles must request selection.
6. A cycle identifier from another project must be rejected.
7. Restarting the service or removing a checkpoint must not lose accepted DBTL
   state.
8. Retried transition requests must be idempotent.
9. Concurrent transitions at the same revision must produce one accepted
   result and a revision conflict for the other.
10. Agents, schedulers, and service tokens must not approve human gates.
11. GreenAgent projection divergence must visibly block further transitions.
12. Every accepted artifact and claim must preserve source, version, hash,
    producing actor, and run.
13. Project working memory must not cross project boundaries.
14. Unauthorized users must not retrieve project working memory.
15. Provisional memory must not appear as validated knowledge.
16. Learn output must remain a candidate until a distinct human promotion.
17. Promotion must preserve reviewer, evidence, scope, and authorization.
18. Cross-project or workspace publication must require an explicit sharing
    scope.
19. Superseded or retracted knowledge must remain in audit history and stop
    appearing as current validated knowledge.
20. Existing chat streaming, artifacts, cancellation, event ordering, and
    project-file behavior must remain compatible.

## Human review decisions

The following decisions should be confirmed before Builder execution:

1. May a project have multiple active cycles in the first durable release, or
   should the first increment enforce one active cycle per project?
2. Which transitions require human review beyond approval to Build, entry into
   Learn, and completion?
3. Which project roles may submit working-memory candidates?
4. Which project roles may promote validated knowledge?
5. Does promotion validate knowledge only inside the project, with a separate
   publication action for broader sharing?
6. Which initial maize workflow and artifact schemas should be the first
   reference domain pack?

## Recommendation

Proceed with the Project Supervisor Graph and the first vertical slice.

Do not extend the current placeholder artifact loop into production behavior.
Preserve it only as compatibility scaffolding while the durable graph,
repository, and policy interfaces are introduced.

