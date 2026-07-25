# Workspace-Oriented AI Plant Breeding System

Status: awaiting human design review  
GreenAgent role: Designer  
Work item: `workspace-oriented-breeding-design`  
DBTL cycle: `workspace-oriented-breeding-design`  
Authoring agent: Codex  
Date: 2026-07-24

This document consolidates the independently authored design with
`plan/design/2026-07-24-project-oriented-refactor-design.md`. Where the two
designs disagreed, the human direction recorded on 2026-07-24 is authoritative.

## Design summary

### High-level goal

Refactor DeerFlow from a conversation-centered AI interface into a durable,
workspace/project-centered operating system for plant-breeding teams. Human
coworkers and bounded AI agents collaborate through GreenAgent-gated DBTL
cycles, structured breeding records, project-scoped knowledge, explicit
decisions, and auditable evidence.

The durable project becomes the primary product object. Chat remains available
as a contextual command and discussion surface attached to a project, DBTL
cycle, task, trial, record, or review.

### In scope

- Workspace and breeding-project information architecture.
- Human and AI project-team model.
- DBTL cycle, evidence, artifact, and approval experience.
- Project-scoped authorization and audit requirements.
- Durable operational, breeding, and knowledge data boundaries.
- Migration constraints for existing threads, runs, files, agents, memory, and
  scheduled tasks.
- Visual and interaction direction for a scientific operational application.
- Testable product hypotheses, tester requirements, and human review gates.

### Out of scope for this Designer package

- Code changes or executable scripts.
- A phased Builder implementation plan, estimates, staffing, or delivery dates.
- Selection of a final vector database, embedding model, or cloud vendor.
- A complete plant-breeding ontology or interchange standard.
- Automatic approval of research direction, methodology, data use, wet-lab
  changes, knowledge promotion, or DBTL transitions.
- Claims that the proposed workflow improves breeding outcomes without a
  prospective evaluation.

### Deliverables

- This human-readable design plan.
- A schema-valid GreenAgent design package.
- A schema-valid Designer council debate preserving minority positions.
- Tester requirements and falsifiable acceptance outcomes.
- A pending human-review record.

## Human direction incorporated

The following decisions are no longer open:

1. **The application database is authoritative.** `.greenagent` is a temporary
   development policy/projection tool and will eventually be retired. It is
   never the final source of project, cycle, approval, or knowledge state.
2. **PostgreSQL is required in production.** SQLite is limited to single-user
   local development and evaluation.
3. **The workspace has a global Inbox.** Humans and workspace-scoped agents can
   converse without first choosing a project. Legacy and exploratory threads
   are not forced into projects.
4. **The first reference breeding workflow is maize.** The core breeding schema
   remains generic; maize-specific attributes use versioned JSONB or extension
   tables until the reference workflow proves stable.
5. **Knowledge promotion is human-gated.** Learn-stage AI output enters Project
   Working Memory and cannot enter Validated Project Knowledge without an
   authorized human promotion decision.

## Independent codebase assessment

Existing design or roadmap documents were intentionally excluded from this
assessment.

### Current product center

1. `frontend/src/app/workspace/page.tsx:8-19` redirects the workspace root to a
   new or demo conversation.
2. `frontend/src/components/workspace/workspace-nav-chat-list.tsx:21-93`
   presents Chats, Agents, and Scheduled Tasks as the primary navigation.
3. `frontend/src/app/workspace/chats/[thread_id]/page.tsx:261-442` makes the
   message list and composer the central canvas. Files, artifacts, goals,
   scheduled tasks, browser control, and sidecar are thread accessories.
4. `backend/app/gateway/thread_project.py:1-14` defines a project as a
   per-thread filesystem link stored beside the thread and removed with it.
5. `backend/packages/harness/deerflow/agents/thread_state.py:21-24` stores only
   workspace, upload, and output paths as thread data; it has no project
   identity.

### Current persistence and collaboration boundaries

1. `threads_meta` and `runs` identify users and threads but have no project or
   team foreign keys.
