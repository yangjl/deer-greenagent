# Conversational DBTL discovery before cycle creation

**Status:** Proposed standalone implementation plan. No implementation is
authorized by this document.

**Date:** 2026-08-01

**Decision:** When a project request is likely to benefit from DBTL, DeerFlow
should let the Lead Agent continue the conversation, gather relevant context,
and prepare a reviewable cycle proposal before showing the DBTL start card.
Nothing in discovery creates a cycle, opens a stage, or grants the Lead Agent
workflow authority.

## 1. Outcome

A scientist can begin with an underspecified request such as:

> Simulate a maize population with genotype and phenotype data.

DeerFlow recognizes that the work may belong in a Design → Build → Test → Learn
cycle, but it does not immediately interrupt with a setup form. The Lead Agent
responds conversationally, uses the current project and relevant memory, and
asks the few questions that materially change the research design.

Once the request is sufficiently understood—or the scientist asks to proceed—
DeerFlow appends one deterministic start card to the conversation:

> **Ready to start a DBTL cycle**
>
> Objective: Simulate a maize breeding population and evaluate whether the
> generated genotype/phenotype structure supports the intended analysis.
>
> Known: target population, available source files, intended outputs.
>
> Still open: validation threshold and train/test separation.
>
> **Start DBTL cycle** · **Keep discussing** · **Continue as ordinary work**

The card shows what came from the scientist, what came from project evidence or
memory, and what remains an assumption. **Start DBTL cycle** is a server-bound
human confirmation against an exact discovery revision. Only then may the
Supervisor create the durable cycle and enter Design.

## 2. Why this is a separate feature

The current entry flow has the correct authority boundary but the wrong
conversational order for an early, underspecified request:

```text
request looks DBTL-shaped
    -> proposal / setup confirmation
    -> human agrees to start
    -> setup questions
    -> cycle creation and Design
```

That order protects the person from accidentally creating a record, but it asks
for a workflow commitment before DeerFlow has demonstrated that it understands
the work. It also leaves useful context scattered across the transcript,
project files, project memory, and user-global memory for the later setup call
to rediscover.

The target order is:

```text
request looks DBTL-shaped
    -> conversational discovery with the Lead Agent
    -> bounded context gathering and structured draft
    -> deterministic, reviewable start card
    -> explicit human confirmation
    -> durable cycle creation by the Supervisor boundary
    -> Design begins with the accepted discovery package
```

This is not a fifth `dbtl.mode`. The existing modes (`disabled`, `audit_only`,
`manual`, and `graph_enabled`) govern operational authority and cutover.
Conversational discovery is an interaction and routing policy inside an
eligible mode and should have its own rollout switch.

## 3. Product principles

### 3.1 Conversation first, card when useful

The first classifier signal changes how the Lead Agent frames the conversation;
it does not immediately render a card. The agent should ask at most one focused,
high-value question at a time unless the scientist asks for a checklist.

The user can request the card immediately, keep discussing indefinitely, or opt
out. Read/explain requests remain ordinary even when their subject is related to
DBTL.

### 3.2 Discovery is not a cycle

A discovery draft may be durably stored so it survives refresh and process
restart, but it is not a DBTL governance record:

- it has no cycle id, stage rows, gate status, or stage-owned output path;
- it does not appear in the project rail as a cycle;
- it cannot authorize Build/Test/Learn work;
- it may be abandoned or superseded without retiring a cycle; and
- every visible start card says that no cycle exists yet.

Telemetry and a pre-cycle draft are allowed records. They must never be
described as a started research cycle.

### 3.3 Awareness is not authority

The Lead Agent receives authoritative facts about the discovery state and may
read project evidence, explain DBTL, ask questions, and summarize the proposed
work. It may not:

- create or advance a cycle;
- start a governed stage;
- approve a gate;
- write stage-owned DBTL outputs; or
- report that a transition occurred unless the repository recorded it.

The Supervisor boundary owns cycle creation and later routing. The Lead Agent's
job is understanding and communication.

