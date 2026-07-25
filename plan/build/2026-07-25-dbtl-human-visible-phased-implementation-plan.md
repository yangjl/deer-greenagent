# Builder Plan: Human-Visible DBTL Implementation

Status: proposed for human review  
Role: Builder  
Date: 2026-07-25  
Design authority:
`plan/design/2026-07-25-dbtl-workflow-final-plan.md`  
Visual companion:
`plan/presentations/dbtl-human-visible-roadmap/index.html`

## Purpose

This plan converts the approved project-centric DBTL design into a
dependency-ordered implementation guideline. It does not authorize product
implementation, database migration, memory movement, or runtime configuration
changes.

Every phase must produce something a human can see, operate, and evaluate. A
backend-only phase is not complete. Each phase therefore includes:

1. a visible frontend state;
2. a bounded user interaction;
3. a seeded demonstration path;
4. automated tests at the relevant boundaries;
5. a human review checklist and explicit go/no-go decision.

The sequencing rules remain:

> Trust PostgreSQL and the review gates before adding autonomous routing or
> stage execution.

> AI may recommend DBTL, but only a human can turn ordinary work into a durable
> cycle.

> Reconcile scientific inputs before Build, and reject invalid evidence even
> when the headline accuracy is high.

## Product principles carried into implementation

- The project, not the conversation, owns durable research state.
- Ordinary project chat remains the default and keeps its current capabilities.
- A classifier can propose a DBTL cycle but cannot create one.
- PostgreSQL is the authority for cycles, stages, reviews, evidence, and
  validated knowledge.
- DeerMem supplies scoped working context; it is not the authority for
  validated scientific claims.
- Design, Build, Test, and Learn are LangGraph stages, not fixed agent
  identities.
- Data Readiness and Reconciliation is a required bridge between Design and
  Build.
- Test outcomes are non-binary:
  `supported`, `not_supported`, `inconclusive`, and `invalidated`.
- Knowledge promotion and publication are separate, human-reviewed actions.
- One active top-level cycle per project is the initial product rule.
- A season/program cycle may contain child computational cycles.
- Human reviewers approve all gates initially. Experienced-agent approval may
  be considered only in a later, separately reviewed policy change.
- Initial publication widens knowledge only to explicitly selected projects.

## What “human-visible and testable” means

A phase can be marked complete only when the review build exposes:

- **State:** what the system believes and which authority supplied it;
- **Action:** what the user can do next and who is allowed to do it;
- **Reason:** why a transition is allowed, blocked, or invalidated;
- **Evidence:** the artifact, test, migration row, or review event supporting
  the state;
- **Recovery:** how a user returns to a valid state without hidden data edits.

Every state-changing control must show an immediate pending state, a durable
success or failure result, and a link to the resulting activity event.

## Shared frontend model

The implementation should extend the current three-rail project workspace
instead of introducing a separate research application:

```text
┌────────────────┬─────────────────────┬─────────────────────────────────────┐
│ Project rail   │ DBTL project rail   │ Main work area                      │
│ projects/files │ cycles/stages/chat  │ chat, stage detail, or review panel │
└────────────────┴─────────────────────┴─────────────────────────────────────┘
```

The first rail remains the single home for project files. The second rail
evolves from its browser-local cycle projection to durable server state. The
main work area continues to show chat by default and opens DBTL details in the
existing right-side inspection pattern when focused review is needed.

### Shared interaction rules

- A cycle or stage opens without replacing the current conversation context.
- A back/close action returns to the exact prior chat and rail state.
- Status is never conveyed by color alone.
- Gate controls state the required role, affected revision, and consequence.
- Destructive or scope-widening actions require a confirmation dialog.
- Streaming agent activity uses existing task events and artifact inspection.
- Narrow layouts use one rail plus a full-width detail sheet; no horizontal
  three-column compression.
- All phase-specific surfaces support keyboard navigation, visible focus, and
  reduced motion.

## Delivery order

```text
P0 Freeze + readiness contract
  └─ P1 PostgreSQL + trusted gates
      └─ P2 DeerMem scope migration
          └─ P3 Durable manual DBTL
              └─ P4 Classifier shadow + proposal UX
                  └─ P5 Thin Supervisor Graph
                      └─ P6 Design + Reconciliation
                          └─ P7 Build + Test validity
                              └─ P8 Learn + governed knowledge
```

