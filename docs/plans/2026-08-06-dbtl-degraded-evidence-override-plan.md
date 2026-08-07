# DBTL degraded-evidence continuation plan

**Status:** Proposed; this document does not authorize implementation or rollout.

**Date:** 2026-08-06

**Goal:** Let an authorized human continue a DBTL cycle when Build or Test
evidence is incomplete or untrustworthy, while preserving a prominent red flag,
preventing scientific overclaiming, and making every retry human-triggered.

**Audience:** Reviewers deciding whether a failed or partially evidenced run is
still useful enough to evaluate, learn from, or close deliberately.

---

## 1. Decision

Separate **workflow progression** from **scientific acceptance**.

A person may continue the workflow despite missing, inconsistent, or
untrustworthy evidence. That decision does not repair the evidence, approve the
stage, or turn an agent claim into a fact. It records an explicit exception and
constrains every downstream interpretation.

Use three evidence conditions:

| Condition | Meaning | Default behavior | Human continuation |
| --- | --- | --- | --- |
| `observed_variance` | Work and bytes are trustworthy; bookkeeping or path presentation differs. | Record an observation and review normally. | No exception needed. |
| `degraded_verified` | Core execution and hashes are trustworthy, but a required peripheral output/check is incomplete. | Show a red-flagged review. | May continue to Test with an exception. |
| `untrusted` | Execution is absent, inputs changed, core output is missing/unreadable, or hashes disagree. | Quarantine the affected claims and show a red-flagged review. | May continue only as invalid evidence; never as scientific support. |

This replaces the earlier hard-stop proposal for `untrusted` evidence. The
audit trail does not lie because the transition is named
`advanced_with_exception`, never `approved`.

## 2. What already exists

Reuse the current mechanisms:

- path-tail/content-hash reconciliation for harmless path variance;
- non-gating Build observations;
- Build fulfillment and Test deliverable-audit checklists;
- server-owned Test rerun evidence;
- HTML deck review actions and slide comments;
- chat Human Input Cards and Build recovery controls;
- append-only stage transitions; and
- Test outcomes and Learn's fail-closed candidate eligibility.

Do not add a second review UI, retry engine, or knowledge system.

## 3. Typed evidence dossier

When Build or Test cannot take its clean review path, produce one immutable
`EvidenceExceptionDossier` before presenting a human decision.

The bounded dossier contains:

| Field | Rule |
| --- | --- |
| `version` | Literal contract version. |
| `condition` | One of the three evidence conditions above. |
| `stage` / `stage_attempt_id` | Exact affected attempt. |
| `reason_codes` | Closed server-owned taxonomy. |
| `verified_facts` | Facts independently established by the server. |
| `untrusted_claims` | Worker/client claims that cannot be relied on. |
| `affected_inputs` | Changed, missing, or unreadable input bindings. |
| `affected_deliverables` | Deliverable IDs and fulfillment/audit state. |
| `failed_checks` | Exact failed or missing contract/validity checks. |
| `available_artifacts` | Existing content-addressed evidence only. |
| `scientific_effect` | `none`, `limits_scope`, or `invalidates_support`. |
| `recovery_options` | Legal server-derived routes. |
| `content_hash` | Canonical dossier hash used by the human decision. |

Initial classification reason codes include:

- `execution_absent`;
- `input_changed`;
- `core_output_missing`;
- `core_output_unreadable`;
- `hash_mismatch`;
- `deliverable_not_attempted`;
- `deliverable_attempt_failed`;
- `audit_incomplete`;
- `rerun_unavailable`; and
- `contract_bookkeeping_variance`.

Classification is deterministic and server-owned. A worker may report a
problem but cannot choose its condition, scientific effect, or legal route.
An `observed_variance` classification becomes the existing non-gating
observation; only `degraded_verified` and `untrusted` produce this dossier.

## 4. Human decision and UX

The authenticated stage deck remains the evidence-decision surface. A dossier
adds a persistent red banner and separates three actions:

1. **Retry with guidance** — opens the existing chat recovery flow. The chat
   card asks what should change and identifies exactly what will rerun and what
   evidence will be retained.
2. **Continue with red flag** — records `advanced_with_exception`; requires a
   written rationale and shows the downstream scientific restriction before
   submission.
3. Existing legal return, hold, or close actions — unchanged where compatible.

When trustworthy evidence is too incomplete to render the normal review deck,
the server renders a **dossier-only exception deck** in the same authenticated
deck shell. It shows verified metadata, missing or quarantined items, the
scientific restriction, and the legal actions. It has no headline-result slide
and never reads or embeds quarantined artifact bytes.

Exact continuation copy:

> **Continue with red flag**
> The workflow will continue, but this evidence remains failed or untrusted. It
> cannot be reported as scientific support.

Rules:

- no exception action is preselected or recommended;
- rationale is required and bounded;
- the action binds reviewer identity, project role, dossier hash, stage/cycle
  revision, chosen route, and timestamp;
- a stale dossier or stage revision refuses the action;
- the decision is immutable and visible in later decks and the transition
  timeline; and
- free text such as “approve anyway” never records the exception.

The new durable review outcome is `advanced_with_exception`. Do not store the
decision as `approved` plus a warning; existing readers would eventually drop
the warning and misstate the record.

## 5. Downstream rules

### 5.1 Build → Test

- `observed_variance` follows the normal path.
- `degraded_verified` may enter Test with the dossier attached. Test runs the
  applicable checks and computes its ordinary outcome; the dossier remains
  visible and may limit the claim scope.
- `untrusted` may enter Test only to evaluate and record the failure. Test does
  not treat quarantined bytes or worker claims as evidence.

If execution never ran or trusted inputs/outputs are unavailable, Test must not
spend a worker pretending to rerun them. It creates a deterministic invalidated
assessment from the dossier and presents that result to the human.

### 5.2 Test → Learn

Add one outcome-compatible route for invalid evidence:
`learn_from_invalidated_evidence`.

This route:

- preserves Test outcome `invalidated`;
- opens Learn with the dossier and human rationale;
- allows synthesis of limitations, process lessons, failed-method notes, and a
  next-cycle proposal; and
- produces zero scientific knowledge candidates.

It must not be named `advance_to_learn`, because that existing route reads as
accepting a qualified Test outcome.

### 5.3 Knowledge boundary

- `untrusted` or `invalidated` evidence can never create, promote, or publish a
  scientific candidate. Human continuation cannot override this boundary.
- `degraded_verified` evidence may create a candidate only when Test computes
  `supported` or `not_supported`. The dossier is inherited as a limitation and
  remains visible during the existing separate promotion/publication decisions.
- no exception decision implicitly promotes or publishes anything.

Workflow authority belongs to the human reviewer; scientific support remains a
server-computed property of trustworthy evidence.

## 6. Retry semantics

After a dossier is recorded, no stage worker, verifier, or retry watcher reruns
automatically.

A retry requires a server-owned chat control answered by a human. The control
must state:

- the failed unit/check;
- the evidence that will be reused;
- the work that will rerun;
- whether completed work will be discarded; and
- a free-text guidance field.

Path-only or bookkeeping repairs re-evaluate recorded evidence without rerunning
expensive workers. A scientific or execution retry creates a new attempt and
preserves the failed attempt and dossier.

This plan also removes or disables automatic same-attempt correction after an
affected Build/Test check fails. A model may correct work before it reports a
terminal result; once the server records a failure or dossier, only a human may
start another attempt.

## 7. Persistence and boundaries

Prefer existing storage:

- store the dossier with the stage evidence package or existing bounded action
  payload;
- store the human exception through the stage-feedback action and append-only
  transition record; and
- project the same dossier into Test, Learn, decks, chat receipts, and audit
  views.

Add no table unless atomicity or query requirements cannot be met by the current
stage-feedback/transition records.

`advanced_with_exception` is not part of the generic `approved` predicate and
does not satisfy unrelated stage prerequisites. The bound transition action may
open only its explicitly named downstream edge; every other prerequisite keeps
its existing approval semantics.

The server validates condition, routes, stage revision, reviewer authority, and
knowledge restrictions at the write boundary. Frontend checks are UX only.

## 8. Failure behavior

- **Dossier cannot be built:** keep the stage paused and show the server error;
  do not offer continuation.
- **Dossier persistence fails:** no deck action and no transition.
- **Exception delivery fails:** keep the durable decision and retry only its
  receipt using the existing outbox pattern.
- **Retry delivery fails:** preserve the failed evidence and reopen the exact
  chat control; dispatch nothing.
- **Changed evidence after review:** invalidate the dossier/action and require a
  new review.
- **Unknown reason code or route:** fail closed to a paused stage.
- **Legacy client:** renders the deck/read-only warning; cannot fabricate the
  new action.

## 9. Non-goals

- No relabelling failed or untrusted evidence as approved.
- No worker-authored exception or scientific outcome.
- No automatic retry after a recorded dossier.
- No use of quarantined bytes to satisfy hashes, checks, or candidate evidence.
- No direct promotion/publication exception.
- No replacement for HTML deck decisions or chat retry guidance.
- No default-on rollout.

## 10. Phased implementation

### Phase 0 — taxonomy and characterization