That boundary needs a run-time execution fence, not only prompt wording. During
discovery the project workspace is read-only to the Lead Agent, mutating file
tools, generic shell execution, side-effecting external connectors, and ordinary
subagent delegation cannot be used to do the proposed Build early.
Purpose-built read-only inspection/retrieval tools may remain available.
Choosing **Continue as ordinary work** starts a later ordinary run with its
normal grants; it does not retroactively widen the discovery run.

### 3.4 Context is scoped and provenance-labelled

“Use memory” must not mean concatenating every remembered statement into the
prompt. Retrieval is query-specific, bounded, access-controlled, and labelled
by source. Current project facts and the scientist's current words are not
interchangeable with preferences inferred months ago.

### 3.5 The chatbox remains the primary input

Discovery questions and corrections happen in chat. The start card is a compact
review and action surface, not a second multi-page setup form. “Keep discussing”
returns focus to the composer; the person corrects the draft in ordinary
language.

## 4. User experience

### 4.1 Entry routes

Discovery can begin from either route:

1. **Explicit:** the person selects **Start a new cycle** or types “start a DBTL
   cycle.” This enters discovery instead of immediately asking for cycle
   confirmation. A **Show the start card now** action remains available.
2. **Suggested:** the existing classifier identifies a likely DBTL request. If
   proposal visibility is enabled, the next run uses discovery framing instead
   of showing the current upgrade card immediately.

An explicit **Ordinary project work** choice still wins and does not enter
discovery. A request already bound to an existing cycle continues that cycle and
never opens a second discovery session implicitly.

### 4.2 Discovery conversation

The first Lead Agent response should briefly set expectations without sounding
like a modal dialog:

> This looks like work that may benefit from a DBTL cycle. Before starting one,
> I want to pin down the population design and what would count as a successful
> simulation. Are you modeling a breeding population from existing project
> data, or should the population be synthetic from specified assumptions?

On later turns the agent should:

- acknowledge and preserve decisions already made;
- inspect relevant project files when that can answer a question;
- distinguish facts from recommendations;
- avoid asking for information already available in accepted context;
- explain why a missing decision matters; and
- continue useful read-only analysis without pretending the governed work has
  started.

A quiet composer/status treatment may say **Exploring a DBTL cycle**. It must
not look like an active cycle stage and must offer **Show proposal** and
**Continue as ordinary work**.

### 4.3 When the start card appears

The card appears when any of the following is true:

- the deterministic minimum-readiness checks pass and the Lead Agent has just
  completed a useful response;
- the person asks to see, review, or start the cycle proposal; or
- a configured turn ceiling is reached, in which case unresolved items are
  shown rather than silently guessed.

The card never appears in the middle of the Lead Agent's streamed answer. It is
appended after the answer as a deterministic, durable message event so live
rendering and thread reload see the same control.

The card contains:

- proposed title and objective;
- why DBTL is recommended;
- known inputs and intended outputs;
- success and rejection criteria, if known;
- unresolved decisions;
- a compact provenance view;
- the durable-record effect of starting; and
- **Start DBTL cycle**, **Keep discussing**, and **Continue as ordinary work**.

Nothing is preselected.

### 4.4 Card actions

- **Start DBTL cycle:** confirms the exact displayed discovery revision. The
  server revalidates project/thread membership, revision, status, and
  idempotency before creating one cycle.
- **Keep discussing:** keeps the discovery active and returns to the composer.
  Any new decision creates a new draft revision and supersedes the old card.
- **Continue as ordinary work:** closes the discovery candidate. The transcript
  remains, no cycle is created, and later classification requires new evidence
  before suggesting DBTL again.

A stale card is read-only and points to the current proposal. A double click or
retry replays the same start receipt and cannot create a second cycle.

## 5. Discovery lifecycle

Use one active discovery per originating project thread:

```text
inactive
  -> gathering
  -> ready
  -> offered
       |- Start -> confirmed -> cycle created
       |- Keep discussing / new information -> gathering
       `- Continue as ordinary work -> declined

