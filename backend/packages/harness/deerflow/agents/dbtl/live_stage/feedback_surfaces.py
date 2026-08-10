"""What a review deck is registered as, and which evidence it binds.

A DBTL deck is the authenticated surface a human answers a stage gate from, so
two questions have to be settled before its bytes exist: *which surface id will
this file carry*, and *which artifact row does it speak for*. Both are decided
here; the adapter keeps the repository reads and writes that surround them.

That split is the point. Everything in this module is a pure function of values
already in hand, so the rules can be read, tested, and changed without a
repository, a cycle, or a running stage — and the durable write boundary stays
in exactly one place, where it can still be seen.

Three rules live here and none of them is arbitrary:

**Evidence is matched by hash, never by recency alone.** A deck that bound "the
newest artifact" would let a later, unrelated document inherit a verdict that
was recorded against a different one. Among rows that *are* the same document,
the newest revision wins — a retry may record the same bytes again, and the
surface must point at the row that still exists.

**The surface id is derived, not minted.** A retried turn re-derives the same
id, so it produces the same bytes, so it hashes the same, so re-registration
collapses onto the existing row instead of superseding it with a copy of
itself. Randomness here would mean a new row every retry.

**Mode is decided before rendering, and can refuse.** A completed round whose
evidence cannot be matched by hash is registered ``read_only`` rather than
reviewable, because binding a future verdict to a document nobody confirmed
this deck was rendered from is worse than shipping a deck that cannot answer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from deerflow.dbtl.build_deck import BUILD_DECK_SURFACE_VERSION
from deerflow.dbtl.decision_request import DecisionRequest


@dataclass(frozen=True, slots=True)
class FeedbackSurfacePlan:
    """What a deck will be registered as, decided before it is rendered."""

    surface_id: str
    mode: str
    #: Which DBTL stage this deck speaks for. A verdict recorded against
    #: another stage's attempt is a verdict on the wrong document, so this
    #: travels with the plan rather than being assumed by the writer.
    stage: str
    stage_attempt_id: str
    originating_thread_id: str
    round_number: int
    evidence: Mapping[str, Any] | None = None
    evidence_content_hash: str = ""
    decision_request: Mapping[str, Any] | None = None
    chair_worker_run_id: str | None = None

    @property
    def answerable(self) -> bool:
        """Whether this deck should carry a bridge at all.

        A ``read_only`` deck ships with no bridge script whatsoever rather than
        a disabled one: the safest version of "this file cannot answer" is a
        file containing no code that could.
        """
        # Design owns both chair questions and its review gate. Every later
        # stage owns its review gate in the same HTML deck so comments stay
        # attached to the slide they address. Chat may mirror progress, but it
        # is not a second decision surface.
        if self.stage == "design":
            return self.mode in {"chair_feedback", "stage_review"}
        return self.stage in {"build", "test", "learn"} and self.mode == "stage_review"


def stage_attempt_row_id(cycle: Mapping[str, Any], stage: str) -> str:
    """The durable ``dbtl_stage_runs`` id, not the worker plan's attempt token."""
    stages = cycle.get("stages")
    if not isinstance(stages, Sequence) or isinstance(stages, str):
        return ""
    for item in stages:
        if isinstance(item, dict) and item.get("stage") == stage:
            return str(item.get("id") or "")
    return ""


def bound_evidence(cycle: Mapping[str, Any], *, artifact_uri: str, content_hash: str) -> Mapping[str, Any] | None:
    """The artifact row a review deck projects, matched by its exact hash.

    Matched on the content hash rather than merely "the newest artifact",
    because attachment order is not evidence: the deck must bind to the
    document it was rendered from or to nothing at all. A retry may record the
    same document again, so identical matches bind the newest revision.
    """
    artifacts = cycle.get("artifacts")
    if not artifacts or not isinstance(artifacts, Sequence) or isinstance(artifacts, str):
        return None
    return max(
        (item for item in artifacts if isinstance(item, dict) and str(item.get("content_hash") or "") == content_hash and str(item.get("uri") or "") == artifact_uri),
        key=lambda item: int(item.get("revision") or 0),
        default=None,
    )


def surface_mode(*, thread_id: str, paused: bool, evidence: Mapping[str, Any] | None) -> str:
    """How this deck may be answered, if at all."""
    if not thread_id:
        return "read_only"
    if paused:
        return "chair_feedback"
    if evidence is not None:
        return "stage_review"
    # A completed round whose evidence could not be matched by hash.
    # Registering it as reviewable would bind a future verdict to a
    # document nobody confirmed this deck was rendered from.
    return "read_only"