Later phase code may be prototyped behind non-mergeable local experiments, but
no phase may be product-enabled before all preceding exit gates pass.

## Phase 0 — Freeze unsafe DBTL and expose readiness

### Outcome

Current experimental DBTL execution cannot silently create or advance durable
research state. The team can inspect existing state and agree on whether
invalid cycle/handoff files will be repaired or superseded.

### Backend and contract scope

- Add a feature/readiness contract that reports DBTL as
  `disabled`, `audit_only`, `manual`, or `graph_enabled`.
- Identify current experimental orchestrator entry points and fail closed when
  the mode is `disabled` or `audit_only`.
- Inventory existing cycle, handoff, projection, and policy-version state
  without mutating it.
- Define the new policy vocabulary for optional knowledge candidates and the
  four non-binary Test outcomes.

No migration, repair, graph, classifier, or new scientific write is permitted.

### Visible frontend

Add a read-only **DBTL readiness** section to the existing Settings dialog.
Project rail cycle controls remain visible but disabled when the system is
frozen, with a short explanation and a “View readiness” link.

```text
DBTL readiness                                      Audit only
──────────────────────────────────────────────────────────────
Experimental orchestrator       Frozen
Database authority              Not verified
Gate policy                     Legacy / unsafe
Existing records                3 need a disposition

[Inspect existing records]                    [Export report]
```

The inspection drawer groups records as:

- valid and compatible;
- repairable;
- invalid or ambiguous;
- safe to supersede.

It must not provide a repair button in this phase.

### Demo path

Seed one compatible cycle file, one malformed human decision, and one orphaned
handoff. The reviewer opens Settings, sees the three classifications, exports
the report, and confirms that “Start cycle” is disabled.

### Automated tests

- Backend: all experimental DBTL write paths reject writes outside explicitly
  allowed test mode.
- Backend: readiness classification is deterministic and read-only.
- Frontend: status, disabled controls, reason text, and report link render from
  the feature response.
- E2E: ordinary chat, files, artifacts, and exports still work while DBTL is
  frozen.
- Accessibility: disabled controls are discoverable and explain why.

### Human exit review

- Confirm the inventory is understandable and complete.
- Choose repair or supersession for each invalid record class.
- Approve the policy vocabulary used by later phases.

No-go if any legacy path can still create or advance a DBTL cycle.

## Phase 1 — PostgreSQL authority and trusted gate governance

### Outcome

PostgreSQL reliably owns DBTL state, and human reviews are typed, identity
bound, revision bound, single use, and auditable before any graph relies on
them.

### Backend and contract scope

- Add PostgreSQL DBTL core tables and Alembic revisions.
- Preserve create-all/Alembic schema parity.
- Add project-scoped repository and API authorization.
- Replace permissive truthy decision parsing with a strict review schema.
- Capture authenticated reviewer identity server-side.
- Bind reviews to cycle, stage attempt, artifact revision, policy version, and
  idempotency key.
- Reject replay, stale revision, wrong project, internal principal, scheduled
  principal, and malformed payloads.
- Add `db_revision`, projection hash, and mismatch blocking.
- Implement migration preflight, validation, cutover report, and rollback
  posture.

Classifier, supervisor, stage workers, and scientific execution remain
excluded.

### Visible frontend

Extend **DBTL readiness** into an operator-facing checklist. It is inspection
and verification, not a raw database console.

```text
Foundation readiness                          7 / 9 checks pass
──────────────────────────────────────────────────────────────
✓ PostgreSQL reachable        revision 001x
✓ Core schema parity          create-all = upgrade
✓ Cross-project isolation     42 tests
✓ Review identity binding     verified
✕ Projection consistency      2 mismatches
○ Cutover approval            waiting for human

[View mismatches] [Run validation] [Download evidence bundle]
```

Selecting a failed check opens its evidence, affected projects, safe recovery
instruction, and last validation timestamp. “Approve cutover” is shown only
after every technical check passes and explains:

> Cutover means PostgreSQL becomes the authority used by DBTL. Existing data
> stays recoverable, but graph work remains blocked until this decision is
> approved.

The control is initially hidden behind an operator capability.

