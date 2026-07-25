# Final Plan: Project-Centric DBTL Workflow over DeerMem

**Status:** final Designer proposal for human approval  
**Role:** GreenAgent Designer  
**Date:** 2026-07-25  
**Document type:** planning only; no implementation is authorized by this plan  
**Supersedes:** `2026-07-25-dbtl-workflow-consolidated-design.md`  
**Source plans retained:**

- `2026-07-25-dbtl-langgraph-deermem-debate.md`
- `2026-07-25-project-supervisor-dbtl-deermem-design.md`
- `2026-07-25-dbtl-workflow-consolidated-design.md`
- `workspace-oriented-breeding-system-design.md`

## 1. Executive decision

Adopt a thin, project-scoped **Supervisor LangGraph** that evaluates each
project conversation turn before the ordinary lead agent responds.

The supervisor:

- routes ordinary project work to the existing lead-agent graph as a terminal
  branch;
- proposes, but never silently creates, a DBTL cycle;
- resumes durable project cycles when the request and project state agree;
- runs Design, Build, Test, and Learn as stage subgraphs;
- dynamically selects one or more capability-matched specialized agents for
  each work item;
- uses PostgreSQL as the final authority for cycles, stage attempts,
  transitions, artifacts, evidence, reviews, and promoted knowledge;
- uses DeerMem for personal and provisional project working context;
- renders validated knowledge into the human-visible project folder and places
  only a retrieval pointer in DeerMem;
- requires a distinct human promotion before knowledge becomes validated or
  receives a wider sharing scope.

Four additions are mandatory in the final design:

1. A **Data Readiness and Reconciliation bridge** sits between Design approval
   and Build execution. Test repeats critical reconciliation assertions against
   the exact evaluated dataset version.
2. Scientific outcomes are non-binary. Valid negative findings,
   inconclusive results, and validity rejection—including implausibly high
   accuracy—are first-class outcomes.
3. An AI decision to upgrade ordinary work into DBTL produces a warning and
   confirmation card before any DBTL domain record is created.
4. PostgreSQL migration and gate-security repairs form a strict prerequisite
   phase. No Supervisor Graph implementation begins until that foundation
   passes its go/no-go review.

## 2. Final design principles

1. **Project before conversation.** The project owns files, cycles, memory,
   evidence, knowledge, decisions, and activity. A conversation is a working
   session attached to that project.
2. **Database before graph.** PostgreSQL owns accepted DBTL state. LangGraph
   checkpoints own execution continuity only.
3. **No silent workflow upgrade.** Classification may propose DBTL; only an
   explicit human action or explicit user command may create a cycle.
4. **Stages are workflow structure, not people.** Design, Build, Test, and
   Learn are graph subgraphs. Agents are dynamically assigned by capability.
5. **Reconciliation before execution.** Data identity, coding, direction,
   units, provenance, and train/test separation are checked before Build uses
   the data.
6. **Validity before performance.** A high metric cannot satisfy Test when
   structure, leakage, biological plausibility, or holdout checks fail.
7. **Negative and inconclusive are legitimate.** The workflow never requires a
   positive finding or promotable claim to close a cycle.
8. **One authority per fact.** DeerMem owns provisional prompt context;
   PostgreSQL owns validated claims and promotion history; project Markdown is
   a portable rendering, not a competing authority.
9. **Promotion and publication are different.** Human validation within the
   project does not automatically widen knowledge to other projects or the
   workspace.
10. **Gates are durable records.** A checkpoint interrupt, chat reply, or
    truthy payload cannot itself authorize a transition.
11. **GreenAgent remains the deterministic policy evaluator.** DeerFlow does
    not reimplement transition legality in model prompts or Python branches.
12. **Reuse the working DeerFlow substrate.** Preserve project scoping,
    sandboxing, lead-agent behavior, subagent execution, task events, files,
    artifacts, Human Input Cards, tracing, cancellation, and run history.
13. **Ceremony scales with consequence.** Cycle class and weight control the
    required evidence and review burden.
14. **Migration never silently shares private memory.** Existing per-user
    project facts are inventoried and re-labeled before any user-approved
    promotion into shared project working memory.

## 3. Current state and blockers

### 3.1 DBTL is opt-in and incomplete

The committed `dbtl_orchestrator`:

- is a separate assistant target;
- uses a fixed, hand-copied happy path;
- writes placeholder artifacts;
- lacks real stage workers;
- invokes live `interrupt()` gates that the current frontend does not render;
- carries a lean state that omits the normal thread's sandbox, files, uploads,
  artifacts, summaries, and other channels.

It remains useful as compatibility scaffolding and a headless diagnostic
target. It is not the final interactive architecture.

### 3.2 Gate behavior is not safe enough to build upon

The current skeleton has four live governance defects:

1. A bare truthy string is accepted as both approval and authorization.
2. The gate actor is inferred from the target state rather than from an
   authenticated reviewer principal.
