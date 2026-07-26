"""Phase 1 no-go: serialized resume payloads must not satisfy a human gate.

The Phase 1 plan states:

    No-go if a bare string, replay, wrong actor, or stale artifact can satisfy
    a gate.

The Gateway review route captures authenticated identity and binds reviews to
durable project, cycle, revision, artifact, and policy records. A graph resume
payload has none of those guarantees, even when its JSON looks complete.
"""

from __future__ import annotations

import pytest

from deerflow.agents.dbtl.orchestrator import _parse_human_decision


@pytest.mark.parametrize(
    "payload",
    [
        "yes",
        "approved",
        "no",  # a *refusal* that is truthy as a string
        "human-directed",
        True,
        1,
        ["approve"],
        {"approved": True},  # no authorization reference
        {"authorization": "DEC-1"},  # no explicit decision
        {"approved": True, "authorization": "   "},  # blank reference
        {"approved": True, "authorization": "human-directed"},  # greenagent rejects this
        {"decision": "approve", "authorization": "DEC-1"},  # missing reviewer identity
        {
            # Still client-asserted: a browser can forge every field.
            "decision": "approve",
            "authorization": "DEC-2026-07-25-cutover",
            "reviewer_user_id": "user-1",
        },
        {
            # Adding apparent durable bindings does not make JSON authoritative.
            "decision": "approve",
            "review_id": "review-1",
            "project_id": "project-1",
            "cycle_id": "cycle-1",
            "db_revision": 7,
            "artifact_revision": 3,
            "policy_version": "v1",
            "authorization": "DEC-1",
            "reviewer_user_id": "user-1",
        },
    ],
)
def test_client_resume_payloads_cannot_approve_a_gate(payload: object) -> None:
    approved, _authorization = _parse_human_decision(payload)
    assert approved is False, f"payload {payload!r} satisfied a human gate"


@pytest.mark.parametrize("decision", ["reject", "request_changes"])
def test_explicit_non_approvals_do_not_advance(decision: str) -> None:
    approved, _ = _parse_human_decision(
        {
            "decision": decision,
            "authorization": "DEC-2026-07-25-cutover",
            "reviewer_user_id": "user-1",
        }
    )
    assert approved is False