2. Custom agents are unique per `(user_id, name)` and are not assigned to a
   durable project team.
3. Scheduled tasks target a user and optionally a thread; they are not scoped
   to projects, DBTL cycles, or work items.
4. `backend/app/gateway/authz.py:90-107` gives every authenticated user the same
   global permissions. Ownership checks exist, but workspace membership,
   project membership, and project roles do not.
5. `backend/app/gateway/routers/memory.py:1-36` resolves memory around a user
   owner. There is no explicit shared project-memory contract.
6. No first-class knowledge-source, document, chunk, claim, provenance, or
   project retrieval schema was found.

### Current assets worth preserving

- The Next.js workspace shell, side panels, streaming thread hooks, file
  browser, artifacts, human-input cards, todos, goals, and activity rendering.
- The Gateway run lifecycle, persistence abstraction, migrations, run events,
  scheduling, user isolation, and custom-agent configuration.
- The existing project-folder candidate and thread project-link APIs as
  migration and project-creation inputs.
- The Console API as a seed for project activity and cross-run operational
  views.
- LangGraph checkpoints for execution continuity.
- Subagent delegation and structured `task_*` status events, which can expose
  DBTL stage activity without inventing a second streaming protocol.
- Existing Langfuse tracing seams, extended to DBTL runs rather than replaced.
- Existing channels and notifications as delivery paths for pending gates and
  handoffs.
- GreenAgent DBTL transition validation and human gates.

### DBTL readiness

`backend/packages/harness/deerflow/agents/dbtl/orchestrator.py:1-20` is a
Phase-one run target with:

- a fixed happy path;
- placeholder artifacts;
- three human-gated transitions;
- a deterministic GreenAgent CLI gate;
- no role-agent execution yet.

This should become a durable project workflow rather than another specialized
chat assistant.

## Testable design hypotheses

### H0 — Null

Making the project the primary object will not materially improve a returning
team member's ability to identify the current DBTL state, accountable owner,
blocking review, supporting evidence, and next decision compared with the
current chat-first interface.

Falsification of H0: in a controlled task study using the same project state,
the project-first prototype produces a meaningfully lower median correct
orientation time and no worse error rate than the current interface.

### H1 — Primary alternative

A project-first workbench with a visible DBTL lifecycle, review inbox, evidence
provenance, and contextual AI-team inspector will reduce reconstruction work
and make long-term breeding activity safer and more legible.

Falsification: users still depend on conversation search to answer the five
orientation questions, or the project surface increases incorrect decisions,
missed gates, or cross-project information exposure.

### H2 — Competing, lower-change alternative

Chat can remain primary if threads receive project tags, a project filter, and
a stronger artifact sidebar.

Falsification: tagged chat cannot present authoritative cross-thread state,
team accountability, structured trial records, or review gates without
re-creating a project domain behind the conversation UI.

### H3 — Competing generic-PM alternative

A conventional board with tasks, owners, comments, and files is sufficient;
DBTL and breeding semantics can be represented by labels and templates.

Falsification: teams repeatedly need stage-specific evidence requirements,
human transition authority, season/environment context, pedigree and trial
relationships, or validation rules that generic labels cannot enforce.

## Debate synthesis

The council agrees that chat should survive but should not remain the unit of
ownership, authorization, project state, or scientific evidence.

The minority chat-first position is preserved: it is cheaper, familiar, and
may be sufficient for solo exploratory work. It should be tested as a baseline
and retained as a "working session" within projects.

The minority generic-PM position is also preserved: avoid implementing an
overly broad breeding ontology before real user workflows are observed. The
recommended design responds by separating a small collaboration core from an
incrementally expanded breeding domain.

## Target information architecture