3. A non-interactive scheduled run can park at a human interrupt and hold the
   thread's active-run slot.
4. A replayed checkpoint can reuse a previously accepted approval payload.

Additional reproducibility gaps:

- authorization references are not verified as single-use review records;
- evaluations are not bound to exact artifact hashes and database revision;
- GreenAgent tool version is not persisted;
- the blocking CLI call is unsuitable inside an asynchronous graph node;
- `run_events` is an observability stream, not an immutable audit ledger.

These defects must be resolved before the new graph is built.

### 3.3 DBTL state is not durable

The project rail stores cycles and to-dos in browser `localStorage`. The
project row carries only a summary phase and reconciliation status. There are
no authoritative cycle, stage-attempt, transition, evidence, or review rows.

### 3.4 DeerMem project memory is not shared

Current project memory derives a bucket from `(user_id, project_id)`. This
prevents cross-project leakage but intentionally gives two users in one project
different memories.

Existing facts live under:

```text
{storage_root}/users/{safe_user_or_synthetic_scope}/
├── memory.json
└── agents/{agent_name}/facts/{sha256-prefix}/{fact_id}.md
```

The facts are already canonical Markdown with revisions and journaling, but:

- categories are closed;
- facts must remain `status: active`;
- deletion is physical;
- there is no immutable retraction or supersession chain;
- search is currently substring-oriented;
- the public `source` value is lossy;
- the configured fact cap can evict lower-confidence facts;
- storage and consolidation settings are process-global.

DeerMem is therefore appropriate for provisional working context and retrieval
pointers, not validated scientific authority.

### 3.5 The current workflow is software-delivery-shaped

The existing path omits two critical research controls:

- reconciliation of contradictory or incorrectly coded inputs before
  modelling;
- non-binary validity assessment after results.

A pooled accuracy can appear excellent while within-population accuracy,
direction checks, holdouts, or structure-null comparisons invalidate the
claim. The workflow must make those failures structural, not optional advice.

## 4. Final information and authority model

### 4.1 Durable hierarchy

```text
Workspace
└── Project
    ├── Conversations
    ├── DBTL cycles
    │   ├── Intake and scope
    │   ├── Design
    │   ├── Data readiness and reconciliation
    │   ├── Build
    │   ├── Test and validity assessment
    │   ├── Learn and closeout
    │   ├── Stage attempts and work items
    │   ├── Evidence and artifacts
    │   └── Reviews and transitions
    ├── Project working memory
    ├── Validated knowledge
    ├── Team and agent assignments
    └── Activity and audit history
```

Data Readiness and Reconciliation is not a fifth DBTL stage. It is a mandatory
bridge node after Design approval and before Build execution. This preserves
the four-stage vocabulary while preventing Build from operating on unresolved
data.

### 4.2 Authority by layer

| Layer | Authority | Human-readable form | DeerMem responsibility |
| --- | --- | --- | --- |
| Personal memory | DeerMem | none required | owns private continuity and preferences |
| Project working memory | DeerMem project scope | optional project summary rendering | owns provisional context, visibly unvalidated |
| DBTL workflow | PostgreSQL | project status/review rendering | retrieval context only |
| Structured research records | PostgreSQL | exports and project documents | retrieval context only |
| Validated project knowledge | PostgreSQL claims and promotions | `<project>/knowledge/*.md` | pointer facts only |
| Published/shared knowledge | PostgreSQL publication scope | same rendered documents | widened-scope pointer facts |
| GreenAgent policy projection | PostgreSQL-derived temporary files | best-effort `.greenagent` render | none |

The Markdown knowledge file is a rendering of the SQL-authoritative claim. It
is portable, diffable, and human-readable, but it cannot overwrite a newer SQL
revision.

## 5. Project Supervisor Graph

### 5.1 Top-level topology

```text
project-scoped user turn
   │
   ▼
hydrate project context
   ├── authenticated project and membership
   ├── pinned and active cycles
   ├── pending intake or review responses
   ├── fresh project working memory
   └── validated-knowledge references
   │
   ▼
route request
   ├── deterministic rules
   └── structured classifier only if no rule resolves it
   │
   ├── ordinary_work ───────────────▶ existing lead-agent graph
   ├── propose_cycle ───────────────▶ DBTL Upgrade Proposal card
   ├── clarify_cycle ───────────────▶ Human Input Card
   └── continue_cycle ──────────────▶ resolve current stage
                                               │
                                               ▼
                                        stage subgraph
                                               │
                                   policy evaluation and review
                                               │
                                      atomic durable transition
```

The supervisor state extends the complete thread state. It must not replace
the normal conversation state with the current lean `DBTLState`.

The ordinary-work lead agent is a terminal branch of the supervisor. It is not
used as a stage worker. This preserves the full middleware chain while avoiding
nested stage-worker harms involving title generation, clarification endings,
passive memory writes, and competing goal loops.

Projectless Inbox conversations bypass the supervisor and retain their current
lead-agent behavior.

