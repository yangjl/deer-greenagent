# Design meeting deck feedback

**Status:** Proposed implementation plan; no product code is changed by this
document.
**Date:** 2026-07-28
**Scope:** Make the slide deck the only post-convening human-input surface for a
Design meeting, and remove the separate Design review sheet.

## 1. Outcome

The Design meeting deck should become the place where a project owner:

1. sees what the participants agreed on and where they disagreed;
2. answers a paused chair through option cards and an optional comment;
3. explicitly submits a completed design for review;
4. approves it, requests changes, or rejects it; and
5. sees the durable receipt for the action.

The end-to-end flow is:

```text
meeting
  → recorded chair result
  → revision-bound feedback deck
  → human choice in that deck
  → same conversation resumes, or a durable review is recorded
  → updated/final deck
```

The deck is the only **input** surface after the meeting is convened. It is not
the database, an API client, or a new agent. The authenticated DeerFlow parent
application owns every mutation, and the backend remains the authority for
membership, state transitions, idempotency, artifact binding, and reviewer
identity.

The existing participant/depth preflight remains the meeting's setup form in
the first version. It happens before the meeting is convened and is not feedback
on a round. Moving preflight into a setup deck is explicitly deferred.

Only the Design review sheet is removed. Reconciliation, Build, Test, and Learn
keep their existing review surfaces.

## 2. Debate record

Three independent positions were used to make this plan.

### Product and interaction position

One contextual surface is easier to understand than a deck plus a clarification
card plus a review sheet. The deck should open on the decision that needs the
owner, keep drafts through a retry, freeze accepted answers, and state which
conversation will resume. It should distinguish meeting feedback from formal
stage review and preserve access to the full evidence.

### Architecture and governance position

The HTML must remain an untrusted projection. It should emit bounded intents to
its sandbox host, while the authenticated parent maps those intents to fixed
handlers. A durable, server-issued surface descriptor should bind the deck to
the project, cycle, Design attempt, round, originating thread, chair request,
review artifact, and exact hashes. Final review must keep the explicit
`submit_for_review` and `stage_review` transitions.

### Red-team position

A generic HTML write bridge would let any agent-generated page imitate a Design
deck. A downloaded deck could be framed by a hostile page, an old tab could
submit against a later round, and the current truncated deck could make an audit
claim that the reviewer read a different Markdown document. Partial meetings,
multiple tabs, retries, wrong-thread views, historical decks, and accessibility
all need fail-closed behavior before the old UI is removed.

### Chair synthesis

Adopt the deck as the sole post-convening Design input surface with five
constraints:

- The persisted HTML starts inert and is interactive only after an authenticated
  parent validates a server-issued descriptor and the current hashes/state.
- The deck remains a pure renderer. Scientific option text comes from a
  validated chair result; workflow action text comes from server-owned
  vocabulary.
- Canonical Markdown/JSON remain the evidence record, while the exact deck is a
  separately hashed reviewed projection. A review binds both.
- A paused chair resumes only in the conversation that produced the deck.
  Opening another chat or starting another cycle is independent.
- The Design sheet stays available only behind a rollback flag until parity and
  live-state migration are proven; it is then removed from the normal UI.

The debate did not support an invisible one-click submit-and-approve operation.
The first version keeps “Submit for review” and the later verdict as two visible
steps in the same deck. A future one-click action would require one atomic
server command, not two frontend mutations that can half-complete.

## 3. Current state

The relevant current contracts are:

- `deerflow.dbtl.council_deck` renders one self-contained, no-network HTML file
  from the recorded chair result. It deliberately does not invent prose.
- The deck is currently unregistered, fail-soft, and explicitly tells the owner
  to use the Design review sheet.
- A paused meeting emits the deck and a standard `ask_clarification` request.
  Answering that request in chat resumes only the chair over the prior recorded
  positions and participant settings.
- A completed Design package stays `in_progress` until a person submits it for
  review. The separate sheet then calls the existing review endpoint.
- The backend owns reviewer identity, requires project membership, rejects
  internal-system reviewers, checks expected DB revision and idempotency, and
  binds a review to the current evidence revision.