def surface_id_for(*, execution_key: str, mode: str, round_number: int, stage: str) -> str:
    """Derive the id the deck will carry, stably across retries."""
    # Design ids predate the stage parameter and stay byte-for-byte stable.
    # Build includes the renderer's surface version: its deck now embeds a
    # live bridge, so reusing an id from bridge-less bytes would make the
    # repository rename the new row *after* rendering and leave the id
    # inside the file pointing at the stale descriptor.
    identity = (execution_key, mode, str(round_number))
    if stage == "build":
        identity = (*identity, stage, BUILD_DECK_SURFACE_VERSION)
    elif stage in {"test", "learn"}:
        # These stages were originally registered as inert provenance
        # pages. Version the interactive bytes so an existing inert row
        # can never steal the id embedded in a replacement deck.
        identity = (*identity, stage, "interactive-v1")
    digest = hashlib.sha256("\x1f".join(identity).encode("utf-8")).hexdigest()
    return f"dfs-{digest[:32]}"


def plan_surface(
    cycle: Mapping[str, Any],
    *,
    stage: str,
    execution_key: str,
    round_number: int,
    originating_thread_id: str,
    paused: bool,
    artifact_uri: str,
    artifact_hash: str,
    decision_request: DecisionRequest | None = None,
    chair_worker_run_id: str | None = None,
    review_issue_ids: Sequence[str] = (),
    transition_gate: Mapping[str, Any] | None = None,
) -> FeedbackSurfacePlan | None:
    """Decide the surface for an already-read cycle.

    Returns ``None`` when there is nothing to bind to; the caller still writes
    the deck, just without a bridge.
    """
    attempt_row_id = stage_attempt_row_id(cycle, stage)
    if not attempt_row_id:
        return None

    thread_id = (originating_thread_id or "").strip()
    evidence: Mapping[str, Any] | None = None
    if artifact_uri and artifact_hash:
        evidence = bound_evidence(cycle, artifact_uri=artifact_uri, content_hash=artifact_hash)

    mode = surface_mode(thread_id=thread_id, paused=paused, evidence=evidence)
    return FeedbackSurfacePlan(
        surface_id=surface_id_for(execution_key=execution_key, mode=mode, round_number=round_number, stage=stage),
        mode=mode,
        stage=stage,
        stage_attempt_id=attempt_row_id,
        # A read-only surface still needs a non-empty column; it names no
        # live conversation and is refused as an answer target.
        originating_thread_id=thread_id or "unbound",
        round_number=round_number,
        evidence=evidence if mode == "stage_review" else None,
        evidence_content_hash=artifact_hash if mode == "stage_review" and evidence is not None else "",
        decision_request={
            **(decision_request.as_dict() if decision_request is not None else {}),
            **({"review_issue_ids": list(review_issue_ids)} if review_issue_ids else {}),
            **({"transition_gate": dict(transition_gate)} if transition_gate is not None else {}),
        }
        or None,
        chair_worker_run_id=chair_worker_run_id,
    )


def registration_kwargs(
    plan: FeedbackSurfacePlan,
    deck: Any,
    *,
    cycle_id: str,
    project_id: str,
) -> dict[str, Any]:
    """The exact row a registered deck becomes.

    Built here and written by the caller, so the durable write stays at one
    visible boundary while the shape of the row stays testable without one.
    """
    decision_request = dict(plan.decision_request) if plan.decision_request is not None else {}
    commentable_slides = getattr(deck, "commentable_slides", ())
    if commentable_slides:
        decision_request["commentable_slides"] = [dict(item) for item in commentable_slides]
    return dict(
        surface_id=plan.surface_id,
        project_id=project_id,
        cycle_id=cycle_id,
        stage_attempt_id=plan.stage_attempt_id,
        design_round=plan.round_number,
        originating_thread_id=plan.originating_thread_id,
        mode=plan.mode,
        chair_worker_run_id=plan.chair_worker_run_id,
        decision_request=decision_request or None,
        deck_uri=deck.uri,
        deck_content_hash=deck.content_hash,
        evidence_artifact_id=str(plan.evidence["id"]) if plan.evidence is not None else None,
        evidence_artifact_revision=int(plan.evidence["revision"]) if plan.evidence is not None else None,
        evidence_content_hash=plan.evidence_content_hash or None,
        stage=plan.stage,
    )