### 5.2 Deterministic-first routing

Routing precedence:

1. Explicit user instruction to work ordinarily, start a cycle, or use a named
   cycle.
2. A valid response to a pending DBTL proposal, intake question, or review.
3. A conversation pinned to a valid project cycle.
4. An unambiguous applicable active project cycle.
5. Structured model classification.
6. Ordinary work when evidence remains insufficient.

Rules 1–4 require no classifier call. The classifier cannot create a cycle or
approve a transition.

### 5.3 Classifier output

```text
route:
  ordinary_work
  | propose_cycle
  | continue_cycle_candidate
  | clarify
candidate_cycle_id: optional
proposed_stage: design | build | test | learn
confidence: 0..1
reason_codes: list
missing_fields: list
risk_level: low | medium | high
user_confirmation_required: true
```

`user_confirmation_required` is always true for `propose_cycle` unless the
user's current message explicitly commands creation and supplies the required
intake.

### 5.4 DBTL Upgrade Proposal

When the AI believes ordinary work should become a tracked DBTL cycle, the UI
must show:

> This request appears to involve a durable research objective and may benefit
> from a tracked DBTL cycle. No cycle has been created yet.

The card includes:

- why the request was classified as DBTL;
- proposed objective and stage;
- expected evidence and review burden;
- whether a matching active cycle exists;
- missing intake information;
- cycle class and weight suggestion.

Actions:

- **Start DBTL cycle**
- **Continue existing cycle**
- **Keep as ordinary chat**
- **Edit proposed scope**

Before the human selects an action:

- no `dbtl_cycles` row is created;
- no DBTL transition or artifact is written;
- no project phase changes;
- no stage worker starts.

The ordinary conversation and its Human Input Card remain durable in normal
thread history. If the user ignores or dismisses the proposal, the request
remains ordinary work.

### 5.5 Classifier rollout safeguards

Before classifier-directed routing becomes active:

1. Run it in shadow mode against representative project requests.
2. Compare proposed route with explicit human choices.
3. Measure false-upgrade, missed-cycle, and wrong-cycle rates.
4. Inspect performance separately for ordinary file work, short analyses,
   research planning, active-cycle continuation, and ambiguous requests.
5. Enable the warning card only after the false-upgrade rate is acceptable.
6. Keep a visible “Always keep this request ordinary” override.

Classifier confidence changes presentation, not authority. A confidence of
1.0 still cannot create a cycle.

## 6. DBTL workflow and plant-science validity

### 6.1 Research-native flow

```text
intake
  → Design
  → human Design review
  → Data Readiness and Reconciliation
  → Build
  → Test and Validity Assessment
  → Learn and Closeout
  → supported | not_supported | inconclusive | invalidated
```

At any point, policy may route to:

- request changes;
- repeat the current stage;
- return to Design;
- return to Data Reconciliation;
- return to Build;
- pause;
- abort;
- supersede.

### 6.2 Design

Required Design outputs:

- research objective and decision context;
- null and alternative hypotheses where appropriate;
- target population, germplasm, environment, season, and trait scope;
- success, rejection, and inconclusive criteria;
- data source inventory;
- expected identifiers, units, coding, direction, and biological constraints;
- analysis or implementation plan;
- test strategy;
- leakage controls and holdout policy;
- risks and required human decisions;
- proposed cycle class and weight.

Design may not advance until success and rejection criteria are operational,
not merely descriptive.

### 6.3 Data Readiness and Reconciliation bridge

This bridge executes after Design approval and before Build may use the data.
It may assign a data steward, breeder, statistician, or other
capability-matched agents.

Required reconciliation checks, selected by the cycle's domain pack:

- source-by-source inventory and provenance;
- file and dataset versions;
- immutable raw-data declaration;
- identifier uniqueness and referential integrity;
- germplasm, pedigree, plot, treatment, and environment mapping;
- units, encodings, missing-value conventions, and trait direction;
- contradictory source comparison;
- expected sample/material counts;
- duplication, exclusion, and missingness report;
- biologically implausible values and known-direction contrasts;
- phenotype/genotype/sample alignment;
- train/test and temporal/environmental separation;
- leakage risk;
- reconciliation decisions with actor and evidence.

Possible bridge outcomes:

- `ready_for_build`
- `changes_required`
- `blocked_missing_data`
- `blocked_conflicting_sources`
- `inconclusive_data`

Build cannot begin from any outcome other than `ready_for_build`.

The reconciliation artifact includes exact dataset hashes. Build and Test bind
their work to those hashes. If a dataset changes, the reconciliation gate is
invalidated and must be rerun.

### 6.4 Build

Build produces:

- executable implementation, analysis pipeline, or field/lab protocol;
- configuration and environment capture;
- data-lineage references to the reconciled inputs;
- artifact manifest;
- implementation deviations;
- logs sufficient to reproduce the work;
- handoff describing unresolved risks.