- HTML artifacts run as Blob URLs in an opaque-origin iframe with
  `allow-scripts allow-forms`. The only current `postMessage` protocol restores
  scroll position, and the parent validates `event.source`.
- The generic HTML preview can display any agent-produced HTML. It is not a
  trusted Design surface.

The implementation must preserve these useful boundaries while changing the
visible interaction.

## 4. Product behavior

### 4.1 Deck states

| Server state | Deck behavior |
| --- | --- |
| Meeting still running | Read-only progress/status; no feedback controls |
| Chair needs input | Open on “Needs your decision”; show chair-authored option cards or an exact-question free-text fallback |
| Feedback submitting | Freeze inputs, show progress, retain the draft for retry |
| Feedback accepted | Show a receipt and “Chair resuming in \<conversation\>” |
| Completed Design, `in_progress` | Show a distinct “Submit for review” action |
| `awaiting_review` | Show Approve, Request changes, and Reject cards with consequences |
| Approved or rejected | Read-only receipt with reviewer, time, revision, and bound hashes |
| Changes requested | Read-only receipt while a focused refinement starts in the originating conversation |
| Superseded, stale, or already answered | Disable input and link/open the newest feedback deck |
| Invalid descriptor, hash mismatch, or incomplete chair result | Read-only failure state with “Regenerate feedback deck”; never expose gate actions |

Selections are drafts until an explicit submit. A successful response freezes
the controls. A failed network request retains the draft and reuses the same
idempotency key.

### 4.2 Option and comment rules

For chair feedback:

- exactly one option is selected in the first version;
- no option is preselected, including a recommended option;
- a recommendation may be labelled and explained as recorded evidence;
- the comment is optional;
- an “Other” option requires text; and
- if the chair did not produce valid structured options, the deck shows the
  exact recorded question with a free-text answer inside the deck.

For formal review:

- “Submit for review” has no comment;
- Approve permits an optional comment because the selected action and exact
  evidence/deck bindings are already structured audit facts;
- Request changes requires a comment, or at least one explicitly selected
  unresolved/contested card, so the focused round has an actionable objection;
- Reject requires a comment; and
- the backend stores the selected action separately from the human's comment.
  It must not manufacture prose and attribute it to the reviewer.

The existing non-empty `rationale` string can remain as a human-readable
projection for compatibility, but it must be marked as a server projection of
the structured selection. The nullable human comment remains distinguishable
from that projection in the durable record.

### 4.3 Same-conversation behavior

Every surface names its originating conversation. Submitting chair feedback or
requesting changes continues work only there.

The owner may leave that conversation, open a new chat, and start another cycle.
No project-global modal, pending-input lock, composer lock, or automatic thread
switch is allowed. When the original chair resumes or finishes, its normal
conversation notification/badge should update without interrupting the active
chat.

Opening the deck from another project view may display it, but mutation is
allowed only when the authenticated surface descriptor still resolves to the
originating project, cycle, and thread. The UI should say where the work will
continue and offer a link; it must never create a substitute thread.

### 4.4 Evidence and receipts

Removing the sheet must not remove:

- the full bound Markdown review package;
- the structured JSON package;
- agreements, disagreements, open questions, risks, and limitations;
- participant/round provenance;
- the activity and worker record; or
- the durable submission/review receipt.

The deck may link or open those as read-only inspectors. “Deck is the only input
surface” does not mean “the deck is the only readable evidence.”

## 5. Authority and invariants

These are implementation gates, not preferences.

1. **The iframe is untrusted.** It receives no cookie, bearer token, CSRF token,
   reusable write capability, or endpoint selection.
2. **The parent owns writes.** Deck messages are intents. The parent maps a
   finite action enum to fixed handlers after fetching current server state.
3. **Generic HTML cannot write.** Feedback handling is enabled only for a
   server-registered, hash-matched Design feedback surface.
4. **The renderer invents nothing scientific.** Cards are rendered from the
   validated chair result; malformed options do not become guessed choices.