### Demo path

Run a staged migration with one injected projection mismatch and one replayed
review. The UI must show both failures, prevent cutover, then show a clean
validation after approved repair. No production data is required.

### Automated tests

- Backend unit tests for every malformed/unauthorized review form.
- PostgreSQL integration tests for concurrency, idempotency, rollback, and
  failure injection.
- Migration tests preserving IDs, ownership, timestamps, and event ordering.
- Repository and API tests for cross-project isolation.
- Frontend tests for readiness state mapping and stale validation warnings.
- E2E operator path proving cutover cannot be approved with a failed check.

### Human exit review

Review the downloadable evidence bundle and explicitly approve or defer the
PostgreSQL cutover. The Supervisor Graph cannot begin while cutover is
deferred.

No-go if a bare string, replay, wrong actor, or stale artifact can satisfy a
gate.

## Phase 2 — DeerMem scope bridge and legacy-fact migration

### Outcome

Memory retrieval is project-specific, old facts are classified safely, and no
private fact becomes shared project memory without human approval.

### Backend and contract scope

- Introduce canonical scope objects for personal, project, agent, and
  publication contexts.
- Keep a compatibility adapter to the current `(user_id, agent_name)` DeerMem
  storage boundary.
- Inventory `~/.deer-flow/users/*/agents/` facts into a checksum-preserving
  manifest.
- Recompute recognized legacy project bucket mappings from authoritative
  project/user pairs.
- Quarantine unknown or ambiguous buckets.
- Relabel recognized legacy facts as private project facts.
- Support user-approved copy/promotion into shared project working memory.
- Make migration idempotent, restartable, and reversible.

Automatic stage writes, validated claims, and publication remain excluded.

### Visible frontend

Add **Memory scope migration** under each project’s settings. The landing view
shows counts, not raw personal facts, until the current user opens their own
review queue.

```text
Memory migration · G2F
──────────────────────────────────────────────────────────────
Private legacy facts       24
Suggested for project       7
Already project-scoped      3
Needs classification        2

[Review my 7 suggestions]              [Download manifest]
```

The review drawer shows one fact at a time with its source bucket, checksum,
suggested scope, and reason. Actions are:

- Keep private;
- Share with this project;
- Edit then share;
- Quarantine for later.

Bulk “share all” is deliberately unavailable. A second authorized project
member sees only facts that have been explicitly shared.

### Demo path

Use two users, two projects, one recognized legacy bucket, one unknown bucket,
and one custom-agent fact. Demonstrate private retention, explicit sharing,
cross-user retrieval, project isolation, custom-agent preservation, and
rollback.

### Automated tests

- Canonical Markdown and checksum preservation tests.
- Mapping and quarantine tests for known/unknown legacy buckets.
- Migration idempotency and interrupted-resume tests.
- Personal/project/agent isolation tests in retrieval and write paths.
- Frontend tests for per-fact actions and privacy-safe counts.
- E2E two-user scenario proving only approved facts are shared.

### Human exit review

Two authorized users must retrieve the same approved project fact while all
unapproved facts remain private. The reviewer must also observe a successful
rollback and rerun.

No-go if project selection can expose a previous project’s memory snapshot.

## Phase 3 — Durable cycles and manual workflow

### Outcome

Humans can create and advance a durable DBTL cycle through the same contracts
the later graph will call. Restart, replay, or concurrent action cannot lose or
duplicate state.

### Backend and contract scope

- Add durable cycles, stage attempts, work items, artifacts, reviews, and
  activity events.
- Enforce one active top-level cycle per project initially.
- Support child computational cycles under a season/program parent.
- Replace browser-local cycle state with database-backed queries and
  mutations.
- Provide manual stage entry and gate review endpoints.
- Require Design approval and Reconciliation approval before Build readiness.
- Keep all scientific work manual or fixture-driven in this phase.

### Visible frontend

The second rail becomes durable:

```text
CYCLES
● Cycle 01 · Design
  ├─ Design                 Awaiting review
  ├─ Data reconciliation   Locked
  ├─ Build                 Locked
  ├─ Test                  Locked
  └─ Learn                 Locked
```

“Start a cycle” opens a focused dialog with:

- research question;
- cycle class (`season/program`, `computational`, or `other`);
- optional parent cycle;
- objective and success criteria;
- explicit statement that this creates a durable research record.

Selecting a stage opens a main-area stage detail with status, revision,
artifacts, work items, blockers, activity, and a review panel. The reviewer can
approve, request changes, or reject with a required rationale.

### Demo path

Create a manual Design cycle, attach a fixture design artifact, approve it,
enter Data Reconciliation, record a blocker, resolve it with a new revision,
approve `ready_for_build`, restart the stack, and verify the exact state.

### Automated tests

- Backend state-machine tests for allowed and forbidden transitions.
- PostgreSQL concurrency and duplicate-review tests.
- Parent/child cycle and one-active-top-level-cycle tests.
- Frontend query/mutation tests replacing localStorage behavior.
- E2E manual Design → Reconciliation → `ready_for_build` path.
- Restart test showing all state and activity survive.

### Human exit review

The reviewer completes the demo from the UI without database access and
confirms that every action appears in activity with actor and revision.

No-go if localStorage can override durable cycle state.

## Phase 4 — Classifier shadow mode and DBTL Upgrade Proposal

### Outcome

The system can identify likely research-cycle requests without changing
behavior or creating records, and users can preview, correct, or dismiss a
proposed upgrade.

### Backend and contract scope

- Implement deterministic-first routing with structured classifier output.
- Evaluate explicit user choice, selected cycle, and current project before
  any model classification.
- Run classifier shadow evaluations against ordinary project requests.
- Store evaluation telemetry separately from DBTL domain records.
- Define clarification fields and the no-record proposal contract.
- Require explicit user confirmation before cycle creation.

### Visible frontend

When the system thinks a DBTL cycle may help, chat stays intact and displays an
inline **DBTL Upgrade Proposal**:

```text
This looks like multi-step research work.
No cycle has been created yet.

Proposed objective
Compare drought-response models across G2F environments

Missing before start
• target trait  • season range  • validation expectation

[Start DBTL setup] [Keep as ordinary chat] [Not sure]
```

“Start DBTL setup” opens clarification in the same card. The final confirmation
lists the project, cycle class, objective, required human gates, and record
creation effect. “Keep as ordinary chat” immediately resumes the unchanged lead
agent. Dismissal creates no DBTL record.

An internal evaluation drawer, available only to authorized testers, shows
shadow decision, confidence band, rule hits, human choice, and whether the
proposal was false or missed.

### Demo path

Exercise:

- “Explain this README” → ordinary;
- “Create a small chart” → ordinary;
- “Design and validate a genomic-selection experiment” → proposal;
- explicit “start a DBTL cycle” → setup;
- ambiguous request → clarification;
- selected existing cycle → continuation proposal, not a new cycle.

### Automated tests

- Router precedence tests for every explicit-choice case.
- Golden-set classifier tests including ordinary file and artifact work.
- Frontend tests proving “No cycle has been created yet” is visible.
- E2E dismiss/ignore/ordinary paths asserting no DBTL domain rows exist.
- Accessibility and keyboard tests for card actions.
- Telemetry tests proving shadow events cannot mutate cycle state.

### Human exit review

Review false-upgrade and missed-cycle rates on an agreed request set. Approve
thresholds and wording before enabling proposals for general users.

No-go if classifier confidence can bypass confirmation.

## Phase 5 — Thin Project Supervisor Graph

### Outcome

A small LangGraph supervisor routes ordinary work, cycle setup, clarification,
and existing-cycle continuation while preserving the full current chat
experience.

### Backend and contract scope

- Add a thread-state-preserving supervisor with terminal branches for:
  ordinary lead-agent work, clarification, cycle proposal/confirmation, and
  existing-cycle continuation.
- Reuse current lead agent, middleware, run lifecycle, project mount, file
  tools, task events, cancellation, goals, memory injection, and artifacts.
- Keep stage execution behind a controlled manual/stub adapter.
- Prevent the stub from writing scientific results or satisfying gates.
- Preserve selected project and selected cycle as explicit runtime context.

### Visible frontend

The main chat format does not change. A small, accessible context chip above
the composer shows:

```text
[ Ordinary project work ▾ ]   or   [ Cycle 01 · Design ▾ ]
```