gathering/ready/offered -> expired or superseded
```

Suggested statuses:

- `gathering`: likely DBTL; conversation is still collecting material facts;
- `ready`: minimum content exists, but a card has not yet been projected;
- `offered`: an exact revision is durably present as a start card in the
  canonical thread journal — not merely generated in graph output. A card that
  was produced but whose delivery failed stays `ready` and is redelivered by the
  delivery-contract outbox below, so `offered` means the control is durably
  available in the conversation—not that the person has necessarily viewed it;
- `confirmed`: that revision created a cycle;
- `declined`: the person chose ordinary work;
- `superseded`: a replacement discovery owns the thread; and
- `expired`: bounded cleanup closed an abandoned draft.

The server, not the browser, owns this state. A browser-local state would lose
the discovery on refresh and reintroduce the same one-shot scope failure seen in
post-approval handoffs. Discovery therefore adopts the *target* durable handoff
contract proposed in the stage-chat progress plan: a status row advanced only
by compare-and-set, an `offered` card recorded only after durable journal
insertion, and redelivery by outbox rather than loss on failure. That SQL
handoff/outbox is not implemented on the current branch; if discovery lands
first, it must introduce a small shared delivery primitive rather than claiming
to reuse a row that does not yet exist. A single-use start consumes the
discovery exactly once (see the server-bound start action below), with an
explicit retryable delivery failure state rather than a status that can strand
the record.

## 6. Context assembly

### 6.1 Inputs

Build a bounded `DiscoveryContextPack` for the active project and current query:

1. **Current conversation:** visible user/assistant turns and accepted card
   decisions already in the checkpoint.
2. **Current project identity and files:** the existing project context plus a
   bounded file manifest; file contents are read only when relevant.
3. **Project-scoped private memory:** the existing `(user, project)` bucket.
4. **Shared project memory:** existing member-approved project buckets, kept in
   their labelled section.
5. **Relevant project history:** bounded summaries of authorized prior project
   conversations and their referenced artifacts, not raw cross-thread dumps.
6. **User-global memory:** project-independent preferences and stable background
   facts from the user's global bucket.
7. **Published DBTL knowledge:** active, project-authorized knowledge pointers;
   provisional, superseded, or retracted claims are excluded or explicitly
   labelled non-authoritative.

Each retrieved item carries source type, stable reference, timestamp/revision,
scope, and a short excerpt or summary. The pack has per-source and total token
budgets.

User-global memory is retrieved afresh for the current discovery turn and
placed in its own labelled section. It is not checkpointed as the thread's
memory snapshot and is not merged into the project bucket: current project
conversations deliberately replace frozen global snapshots with
`(user, project)` memory to prevent sibling-project leakage. Passive memory
writes from the discovery conversation continue to update only the existing
project-scoped bucket unless the person uses an explicit global-memory action.

### 6.2 Trust and prompt placement

Framework-owned discovery state—status, project/thread binding, allowed
actions, and the statement that no cycle exists—may be placed in a system
context block.

Memory, project-file excerpts, prior conversation summaries, and published
claim text remain user/data context, following the existing dynamic memory
pattern. They must not gain system authority merely because DeerFlow retrieved
them. Source content is delimiter-escaped and cannot introduce instructions.

When sources conflict:

1. the scientist's current explicit statement wins for the draft;
2. accepted decisions in the current discovery beat earlier summaries;
3. current project artifacts beat stale memory about those artifacts;
4. project-scoped context beats user-global defaults for project facts; and
5. unresolved scientific conflicts are shown, not silently resolved by rank.

The Lead Agent can recommend a resolution, but the provenance record preserves
the conflict.

### 6.3 Memory does not become “the user said”

The existing setup draft uses a `grounded` boolean tied to the request text.
Discovery needs a richer provenance value because expanded context introduces
several legitimate sources:

- `user_turn`;
- `project_artifact`;
- `project_history`;
- `project_memory`;
- `user_global_memory`;
- `published_knowledge`; and
- `model_suggestion`.

Provenance and acceptance are separate fields. Only `user_turn` may be
described as something the scientist originally stated. A value from any source
may later be explicitly accepted or corrected by the scientist, but that action
does not erase its source: an accepted model suggestion is described as an
accepted assumption, not as the scientist's original claim. Memory-backed or
artifact-backed values can be proposed as context, and the start card shows
both their origin and acceptance state.

## 7. Backend architecture

### 7.1 Add a terminal discovery branch

Extend the thin Supervisor with a `discovery` terminal branch. It invokes the
real compiled Lead Agent exactly once, with the discovery context attached, then
runs bounded post-processing and optionally appends the deterministic start
card.

```text
START -> route -> discovery -> Lead Agent -> validate/update draft
                                      -> optional deterministic card -> END