Build may never modify declared raw-data paths. Derived data is written to a
separate, versioned location.

### 6.5 Test and Validity Assessment

Test evaluates scientific and implementation validity separately.

Core contract:

- verify exact input and artifact versions;
- recheck critical reconciliation assertions;
- run implementation and regression tests;
- compare results with Design criteria;
- report uncertainty and limitations;
- distinguish metric performance from claim validity.

For genomic selection and similar predictive work, the first reference
validity pack includes:

1. A structure-null or appropriate nuisance baseline reported beside every
   claimed accuracy.
2. Asserted fold composition, train/test counts, and disjointness—not log-only
   reporting.
3. A heritability or biological ceiling check. Accuracy exceeding a plausible
   ceiling increases scrutiny and blocks promotion until explained.
4. Direction checks for known-direction contrasts.
5. A tester-controlled holdout that Build workers did not inspect.
6. Within-population or within-environment results when pooled results can hide
   structure.
7. Leakage, duplicate, and relatedness sensitivity analyses where applicable.

High reported accuracy is never a reason to bypass a gate.

### 6.6 Non-binary Test decisions

Test produces both an evidence result and a workflow recommendation:

```text
evidence_result:
  supported
  | not_supported
  | inconclusive
  | invalidated

workflow_recommendation:
  advance_to_learn
  | repeat_test
  | return_to_build
  | return_to_reconciliation
  | return_to_design
  | close_cycle
```

`invalidated` includes reason codes such as:

- implausible_high_accuracy;
- population_structure_artifact;
- train_test_leakage;
- direction_check_failure;
- holdout_failure;
- unreconciled_data;
- irreproducible_execution.

An implausibly high accuracy may route to rework or may close the cycle as
`invalidated`. The UI must describe it as a validity rejection, not as a
successful high-performing result.

### 6.7 Learn and closeout

Learn synthesizes:

- what was attempted;
- what the evidence supports;
- what the evidence rejects;
- uncertainty and unresolved contradictions;
- data and method limitations;
- reusable process lessons;
- candidate changes to future Design, reconciliation, Build, or Test practice;
- optional working-memory and validated-knowledge candidates.

Learn cannot manufacture a knowledge candidate merely to close a cycle.

Cycle close outcomes:

- `supported`
- `not_supported`
- `inconclusive`
- `invalidated`
- `abandoned`
- `superseded`

Only `supported` and `not_supported` normally imply a scientific claim.
`inconclusive` may produce a process lesson but requires no scientific claim.
`invalidated` may produce a QA or methodology lesson but cannot promote the
invalidated result as a finding.

## 7. Stage architecture and dynamic agents

### 7.1 StageSpec

Every stage or bridge node is versioned data:

```text
id
stage
domain_profile
cycle_classes
cycle_weights
required_inputs
required_artifact_types
output_schemas
required_capabilities
allowed_tools
memory_read_policy
memory_write_policy
validity_gates
transition_policy
human_gate_policy
version
```

The Data Readiness bridge is represented by its own `StageSpec` but maps to
the Design-to-Build transition rather than becoming a fifth DBTL phase.

### 7.2 AgentSelector

Each work unit declares capabilities, not a fixed role name. Agent selection
considers:

- capability match;
- active project assignment;
- skills and tools;
- project and dataset scope;
- write permissions;
- model and budget policy;
- autonomy level;
- human-review requirements;
- availability.

Possible capabilities:

- breeding strategy;
- germplasm and pedigree analysis;
- experimental design;
- quantitative genetics;
- statistical analysis;
- data reconciliation and lineage;
- literature review;
- software and workflow engineering;
- field-trial quality control;
- validity assessment;
- scientific reporting;
- knowledge synthesis.

### 7.3 Worker execution

DBTL stage workers use `SubagentExecutor`, not nested lead-agent graphs.

Reuse:

- skill and tool authorization;
- sandbox and project workspace;
- token, turn, loop, and time budgets;
- task lifecycle events;
- cancellation;
- Langfuse tracing;
- custom and built-in agent registries.

Each worker returns a structured `StageWorkerResult`:

```text
status
summary
artifact_refs
evidence_refs
claims
limitations
provenance
quality_checks
recommended_next_actions
```

Free-form text alone cannot satisfy a stage contract.

## 8. PostgreSQL-first durable model

### 8.1 Required entities

The trusted DBTL foundation includes:

- `dbtl_cycles`
- `dbtl_stage_runs`
- `dbtl_transition_intents`
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

Existing run records gain nullable:

- `project_id`
- `dbtl_cycle_id`
- `dbtl_stage_run_id`
- `work_item_id`

Conversation scope may gain a validated pinned cycle association. A cycle
belongs to a project, not to a conversation.

### 8.2 Cycle fields

```text
id
workspace_id
project_id
name
objective
hypothesis
cycle_class: computational | season | program
cycle_weight: full | light | retroactive
status
current_stage
close_outcome
db_revision
reconciliation_status
created_by
created_at
updated_at
```