```text
Breeding Workspace
├── Project portfolio
├── Global Inbox
│   ├── Workspace human-agent conversations
│   ├── Unassigned legacy threads
│   └── Private personal scratchpads
├── Cross-project review inbox
├── Agent library
├── Search
└── Breeding Projects
    ├── Overview
    ├── DBTL
    │   ├── Cycles
    │   ├── Evidence and artifacts
    │   ├── Reviews and transitions
    │   └── Decision history
    ├── Work
    │   ├── Tasks
    │   ├── Automations
    │   └── Working sessions
    ├── Materials
    │   ├── Germplasm
    │   ├── Pedigrees
    │   ├── Crosses
    │   └── Populations
    ├── Trials
    │   ├── Environments
    │   ├── Plots
    │   ├── Traits
    │   └── Observations
    ├── Data
    │   ├── Datasets
    │   ├── Imports and validation
    │   └── Analysis outputs
    ├── Knowledge
    │   ├── Sources and documents
    │   ├── Validated findings
    │   └── Search and citations
    ├── Team
    │   ├── Human coworkers
    │   └── Assigned AI agents
    ├── Activity
    └── Settings
```

### Route contract

- `/workspace` — project portfolio, recent project activity, pending reviews.
- `/workspace/inbox` — global human-agent conversations, unassigned legacy
  threads, and private scratchpads.
- `/workspace/projects/new` — project creation.
- `/workspace/projects/[project_id]` — project overview.
- `/workspace/projects/[project_id]/dbtl` — cycle timeline and evidence.
- `/workspace/projects/[project_id]/work` — tasks and automations.
- `/workspace/projects/[project_id]/materials` — breeding materials.
- `/workspace/projects/[project_id]/trials` — trial operations.
- `/workspace/projects/[project_id]/data` — datasets and validation.
- `/workspace/projects/[project_id]/knowledge` — sources and findings.
- `/workspace/projects/[project_id]/team` — people and agent assignments.
- `/workspace/projects/[project_id]/activity` — chronological audit stream.
- `/workspace/projects/[project_id]/sessions/[thread_id]` — contextual chat.

Legacy `/workspace/chats/*` routes remain readable during migration. Unassigned
threads resolve into the global Inbox and redirect into a project only after an
explicit project association is created.

The Inbox is workspace-scoped, but migration must not silently expose a
previously private legacy conversation. Imported legacy threads retain
`private-owner` visibility until their owner shares them with the workspace or
moves them into a project. New Inbox conversations may be workspace-visible or
private by explicit choice and can remain projectless indefinitely.

## Workspace composition

### Visual thesis

A calm field-and-laboratory operating system: paper-white working surfaces,
deep botanical green as the single action and lifecycle accent, rigorous
scientific typography, quiet dividers, dense tables, and minimal ornamental
chrome.

### Content plan

1. Global rail: workspace, project switcher, portfolio, Inbox, review inbox,
   search.
2. Project rail: stable domain navigation and current season/program context.
3. Primary canvas: the selected operational object, not a dashboard-card
   mosaic.
4. Context inspector: provenance, discussion, assigned humans and agents,
   related records, and contextual "Ask team" composer.
5. Activity layer: durable chronological events and review decisions.

### Interaction thesis

- A DBTL lifecycle strip expands a stage into its owner, requirements,
  evidence, gate status, and permitted transitions.
- Shared-layout transitions preserve orientation when moving from portfolio to
  project to cycle detail.
- Agent activity appears as restrained state changes in the activity stream
  and inspector, with reduced-motion support; no animated chatbot theater.

### Project overview

The first project viewport must answer:

1. What breeding objective is active?
2. Which DBTL stage, season, and environment are active?
3. What is blocked or awaiting human review?
4. What changed recently?
5. What should a human or agent do next?

Recommended primary composition:

- Project identity, objective, status, season, and project lead.
- One continuous DBTL lifecycle, not a row of disconnected cards.
- Pending approvals and blockers.
- Recent activity with actor attribution.
- Next work, ordered by decision consequence.
- Context inspector containing the selected object's evidence and team console.

## Durable domain model

### Collaboration core

- `workspaces`
- `workspace_members`
- `projects`
- `project_members`
- `project_agent_assignments`
- `conversation_scopes`
- `work_items`
- `comments`
- `decisions`
- `approvals`
- `activity_events`