```

The branch does not loop and does not call a DBTL stage adapter. Ordinary work
continues to use the unchanged ordinary branch.

The branch also applies a discovery-specific execution policy before invoking
the Lead Agent: project mounts are read-only, mutating file tools and general
delegation are denied, and only bounded inspection/retrieval capabilities are
visible. A system reminder saying “do not build yet” is useful explanation but
is not enforcement. The policy decision and any denied mutation attempt are
recorded in the run journal.

Routing precedence becomes:

1. server-emitted card answers and active-cycle controls;
2. an explicit per-request choice: ordinary, continue a named cycle, or begin
   discovery for a new one;
3. a selected existing cycle when no explicit choice overrode it;
4. active thread-bound discovery;
5. classifier suggestion; and
6. ordinary work.

The stage-control recovery fence from the stage-chat progress plan remains
higher priority than discovery. A thread with a pending Build handoff must
recover that handoff, not open a new pre-cycle conversation.

### 7.2 Persist a versioned discovery draft

Introduce a repository contract resembling:

```text
DbtlDiscovery
  id
  project_id
  thread_id
  user_id
  status
  trigger (explicit | classifier)
  policy_version
  revision
  proposed_title
  objective
  rationale
  known_inputs[]
  intended_outputs[]
  success_criteria[]
  rejection_criteria[]
  open_questions[]
  context_refs[]
  offered_revision
  created_at / updated_at / expires_at