`close_outcome` is distinct from execution `status`.

### 8.3 Gate evaluation and review

Gate evaluation records:

```text
id
project_id
cycle_id
transition_intent_id
expected_db_revision
from_state
proposed_state
artifact_versions_and_hashes
projection_hash
policy_tool_name
policy_tool_version
policy_input
policy_output
result
created_at
```

Review records:

```text
id
gate_evaluation_id
decision: approve | request_changes | reject
actor_type
actor_user_id
actor_project_role
authorization_reference
idempotency_key
expected_db_revision
artifact_hashes
created_at
consumed_at
```

A review is single-use. Approval requires:

- an authenticated non-internal human principal;
- an authorized project role;
- matching gate evaluation;
- matching prior revision;
- unchanged artifact hashes;
- unused idempotency key;
- unconsumed review row.

### 8.4 Database revision and reconciliation

Every accepted mutation uses optimistic revision checks.

Transition sequence:

1. Create an idempotent transition intent at the expected revision.
2. Render a throwaway GreenAgent projection from that exact database snapshot.
3. Run the policy evaluator against the temporary projection.
4. Persist evaluator input, output, hashes, and tool version.
5. Collect a durable human review if required.
6. Atomically commit transition, review consumption, activity, and new
   revision.
7. Refresh the human-visible `.greenagent` projection best-effort.
8. Compare projection revision and hash.

Projection states:

- `in_sync`
- `projection_pending`
- `reconciliation_blocked`
- `projection_retired`

The database always wins. A projection mismatch blocks new transitions but
does not hide read-only project history.

## 9. Gate hardening before graph work

### 9.1 Required corrections

The existing gate path must be redesigned around durable records:

- accept only a typed review decision, never a bare truthy value;
- derive actor identity from the authenticated reviewer principal;
- verify project membership and reviewer role;
- reject internal agents and service principals as approvers;
- bind the review to one evaluation, revision, and artifact hash set;
- enforce single-use consumption with an idempotency key;
- prevent checkpoint replay from reusing approval;
- persist GreenAgent tool version;
- offload blocking policy evaluation from the async event loop;
- remove live long-lived `interrupt()` as gate authority;
- make scheduled/non-interactive behavior terminate cleanly as
  `pending_human_review` without holding an active-run slot.

### 9.2 Human review transport

The existing Human Input Card protocol may notify and collect conversational
input. The durable review row remains authoritative.

Long-lived gates:

- create the gate request;
- emit a normal terminal assistant message/card;
- finish the run cleanly;
- notify the accountable human;
- continue in a new run after a valid review is committed.

No multi-day checkpoint interrupt is retained.

### 9.3 Go/no-go foundation gate

Supervisor Graph implementation is prohibited until all of the following are
demonstrated:

- PostgreSQL is the configured production authority;
- migrations and rollback have been rehearsed;
- all DBTL core tables and constraints exist;
- cross-project authorization tests pass;
- typed review parsing passes;
- internal and non-interactive approval attempts are rejected;
- replay and duplicate approval tests pass;
- actor attribution derives from the principal;
- artifact/revision drift invalidates approval;
- policy tool version is persisted;
- pending-human runs release the active-run slot;
- database-led projection rebuild passes failure-injection tests.

This is the first formal human review gate in the delivery plan.

## 10. PostgreSQL migration plan

The production migration precedes graph development.

### 10.1 Preflight

- Inventory current SQLite tables, row counts, schema revision, database size,
  and active runs.
- Record existing workspace, project, thread, run, scheduled-task, and agent
  relationships.
- Snapshot the SQLite database.
- Snapshot the configured DeerMem storage root separately.
- Record project root paths; project folders themselves are not copied into
  PostgreSQL.
- Reject migration while active runs or memory writes are in flight.

### 10.2 Target preparation

- Provision PostgreSQL with least-privilege application and migration roles.
- Run the complete Alembic chain, including the DBTL foundation migration.
- Verify foreign keys, unique constraints, partial indexes, time zones, JSON
  behavior, and transaction isolation.
- Enable production preflight that refuses SQLite for collaborative DBTL mode.

### 10.3 Data transfer

- Export existing SQLite rows in dependency order.
- Preserve primary IDs, ownership, timestamps, metadata, and conversation
  scope.
- Import workspaces and memberships before projects, projects before scoped
  threads, and threads before runs/events.
- Initialize new DBTL tables empty unless an explicit legacy import is
  approved.
- Do not automatically convert browser-local cycle plans into authoritative
  cycles.
- Reset PostgreSQL sequences after preserving existing identifiers.

### 10.4 Validation

- Compare table counts and deterministic row hashes.
- Verify every project has an active workspace membership path.
- Verify every scoped thread references its expected project.
- Verify project root paths still resolve and are not duplicated.
- Verify run and event ordering.
- Execute cross-project denial probes.
- Compare shadow reads from SQLite and PostgreSQL for representative users.