- Pin current refusal paths for fulfillment, audit, rerun, input drift, missing
  outputs, unreadable outputs, and hash mismatch.
- Add pure dossier classification tests for all three conditions.
- Pin current transition, Test outcome, and candidate-eligibility behavior.

**Exit:** every known refusal maps deterministically to a condition, reason code,
scientific effect, and legal route.

### Phase 1 — dossier and review action

- Implement the typed dossier and canonical hash.
- Render the dossier-only exception deck when the normal evidence deck is not
  safe or possible to build.
- Register `continue_with_red_flag` as a stage-feedback intent.
- Add `advanced_with_exception` without treating it as approval.
- Persist who, why, what, and the exact dossier/revision atomically.

**Exit:** a human may record an exception, but no downstream stage consumes it.

### Phase 2 — Test and Learn propagation

- Allow Build exceptions to open Test under the rules above.
- Force `untrusted` Test evidence to `invalidated` without unnecessary worker
  reruns.
- Add `learn_from_invalidated_evidence` and zero-candidate Learn behavior.
- Preserve existing knowledge promotion/publication guards.

**Exit:** the workflow can reach a human-reviewed Learn result without
laundering failed evidence into a scientific claim.

### Phase 3 — chat retry and UI projection

- Add the deck banner, rationale requirement, and exact action copy.
- Route retry requests to the existing chat Human Input transport with guidance.
- Remove post-dossier automatic retries and reuse trustworthy evidence for
  bookkeeping-only repair.
- Project the dossier and exception into later decks and the transition timeline.

**Exit:** every rerun is visibly human-triggered, and every downstream reader
sees the red flag.

### Phase 4 — dark pilot

- Gate the feature behind `dbtl.degraded_evidence_continuation=false`.
- Replay one case per condition, including absent execution, input drift, hash
  mismatch, missing peripheral output, and path-only variance.
- Verify no duplicate dispatch, no silent approval, no candidate from
  invalidated evidence, and exact recovery after refresh.
- Review whether people understand the difference between “continue” and
  “approve” before considering rollout.

**Exit:** a documented human rollout decision exists; implementation alone does
not enable the feature.

## 11. Acceptance criteria

1. Every `degraded_verified` or `untrusted` Build/Test refusal offered for human
   continuation receives exactly one server-classified dossier or remains
   paused with a visible server error; `observed_variance` remains an ordinary
   non-gating observation.
2. Path/bookkeeping variance does not require an exception or rerun workers.
3. `degraded_verified` and `untrusted` continuation requires an authorized human,
   a rationale, and the current dossier/revision.
4. Exception continuation is never stored or rendered as approval.
5. An exception opens only the route named in its bound action and cannot
   satisfy any generic approval prerequisite.
6. Execution absence, input drift, missing core outputs, unreadable core outputs,
   and hash mismatch cannot support a passing Test check.
7. `untrusted` evidence deterministically produces Test outcome `invalidated`.
8. Invalidated evidence may reach Learn only through
   `learn_from_invalidated_evidence`, with zero scientific candidates.
9. Promotion and publication remain impossible for invalidated/untrusted
   evidence.
10. After dossier creation, no retry occurs without a human answer in chat.
11. A bookkeeping repair reuses recorded evidence; a substantive retry creates a
    new attempt and preserves the failed one.
12. The dossier and human rationale remain visible in every downstream review
    and transition projection.
13. Stale, duplicate, unauthorized, malformed, or legacy actions mutate nothing.

## 12. Alternatives rejected

- **Permanent hard stop for untrusted evidence:** preserves integrity but traps
  the workflow and prevents a human from recording an invalidated result or
  learning from failure.
- **Approve with a warning:** reuses the happy path but eventually launders the
  warning when a reader sees only `approved`.
- **Extra promotion override:** lets workflow authority weaken scientific truth;
  invalidated evidence must remain ineligible for scientific claims.
- **Automatic retry until clean:** wastes workers and removes human guidance from
  the decision.

## 13. Review checkpoint

- **Decision:** Permit explicit human workflow continuation for degraded and
  untrusted evidence through `advanced_with_exception`.
- **Scientific boundary:** Untrusted evidence remains invalidated and cannot
  support knowledge candidates, promotion, or publication.
- **Interaction boundary:** Deck records the exception; chat owns retry guidance
  and dispatch.
- **Primary risk:** Users may read continuation as approval; distinct state,
  copy, and downstream propagation must prevent that.
- **Rollback:** Disable the flag; existing observations and hard refusal paths
  remain intact.
- **Next step after approval:** Convert Phases 0–4 into agent-ready tasks; do not
  implement directly from this document.