The menu lets the user keep the request ordinary, continue one visible cycle,
or ask the AI to recommend. Changing context affects the next request only and
is echoed in the run activity. No new agent transcript format is introduced.

Cycle-linked responses may show a compact “Recorded in Cycle 01” link, but
ordinary responses remain visually identical to current chat.

### Demo path

Within one project conversation:

1. inspect a file and create an artifact in ordinary mode;
2. receive and dismiss a DBTL proposal;
3. explicitly select Cycle 01;
4. ask for a Design clarification;
5. switch back to ordinary mode;
6. stop and regenerate a response.

All current chat functions must still behave normally.

### Automated tests

- Graph routing tests with complete thread state on every branch.
- Regression tests for uploads, files, artifacts, workspace changes,
  summarization, goals, cancellation, title generation, and memory scope.
- Stream-contract tests keeping subgraph frames out of the root state.
- Frontend tests for context selection and per-request scope.
- E2E mixed ordinary/cycle conversation with no silent records.

### Human exit review

The reviewer completes the mixed conversation and confirms ordinary chat is
indistinguishable from the pre-supervisor experience unless a cycle is
explicitly selected or proposed.

No-go if routing drops messages, project scope, file access, or artifact
inspection.

## Phase 6 — Dynamic Design and Data Reconciliation

### Outcome

The first useful graph-enabled research slice can design work, dynamically
select specialist agents, reconcile input data, and stop visibly at either
`ready_for_build` or a scientific blocker.

### Backend and contract scope

- Add a versioned `StageSpec` registry.
- Add constrained `AgentSelector` and existing `SubagentExecutor` fan-out.
- Require structured worker results, budgets, evidence references, and stop
  reasons.
- Add Design artifacts and human Design review.
- Add Data Readiness/Reconciliation work items and gate.
- Record source identity, units, coding, joins, exclusions, missingness,
  population structure, dataset hashes, and unresolved contradictions.
- Make raw data immutable.
- Invalidate approval when reconciled inputs or relevant policy changes.

### Visible frontend

The Design stage uses a structured research brief beside the existing artifact
viewer:

```text
DESIGN · revision 3                     Awaiting human review
Question        Does model X generalize across target environments?
Population      1,204 hybrids · 14 environments
Success         preregistered metric + uncertainty interval
Risks           structure leakage · unbalanced environments

Artifacts 3   Evidence 8   Agent runs 2
[Request changes] [Approve Design]
```

After approval, **Data Reconciliation** displays a matrix:

```text
Field                  Source A       Source B       Decision
Treatment coding       WW / WS        0 / 1          mapped ✓
Yield units            bu/ac          Mg/ha          converted ✓
Hybrid ID              19 missing     complete       BLOCKED
Population group       inferred       curated        review
```

Every row links to evidence and records whether a human or agent proposed the
resolution. The gate shows a blocker summary and cannot be approved while a
required row is unresolved. Agent runs appear through the existing task
timeline and their artifacts open in the current inspector.

### Demo path

Use a synthetic G2F-like fixture with contradictory treatment codes, mixed
yield units, missing hybrid IDs, and a changed dataset hash. Resolve two
issues, leave one blocker, observe Build remain locked, resolve the blocker,
approve reconciliation, and reach `ready_for_build`.

### Automated tests

- StageSpec schema/version and selector constraint tests.
- Worker budget, timeout, retry, and structured-result tests.
- Reconciliation validation and raw-data immutability tests.
- Dataset-hash invalidation tests.
- Frontend matrix, evidence-link, blocker, and review-state tests.
- E2E real reference project reaching `ready_for_build`.

### Human exit review

A plant-science practitioner must verify that the reconciliation matrix
expresses the actual scientific ambiguity and that Build cannot start early.

No-go if an agent can silently resolve a contradiction or mutate raw inputs.

## Phase 7 — Build execution and Test validity

### Outcome

Build produces traceable artifacts, and Test separates headline performance
from scientific validity. Strong metrics cannot override leakage, broken
folds, biological implausibility, or holdout failure.

### Backend and contract scope

- Add Build execution plans, lineage, environment capture, and artifact
  revisions.
- Add validity packs for fold composition, structure-null comparison,
  predictive ceiling, direction checks, leakage checks, and tester holdout.