### 10.5 Cutover and rollback

- Enter a short write-maintenance window.
- Re-run delta validation.
- Switch the application to PostgreSQL.
- Run smoke tests for login, projects, conversations, files, runs, memory,
  scheduling, and authorization.
- Retain the SQLite snapshot read-only for a defined rollback period.
- Roll back by configuration and restore only before new PostgreSQL-only DBTL
  writes are accepted.
- After DBTL writes begin, rollback requires an explicit reverse migration or
  forward repair; silently reverting to stale SQLite is prohibited.

## 11. DeerMem scope migration plan

### 11.1 Goal

Move from implicit per-user project buckets to explicit memory scopes without:

- losing existing facts;
- silently sharing private facts;
- breaking custom-agent buckets;
- bypassing DeerMem's locks, journal, revisions, or retrieval notifications.

### 11.2 Canonical scopes

```text
Personal:
  kind=personal
  actor_user_id=<user>

Project working:
  kind=project_working
  workspace_id=<workspace>
  project_id=<project>
  actor_user_id=<request actor, for audit only>
```

The compatibility adapter resolves the project-working scope to one
project-owned DeerMem bucket independent of the current actor. The exact path
encoding is implementation-private and recorded in a scope manifest; it must
not rely on an irreversible hash with no reverse mapping.

### 11.3 Existing-fact classification

The migration inventory examines:

```text
{storage_root}/users/*/memory.json
{storage_root}/users/*/agents/*/facts/**/*.md
```

For each bucket:

1. Personal user bucket → retain as personal memory.
2. Recognized synthetic `(user, project)` bucket → label as legacy private
   project memory.
3. Unknown synthetic bucket → quarantine as orphaned; never guess a project.
4. Custom-agent bucket → preserve the agent name and fact isolation.
5. Conflicting fact IDs or corrupt documents → stop that scope and report.

Recognized project mappings are reconstructed by enumerating authorized
`(user_id, project_id)` pairs from PostgreSQL and recomputing the existing
bucket digest. The migration never attempts to reverse the hash.

### 11.4 Safe migration stages

#### Stage A: format preflight

- Run the existing Markdown-memory migration in dry-run mode.
- Ensure every active legacy JSON fact has been migrated into canonical
  Markdown.
- Require a full filesystem snapshot.
- Preserve immutable `.v1.bak` files.

#### Stage B: scope inventory

- Produce an idempotent manifest with source path, user, inferred project,
  agent, fact ID, revision, checksum, category, source thread, and disposition.
- Make no writes.
- Require human review of orphaned and conflicting buckets.

#### Stage C: private relabel

- Mark recognized legacy project facts as private-to-contributor migration
  input.
- Do not copy them into the shared project bucket.
- Keep them readable through a temporary compatibility view during review.

This is the safe default because existing facts may contain personal
preferences, stale project identity, or content never intended for teammates.

#### Stage D: user-approved promotion into project working memory

- Present each contributor with a reviewable candidate set.
- Permit accept, edit, reject, or keep-private decisions.
- Deduplicate approved candidates by normalized content hash and provenance.
- Preserve original fact ID, source path, checksum, contributor, and revision
  in migration metadata.
- Resolve same-ID/different-content conflicts manually.
- Write through the DeerMem repository API, never direct filesystem writes.

#### Stage E: cutover

- Stop new writes to legacy synthetic buckets.
- Enable the explicit project scope adapter.
- Load project working memory fresh through the new scope.
- Verify two authorized members retrieve the same accepted project facts.
- Verify personal and rejected legacy facts remain private.

#### Stage F: archive

- Keep legacy buckets read-only for the rollback window.
- Store the migration manifest and verification report.
- Archive rather than delete after human confirmation.
- Never delete `.v1.bak` migration evidence automatically.

### 11.5 Migration invariants

- The migration is idempotent.
- Every changed scope has a backup and manifest.
- No existing fact is overwritten in place.
- No private fact becomes shared without a human decision.
- Unknown buckets are quarantined, not guessed.
- Custom-agent separation remains intact.
- DeerMem fact and manifest revisions remain valid.
- Retrieval adapters receive normal post-commit notifications.
- A failed scope does not prevent other scopes from being inventoried, but the
  command exits non-zero and cutover remains blocked.

## 12. Knowledge lifecycle

### 12.1 Working-memory candidate

A stage aggregation node may submit:

```text
project_id
cycle_id
stage_run_id
producing_run_id
producing_agent
content
category
source_artifact_versions
evidence_refs
confidence
limitations
data_use_constraints
contradictions
scope
```

Candidates are provisional and cannot satisfy an authoritative knowledge query.

### 12.2 Validated project knowledge

Human promotion creates:

- immutable `knowledge_claim`;
- immutable `knowledge_promotion`;
- evidence and artifact links;
- reviewer identity and authorization;
- limitations and scope;
- supersession/retraction relationships;
- a Markdown rendering under `<project>/knowledge/`;
- a short DeerMem pointer fact.