Humans and agents have separate assignment tables because their identity,
authorization, and lifecycle differ. Their actions share an audit envelope:

```text
actor_type: human | agent | system
actor_id
project_id
action
subject_type
subject_id
run_id
occurred_at
metadata
```

Threads receive an explicit workspace conversation scope:

```text
workspace_id
project_id: nullable
scope_type: inbox | project | scratchpad
visibility: workspace | project-members | private-owner
```

`project_id = NULL` is a supported product state, not a migration error.

### DBTL core

- `dbtl_cycles`
- `dbtl_transitions`
- `dbtl_artifacts`
- `dbtl_reviews`
- `dbtl_gate_evaluations`

Each transition records the prior state, proposed state, GreenAgent input and
result, artifact versions, actor, authorization reference, decision, and
timestamps. Human-gated transitions cannot be satisfied by scheduled or other
non-interactive runs.

### Breeding domain

The initial bounded vocabulary should support:

- germplasm and pedigree edges;
- crosses and populations;
- sites, seasons, and environments;
- trials, plots, traits, and observations;
- selections and advancement decisions.

The first reference workflow is **maize breeding**. The relational core remains
crop-neutral, while maize-specific attributes such as heterotic group, maturity
group, inbred or hybrid classification, and crop-specific trait metadata live
in versioned JSONB documents or typed extension tables. Every extension record
includes a `schema_profile` such as `maize-v1` and a schema version.

The schema should not attempt to encode every crop or breeding method in its
first version. Stable maize attributes may graduate into typed relational
columns only after the reference workflow and tests demonstrate that the
meaning, cardinality, and query patterns are durable.

### Knowledge domain

- `knowledge_sources`
- `knowledge_documents`
- `knowledge_chunks`
- `knowledge_claims`
- `knowledge_links`
- `knowledge_retrieval_events`

Knowledge must preserve:

- source and document version;
- project and data-use scope;
- ingestion and validation status;
- extracted text location;
- claim-to-source links;
- producing run and agent;
- human review status;
- supersession and retraction.

Structured trial, pedigree, observation, and selection records remain
authoritative database objects. The knowledge base retrieves and explains
them; it does not replace them.

Knowledge lifecycle:

```text
Learn-stage output
  → Project Working Memory
  → awaiting human promotion review
  → Validated Project Knowledge
  → superseded or retracted when later evidence requires it
```

Working Memory is explicitly provisional. It may be retrieved only when a user
or agent requests provisional context, and every result is labeled
unvalidated. Default authoritative retrieval searches Validated Project
Knowledge plus authoritative structured records. Promotion is an immutable
human decision containing reviewer, evidence versions, source claims, scope,
limitations, and authorization reference.

### Existing-object associations

Add nullable project associations before making them mandatory:

- `threads_meta.project_id`
- `threads_meta.workspace_id`
- `threads_meta.scope_type`
- `threads_meta.visibility`
- `runs.project_id`
- `runs.dbtl_cycle_id`
- `runs.work_item_id`
- `scheduled_tasks.project_id`
- artifact and workspace-change project/cycle references

Existing project-link markers are migration inputs, not the future source of
truth. A marker can create or resolve one project, associate its thread, and
remain readable until migration is verified.

## Authorization model

### Workspace roles

- Owner
- Administrator
- Member
- Observer

### Project roles

- Breeding lead
- Scientist
- Data steward
- Field operator
- Reviewer
- Observer

Permissions are evaluated from membership and project role for every project
object. Global system roles do not imply access to every breeding project.

Required properties:

- deny access when project context is missing for a project-owned object;
- authorize knowledge retrieval before ranking or generating an answer;
- include actor identity and delegated authority in all mutations;
- distinguish an agent's tool capability from its project data scope;
- prevent scheduled/non-interactive runs from bypassing approval gates;
- test cross-workspace and cross-project isolation at repository and API
  boundaries.

## Human and AI working team

