"""Durable descriptors for rendered Design feedback decks.

The deck is HTML a workflow produced and a browser renders in an opaque-origin
iframe. Nothing about the file itself distinguishes it from any other page an
agent wrote, so before a parent application may treat one as a Design surface it
has to be able to ask a server: *did you produce these exact bytes, for which
cycle, from which evidence, and which conversation may they answer?* That is all
this table answers. It grants no authority on its own — an interactive deck also
needs the authenticated parent bridge and the human-review transitions. Those
paths treat this descriptor as a binding to revalidate, never as a credential.

Two rules shape the write path. **Server-owned binding**: the revision,
projection hash, and policy version are read off the cycle rather than accepted
from the caller, because a deck that could assert what it was rendered against
could assert that a stale one is current. **Supersede, never mutate**: a
regenerated deck writes a new row and points the old one at it, since the old
row is the record of what somebody was actually shown and a review may already
refer to it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from deerflow.dbtl.stage_feedback import (
    STAGE_FEEDBACK_STAGES,
    allowed_stage_feedback_intents,
    validate_stage_feedback_intent,
)
from deerflow.persistence.dbtl.model import (
    DbtlArtifactRow,
    DbtlCycleRow,
    DbtlDesignFeedbackActionRow,
    DbtlDesignFeedbackSurfaceRow,
    DbtlStageAttemptRow,
)

logger = logging.getLogger(__name__)

#: What a surface may be used for. ``read_only`` exists for a deck that is worth
#: showing but must never collect anything — a historical round, or one whose
#: evidence has moved on.
SURFACE_MODES = ("chair_feedback", "stage_review", "read_only")

#: Bumped when the rendered deck's structure changes in a way a parent bridge
#: would have to understand. A review records it beside the content hash, so a
#: reader can tell a re-render from a redesign.
DECK_SCHEMA_VERSION = "1"
DESIGN_FEEDBACK_ACTIONS = allowed_stage_feedback_intents("design")
TRANSITION_DIFFICULTIES = frozenset({"routine", "standard", "high_stakes"})
_ACTION_GROUP = {
    "chair_option": "chair_response",
    "chair_text": "chair_response",
    "submit_for_review": "stage_submit",
    "approve": "stage_review",
    "request_changes": "stage_review",
    "reject": "stage_review",
    "advance": "stage_review",
    "park": "stage_park",
    "convene_review_meeting": "review_meeting",
    "choose_route": "stage_review",
    "recommend_promotion": "stage_review",
    "close_without_candidate": "stage_review",
}


class DesignFeedbackConflict(ValueError):
    """A deck intent no longer matches the durable surface it names."""


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _locked_cycle_for_feedback_surface(cycle_id: str, project_id: str):
    """Serialize revision allocation and supersession for one cycle."""
    return (
        select(DbtlCycleRow)
        .where(
            DbtlCycleRow.id == cycle_id,
            DbtlCycleRow.project_id == project_id,
        )
        .with_for_update()
    )


class DesignFeedbackOpsMixin:
    """Register and resolve the decks a person may be shown."""

    @staticmethod
    def _surface_payload(row: DbtlDesignFeedbackSurfaceRow) -> dict[str, Any]:
        from deerflow.persistence.dbtl.cycles import _iso

        return {
            "surface_id": row.id,
            "project_id": row.project_id,
            "cycle_id": row.cycle_id,
            "stage_attempt_id": row.stage_attempt_id,
            "stage": row.stage,
            "surface_revision": row.surface_revision,
            "design_round": row.design_round,
            "originating_thread_id": row.originating_thread_id,
            "mode": row.mode,
            "chair_worker_run_id": row.chair_worker_run_id,
            "human_input_request_id": row.human_input_request_id,
            "decision_request": row.decision_request,
            "deck_uri": row.deck_uri,
            "deck_content_hash": row.deck_content_hash,
            "deck_schema_version": row.deck_schema_version,
            "evidence_artifact_id": row.evidence_artifact_id,
            "evidence_artifact_revision": row.evidence_artifact_revision,
            "evidence_content_hash": row.evidence_content_hash,
            "bound_db_revision": row.bound_db_revision,
            "projection_hash": row.projection_hash,
            "policy_version": row.policy_version,
            "superseded_by_surface_id": row.superseded_by_surface_id,
            # Derived rather than stored: "current" is the absence of a
            # successor, and a second column saying so could disagree with it.
            "is_current": row.superseded_by_surface_id is None,
            "lifecycle_state": ("superseded" if row.superseded_by_surface_id is not None else "open"),
            "created_at": _iso(row.created_at),
        }

    @staticmethod
    def _action_payload(row: DbtlDesignFeedbackActionRow) -> dict[str, Any]:
        from deerflow.persistence.dbtl.cycles import _iso

        return {
            "client_submission_id": row.id,
            "project_id": row.project_id,
            "cycle_id": row.cycle_id,
            "surface_id": row.surface_id,
            "stage": row.stage,
            "action_group": row.action_group,
            "action_kind": row.action_kind,
            "selected_card_ids": list(row.selected_card_ids or []),
            "human_comment": row.human_comment,
            "expected_db_revision": row.expected_db_revision,
            "expected_evidence": row.expected_evidence,
            "expected_deck_hash": row.expected_deck_hash,
            "status": row.status,
            "run_id": row.run_id,
            "receipt": row.receipt,
            "failure_code": row.failure_code,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    async def register_stage_feedback_surface(
        self,
        *,
        stage: str,
        project_id: str,
        cycle_id: str,
        stage_attempt_id: str,
        design_round: int,
        originating_thread_id: str,
        mode: str,
        deck_uri: str,
        deck_content_hash: str,
        deck_schema_version: str = DECK_SCHEMA_VERSION,
        surface_id: str | None = None,
        chair_worker_run_id: str | None = None,
        human_input_request_id: str | None = None,
        decision_request: dict[str, Any] | None = None,
        evidence_artifact_id: str | None = None,
        evidence_artifact_revision: int | None = None,
        evidence_content_hash: str | None = None,
    ) -> dict[str, Any]:
        """Record that this workflow produced this stage deck and supersede the last.

        Re-registering identical bytes for the same attempt and mode returns the
        existing descriptor: a retried turn re-renders the same file, and minting
        a second id for it would leave two live surfaces answering one question.

        ``surface_id`` may be supplied because the deck embeds its own id — the
        caller has to know it before rendering the bytes this row will bind. It
        is still server-generated; it simply comes from the workflow rather than
        from here. A caller that re-registers a *known* id with different bytes
        is superseding its own earlier surface, which is the same path a
        regenerated deck takes.
        """
        from deerflow.persistence.dbtl.cycles import DbtlWorkflowRefused

        normalized_stage = (stage or "").strip().lower()
        if normalized_stage not in STAGE_FEEDBACK_STAGES:
            raise DbtlWorkflowRefused(f"Stage {stage!r} cannot own a feedback surface.")
        if mode not in SURFACE_MODES:
            allowed = ", ".join(SURFACE_MODES)
            raise DbtlWorkflowRefused(f"Unknown feedback surface mode {mode!r}; expected one of: {allowed}")
        if not _is_sha256(deck_content_hash):
            # Not normalized: two spellings of one hash would compare unequal
            # when the bridge later checks the file it was handed.
            raise DbtlWorkflowRefused("A feedback surface needs the deck's lowercase SHA-256 content hash.")
        thread_id = (originating_thread_id or "").strip()
        if not thread_id:
            raise DbtlWorkflowRefused("A feedback surface must name the conversation it may answer.")
        if not (deck_uri or "").strip():
            raise DbtlWorkflowRefused("A feedback surface needs the deck's location.")

        evidence_bound = bool(evidence_artifact_id)
        if mode == "stage_review" and not evidence_bound:
            raise DbtlWorkflowRefused("A stage_review surface must name the evidence it shows.")
        if mode == "chair_feedback" and evidence_bound:
            raise DbtlWorkflowRefused("A chair_feedback surface cannot bind review evidence before the chair completes.")
        if evidence_bound and not _is_sha256(evidence_content_hash):
            raise DbtlWorkflowRefused("Bound evidence needs its lowercase SHA-256 content hash.")

        async with self._sf() as session:  # type: ignore[attr-defined]
            # This lock covers MAX(revision), insert, and supersession below.
            # Without it, two different deck hashes can both allocate the same
            # revision and both remain live under PostgreSQL READ COMMITTED.
            cycle = await session.scalar(
                _locked_cycle_for_feedback_surface(cycle_id, project_id)
            )
            if cycle is None:
                raise DbtlWorkflowRefused("That cycle does not belong to this project.")

            attempt = await session.scalar(
                select(DbtlStageAttemptRow).where(
                    DbtlStageAttemptRow.id == stage_attempt_id,
                    DbtlStageAttemptRow.cycle_id == cycle_id,
                )
            )
            if attempt is None:
                raise DbtlWorkflowRefused("That stage attempt does not belong to this cycle.")
            if attempt.stage != normalized_stage:
                raise DbtlWorkflowRefused("The feedback stage does not match its stage attempt.")

            if evidence_bound:
                artifact = await session.scalar(
                    select(DbtlArtifactRow).where(
                        DbtlArtifactRow.id == evidence_artifact_id,
                        DbtlArtifactRow.cycle_id == cycle_id,
                    )
                )
                if artifact is None:
                    raise DbtlWorkflowRefused("That evidence artifact does not belong to this cycle.")
                if artifact.stage_attempt_id != stage_attempt_id:
                    raise DbtlWorkflowRefused("That evidence artifact does not belong to this stage attempt.")
                if artifact.revision != evidence_artifact_revision or artifact.content_hash != evidence_content_hash:
                    raise DbtlWorkflowRefused("The evidence revision or content hash does not match the artifact.")

            existing = await session.scalar(
                select(DbtlDesignFeedbackSurfaceRow).where(
                    DbtlDesignFeedbackSurfaceRow.stage_attempt_id == stage_attempt_id,
                    DbtlDesignFeedbackSurfaceRow.stage == normalized_stage,
                    DbtlDesignFeedbackSurfaceRow.mode == mode,
                    DbtlDesignFeedbackSurfaceRow.deck_content_hash == deck_content_hash,
                )
            )
            if existing is not None:
                return self._surface_payload(existing)

            new_id = (surface_id or "").strip() or f"dfs-{uuid4().hex}"
            # A caller-derived id can collide with its own earlier surface when
            # the same execution renders different bytes (a round that paused,
            # then completed). That is a supersession, not a duplicate, so the
            # old row keeps its identity and hands the id to the new one.
            clash = await session.scalar(select(DbtlDesignFeedbackSurfaceRow).where(DbtlDesignFeedbackSurfaceRow.id == new_id))
            if clash is not None:
                new_id = f"{new_id[:60]}-{uuid4().hex[:8]}"

            row = DbtlDesignFeedbackSurfaceRow(
                id=new_id,
                project_id=project_id,
                cycle_id=cycle_id,
                stage_attempt_id=stage_attempt_id,
                stage=normalized_stage,
                surface_revision=int(
                    (
                        await session.scalar(
                            select(func.max(DbtlDesignFeedbackSurfaceRow.surface_revision)).where(
                                DbtlDesignFeedbackSurfaceRow.cycle_id == cycle_id,
                                DbtlDesignFeedbackSurfaceRow.stage == normalized_stage,
                            )
                        )
                        or 0
                    )
                    + 1
                ),
                design_round=max(1, int(design_round)),
                originating_thread_id=thread_id,
                mode=mode,
                chair_worker_run_id=chair_worker_run_id,
                human_input_request_id=human_input_request_id,
                decision_request=decision_request,
                deck_uri=deck_uri,
                deck_content_hash=deck_content_hash,
                deck_schema_version=deck_schema_version,
                evidence_artifact_id=evidence_artifact_id,
                evidence_artifact_revision=evidence_artifact_revision,
                evidence_content_hash=evidence_content_hash if evidence_bound else None,
                bound_db_revision=int(cycle.db_revision),
                projection_hash=cycle.projection_hash,
                policy_version=cycle.policy_version,
            )
            session.add(row)
            await session.flush()

            # Every earlier live surface on this attempt now describes a deck
            # nobody should answer. They keep their rows; they gain a successor.
            superseded = await self._live_surfaces(
                session,
                stage_attempt_id,
                stage=normalized_stage,
                exclude_id=row.id,
            )
            for stale in superseded:
                stale.superseded_by_surface_id = row.id

            await session.commit()
            payload = self._surface_payload(row)
            logger.info(
                "design_feedback.surface_created",
                extra={
                    "design_feedback": {
                        "project_id": project_id,
                        "cycle_id": cycle_id,
                        "thread_id": thread_id,
                        "surface_id": row.id,
                        "round": row.design_round,
                        "stage": normalized_stage,
                        "mode": mode,
                    }
                },
            )
            for stale in superseded:
                logger.info(
                    "design_feedback.superseded",
                    extra={
                        "design_feedback": {
                            "project_id": project_id,
                            "cycle_id": cycle_id,
                            "thread_id": thread_id,
                            "surface_id": stale.id,
                            "newest_surface_id": row.id,
                            "round": row.design_round,
                        }
                    },
                )
            return payload

    async def register_design_feedback_surface(self, **kwargs: Any) -> dict[str, Any]:
        """Compatibility wrapper for pre-Phase 2 callers and captured decks."""
        return await self.register_stage_feedback_surface(stage="design", **kwargs)

    async def bind_design_feedback_request(
        self,
        surface_id: str,
        *,
        project_id: str,
        human_input_request_id: str,
        chair_worker_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Bind the card emitted after rendering to the surface that will answer it."""
        request_id = human_input_request_id.strip()
        if not request_id:
            raise ValueError("A feedback surface needs the emitted Human Input request id.")
        async with self._sf() as session:  # type: ignore[attr-defined]
            row = await session.scalar(
                select(DbtlDesignFeedbackSurfaceRow).where(
                    DbtlDesignFeedbackSurfaceRow.id == surface_id,
                    DbtlDesignFeedbackSurfaceRow.project_id == project_id,
                )
            )
            if row is None:
                raise DesignFeedbackConflict("Feedback surface not found.")
            if row.mode != "chair_feedback" or row.superseded_by_surface_id is not None:
                raise DesignFeedbackConflict("Only the current paused-chair surface can be bound to a request.")
            if row.human_input_request_id and row.human_input_request_id != request_id:
                raise DesignFeedbackConflict("This surface is already bound to another Human Input request.")
            row.human_input_request_id = request_id
            if chair_worker_run_id:
                row.chair_worker_run_id = chair_worker_run_id
            await session.commit()
            return self._surface_payload(row)

    async def mark_bound_design_feedback_answer(
        self,
        *,
        project_id: str,
        human_input_request_id: str,
        answer: str,
    ) -> None:
        """Consume a bound chair surface when the legacy card answered it.

        The rollback UI and the deck share one durable Human Input request.
        Recording fallback consumption in the same action group prevents
        turning the flag back on from reopening a request already answered
        through the card.
        """
        request_id = human_input_request_id.strip()
        if not request_id:
            return
        async with self._sf() as session:  # type: ignore[attr-defined]
            surface = await session.scalar(
                select(DbtlDesignFeedbackSurfaceRow)
                .where(
                    DbtlDesignFeedbackSurfaceRow.project_id == project_id,
                    DbtlDesignFeedbackSurfaceRow.human_input_request_id == request_id,
                    DbtlDesignFeedbackSurfaceRow.superseded_by_surface_id.is_(None),
                )
                .order_by(DbtlDesignFeedbackSurfaceRow.created_at.desc())
                .limit(1)
            )
            if surface is None:
                return
            existing = await session.scalar(
                select(DbtlDesignFeedbackActionRow).where(
                    DbtlDesignFeedbackActionRow.surface_id == surface.id,
                    DbtlDesignFeedbackActionRow.action_group == "chair_response",
                )
            )
            if existing is not None:
                return
            normalized_answer = answer.strip()[:4_000]
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "surface_id": surface.id,
                        "request_id": request_id,
                        "answer": normalized_answer,
                        "source": "legacy_human_input_card",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            row = DbtlDesignFeedbackActionRow(
                id=f"fallback-{digest[:40]}",
                project_id=project_id,
                cycle_id=surface.cycle_id,
                surface_id=surface.id,
                stage=surface.stage,
                action_group="chair_response",
                action_kind="chair_text",
                payload_hash=digest,
                selected_card_ids=[],
                human_comment=normalized_answer or None,
                expected_db_revision=surface.bound_db_revision,
                expected_evidence=None,
                expected_deck_hash=surface.deck_content_hash,
                status="resume_started",
                receipt={
                    "kind": "chair_text",
                    "originating_thread_id": surface.originating_thread_id,
                    "message": "This chair request was answered through the rollback Human Input card.",
                },
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # The deck action and the fallback run can race at this exact
                # boundary; the unique action group is the arbiter.
                await session.rollback()

    async def reserve_stage_feedback_action(
        self,
        *,
        project_id: str,
        cycle_id: str,
        surface_id: str,
        originating_thread_id: str,
        action_kind: str,
        selected_card_ids: list[str],
        human_comment: str,
        client_submission_id: str,
        expected_db_revision: int,
        expected_evidence: dict[str, Any] | None,
        expected_deck_hash: str,
        difficulty_override: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Validate and reserve one single-use stage-deck intent.

        Returns ``(surface, action, replayed)``. The unique surface/action-group
        constraint is the atomic arbiter when two tabs submit concurrently.
        """
        from deerflow.persistence.dbtl.cycles import DbtlWorkflowRefused

        if difficulty_override is not None and difficulty_override not in TRANSITION_DIFFICULTIES:
            raise DbtlWorkflowRefused("Unknown transition difficulty override.")
        if not _is_sha256(expected_deck_hash):
            raise DbtlWorkflowRefused("The expected deck hash must be a lowercase SHA-256.")
        submission_id = client_submission_id.strip()
        if not submission_id or len(submission_id) > 128:
            raise DbtlWorkflowRefused("A bounded client submission id is required.")
        comment = human_comment.strip()
        if len(comment) > 4_000:
            raise DbtlWorkflowRefused("A Design feedback comment cannot exceed 4000 characters.")
        card_ids = [str(value).strip() for value in selected_card_ids]
        if any(not value or len(value) > 64 for value in card_ids) or len(card_ids) != len(set(card_ids)):
            raise DbtlWorkflowRefused("Selected Design card ids must be unique bounded identifiers.")

        normalized = {
            "surface_id": surface_id,
            "action_kind": action_kind,
            "selected_card_ids": card_ids,
            "human_comment": comment,
            "expected_db_revision": expected_db_revision,
            "expected_evidence": expected_evidence,
            "expected_deck_hash": expected_deck_hash,
            "difficulty_override": difficulty_override,
        }
        payload_hash = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        action_group = _ACTION_GROUP[action_kind]

        async with self._sf() as session:  # type: ignore[attr-defined]
            surface = await session.scalar(
                select(DbtlDesignFeedbackSurfaceRow).where(
                    DbtlDesignFeedbackSurfaceRow.id == surface_id,
                    DbtlDesignFeedbackSurfaceRow.project_id == project_id,
                    DbtlDesignFeedbackSurfaceRow.cycle_id == cycle_id,
                )
            )
            if surface is None:
                raise DesignFeedbackConflict("Feedback surface not found.")
            try:
                validate_stage_feedback_intent(surface.stage, action_kind)
            except ValueError as exc:
                raise DbtlWorkflowRefused(str(exc)) from exc
            if surface.superseded_by_surface_id is not None:
                raise DesignFeedbackConflict("A newer Design feedback deck replaced this one.")
            if surface.originating_thread_id != originating_thread_id:
                raise DesignFeedbackConflict("This deck can only answer in the conversation where the meeting started.")
            if surface.deck_content_hash != expected_deck_hash:
                raise DesignFeedbackConflict("The deck bytes no longer match the registered feedback surface.")

            existing = await session.scalar(
                select(DbtlDesignFeedbackActionRow).where(
                    DbtlDesignFeedbackActionRow.surface_id == surface_id,
                    DbtlDesignFeedbackActionRow.action_group == action_group,
                )
            )
            existing_receipt = dict(existing.receipt or {}) if existing is not None else {}
            if (
                existing is not None
                and existing.status == "handoff_failed"
                and existing_receipt.get("handoff_status") == "failed"
            ):
                # The review itself is already committed. Retrying this exact
                # payload only redelivers its next-stage card; it must not rebind
                # the verdict to a newer cycle revision or execute review_stage
                # again. The original payload hash includes that reviewed
                # revision, so any changed answer still fails closed.
                if existing.payload_hash != payload_hash or existing.id != submission_id:
                    raise DesignFeedbackConflict(
                        "The recorded approval can retry its handoff only with the exact original payload."
                    )
                existing.status = "pending"
                existing.failure_code = None
                existing.receipt = {
                    **existing_receipt,
                    "handoff_status": "retrying",
                    "message": "The recorded approval's next-stage prompt is being retried.",
                }
                await session.commit()
                return self._surface_payload(surface), self._action_payload(existing), True
            if (
                existing is not None
                and existing.status == "pending"
                and existing_receipt.get("handoff_status") == "retrying"
            ):
                raise DesignFeedbackConflict("The approval handoff retry is already in progress.")

            cycle = await session.scalar(
                select(DbtlCycleRow).where(
                    DbtlCycleRow.id == cycle_id,
                    DbtlCycleRow.project_id == project_id,
                )
            )
            if cycle is None:
                raise DesignFeedbackConflict("Cycle not found.")
            if cycle.db_revision != expected_db_revision:
                raise DesignFeedbackConflict("The cycle changed since this deck state was loaded.")

            if action_group == "chair_response":
                if surface.mode != "chair_feedback" or not surface.human_input_request_id:
                    raise DesignFeedbackConflict("This deck is not bound to an unanswered chair request.")
                request = surface.decision_request or {}
                options = request.get("options") if isinstance(request, dict) else None
                option_ids = {str(item.get("id")) for item in (options or []) if isinstance(item, dict) and item.get("id")}
                if action_kind == "chair_option" and (len(card_ids) != 1 or card_ids[0] not in option_ids):
                    raise DesignFeedbackConflict("That option was not offered by the recorded chair result.")
                if action_kind == "chair_option" and card_ids[0].lower() == "other" and not comment:
                    raise DesignFeedbackConflict("The Other chair option requires a comment.")
                if action_kind == "chair_text" and card_ids:
                    raise DesignFeedbackConflict("A free-text chair answer cannot select option cards.")
            else:
                if surface.mode != "stage_review" or surface.evidence_artifact_id is None:
                    raise DesignFeedbackConflict("This deck is not bound to reviewable Design evidence.")
                request_payload = surface.decision_request or {}
                transition_gate = request_payload.get("transition_gate") if isinstance(request_payload, dict) else None
                assessment = transition_gate.get("assessment") if isinstance(transition_gate, dict) else None
                assessed_difficulty = str(assessment.get("difficulty") or "") if isinstance(assessment, dict) else ""
                effective_difficulty = difficulty_override or assessed_difficulty
                routes = transition_gate.get("routes") if isinstance(transition_gate, dict) else []

                def route_is_available(slug: str) -> bool:
                    return any(isinstance(route, dict) and route.get("slug") == slug and not bool(route.get("blocked")) for route in (routes if isinstance(routes, list) else []))

                # Policy checks happen before the single-use ledger row is
                # inserted. A refused click must not consume the deck and
                # prevent the reviewer from correcting their choice.
                if action_kind == "advance" and not route_is_available("advance"):
                    raise DesignFeedbackConflict("Continue to Build is currently blocked.")
                if action_kind == "park" and not route_is_available("park"):
                    raise DesignFeedbackConflict("Park is not a legal route from this gate.")
                # Depth changes how carefully a verdict must be justified, not
                # how many actions it takes to record one. High stakes is the
                # one place it still costs the reviewer something: their own
                # words, on every approving or ending verdict.
                if action_kind in {"advance", "approve", "request_changes", "reject"} and effective_difficulty == "high_stakes" and not comment:
                    raise DesignFeedbackConflict("A high-stakes Design verdict requires the reviewer's written rationale.")
                evidence = expected_evidence or {}
                exact = {
                    "artifact_id": surface.evidence_artifact_id,
                    "revision": surface.evidence_artifact_revision,
                    "content_hash": surface.evidence_content_hash,
                }
                if evidence != exact:
                    raise DesignFeedbackConflict("The submitted evidence binding differs from the deck's evidence.")
                artifact = await self._latest_reviewable_artifact(
                    session,
                    surface.stage_attempt_id,
                    surface.stage,
                )
                if artifact is None or artifact.id != surface.evidence_artifact_id or artifact.revision != surface.evidence_artifact_revision or artifact.content_hash != surface.evidence_content_hash:
                    raise DesignFeedbackConflict(
                        f"The {surface.stage.title()} evidence changed after this deck was rendered."
                    )
                if action_kind == "request_changes":
                    request_payload = surface.decision_request or {}
                    issue_ids = {str(value) for value in request_payload.get("review_issue_ids", []) if isinstance(value, str)}
                    if card_ids and not set(card_ids) <= issue_ids:
                        raise DesignFeedbackConflict("Request changes must select issues shown in this deck.")
                    if not card_ids and not comment:
                        raise DesignFeedbackConflict("Request changes requires a selected issue or a comment.")
                if action_kind in {"advance", "park"} and card_ids:
                    raise DesignFeedbackConflict("A progressive route action cannot select issue cards.")

            if existing is not None:
                same_failed_retry = bool(
                    action_group == "chair_response"
                    and existing.status == "failed"
                    and existing.id == submission_id
                    and existing.action_kind == action_kind
                    and list(existing.selected_card_ids or []) == card_ids
                    and (existing.human_comment or "") == comment
                    and existing.expected_evidence == expected_evidence
                    and existing.expected_deck_hash == expected_deck_hash
                )
                if same_failed_retry:
                    # A resumed chair worker is itself durable audit work, so a
                    # failed attempt advances the cycle revision even though it
                    # produces no successor deck. Retrying the exact same
                    # payload under the same client submission id may bind to
                    # that freshly-read revision; changing the answer, deck,
                    # evidence, or id is still a conflict. This is a retry of
                    # one decision, not a second answer or a stale rebase.
                    existing.payload_hash = payload_hash
                    existing.expected_db_revision = expected_db_revision
                    existing.status = "pending"
                    existing.failure_code = None
                    await session.commit()
                    return self._surface_payload(surface), self._action_payload(existing), True
                if existing.payload_hash != payload_hash:
                    raise DesignFeedbackConflict("This feedback step was already answered with a different payload.")
                return self._surface_payload(surface), self._action_payload(existing), True

            row = DbtlDesignFeedbackActionRow(
                id=submission_id,
                project_id=project_id,
                cycle_id=cycle_id,
                surface_id=surface_id,
                stage=surface.stage,
                action_group=action_group,
                action_kind=action_kind,
                payload_hash=payload_hash,
                selected_card_ids=card_ids,
                human_comment=comment or None,
                expected_db_revision=expected_db_revision,
                expected_evidence=expected_evidence,
                expected_deck_hash=expected_deck_hash,
                status="pending",
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                winner = await session.scalar(
                    select(DbtlDesignFeedbackActionRow).where(
                        DbtlDesignFeedbackActionRow.surface_id == surface_id,
                        DbtlDesignFeedbackActionRow.action_group == action_group,
                    )
                )
                if winner is None or winner.payload_hash != payload_hash:
                    raise DesignFeedbackConflict("This feedback step was accepted from another tab.") from None
                return self._surface_payload(surface), self._action_payload(winner), True
            return self._surface_payload(surface), self._action_payload(row), False

    async def reserve_design_feedback_action(self, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Compatibility wrapper for callers using the former Design name."""
        return await self.reserve_stage_feedback_action(**kwargs)

    async def update_stage_feedback_action(
        self,
        action_id: str,
        *,
        project_id: str,
        status: str,
        run_id: str | None = None,
        receipt: dict[str, Any] | None = None,
        failure_code: str | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session:  # type: ignore[attr-defined]
            row = await session.scalar(
                select(DbtlDesignFeedbackActionRow).where(
                    DbtlDesignFeedbackActionRow.id == action_id,
                    DbtlDesignFeedbackActionRow.project_id == project_id,
                )
            )
            if row is None:
                raise DesignFeedbackConflict("Design feedback action not found.")
            row.status = status
            row.run_id = run_id or row.run_id
            row.receipt = receipt if receipt is not None else row.receipt
            row.failure_code = failure_code
            await session.commit()
            return self._action_payload(row)

    async def update_design_feedback_action(self, action_id: str, **kwargs: Any) -> dict[str, Any]:
        """Compatibility wrapper for callers using the former Design name."""
        return await self.update_stage_feedback_action(action_id, **kwargs)

    async def stage_feedback_actions(self, surface_id: str, *, project_id: str) -> list[dict[str, Any]]:
        async with self._sf() as session:  # type: ignore[attr-defined]
            rows = (
                await session.execute(
                    select(DbtlDesignFeedbackActionRow)
                    .where(
                        DbtlDesignFeedbackActionRow.surface_id == surface_id,
                        DbtlDesignFeedbackActionRow.project_id == project_id,
                    )
                    .order_by(DbtlDesignFeedbackActionRow.created_at.asc())
                )
            ).scalars()
            return [self._action_payload(row) for row in rows]

    async def design_feedback_actions(self, surface_id: str, *, project_id: str) -> list[dict[str, Any]]:
        """Compatibility wrapper for callers using the former Design name."""
        return await self.stage_feedback_actions(surface_id, project_id=project_id)

    @staticmethod
    async def _live_surfaces(
        session: AsyncSession,
        stage_attempt_id: str,
        *,
        stage: str,
        exclude_id: str,
    ) -> list[DbtlDesignFeedbackSurfaceRow]:
        result = await session.execute(
            select(DbtlDesignFeedbackSurfaceRow).where(
                DbtlDesignFeedbackSurfaceRow.stage_attempt_id == stage_attempt_id,
                DbtlDesignFeedbackSurfaceRow.stage == stage,
                DbtlDesignFeedbackSurfaceRow.superseded_by_surface_id.is_(None),
                DbtlDesignFeedbackSurfaceRow.id != exclude_id,
            )
        )
        return list(result.scalars())

    async def get_design_feedback_surface(self, surface_id: str, *, project_id: str) -> dict[str, Any] | None:
        """Compatibility wrapper for the generalized stage surface lookup."""
        return await self.get_stage_feedback_surface(surface_id, project_id=project_id)

    async def get_stage_feedback_surface(self, surface_id: str, *, project_id: str) -> dict[str, Any] | None:
        """Resolve one surface within its own project. Never leaks across."""
        async with self._sf() as session:  # type: ignore[attr-defined]
            row = await session.scalar(
                select(DbtlDesignFeedbackSurfaceRow).where(
                    DbtlDesignFeedbackSurfaceRow.id == surface_id,
                    DbtlDesignFeedbackSurfaceRow.project_id == project_id,
                )
            )
            return self._surface_payload(row) if row is not None else None

    async def latest_design_feedback_surface(
        self,
        *,
        project_id: str,
        cycle_id: str,
        stage_attempt_id: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any] | None:
        """Compatibility wrapper for the generalized latest-surface lookup."""
        return await self.latest_stage_feedback_surface(
            project_id=project_id,
            cycle_id=cycle_id,
            stage="design",
            stage_attempt_id=stage_attempt_id,
            mode=mode,
        )

    async def latest_stage_feedback_surface(
        self,
        *,
        project_id: str,
        cycle_id: str,
        stage: str,
        stage_attempt_id: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any] | None:
        """The newest surface for a cycle and stage, for stale-deck forwarding.

        ``mode`` narrows that to one kind of surface. Callers deciding whether a
        deck may still *act* must pass ``stage_review``: supersession records
        what a person was most recently shown, and a later deck that grants
        nothing — a round that produced no package renders ``read_only`` — must
        not be read as revoking the reviewable package's own deck, or a Design
        awaiting a verdict becomes undecidable.
        """
        async with self._sf() as session:  # type: ignore[attr-defined]
            statement = select(DbtlDesignFeedbackSurfaceRow).where(
                DbtlDesignFeedbackSurfaceRow.project_id == project_id,
                DbtlDesignFeedbackSurfaceRow.cycle_id == cycle_id,
                DbtlDesignFeedbackSurfaceRow.stage == stage,
            )
            if stage_attempt_id is not None:
                statement = statement.where(DbtlDesignFeedbackSurfaceRow.stage_attempt_id == stage_attempt_id)
            if mode is not None:
                statement = statement.where(DbtlDesignFeedbackSurfaceRow.mode == mode)
            row = await session.scalar(statement.order_by(DbtlDesignFeedbackSurfaceRow.created_at.desc(), DbtlDesignFeedbackSurfaceRow.id.desc()).limit(1))
            return self._surface_payload(row) if row is not None else None