The pointer carries stable claim ID, project, topics, cycle, evidence grade,
and rendered-document path at top-level frontmatter. It does not duplicate the
entire claim.

Project pointer facts are protected from consolidation and staleness deletion.
If per-scope DeerMem settings remain unavailable, project pointer protection is
a release requirement before pointer creation is enabled.

### 12.3 Publication

Publication is a second human decision:

- project-only validation does not imply workspace visibility;
- the publisher chooses the widened scope;
- authorization is evaluated before retrieval;
- publication produces a new immutable event;
- retraction propagates to every widened scope and pointer.

## 13. Delivery plan and hard dependencies

This is a Builder planning sequence only. It does not authorize implementation.

### Phase 0: freeze unsafe DBTL execution

Planning outcomes:

- Treat the current interactive `dbtl_orchestrator` as experimental.
- Define the expected behavior for existing invalid cycle and handoff files.
- Decide whether to repair or supersede them.
- Define the GreenAgent policy version needed for non-binary close outcomes and
  optional knowledge candidates.

Exit review:

- Human approves the migration and repair posture.

### Phase 1: PostgreSQL and gate-governance foundation

Scope:

- PostgreSQL migration;
- DBTL core schema;
- authorization and audit envelope;
- typed, single-use gate reviews;
- policy projection and version capture;
- non-interactive and replay safety;
- failure-injection tests.

Explicitly excluded:

- intent classifier;
- Supervisor Graph;
- stage workers;
- cycle UI beyond administrative inspection.

Exit review:

- The go/no-go foundation gate in Section 9.3 passes.

### Phase 2: DeerMem scope bridge and legacy-fact migration

Scope:

- explicit scope object and compatibility adapter;
- fact inventory and migration manifest;
- private legacy relabel;
- user-approved sharing;
- fresh project retrieval;
- personal/project isolation tests.

Explicitly excluded:

- automatic stage memory writes;
- knowledge promotion UI;
- Supervisor Graph.

Exit review:

- Two authorized users share only approved project facts; unapproved facts stay
  private; rollback is demonstrated.

### Phase 3: durable cycles and manual workflow

Scope:

- durable cycle and stage APIs;
- cycle class and weight;
- stage attempts, work items, artifacts, and reviews;
- database-backed project rail;
- manual cycle creation and manual stage/gate advancement through the same
  durable contracts the graph will later call.

Purpose:

- prove the database and review model without classifier or graph complexity.

Exit review:

- A manually driven Design → Reconciliation → Build gate sequence survives
  restart and concurrent/replay tests.

### Phase 4: classifier shadow mode and upgrade UX

Scope:

- structured deterministic-first router;
- classifier shadow evaluation;
- DBTL Upgrade Proposal card;
- explicit ordinary/DBTL/existing-cycle choice;
- intake clarification.

No classifier decision creates a cycle.

Exit review:

- Human accepts false-upgrade and missed-cycle results.

### Phase 5: thin Supervisor Graph

Scope:

- complete thread-state-preserving supervisor;
- terminal ordinary-work lead-agent branch;
- cycle proposal, clarification, and continuation routing;
- no stage workers yet beyond a controlled manual/stub adapter that cannot
  write scientific results.

Exit review:

- Ordinary project conversations retain all existing capabilities and no
  silent cycle is created.

### Phase 6: dynamic Design and Reconciliation subgraphs

Scope:

- StageSpec registry;
- AgentSelector;
- SubagentExecutor fan-out;
- structured worker results;
- Design artifacts;
- Data Readiness and Reconciliation gate;
- durable evidence and activity.

Exit review:

- One real reference project reaches `ready_for_build` with a human-reviewed
  reconciliation report.

### Phase 7: Build and Test validity

Scope:

- Build execution and lineage;
- Test and validity packs;
- structure-null, fold, ceiling, direction, and holdout gates;
- rework routing;
- non-binary evidence outcomes.

Exit review:

- Synthetic high-accuracy leakage and biological-implausibility cases are
  rejected despite strong headline metrics.

### Phase 8: Learn, knowledge promotion, and publication

Scope:

- Learn synthesis;
- optional candidates;
- validated SQL claims;
- Markdown rendering;
- DeerMem pointers;
- supersession, retraction, and separate publication.

Exit review:

- An inconclusive cycle closes without a claim; a valid negative result can be
  promoted; a retraction disappears from current retrieval without losing
  audit history.

## 14. Minimum reviewable product slice

The first graph-enabled product slice occurs only after Phases 1–4:

```text
classify a project request in shadow-validated routing
  → show a no-record DBTL Upgrade Proposal
  → human confirms
  → create one durable Design cycle
  → execute one Design work item
  → persist and display its artifact
  → request human Design approval
  → execute Data Readiness and Reconciliation
  → stop at ready-for-build or a visible reconciliation blocker
```

This replaces the earlier slice that transitioned directly from Design to
Build. Build is not allowed until data readiness is demonstrated.