```

Structured fields carry value, provenance type, source reference, confidence,
and whether the person explicitly accepted them. Store bounded summaries and
references rather than copying whole memories, files, or transcripts into the
row.

Use optimistic revision checks. A turn updates revision `N` to `N+1`; a card
bound to `N` becomes stale as soon as new information changes the draft.

### 7.3 Extract structure without parsing visible prose

Do not scrape the Lead Agent's natural-language answer with regular
expressions. After the response settles, a `DiscoveryUpdater` receives:

- previous validated draft;
- the latest user turn verbatim;
- the Lead Agent's final response;
- the bounded context references actually supplied; and
- any explicit discovery action.

It produces a closed, versioned structured payload. A configured nostream model
may propose that payload, but a deterministic parser enforces field names,
lengths, provenance values, references, and revision. Failure preserves the
previous draft and never blocks the conversational response.

The updater may attach source spans/turn ids to user-derived values. It may not
mark a model inference as an accepted decision.

### 7.4 Readiness is advisory plus deterministic

The model may explain that it thinks the proposal is ready, but the server
decides whether to auto-offer the card. Minimum readiness should require:

- a bounded objective;
- a reason the work benefits from iterative Design/Build/Test/Learn;
- at least one intended output or decision the cycle should produce; and
- no unresolved identity/scope ambiguity about the current project.

Success criteria, datasets, validation design, and scientific constraints are
valuable but may remain explicitly open for Design. The gate should avoid both
extremes: starting a cycle around an empty objective and holding discovery until
the entire Design stage has already happened in chat.

An explicit **Show proposal now** bypasses readiness but not provenance or
confirmation. Missing fields render as open questions.

### 7.5 Server-bound start action

The start card request binds:

- discovery id and revision;
- project and originating thread;
- authenticated user;
- policy version;
- rendered proposal hash; and
- a stable client submission/idempotency id.

On **Start DBTL cycle**, the server transaction:

1. revalidates membership and the exact active discovery revision;
2. compare-and-sets `offered -> confirmed`;
3. stores one immutable canonical discovery package in SQL and computes its
   content hash;
4. creates exactly one cycle using the displayed fields;
5. records the discovery/package-to-cycle provenance link; and
6. commits outbox items for any human-visible package projection, the creation
   receipt, and Design kickoff.

This applies the durable single-use contract proposed for post-approval
handoffs to cycle creation; it does not depend on that future handoff table
already existing. The compare-and-set is the single-use ledger: it makes the
start idempotent, so a double click or retried submission replays the one
creation receipt instead of creating a second cycle, and a non-`offered` row
(already confirmed, superseded, or declined) is refused rather than consumed
into a duplicate. The outbox delivers the receipt under the persist-before-
publish rule in the delivery contract below — the creation receipt reaches the
conversation only after it is durably journaled, and a delivery that fails after
the cycle exists is retried by discovery id, never by re-running creation.

The confirmation accepts the displayed proposal as the starting brief; it does
not rewrite its history. A model suggestion remains an **accepted assumption**,
not a fact the scientist originally stated, and every source/provenance label is
copied unchanged into the immutable package.

SQL is authoritative for this small structured package so cycle creation and
its binding can commit atomically. A Markdown/JSON file in the project folder is
a human-visible projection written from the committed package; projection
failure is visible and retryable but cannot leave a cycle bound to bytes that
the database never recorded.

The Lead Agent is not in this transaction. A cycle created from discovery enters
the same governance schema as any other cycle; there is no lightweight cycle
class that bypasses gates.

The existing frontend-owned `createFromNativeSetup` path can remain during
migration, but the end state should not require the browser to translate a card
answer into an unrelated mutation. The server-issued card and server mutation
must share one idempotent authority boundary.

### 7.6 Delivery contract

The start card and confirmation receipt are not delivered merely because a
graph state contains them. They are delivered only when their assistant/tool
messages are present in the canonical thread event journal consumed by
`GET /api/threads/{id}/messages/page`.

This requirement directly prevents the generated-but-undelivered handoff drift
recorded in the stage-chat progress plan. Refresh, replay, and live streaming
must project the same single card.

Use the persist-before-publish envelope discipline proposed for durable stage
progress: persist the card or receipt event first, then publish that same stored
event to the live bridge, so a live-only control cannot vanish on reload and a
replay cannot invent a second one. The current branch has the canonical
`run_events` history but not the proposed DBTL stage-progress envelope or
durable handoff outbox. Discovery therefore defines a stable
`discovery_event_id`; if the shared envelope work lands first, it reuses its
idempotent event writer, and otherwise implements the smallest shared writer
rather than a discovery-only transport. A replayed confirmation reuses the
original event identity. On a bridge gap or hard refresh the client backfills
from the authenticated thread-history endpoint and then resumes live delivery.

For the offer itself, an outbox item is created from the `ready` revision. Its
dispatcher idempotently inserts the canonical card event, then advances that
same revision to `offered`; a retry that finds the event already present only
repairs the status. For cycle creation, the confirmation transaction commits
the cycle and receipt outbox together, and the dispatcher can retry the receipt
without replaying the mutation. Neither path treats browser acknowledgement as
the durability boundary.

### 7.7 Immutable handoff to every Design participant

Cycle creation binds the accepted discovery package to the cycle. Design
kickoff loads that exact hash, revalidates any external source references, and
builds one bounded shared background block. Every dispatched Design work unit
receives that same block—including independent positions, red team, resumed
chair, and final chair—before its seat-specific focus and instructions. The
chair additionally receives the validated participant results; it must not be
the only participant that sees the original discovery context.

The shared block contains accepted decisions, unresolved questions, authorized
evidence summaries/references, and provenance. It does not dump raw global
memory or unrelated project history into every prompt. A reference that has
become unauthorized or unavailable is marked unavailable; its prior text is not
silently re-fetched from another scope.

Shared background does not mean shared capabilities. Existing DBTL capability
selection still chooses each subagent, and each seat keeps its effective model,
reasoning setting, and instructions plus the resolved subagent's tool whitelist
and skill allowlist. Tools remain least-privilege for the seat, while the Stage
Adapter remains the only component allowed to record Design evidence or satisfy
a gate. The review package records the discovery-package hash supplied to the
meeting so a reviewer can reconstruct what all participants knew.

## 8. Frontend architecture

### 8.1 Composer state

Keep the existing one-request DBTL selector semantics for ordinary, existing
cycle, and recommendation choices. Add a server-derived discovery indicator
that is thread-bound and survives the selector resetting after send.

The indicator is not a cycle scope. It means only that the Lead Agent is
helping prepare a proposal. It exposes:

- **Show proposal**;
- **Keep discussing**; and
- **Continue as ordinary work**.

### 8.2 Native card rendering

Reuse the Human Input Card protocol with a distinct
`clarification_type=dbtl_discovery_start`, or introduce an equally strict
server-bound card type if the richer provenance display cannot fit the generic
renderer. In either case:

- options use stable ids;
- no option is preselected;
- the exact discovery revision is visible/auditable;
- stale and answered cards are inert;
- card replies recover discovery identity from the server-emitted request; and
- the client never invents a project, cycle, or draft revision.

### 8.3 Starting Design

After cycle creation, select the returned cycle and begin the existing Design
preflight using the accepted discovery package. Do not replay an automatic
“start the council” transcript message as if the person typed it.

The Design context should include:

- the accepted discovery summary;
- unresolved questions explicitly carried forward;
- source references that remain authorized and current; and
- a hash/revision binding to the discovery package.

The backend materializes this as the shared participant block in §7.7; the
frontend only initiates the kickoff and never assembles or edits participant
context.

Design still owns scientific debate and the Design gate. Discovery is not a
hidden Design stage.

## 9. Configuration and rollout

Add interaction-level switches rather than another `dbtl.mode`, for example:

```yaml
dbtl:
  conversational_discovery: false
  discovery_global_memory: false
  discovery_project_history: false
  discovery_auto_offer: false