5. **The backend revalidates everything.** Parent validation improves UX but is
   not an authorization boundary.
6. **One accepted response wins.** Retry with the same key and payload replays
   the result. The same key with a changed payload, or a second tab answering a
   consumed/superseded surface, receives a conflict.
7. **No optimistic rebasing.** A response rejected for stale DB, stage,
   artifact, deck, policy, or projection revision is never retried against the
   new revision automatically.
8. **A review binds what was shown.** The review records the canonical evidence
   artifact revision/hash and the exact deck projection hash/schema version.
9. **Human authorship stays exact.** Selected option IDs and comments are stored
   as supplied. Any display sentence assembled from them is labelled as a
   server projection.
10. **Agents cannot satisfy gates.** Internal callers remain forbidden from
    recording a human review or consuming a human feedback surface.
11. **Downloaded decks are inert.** They remain navigable, printable, and
    readable, but say “Open this deck in DeerFlow to respond.”
12. **Failure becomes visible.** Once the deck is the only input UI, failure to
    render or register it blocks Design feedback/review and offers deterministic
    recovery instead of silently omitting the deck.

## 6. Durable contracts

### 6.1 Structured chair decision

Extend the recorded chair result with a versioned decision request when status
is `needs_input`.

```json
{
  "status": "needs_input",
  "clarification_question": "Which population structure should define validation?",
  "decision_request": {
    "version": 1,
    "id": "validation-population",
    "question": "Which population structure should define validation?",
    "options": [
      {
        "id": "family_holdout",
        "label": "Family holdout",
        "value": "Use family-level holdout.",
        "description": "Stricter generalization test across related groups."
      },
      {
        "id": "random_split",
        "label": "Random split",
        "value": "Use a random split.",
        "description": "More observations per fold but weaker structure control."
      }
    ],
    "recommended_option_id": "family_holdout",
    "recommendation": "Matches the stated deployment population."
  }
}
```

Validation should:

- allow one focused request per chair pause;
- require stable, bounded, unique option IDs;
- require 2–5 options for card mode;
- bound labels, values, descriptions, and recommendation text;
- reject a `recommended_option_id` that is not present;
- preserve the existing exact `clarification_question`; and
- fail the chair contract, or fall back to exact-question free text, according
  to an explicit test-covered policy. It must never silently drop the question.

The chair-resume prompt receives a mechanical rendering of the selected value
and comment. The structured response remains in metadata/audit so the exact
human choice does not depend on parsing prose later.

### 6.2 Design feedback surface

Create a durable, opaque surface descriptor, for example
`dbtl_design_feedback_surfaces`:

```text
surface_id
project_id
cycle_id
stage_attempt_id
design_round
originating_thread_id
mode                         chair_feedback | stage_review | read_only
chair_worker_run_id          nullable
human_input_request_id       nullable
deck_uri
deck_content_hash
deck_schema_version
evidence_artifact_id
evidence_artifact_revision
evidence_content_hash
projection_hash
policy_version
created_at
superseded_by_surface_id     nullable
```

This descriptor is not a review and does not replace `dbtl_reviews`. It proves
that a particular HTML projection was produced by the Design workflow from
particular evidence and identifies the only thread/request it may answer.

Do not make the deck the “latest ordinary artifact” and let attachment order
choose the approvable evidence. If deck rows share `dbtl_artifacts`, add an
explicit derived-from link and select the canonical `design_review` artifact by
type and revision.

### 6.3 Surface read model

Add an authenticated read endpoint conceptually equivalent to:

```text
GET /api/projects/{project_id}/dbtl/cycles/{cycle_id}/design-feedback/{surface_id}
```

It returns:

- current surface status;
- the exact allowed actions;
- current DB/stage/artifact revisions;
- evidence and deck hashes;
- originating conversation label/link;
- whether the linked clarification is still the newest unanswered request;
- any accepted response/review receipt; and
- the newest surface when this one is stale.

Project membership, cycle ownership, thread association, and hash/state checks
run before returning actionable state.

### 6.4 Submission envelope

Both interaction paths use a versioned envelope:

```json
{
  "version": 1,
  "surface_id": "opaque-id",
  "action": {
    "kind": "chair_option",
    "option_ids": ["family_holdout"]
  },
  "comment": "Keep one site fully external if sample size allows.",
  "client_submission_id": "uuid",
  "expected_db_revision": 12,
  "expected_evidence": {
    "artifact_id": "artifact-id",
    "revision": 3,
    "content_hash": "sha256..."
  },
  "expected_deck_hash": "sha256..."
}
```

The parent supplies project, cycle, and thread routing from its trusted context,
not from identifiers asserted by the iframe. The server binds the idempotency
key to the whole normalized payload.

### 6.5 Review provenance

Extend the Design review record or its immutable event payload with:

```text
input_source = design_deck
feedback_surface_id
deck_content_hash
deck_schema_version
selected_action
selected_card_ids
human_comment             nullable
rationale_projection
rationale_source = server_projection
```

The existing evidence artifact ID/revision/hash remains bound. Approval,
changes requested, and rejection therefore identify both the authoritative
evidence and the exact presentation used to collect the decision.

## 7. Sandboxed bridge

### 7.1 Activation

Persisted HTML contains only the public `surface_id`, rendered content, and an
inert feedback mount. It initially displays disabled controls.

On load:

1. The deck emits `ready` with protocol version and `surface_id`.
2. The parent checks that `event.source` is the exact iframe
   `contentWindow`.
3. The parent resolves the selected file to a registered Design surface and
   verifies its content hash.
4. The parent fetches the authenticated surface read model.
5. The parent creates a random, per-mount channel ID and sends `initialize`
   with the current status and allowed actions.
6. Later messages must match the source window, channel ID, protocol schema,
   surface ID, and allowed action set.

The channel ID prevents accidental cross-talk; it is not treated as
authorization. The server still repeats every check.

### 7.2 Message vocabulary

Deck to parent:

```text
ready
submit_intent
open_evidence_intent
open_originating_conversation_intent
```

Parent to deck:

```text
initialize
pending
accepted
stale
failed
```

Messages carry no endpoints or credentials. The parent has a closed mapping
from `chair_option`, `chair_text`, `submit_for_review`, `approve`,
`request_changes`, and `reject` to product handlers.

Keep the iframe opaque-origin and omit same-origin, top-navigation, popups, and
downloads. `postMessage("*")` is acceptable only because no secret crosses the
boundary and source window + per-mount channel + server binding are all checked.

### 7.3 Two write paths

**Paused chair**

- The surface references the exact server-emitted Human Input request.
- The parent submits the existing `human_input_response` shape through the
  originating thread's hidden reply path, extended with `surface_id` and
  `client_submission_id`.
- The server confirms it is the newest unanswered linked request, consumes it
  once, and starts or replays the same bound run.
- The supervisor keeps using `clarification_answer`, so only the chair resumes
  over the recorded positions and settings.
- The normal Human Input card remains in durable thread history but is not
  rendered as a second actionable control when a valid deck surface exists.

This path needs request-scoped idempotency at `(thread_id, request_id,
client_submission_id)`. Run concurrency by itself is not sufficient protection
against duplicate hidden human messages.

**Final Design gate**

- `submit_for_review` uses the existing stage submission transition, extended
  to validate `surface_id`, evidence revision/hash, and deck hash.
- Only after the server returns `awaiting_review` does the same deck enable the
  three verdict cards.
- A verdict uses the existing authenticated review transition, extended with
  the structured Design deck provenance.
- Request changes records the exact selected issue IDs and comment, then queues
  the existing focused refinement in the originating conversation. Approval and
  rejection do not start a model run.

If one-click submit-and-review is added later, it must be a single repository
transaction that records both events. The client must never chain two writes
while presenting them as one atomic action.

## 8. Frontend changes

Add a Design-specific controller instead of broadening generic HTML into a
write-capable surface.

Recommended shape:

- a pure `design-deck-feedback` module parses messages, validates schemas, and
  reduces surface state;