## 15. Tester requirements

### 15.1 Routing and UX

1. An ordinary README, file-view, explanation, or export request creates no
   cycle.
2. A classifier-proposed DBTL upgrade displays “No cycle has been created
   yet.”
3. Dismissing or ignoring the proposal leaves no DBTL domain record.
4. “Keep as ordinary chat” routes through the unchanged lead agent.
5. Explicit “start a DBTL cycle” creates a cycle only after required intake is
   complete.
6. Multiple plausible cycles require human selection.
7. A cycle from another project is rejected.
8. Classifier confidence never bypasses confirmation.

### 15.2 Database and gates

9. Production collaborative DBTL mode refuses SQLite.
10. SQLite-to-PostgreSQL migration preserves IDs, ownership, timestamps, and
    event ordering.
11. Cross-project reads and mutations fail at repository and API boundaries.
12. Bare strings and malformed approval payloads are rejected.
13. Internal, scheduled, and agent principals cannot approve.
14. The recorded actor is the authenticated reviewer.
15. Replayed and duplicate reviews cannot re-satisfy a gate.
16. Artifact or revision drift invalidates an approval.
17. A pending-human run releases the active-run slot.
18. Policy input, output, projection hash, and tool version are reproducible.
19. Database/projection mismatch blocks transitions and repairs only from SQL.

### 15.3 Plant-science workflow

20. Build cannot start without `ready_for_build`.
21. Contradictory source coding produces a visible reconciliation blocker.
22. Dataset hash change invalidates prior reconciliation.
23. Raw data remains unchanged.
24. Fold count or composition mismatch blocks Test.
25. A missing structure-null comparison blocks predictive-accuracy claims.
26. Implausibly high accuracy triggers validity rejection, not automatic pass.
27. Direction-check, leakage, and tester-holdout failures block promotion.
28. A valid negative result can advance to Learn.
29. An inconclusive cycle closes without a knowledge candidate.
30. An invalidated result cannot become a scientific finding.

### 15.4 Memory migration and knowledge

31. Existing v1 facts are canonical Markdown before scope migration.
32. Recognized synthetic buckets map only through recomputed known project
    pairs.
33. Unknown buckets are quarantined.
34. Existing private project facts are not automatically shared.
35. User-approved facts appear to all authorized project members.
36. Rejected/private facts remain available only to their original user.
37. Custom-agent facts retain their agent scope.
38. Scope migration is idempotent and preserves checksums and revisions.
39. Project working memory is fresh per run/stage.
40. Provisional memory never appears as validated knowledge.
41. Promotion records reviewer, evidence, limitations, and scope.
42. Validated claims survive DeerMem consolidation and staleness operations.
43. Retraction removes a claim from current retrieval while preserving history.
44. Publication requires a second explicit scope decision.

### 15.5 Compatibility

45. Ordinary project chat retains sandbox, files, uploads, artifacts,
    summarization, goals, cancellation, and title behavior.
46. Stage workers emit existing task events and respect budgets and stop
    reasons.
47. Thread deletion cannot delete or orphan a project, cycle, claim, decision,
    or promotion.
48. Checkpoint deletion cannot alter accepted DBTL state.

## 16. Human decisions required

The final architecture resolves the four requested suggestions. The following
product-policy choices remain for human approval:

1. Permit one active cycle per project initially, or multiple active cycles?
- one project initially
2. May season/program cycles contain child computational cycles?
- yes.
3. Which project roles may approve Design, reconciliation, Test validity, cycle
   close, knowledge promotion, and publication?
- human first. then decide later for experenced Agents.
4. Which transitions beyond the existing GreenAgent gates require human
   review?
- we will need to decide after some test.
5. Is the initial publication scope project → workspace, or project → selected
   projects?
  - selected
   projects
6. Which maize or genomic-selection workflow supplies the first
   `StageSpec`/validity pack?
   - later
7. What evidence grade is required for a positive claim, negative claim, QA
   lesson, and methodology lesson?
   - later. 
8. What retry, alerting, and recovery policy applies to
   `reconciliation_blocked`?
   - later
9. Repair the existing invalid GreenAgent cycle/handoff records or supersede
   them with a new cycle?
   - later
10. Approve PostgreSQL cutover before any Supervisor Graph work?
- not sure what does this mean. we will decide later.

## 17. Final recommendation

Approve the architecture subject to the human decisions above, then authorize
planning handoff in the strict phase order defined in Section 13.

The most important sequencing rule is:

> **Trust the database and gates before adding autonomous routing or stage
> execution.**
- yes

The most important UX rule is:

> **AI may recommend DBTL, but only the human can turn ordinary work into a
> durable cycle.**
- yes

The most important plant-science rule is:

> **Reconcile inputs before Build, and reject invalid evidence even when the
> headline accuracy is high.**
-yes.

This plan authorizes no code changes, migrations, data movement, or runtime
configuration changes.