Agents are coworkers assigned to a project, not top-level destinations.

An assignment records:

- role and responsibility;
- allowed skills and tools;
- project data scopes;
- writable object types;
- autonomy and approval requirements;
- model and budget policy;
- active dates and status;
- escalation owner.

Recommended initial team:

- GreenAgent coordinator: deterministic DBTL policy and gate validation.
- Breeding planner: objective, cross, population, and advancement planning.
- Trial designer: experimental design and environment allocation.
- Quantitative genetics agent: statistical analysis and selection evidence.
- Data steward: ingestion, schema validation, lineage, and QC.
- Literature agent: project-scoped evidence search with citations.
- Report agent: versioned summaries from validated records.

No agent may approve a human-gated DBTL transition, promote knowledge
automatically, or expand its own project scope.

## DBTL operating model

The application database is the final authority for project, DBTL, approval,
artifact, and knowledge state. GreenAgent remains a deterministic policy
evaluator during development, but `.greenagent` files are temporary,
replaceable projections and will be retired.

For a proposed transition, the product must:

1. Identify the exact project and cycle.
2. Resolve versioned required artifacts.
3. evaluate GreenAgent policy against those artifacts;
4. present the proposal, validation outcome, evidence, and consequences;
5. collect an authorized human decision where required;
6. persist the immutable evaluation and decision;
7. emit an activity event;
8. permit downstream work only after the accepted state is durable.

### Strict reconciliation contract

Every DBTL cycle has an authoritative monotonically increasing `db_revision`.
Any temporary `.greenagent` projection records the same revision and a
canonical projection hash.

The transition contract is:

1. Create a database transition intent with an idempotency key and the expected
   prior `db_revision`.
2. Materialize or refresh the temporary GreenAgent projection from that
   database revision.
3. Run GreenAgent validation against the exact projected revision and persist
   the complete gate input, output, tool version, and artifact hashes in the
   database.
4. Commit the accepted transition, review decision, artifact references,
   activity event, and next `db_revision` atomically in PostgreSQL.
5. Refresh `.greenagent` as a best-effort post-commit projection.
6. Compare the file revision and canonical hash with the database. The
   database always wins; filesystem contents are never imported over a newer
   database revision automatically.

Cycle synchronization state is explicit:

- `in_sync`
- `projection_pending`
- `reconciliation_blocked`
- `projection_retired`

If projection or database writes fail, or revisions/hashes disagree, the UI
shows a persistent **Reconciliation blocked** state with the database revision,
projection revision, last successful sync, failed operation, and a safe
retry/rebuild action. Further transitions and downstream stage execution are
disabled until the database-led projection is repaired. Read-only project
history remains available.

The GreenAgent integration sits behind a `DBTLPolicyEvaluator` and
`DBTLProjectionAdapter` boundary. Application business logic must not read
`.greenagent` directly. Retiring GreenAgent files means replacing the adapter,
not migrating the application source of truth again.

The UI replaces generic chat interrupts with a review surface containing:

- from-state and proposed to-state;
- proposer and accountable human;
- required and missing artifacts;
- artifact versions and diffs;
- GreenAgent validation errors;
- evidence citations and data-use constraints;
- Approve, Request changes, and Reject actions;
- required authorization reference.

The reconciler may rebuild `.greenagent` only from an authoritative database
snapshot. It may never "repair" PostgreSQL from file state without a separate,
explicit, human-reviewed import operation.

## Database and knowledge boundaries

### Recommended operational boundary

PostgreSQL is required for production because project membership, concurrent
human reviews, strict RBAC, transactional DBTL transitions, JSONB extensions,
durable activity, and structured breeding queries require production-grade
concurrency and transaction guarantees.

SQLite is supported only for single-user local development, demos, and tests.
The application must fail configuration preflight if a multi-user or production
deployment selects SQLite. Features that rely on PostgreSQL-specific behavior
must have local adapters or explicit local limitations rather than pretending
to provide production parity.

### Retrieval boundary