- Record headline metrics separately from validity decisions.
- Support rework routing to Design, Reconciliation, or Build.
- Emit one of:
  `supported`, `not_supported`, `inconclusive`, or `invalidated`.
- Block knowledge-candidate creation for invalidated outcomes.

### Visible frontend

Build shows a reproducibility panel with inputs, code/config revision,
environment, agent runs, outputs, and rerun controls.

Test uses a two-column evidence view:

```text
Headline result                    Validity assessment
Accuracy       0.94                Structure-null      FAILED
RMSE           0.31                Fold composition    PASSED
Coverage       95%                 Leakage check       FAILED
                                     Holdout            PASSED

Scientific outcome: INVALIDATED
High accuracy is not accepted because structure and leakage checks failed.
[View evidence] [Route back to Design] [Route back to Build]
```

An inconclusive result uses neutral language and explains what evidence is
missing. A valid negative result can proceed to Learn. Outcome controls require
a human reviewer and show the exact validity-pack version.

### Demo path

Run three synthetic fixtures:

1. pooled accuracy `0.94` with leakage and poor within-population performance
   → `invalidated`;
2. adequate execution but insufficient independent holdout
   → `inconclusive`;
3. well-powered negative result with valid protocol
   → `not_supported`, eligible for Learn.

### Automated tests

- Build lineage and reproducibility capture tests.
- Fold count/composition and structure-null gate tests.
- Leakage, ceiling, direction, and holdout tests.
- Outcome transition and rework routing tests.
- Frontend tests proving metric and validity status cannot be conflated.
- E2E high-accuracy rejection, inconclusive close, and valid-negative paths.

### Human exit review

A plant-science practitioner and Tester jointly verify all three fixtures from
the UI and confirm the result language cannot overstate the evidence.

No-go if headline accuracy can render a success state while a validity gate
fails.

## Phase 8 — Learn, knowledge promotion, and selected-project publication

### Outcome

Learn synthesizes what happened without automatically creating truth. Humans
can promote supported or valid-negative findings into project knowledge and
separately publish selected claims to selected projects. Retraction updates
current retrieval without erasing history.

### Backend and contract scope

- Add Learn synthesis and optional memory candidates.
- Add validated SQL knowledge claims with evidence, limitations, grade,
  reviewer, scope, and immutable events.
- Render human-visible Markdown views and write DeerMem retrieval pointers.
- Separate candidate creation, project promotion, and publication.
- Add supersession and retraction propagation.
- Exclude invalidated findings from scientific claims.
- Allow inconclusive cycles to close with no candidate.

### Visible frontend

Learn displays a closeout summary and zero or more candidate cards:

```text
LEARN · Cycle 01
Outcome: NOT SUPPORTED

Candidate finding
Model X did not improve target-environment prediction under protocol v3.
Evidence: 4 artifacts · 2 validity reviews
Limitations: 2024 environments only

[Keep as working memory] [Propose project knowledge] [Discard candidate]
```

Promotion opens a review sheet with evidence, limitations, claim grade, and
retrieval preview. After project promotion, a separate “Publish…” action opens
a project picker; no workspace-wide default is offered.

Knowledge detail clearly distinguishes:

- provisional working memory;
- validated project knowledge;
- published copies/pointers;
- superseded or retracted history.

Retraction requires rationale and previews every selected-project scope from
which current retrieval will be removed.

### Demo path

Demonstrate:

- an inconclusive cycle closing with no candidate;
- a valid negative result promoted to project knowledge;
- publication to two selected projects;
- a second user retrieving the published claim in one selected project but not
  an unselected project;
- retraction removing it from current retrieval while audit history remains.

### Automated tests

- Candidate/claim/publication state-machine tests.
- Evidence, role, scope, and immutable-event tests.
- DeerMem pointer freshness and SQL-authority tests.
- Supersession/retraction propagation tests.
- Frontend tests for scope labels, promotion review, and publication picker.
- E2E inconclusive, valid-negative, selected-publication, and retraction paths.

### Human exit review

The reviewer completes all demo paths and verifies that no wording or control
implies an AI-generated candidate is already validated knowledge.

No-go if publication is implicit, workspace-wide by default, or inseparable
from project promotion.

## Cross-phase test strategy

### Test pyramid