- a `DesignDeckPreview` or an explicitly enabled mode of
  `ArtifactFilePreview` owns the iframe handshake;
- the artifact panel passes trusted project/thread/file context into that
  controller;
- fixed callbacks invoke current thread-send and DBTL cycle hooks;
- parent-owned, tab-scoped draft state is keyed by `surface_id`;
- query invalidation refreshes the exact cycle and originating thread only; and
- the chat artifact experience auto-opens a newly presented feedback deck but
  does not prevent the owner from closing it or changing conversations.

Do not install a global `message` listener that accepts Design actions from all
artifact iframes. Scroll restoration stays independent and namespaced.

At cutover:

- suppress the visible Design `HumanInputCard` only when a valid linked deck
  surface exists;
- remove Design document/review actions from `CycleStageSheet`;
- keep non-Design review components unchanged;
- replace any Design rail review action with “Open feedback deck”; and
- remove `design-review.tsx` only after no remaining read-only consumer needs
  it.

### Accessibility

- Use a real fieldset/radio group for mutually exclusive cards.
- Do not use color alone for selected, recommended, pending, or stale states.
- Move focus to an active slide heading and hide inactive slides from assistive
  technology.
- Announce submission state through a restrained `aria-live` region.
- Keep visible focus, high contrast, zoom/mobile layouts, and reduced-motion
  support.
- Ignore deck navigation keys when focus is in a textarea, button, radio,
  select, link, or contenteditable element.
- Print/download omits drafts and controls while retaining provenance and any
  already-recorded receipt.

## 9. Backend changes

The implementation should:

1. extend and validate the chair's structured decision request;
2. render feedback markup from recorded values only;
3. create the surface descriptor after writing and hashing the deck;
4. make Design deck creation mandatory before an interactive wait/review state;
5. expose the authenticated surface read model;
6. add exact surface/evidence bindings to submit and review writes;
7. add single-use, payload-bound idempotency for chair responses;
8. keep reviewer identity and project role server-owned;
9. queue focused refinement in the originating thread after changes are
   requested; and
10. make regeneration supersede rather than mutate an old surface.

The renderer stays pure: no model call, database access, authentication, or
filesystem access is added to `render_council_deck`.

## 10. Failure, stale, and compatibility behavior

| Condition | Required result |
| --- | --- |
| Legacy deck has no descriptor | Read-only |
| Downloaded or new-window deck has no trusted parent handshake | Read-only |
| Wrong project/thread | Read-only; do not reveal actionable state |
| Deck bytes do not match registered hash | Read-only error |
| Evidence revision/hash changed | Stale; link newest deck |
| Newer Design round exists | Superseded; link newest deck |
| Linked chair request already answered | Receipt/read-only |
| Two tabs submit together | First valid write wins; other receives conflict and refreshes |
| Retry uses same key and payload | Replay the original result |
| Retry reuses key with changed payload | Conflict |
| Chair accepted feedback but later fails | Keep accepted receipt and show recoverable failed-resume state |
| No trustworthy chair result | No decision/review actions |
| Deck renderer/descriptor write fails after cutover | Block feedback/review and offer regenerate |
| Request changes has no actionable selection/comment | Validation error; preserve draft |
| Internal/system caller attempts review | Forbidden |

Slack and other IM channels cannot host the interactive deck. They should
receive an authenticated deep link to the originating web conversation and a
read-only summary. The Design gate should be documented as web-only rather than
leaving those cycles apparently actionable in chat.

## 11. Implementation sequence

Each phase lands tests before behavior.

### Phase 0 — Freeze contracts

- Add failing tests for structured chair options, descriptor binding, iframe
  message rejection, same-thread resumption, exact review binding, stale
  surfaces, and inert downloads.
- Write the JSON schemas/types for `decision_request`, surface read model,
  bridge messages, and submission envelopes.
- Decide the narrow persistence migration for surface descriptors and review
  provenance.

No UI is removed in this phase.

### Phase 1 — Durable backend foundation