Start with permission-filtered metadata and full-text retrieval. Embeddings
remain behind a retrieval interface and should not become a prerequisite for
provenance, authorization, or basic search.

Every generated answer from project knowledge must return:

- source identifier and version;
- project scope;
- relevant excerpt or structured record reference;
- retrieval timestamp;
- producing run;
- explicit "insufficient evidence" behavior.

### Memory boundary

Maintain three distinct scopes:

1. Personal memory: user preferences and private continuity.
2. Project working memory: revisable operational context shared with authorized
   project members and agents.
3. Validated knowledge: reviewed, sourced, versioned findings suitable for
   reuse.

Learn-stage output becomes a knowledge candidate. Human review is required
before it becomes validated project knowledge.

The database records Working Memory and Validated Project Knowledge as
different lifecycle states. No background job, agent, or scheduled Learn-stage
run may promote a record by changing a label alone; promotion uses a dedicated
authorization command and immutable review event.

## Consolidated implementation seams for Builder consideration

These are architectural seams discovered across both Designer plans, not a
phased action plan:

- Preserve the existing chat page and streaming hooks internally. Mount the
  same capability as project working sessions and as the global Inbox rather
  than rewriting message/run internals.
- Reuse project-folder candidate selection when creating a project. The
  per-thread `project_link.json` mechanism becomes a compatibility projection
  of project storage, not the project identity.
- Reuse `FileTreePanel` as a full project Files view rooted at the project
  storage boundary.
- Filter the existing Console API by project and workspace conversation scope
  to seed the Activity view.
- Emit DBTL stage lifecycle through the established `task_*` event contract so
  project and chat views observe the same run.
- Extend tracing callbacks and metadata to DBTL runs so project, cycle, stage,
  agent, and reconciliation identifiers appear in Langfuse.
- Reuse structured Human Input Cards for conversational review prompts, while
  the durable Review Inbox remains the authoritative approval surface.
- Reuse scheduled-task and channel delivery paths for pending gates and
  handoffs, but require explicit project or Inbox routing.
- Keep upstream-sensitive shell changes narrow and isolate new project routes
  and project-domain modules.
- Treat maize personas, skills, Slurm integrations, and evidence sources as a
  reference domain pack layered over the generic project and breeding core.

## Migration constraints for the Builder

These are invariants, not an implementation sequence:

- Existing threads and runs must remain readable throughout migration.
- Project associations begin nullable and become stricter only after verified
  backfill.
- Unassigned and exploratory conversations remain supported in the global
  Inbox; no forced project attachment.
- Migrated legacy conversations retain private-owner visibility unless the
  owner explicitly shares or moves them.
- A deleted conversation must never delete the durable project.
- Existing project-link markers must be migrated idempotently.
- Legacy user-global memory must not silently become shared project knowledge.
- Learn-stage output must enter provisional Project Working Memory and pass an
  authorized promotion review before entering Validated Project Knowledge.
- Scheduled tasks must retain their current run lifecycle and overlap
  guarantees while gaining project context.
- Agent definitions remain reusable library entries; project assignments add
  scope rather than duplicating agent configuration.
- Existing event sequence ordering and streaming behavior must not be rewritten
  merely to support project views.
- User and project authorization must be enforced in repositories and APIs,
  not only hidden in the frontend.
- No historical scientific artifact is overwritten during normalization;
  supersession must be explicit.
- PostgreSQL is the production source of truth; `.greenagent` may be rebuilt
  only from the database and never overrides a newer database revision.
- A DB/file revision mismatch blocks further DBTL transitions and appears
  explicitly in the UI.
- Crop-neutral relational entities use versioned maize JSONB or extension
  records until the reference workflow validates stable fields.

The GreenAgent Builder owns the eventual phased action plan after human design
approval.

## Tester requirements

1. Compare current chat-first and proposed project-first prototypes on correct
   time-to-orientation for DBTL state, owner, blocker, evidence, and next
   decision.
2. Verify that every project route and API object rejects unauthorized
   cross-project access, including knowledge retrieval and artifact previews.
