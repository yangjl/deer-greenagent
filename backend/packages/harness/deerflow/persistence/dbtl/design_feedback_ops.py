"""Durable descriptors for rendered Design feedback decks.

The deck is HTML a workflow produced and a browser renders in an opaque-origin
iframe. Nothing about the file itself distinguishes it from any other page an
agent wrote, so before a parent application may treat one as a Design surface it
has to be able to ask a server: *did you produce these exact bytes, for which
cycle, from which evidence, and which conversation may they answer?* That is all
this table answers. It grants no authority on its own — an interactive deck also
needs the authenticated bridge and the human-review transitions, neither of
which exists yet.

Two rules shape the write path. **Server-owned binding**: the revision,
projection hash, and policy version are read off the cycle rather than accepted
from the caller, because a deck that could assert what it was rendered against
could assert that a stale one is current. **Supersede, never mutate**: a
regenerated deck writes a new row and points the old one at it, since the old
row is the record of what somebody was actually shown and a review may already
refer to it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deerflow.persistence.dbtl.model import (
    DbtlArtifactRow,
    DbtlCycleRow,
    DbtlDesignFeedbackSurfaceRow,
    DbtlStageAttemptRow,
)

#: What a surface may be used for. ``read_only`` exists for a deck that is worth
#: showing but must never collect anything — a historical round, or one whose
#: evidence has moved on.
SURFACE_MODES = ("chair_feedback", "stage_review", "read_only")

#: Bumped when the rendered deck's structure changes in a way a parent bridge
#: would have to understand. A review records it beside the content hash, so a
#: reader can tell a re-render from a redesign.
DECK_SCHEMA_VERSION = "1"


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


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
            "design_round": row.design_round,
            "originating_thread_id": row.originating_thread_id,
            "mode": row.mode,
            "chair_worker_run_id": row.chair_worker_run_id,
            "human_input_request_id": row.human_input_request_id,
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
            "created_at": _iso(row.created_at),
        }

    async def register_design_feedback_surface(
        self,
        *,
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
        evidence_artifact_id: str | None = None,
        evidence_artifact_revision: int | None = None,
        evidence_content_hash: str | None = None,
    ) -> dict[str, Any]:
        """Record that this workflow produced this deck, and supersede the last.

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
        if evidence_bound and not _is_sha256(evidence_content_hash):
            raise DbtlWorkflowRefused("Bound evidence needs its lowercase SHA-256 content hash.")

        async with self._sf() as session:  # type: ignore[attr-defined]
            cycle = await session.scalar(
                select(DbtlCycleRow).where(
                    DbtlCycleRow.id == cycle_id,
                    DbtlCycleRow.project_id == project_id,
                )
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

            if evidence_bound:
                artifact = await session.scalar(
                    select(DbtlArtifactRow).where(
                        DbtlArtifactRow.id == evidence_artifact_id,
                        DbtlArtifactRow.cycle_id == cycle_id,
                    )
                )
                if artifact is None:
                    raise DbtlWorkflowRefused("That evidence artifact does not belong to this cycle.")

            existing = await session.scalar(
                select(DbtlDesignFeedbackSurfaceRow).where(
                    DbtlDesignFeedbackSurfaceRow.stage_attempt_id == stage_attempt_id,
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
                design_round=max(1, int(design_round)),
                originating_thread_id=thread_id,
                mode=mode,
                chair_worker_run_id=chair_worker_run_id,
                human_input_request_id=human_input_request_id,
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
            for stale in await self._live_surfaces(session, stage_attempt_id, exclude_id=row.id):
                stale.superseded_by_surface_id = row.id

            await session.commit()
            return self._surface_payload(row)

    @staticmethod
    async def _live_surfaces(
        session: AsyncSession,
        stage_attempt_id: str,
        *,
        exclude_id: str,
    ) -> list[DbtlDesignFeedbackSurfaceRow]:
        result = await session.execute(
            select(DbtlDesignFeedbackSurfaceRow).where(
                DbtlDesignFeedbackSurfaceRow.stage_attempt_id == stage_attempt_id,
                DbtlDesignFeedbackSurfaceRow.superseded_by_surface_id.is_(None),
                DbtlDesignFeedbackSurfaceRow.id != exclude_id,
            )
        )
        return list(result.scalars())

    async def get_design_feedback_surface(self, surface_id: str, *, project_id: str) -> dict[str, Any] | None:
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
    ) -> dict[str, Any] | None:
        """The newest surface for a cycle, for pointing a stale deck forward."""
        async with self._sf() as session:  # type: ignore[attr-defined]
            statement = select(DbtlDesignFeedbackSurfaceRow).where(
                DbtlDesignFeedbackSurfaceRow.project_id == project_id,
                DbtlDesignFeedbackSurfaceRow.cycle_id == cycle_id,
            )
            if stage_attempt_id is not None:
                statement = statement.where(DbtlDesignFeedbackSurfaceRow.stage_attempt_id == stage_attempt_id)
            row = await session.scalar(statement.order_by(DbtlDesignFeedbackSurfaceRow.created_at.desc(), DbtlDesignFeedbackSurfaceRow.id.desc()).limit(1))
            return self._surface_payload(row) if row is not None else None