- Add the chair decision contract and strict parser.
- Add the feedback-surface persistence model and migration.
- Register/hash rendered decks without making attachment order authoritative.
- Add the authenticated read endpoint and regeneration/supersession behavior.
- Extend stage submit/review validation to exact evidence + deck bindings.
- Add chair-response idempotency and server-owned audit provenance.

Keep the current card and Design sheet as fallback behind
`dbtl.design_deck_feedback`.

### Phase 2 — Inert interactive renderer

- Add option, comment, submit, verdict, receipt, stale, and failure layouts.
- Add the bounded bridge vocabulary and disabled-without-handshake behavior.
- Preserve current navigation, print, dark mode, escaping, and no-network
  guarantees.
- Add evidence/provenance links without adding factual prose.

The deck still cannot mutate anything without the parent controller.

### Phase 3 — Parent bridge and chair resume

- Add the Design-only iframe controller and authenticated handshake.
- Connect chair option/text intents to the existing hidden Human Input response
  path in the originating thread.
- Suppress the duplicate visible clarification card only for a verified
  deck-backed request.
- Prove that opening a new conversation or starting another cycle remains
  independent.

### Phase 4 — Submit and formal review in the deck

- Connect explicit submit-for-review and verdict handlers.
- Record the exact evidence/deck bindings and structured response provenance.
- Start focused refinement in the originating conversation after Request
  changes.
- Render durable receipts and current-state refresh in every open tab.

### Phase 5 — Cut over the Design UI

- Re-render or regenerate v1 feedback decks for live `needs_input`,
  `in_progress`-with-package, and `awaiting_review` Design attempts.
- Keep historical/unregistered decks read-only.
- Remove Design input/document/review controls from the cycle stage sheet.
- Replace rail entry points with “Open feedback deck.”
- Retain a server/operator rollback flag for one release window.

### Phase 6 — Cleanup and documentation

- Remove the fallback Design sheet after telemetry shows no unresolved live
  surfaces and rollback has been exercised.
- Update root, backend, and frontend `AGENTS.md`, user documentation, API
  documentation, and changelog.
- Document the web-only interaction boundary for IM channels.

## 12. Test plan

### Backend unit and contract tests

- chair option bounds, duplicate IDs, missing values, invalid recommendation;
- exact-question fallback without invented options;
- HTML escaping for every chair/user-controlled field;
- deterministic deck bytes and content hash;
- surface creation, lookup, supersession, and project/thread scoping;
- exact evidence/deck/policy/projection binding;
- review provenance and no false human attribution;
- single-use chair response and payload-bound idempotency;
- internal reviewer rejection;
- request-changes actionability validation; and
- renderer/descriptor failure blocks interaction after cutover.

### Frontend unit tests

- strict parse/reject for every bridge message;
- wrong source window, channel ID, protocol version, surface ID, action, and
  payload shape;
- generic HTML cannot activate feedback handlers;
- no-handshake/downloaded mode stays disabled;
- draft retention and stable retry key;
- accepted, stale, failed, superseded, and receipt reducers;
- navigation keys do not capture typing/controls;
- focus, labels, radio semantics, error association, and live announcements;
  and
- Design-only removal leaves other stage sheets unchanged.

### Integration tests

- `needs_input → option/comment → same-thread chair-only resume → new deck`;
- free-text fallback follows the same path;
- explicit submit changes `in_progress → awaiting_review`;
- deck approve writes one durable bound review;
- deck request changes carries the exact objection into the focused round;
- rejection is terminal and starts no run;
- stale evidence/DB/deck hash returns conflict without rebasing;
- two tabs produce one accepted response/review;
- retry returns the original outcome;
- switching threads during submit does not move the action; and
- missing/failed chair exposes no gate action.

### Browser and security tests

- arbitrary artifact HTML cannot invoke a DBTL mutation;
- downloaded deck cannot mutate state, including when framed;
- wrong-thread/project preview remains read-only;
- refresh, back/forward, close/reopen, and multiple tabs;
- owner opens another chat and starts another cycle while the first waits;
- network loss before and after server acceptance;
- keyboard-only use, screen-reader labels, zoom, mobile, reduced motion, print;
  and