3. Verify that a human-gated DBTL transition cannot be completed by an agent,
   scheduler, internal service token, or replayed approval.
4. Verify idempotency and reconciliation when GreenAgent validation succeeds
   but database persistence fails, and the inverse.
5. Verify that PostgreSQL remains authoritative across file corruption,
   missing projection files, stale file revisions, failed post-commit
   projection writes, and reconciler restarts.
6. Verify that every knowledge answer has source/version provenance and that
   insufficient evidence produces no unsupported claim.
7. Verify that Learn output remains in Working Memory until a distinct,
   authorized human promotion event creates Validated Project Knowledge.
8. Verify that deleting a thread cannot delete or orphan a project, cycle,
   trial, decision, or validated knowledge record.
9. Verify that existing thread history, event order, active-run constraints,
   scheduled-task overlap policy, and streaming behavior remain intact.
10. Verify global Inbox collaboration, private Scratchpad behavior, safe
    legacy-thread migration, explicit project moves, and visibility changes.
11. Verify screen-reader, keyboard, mobile, reduced-motion, dense-table, long
   name, empty-state, loading, error, and offline behavior.
12. Verify that agent assignments enforce tool, data, budget, write, and
   approval boundaries.
13. Verify that project activity attributes every mutation to a human, agent,
    or system actor and its run or authorization reference.
14. Verify the generic breeding schema using the approved maize reference
    workflow and reject invalid or wrong-version maize extension documents.

## Product acceptance outcomes

- A returning breeder can correctly identify objective, DBTL stage, owner,
  blocker, evidence, and next decision without searching chat history.
- Every project-visible object is protected by project membership.
- Every scientific claim links to a source, structured record, or versioned
  artifact.
- Every DBTL transition has an immutable GreenAgent evaluation and actor
  attribution.
- Human and agent actions appear in one project activity history.
- Long-term project identity survives process restarts, thread deletion, and
  agent replacement.
- Knowledge never crosses project boundaries without explicit policy.
- Legacy conversations remain accessible until their migration is verified.
- Humans and agents can collaborate in a projectless global Inbox without
  weakening the privacy of migrated legacy threads.
- Production refuses SQLite and uses PostgreSQL as the final authority.
- File/database divergence visibly blocks DBTL progression and is repaired from
  PostgreSQL.
- Learn output cannot pollute Validated Project Knowledge without a human
  promotion decision.

## Risks and unresolved evidence gaps

- No observed breeder usability study establishes the best vocabulary or
  navigation order.
- No approved plant-breeding data standard has been selected.
- The expected scale of observations, documents, projects, and concurrent
  users is unknown.
- The operational limits and service-level objectives for PostgreSQL,
  reconciliation, and projection rebuilds remain to be quantified.
- The future replacement for the temporary GreenAgent policy/projection adapter
  remains to be selected.
- The correct boundary between project working memory and validated knowledge
  needs human governance.
- Trial, pedigree, and selection semantics may vary materially by crop and
  organization.
- The exact maize reference workflow, JSONB profiles, validation schemas, and
  promotion criteria remain to be approved.
- Existing channels and external integrations may require explicit project
  or Inbox routing before inbound work is safe.

## Human decisions required

1. Is a workspace an organization/team security boundary, and which users and
   agents may participate in the global Inbox?
2. Which maize workflow, breeding records, and JSONB extension profiles are
   mandatory in the first approved domain model?
3. Should legacy owner-private conversations remain in a private Inbox section
   by default, and which explicit action makes them workspace-visible?
4. Which AI roles may write structured breeding records, and which may only
   propose changes?
5. Which DBTL transitions beyond the existing three require human approval?
6. What evidence grade and reviewer role are required to promote Learn output
   into validated project knowledge?
7. What reconciliation retry limit, alerting policy, and recovery role are
   required before a cycle becomes administratively blocked?

No Builder handoff should occur until these direction and methodology decisions
are reviewed or explicitly deferred by the human.
