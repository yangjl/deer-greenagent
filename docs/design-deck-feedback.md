# Design feedback decks

After a Design meeting starts, its registered slide deck is the web input
surface for that round. The project owner can:

1. answer a paused chair using the recorded options or exact free-text
   question;
2. submit a completed Design package for review; and
3. record Approve, Request changes, or Reject as a separate final step.

The deck is interactive only in the project conversation where the meeting
started. It does not lock the project or the composer: the owner may continue
ordinary chat elsewhere or start another cycle while a meeting waits.
Requesting changes records the selected contested points and optional comment,
then starts a focused refinement in the originating conversation.

Downloaded files, legacy decks, direct/new-window views, copied or modified
HTML, wrong-thread views, stale evidence, and superseded rounds stay read-only.
The canonical Markdown/JSON Design package remains the scientific evidence; a
review additionally binds the exact deck SHA-256 and schema version that
collected the decision.

## HTTP contract

The parent application resolves a deck through:

```text
GET /api/projects/{project_id}/dbtl/design-feedback/{surface_id}
    ?viewer_thread_id={thread_id}
```

The response includes the server-computed `allowed_actions`, current DB and
stage status, evidence and deck bindings, originating conversation, newest
surface id, and any durable receipt. A successful read never delegates
authorization to the iframe.

The parent submits one bounded intent through:

```text
POST /api/projects/{project_id}/dbtl/cycles/{cycle_id}
     /design-feedback/{surface_id}/actions
```

```json
{
  "version": 1,
  "action": {
    "kind": "request_changes",
    "option_ids": ["issue-1"]
  },
  "comment": "Keep the external site out of model selection.",
  "client_submission_id": "client-generated-uuid",
  "originating_thread_id": "thread-id",
  "expected_db_revision": 12,
  "expected_evidence": {
    "artifact_id": "artifact-id",
    "revision": 3,
    "content_hash": "lowercase-sha256"
  },
  "expected_deck_hash": "lowercase-sha256"
}
```

Action kinds are `chair_option`, `chair_text`, `submit_for_review`, `approve`,
`request_changes`, and `reject`. The backend derives user identity and project
role from authentication, verifies that the user owns the originating
conversation in the project, and rechecks the current surface, stage, evidence,
deck hash, and DB revision. Identical retries replay the recorded action. A
changed payload, second accepted response, stale revision, or mismatched binding
returns `409` and is never optimistically rebased.

## Operations and rollback

Set:

```yaml
dbtl:
  design_deck_feedback: false
```

to restore the legacy Design Human Input card and Design stage sheet during the
rollback window. Turning the flag off does not delete descriptors, action
receipts, or reviews and does not reopen consumed surfaces. Reconciliation,
Build, Test, and Learn review surfaces are unaffected.

Interactive decks are web-only. IM channels should link to the originating web
conversation and may show a read-only summary; they do not receive a write
bridge.