- automated accessibility scan of every interactive deck state.

## 13. Observability and rollback

Emit structured lifecycle events without comment bodies, tokens, or raw deck
content:

```text
design_feedback.surface_created
design_feedback.surface_opened
design_feedback.intent_received
design_feedback.accepted
design_feedback.replayed
design_feedback.conflict
design_feedback.superseded
design_feedback.resume_started
design_feedback.resume_failed
design_feedback.review_recorded
```

Include project/cycle/thread/surface/round identifiers, action kind, latency,
revision, and a bounded failure code.

The rollback flag restores the visible Design Human Input card and Design sheet
without deleting descriptors, responses, reviews, or deck artifacts. Rollback
must never reopen a consumed surface or discard a decision already recorded
through the deck.

## 14. Expected file map

Backend:

- `backend/packages/harness/deerflow/dbtl/worker_result.py`
- `backend/packages/harness/deerflow/dbtl/consensus.py`
- `backend/packages/harness/deerflow/dbtl/council_deck.py`
- `backend/packages/harness/deerflow/agents/dbtl/stage_execution.py`
- `backend/packages/harness/deerflow/agents/dbtl/supervisor.py`
- `backend/packages/harness/deerflow/persistence/dbtl/model.py`
- `backend/packages/harness/deerflow/persistence/dbtl/cycles.py`
- a new persistence migration
- `backend/app/gateway/routers/dbtl_cycles.py`
- focused tests under `backend/tests/test_dbtl_*`

Frontend:

- a new pure `frontend/src/core/dbtl/design-deck-feedback.ts`
- a Design-specific preview/controller beside
  `frontend/src/components/workspace/artifacts/artifact-file-detail.tsx`
- chat artifact selection/submission integration
- `frontend/src/core/dbtl/cycles-api.ts`
- `frontend/src/core/dbtl/cycle-hooks.ts`
- `frontend/src/components/workspace/messages/human-input-card.tsx`
- `frontend/src/components/workspace/project-rail/cycle-stage-sheet.tsx`
- `frontend/src/components/workspace/project-rail/design-review.tsx`
- unit tests under `frontend/tests/unit/`
- browser tests under `frontend/tests/e2e/`

Documentation:

- root `AGENTS.md`
- `backend/AGENTS.md`
- `frontend/AGENTS.md`
- API and user-facing Design workflow documentation
- `CHANGELOG.md`

## 15. Acceptance criteria

The feature is ready to replace the Design sheet only when all of the following
are true:

- A paused chair can be answered entirely inside the deck.
- Choices shown are traceable to a recorded chair result; the renderer adds no
  scientific option text.
- The answer resumes only the chair in the exact originating conversation.
- The owner can use another conversation and start another cycle while the
  first meeting waits.
- Submit-for-review and the final verdict are completed entirely in the deck as
  two visible transitions.
- Approve, Request changes, and Reject write durable human review records with
  exact evidence and deck bindings.
- Request changes carries the exact selected issue IDs and human comment into
  the focused refinement round.
- Downloaded, legacy, wrong-thread, stale, superseded, hash-mismatched, and
  already-consumed decks are read-only.
- Arbitrary agent-generated HTML cannot cause a DBTL write.
- A renderer or descriptor failure cannot strand the owner without a visible,
  deterministic recovery action.
- The Design review sheet and duplicate Design clarification control are absent
  from the normal UI, while all other stage review surfaces still work.
- Keyboard-only and assistive-technology users can navigate, select, comment,
  submit, retry, and understand the result.
- Historical audit records identify what evidence was reviewed, what deck was
  shown, what structured action was selected, what the human actually wrote,
  and which conversation continued.

## 16. Explicit non-goals

- No model call creates or rewrites slide prose.
- No direct API access is added to downloaded HTML.
- No project-wide “waiting for you” lock is introduced.
- No review sheet is removed for non-Design stages.
- No existing historical deck becomes interactive.
- No one-click submit-and-review operation is implemented in the first version.
- No preflight roster/depth configuration is moved into the deck in this
  version.