| Layer | Required evidence |
|---|---|
| Pure contracts | typed schemas, validators, state transitions, scope mapping |
| Backend unit | authorization, policy, routing, migration classification |
| PostgreSQL integration | concurrency, idempotency, failure injection, isolation |
| Graph tests | branch state, interrupts, resume, replay, subgraph events |
| Frontend unit | state rendering, actions, errors, accessibility |
| Mocked E2E | real navigation and interactions with deterministic APIs |
| Review environment | seeded end-to-end walkthrough using synthetic data |

### Human review evidence bundle

Every phase produces:

- phase/version and commit identifier;
- screenshots of the primary state, blocked state, and recovery state;
- seeded fixture manifest;
- automated test summary;
- accessibility check summary;
- migration or activity report where applicable;
- known limitations and rollback instruction;
- named reviewer decision with timestamp.

The visual companion deck is the initial interaction baseline. Product
screenshots replace its mockups in each phase’s evidence bundle.

### Required regression suite from Phase 5 onward

- ordinary project chat;
- project memory isolation;
- project files and uploads;
- run-scoped workspace-change artifacts and diffs;
- goals, cancellation, regeneration, and title behavior;
- scheduling/non-interactive safety;
- history, summarization, and checkpoint compatibility;
- mobile rail/detail behavior;
- reduced motion and keyboard-only operation.

## Release flags and rollout

Use monotonic enablement:

```text
dbtl.mode = disabled
          → audit_only
          → manual
          → proposal
          → supervisor
          → staged_execution
          → knowledge
```

- A mode may be raised only after its phase exit review.
- Lowering the mode disables new mutations but keeps accepted state readable.
- Data created in a later mode is never deleted during rollback.
- Readiness surfaces always reveal the effective mode and blocking reason.
- Production defaults remain at the last human-approved mode.

## Traceability matrix

| Design requirement | First visible phase | Fully exercised |
|---|---:|---:|
| Freeze unsafe orchestrator | P0 | P1 |
| PostgreSQL authority | P1 | P3 |
| Typed identity-bound gates | P1 | P3 |
| Legacy DeerMem scope migration | P2 | P8 |
| Durable manual cycles | P3 | P3 |
| No silent DBTL upgrade | P4 | P5 |
| Ordinary chat compatibility | P4 | P5–P8 |
| LangGraph supervisor | P5 | P8 |
| Dynamic specialist agents | P6 | P7 |
| Data reconciliation before Build | P6 | P7 |
| Non-binary scientific outcomes | P7 | P8 |
| Governed knowledge promotion | P8 | P8 |
| Selected-project publication | P8 | P8 |

## First reviewable graph slice

The first graph-enabled slice starts only after P1–P4 pass:

```text
project request
  → deterministic-first classification
  → no-record DBTL Upgrade Proposal
  → human confirms
  → durable Design cycle
  → one Design work item and inspectable artifact
  → human Design review
  → Data Readiness/Reconciliation
  → ready_for_build OR visible reconciliation blocker
```

This is the minimum slice because it proves the user-control, database,
review, agent-orchestration, artifact, and scientific-validity contracts
without prematurely automating Build or claiming knowledge.

## Human Input Needed

The following choices should be made at their named phase review, not guessed
in advance:

- **P0:** repair or supersede each invalid legacy DBTL record class.
- **P1:** approve or defer PostgreSQL cutover after the readiness evidence
  passes. Deferral is allowed, but P3–P8 cannot proceed.
- **P4:** approve classifier thresholds and proposal language.
- **P6:** select the first maize/genomic-selection `StageSpec` and define the
  reconciliation-blocked retry/alert policy.
- **P7:** approve claim evidence grades and the first validity-pack thresholds.
- **P8:** approve which human project roles may promote, publish, retract, or
  supersede claims.
- **Future policy review:** decide whether experienced agents may approve any
  narrow gate. The initial implementation remains human-only.

## Builder handoff rule

Before implementation of any phase begins, the human must cross-check that
phase’s:

- scope and exclusions;
- frontend interaction;
- demo fixture;
- automated test matrix;
- rollback behavior;
- exit decision.

Approval of this overall plan is not blanket approval for all phases. Each
phase receives a separate implementation authorization after its visual and
test contract is accepted.