```

Exact names may change during implementation, but the controls should permit
independent rollout of routing, expanded retrieval, and automatic card timing.
Because the design uses a Supervisor branch, visible conversational discovery
requires `graph_enabled`. `audit_only` may shadow-evaluate it; `manual` keeps the
current manual/proposal path during migration unless a separate non-graph
implementation is deliberately designed. Neither mode may show a discovery
start action whose server authority path cannot succeed.

Suggested rollout:

1. shadow state/readiness telemetry only;
2. explicit **Start a new cycle** discovery, no classifier entry;
3. classifier-suggested discovery with auto-offer disabled;
4. project history and user-global memory retrieval;
5. automatic card offering after exit review; and
6. default-on only after false-entry, abandonment, and start-quality targets
   are met.

## 10. Phased implementation

Phases 0–2 are the minimum coherent conversational-entry slice. Phases 3–5
deliver the full project-history, system-wide-memory, and automatic-suggestion
experience; Phase 6 removes the temporary duplicate paths. This should not ship
as one large cutover.

### Phase 0 — characterization and contracts

- Pin the current explicit-start, classifier proposal, setup confirmation,
  setup-question, cycle-creation, and automatic Design-kickoff behavior.
- Add manual scenarios for underspecified, well-specified, ordinary, existing
  cycle, and ambiguous multi-cycle requests.
- Define the discovery state, provenance vocabulary, card contract, readiness
  minimum, and event-delivery invariant.

**Exit:** current behavior is replayable and the new state/card contracts can be
reviewed without an implementation.

### Phase 1 — explicit conversational discovery

- Route only explicit **Start a new cycle** requests into a new discovery
  branch.
- Bind one discovery state to the project thread.
- Inject framework-owned “candidate, not cycle” context into the Lead Agent.
- Apply and journal the discovery-specific read-only tool/mount policy.
- Support explicit show/keep-discussing/ordinary actions.
- Do not add expanded project history or user-global memory yet.

**Exit:** the Lead Agent can hold a multi-turn pre-cycle conversation across
refresh, and no cycle exists until the final start action.

### Phase 2 — structured draft and deterministic start card

- Add `DiscoveryUpdater`, validation, revisioning, readiness checks, and
  provenance.
- Render and durably journal one revision-bound start card.
- Add stale-card, retry, idempotency, and cycle-creation linkage.
- Materialize the accepted discovery package and carry its hash-bound shared
  context into every Design meeting work unit without changing per-agent
  tools/skills.

**Exit:** a well-specified explicit request can move from conversation to one
cycle with no duplicate questions and no browser-owned authority gap.

### Phase 3 — project context and history retrieval

- Add bounded project-file manifest and authorized prior-thread summaries.
- Preserve source references and conflict reporting.
- Measure retrieval usefulness and prompt/token cost.

**Exit:** discovery stops asking questions already answered by current project
evidence, while cross-thread content remains scoped and inspectable.

### Phase 4 — project and system-wide memory composition

- Compose project-private, shared-project, user-global, and published-knowledge
  sources under explicit budgets.
- Keep retrieved content at data/user authority.
- Extend provenance rendering and conflict rules.
- Add privacy, access-revocation, stale-memory, and prompt-injection coverage.

**Exit:** relevant memory improves the proposal without being represented as a
new user decision or leaking another project/member's context.

### Phase 5 — classifier entry and automatic offering

- Route eligible classifier suggestions into discovery.
- Calibrate re-entry suppression after a person chooses ordinary work.
- Enable automatic offer timing behind its own flag.
- Extend the evaluation drawer with discovery outcomes and corrections.

**Exit:** observed false-entry, abandonment, time-to-offer, and accepted-draft
quality meet the human-reviewed rollout thresholds.

### Phase 6 — cleanup and migration

- Retire duplicate immediate-proposal/setup paths after rollback coverage proves
  discovery is stable.
- Consolidate frontend cycle creation into the server-bound start action.
- Update DBTL readiness, operator documentation, and manual scenario captures.

**Exit:** one supported pre-cycle path remains, with explicit rollback for the
duration of the release window.

## 11. Failure and safety behavior

- **Classifier unavailable:** ordinary chat continues; explicit discovery still
  works.
- **Discovery updater/model unavailable:** preserve the prior draft, deliver the
  Lead Agent response, and allow manual card request.
- **Memory unavailable:** continue with transcript and project context; label no
  absent source as consulted.
- **Project history unavailable:** do not fall back to unrelated global history.
- **Ambiguous project or thread binding:** do not offer Start.
- **Stale discovery card:** refuse mutation and render the current revision.
- **Start card generated but not durably delivered:** the discovery stays
  `ready`, not `offered`; the persist-before-publish outbox redelivers by
  discovery id, so a card that never reached the journal is retried rather than
  silently lost.
- **Concurrent answers:** compare-and-set yields one cycle and replay receipts.
- **Cycle created but receipt delivery fails:** the persist-before-publish
  outbox retries the canonical thread projection by discovery id; it does not
  create another cycle.
- **User chooses ordinary:** clear discovery routing immediately; filesystem and
  DBTL execution fences still apply independently.
- **Lead Agent attempts mutation/delegation during discovery:** the run-scoped
  policy denies it, journals the denial, and the conversational response may
  continue without widening authority.
- **Prompt injection in memory/files/history:** content remains delimited data,
  retrieval instructions are ignored, and the context source is auditable.

## 12. Test strategy

### 12.1 Pure/unit coverage

- routing precedence, including active cycles and pending stage controls;
- discovery lifecycle transitions and optimistic revisions;
- provenance parsing and conflict precedence;
- deterministic readiness and explicit-show override;
- context budgets, authorization filters, and delimiter escaping;
- stale/forged card rejection and idempotent start; and
- source removal after membership or publication revocation.

### 12.2 Backend integration coverage

- Lead Agent receives discovery awareness before its first model call;
- discovery runs expose only the read-only inspection/retrieval policy and a
  read-only project mount;
- memory/project context is injected in the intended authority role;
- one Lead Agent invocation produces one visible final response;
- start card exists in canonical message events, not only graph output;
- refresh returns exactly one actionable card;
- confirmed discovery creates one cycle and links its source revision;
- every Design work unit receives the same accepted discovery-package hash and
  shared background before its seat-specific prompt, while retaining its own
  configured tools and skills; and
- no discovery path invokes `LiveStageAdapter` or writes stage outputs.

### 12.3 Frontend coverage

- discovery survives navigation and refresh;
- the one-shot scope selector can reset without losing discovery state;
- Show/Keep/Ordinary actions route correctly;
- no option is preselected;
- stale and answered cards disable actions;
- card corrections through chat create a new visible revision; and
- cycle creation selects the new cycle and starts the existing Design preflight
  without duplicate user-authored transcript text.

### 12.4 Manual scenarios

1. **Underspecified maize simulation:** agent asks one material question at a
   time, consults project context, and offers a proposal after several turns.
2. **Well-specified request:** agent acknowledges completeness and offers the
   card after one response.
3. **Explicit start now:** card appears with unresolved decisions rather than
   invented values.
4. **Ordinary opt-out:** conversation continues normally and no cycle exists.
5. **Existing cycle:** a cycle-bound request continues/reports that cycle and
   never opens discovery.
6. **Memory conflict:** current user statement overrides stale global memory and
   the discrepancy remains visible.
7. **Refresh before start:** the same proposal revision and one card return.
8. **Concurrent start:** two submissions create one cycle.
9. **Delivery failure:** the committed start action eventually produces one
   visible receipt through outbox replay.
10. **Revoked access:** prior project history/memory is absent on the next turn.
11. **Premature execution attempt:** a discovery request to “just run it now”
    cannot mutate project files or delegate Build work until the person starts a
    cycle or explicitly returns to ordinary work.

## 13. Telemetry and exit review

Record structure and outcomes, not unrestricted conversation contents:

- trigger source and classifier rule ids/confidence;
- discovery turn count and duration;
- card offered automatically or explicitly;
- keep-discussing, ordinary, dismissed, stale, and started outcomes;
- number and type of context sources consulted;
- which proposed fields were corrected before start;
- cycle creation id and accepted discovery revision;
- updater/retrieval/delivery failures; and
- model/token cost attributable to discovery.

Review especially:

- false discovery entry rate;
- requests where the user repeatedly chooses ordinary;
- questions whose answers were already present in authorized context;
- memory-backed fields corrected by the user;
- time/turns from first request to card;
- card abandonment rate; and
- Design changes caused by missing or misleading discovery context.

No rollout threshold should reward a higher cycle-start rate by itself. A person
choosing ordinary work can be the correct outcome.

## 14. Explicit non-goals

- Discovery does not execute Design, Build, Test, or Learn work.
- It does not replace the Design meeting or human Design gate.
- It does not auto-create a cycle from classifier confidence or readiness.
- It does not merge all project and global memories into one bucket.
- It does not expose raw cross-thread transcripts by default.
- It does not let the Lead Agent record gate decisions.
- It does not introduce a new DBTL operating mode.
- It does not solve later-stage progress/handoff delivery; it consumes the same
  durable-event rule those paths require.

## 15. Likely implementation seams

The work should extend, not duplicate, these existing boundaries:

- `deerflow.dbtl.routing` and `deerflow.dbtl.branches` for precedence and the
  new discovery route;
- `deerflow.agents.dbtl.supervisor` for the terminal branch and server-card
  recovery;
- `deerflow.dbtl.setup_draft` / `setup_questions` for bounded parsing and
  assumption discipline;
- `DynamicContextMiddleware`, memory scope helpers, and
  `ProjectContextMiddleware` for safe context composition;
- runtime tool policy and sandbox mount assembly for the enforced read-only
  discovery grant rather than a prompt-only prohibition;
- DBTL repositories/migrations for versioned discovery state and idempotent
  discovery-to-cycle linkage, implementing or sharing the compare-and-set
  handoff/outbox contract proposed in the stage-chat progress plan rather than
  depending on a currently nonexistent `dbtl_stage_handoffs` row;
- Gateway DBTL proposal/cycle routers for authenticated reads and actions;
- the existing runtime event journal plus the proposed shared
  persist-before-publish/idempotent-event writer for durable, dedup-safe card
  and receipt projection;
- DBTL stage planning/council context construction for hash-bound discovery
  background on every Design work unit while preserving per-agent tools and
  skills;
- `frontend/src/core/dbtl/composer-scope.ts` for explicit entry semantics;
- `useDbtlUpgradeProposal` for shadow/visible evaluation migration; and
- the native Human Input Card path for the review/start control.

## 16. Definition of done

The feature is complete when an underspecified project request can be discussed
over multiple turns with relevant, correctly scoped context; the scientist can
inspect and correct a provenance-labelled proposal; one explicit server-bound
action creates exactly one cycle; the accepted discovery package reaches
every Design participant with its provenance intact; refresh and replay preserve
the same visible controls; discovery cannot mutate project work or delegate the
future Build; and neither the Lead Agent, classifier, memory system, nor browser
can independently create or advance governed DBTL state.
