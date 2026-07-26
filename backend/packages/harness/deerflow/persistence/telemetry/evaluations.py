"""Repository for classifier shadow-evaluation telemetry (Phase 4).

Two write paths and nothing else: record what the classifier concluded, then
record what the human did about it. There is no DBTL model imported here, so
this repository has no way to touch a cycle — the separation the plan asks for
is enforced by what the module can reach, not by convention.

Both writes are first-writer-wins. A replayed evaluation must not overwrite
the evidence a reviewer already saw, and a late second click on a card must not
rewrite the choice that was actually made first; either would silently corrupt
the false-upgrade and missed-cycle rates the exit review is built on.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from deerflow.persistence.telemetry.model import ClassifierEvaluationRow

logger = logging.getLogger(__name__)

DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500

# A human choice that means "this should have been a cycle".
_UPGRADE_CHOICES = frozenset({"start_setup"})
# A human choice that means "this should have stayed ordinary".
_ORDINARY_CHOICES = frozenset({"keep_ordinary", "dismissed"})


class ClassifierEvaluationConflict(ValueError):
    """One idempotency key was reused for different request content."""


def _serialize(row: ClassifierEvaluationRow) -> dict[str, Any]:
    return {
        "evaluation_id": row.id,
        "project_id": row.project_id,
        "thread_id": row.thread_id,
        "user_id": row.user_id,
        "route_kind": row.route_kind,
        "route_source": row.route_source,
        "band": row.band,
        "confidence": row.confidence,
        "rule_hits": list(row.rule_hits_json or []),
        "missing_fields": list(row.missing_fields_json or []),
        "proposed_objective": row.proposed_objective,
        "policy_version": row.policy_version,
        "human_choice": row.human_choice,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "created_at": row.created_at.isoformat(),
    }


class ClassifierEvaluationRepository:
    """Append-mostly telemetry store, scoped to one project per call."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def record_evaluation(
        self,
        *,
        evaluation_id: str,
        project_id: str,
        thread_id: str | None,
        user_id: str,
        route_kind: str,
        route_source: str,
        request_fingerprint: str,
        band: str,
        confidence: float,
        rule_hits: Sequence[dict[str, Any]],
        missing_fields: Sequence[str],
        proposed_objective: str,
        policy_version: str,
    ) -> dict[str, Any]:
        """Record one shadow evaluation. Replays return the stored row."""
        async with self._session_factory() as session:
            existing = await session.get(ClassifierEvaluationRow, evaluation_id)
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise ClassifierEvaluationConflict("This evaluation idempotency key was already used for a different request.")
                return _serialize(existing)

            row = ClassifierEvaluationRow(
                id=evaluation_id,
                project_id=project_id,
                thread_id=thread_id,
                user_id=user_id,
                route_kind=str(route_kind),
                route_source=str(route_source),
                request_fingerprint=request_fingerprint,
                band=str(band),
                confidence=float(confidence),
                rule_hits_json=[dict(hit) for hit in rule_hits],
                missing_fields_json=list(missing_fields),
                proposed_objective=proposed_objective[:240],
                policy_version=policy_version,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # Two concurrent sends of the same evaluation: the loser reads
                # the winner's row rather than raising at the caller.
                await session.rollback()
                stored = await session.get(ClassifierEvaluationRow, evaluation_id)
                if stored is None:
                    raise
                if stored.request_fingerprint != request_fingerprint:
                    raise ClassifierEvaluationConflict("This evaluation idempotency key was already used for a different request.")
                return _serialize(stored)
            return _serialize(row)

    async def record_outcome(
        self,
        *,
        evaluation_id: str,
        project_id: str,
        user_id: str,
        outcome: str,
    ) -> dict[str, Any] | None:
        """Attach the human's choice. ``None`` when the row is not theirs.

        Project and user are both checked so one person's dismissal cannot be
        recorded against another person's card, and a card cannot be answered
        from outside the project it belongs to.
        """
        async with self._session_factory() as session:
            # One conditional UPDATE is the arbiter. A SELECT followed by an
            # UPDATE lets two concurrent clicks both observe NULL and makes
            # the last commit win, contradicting the audit trail.
            result = await session.execute(
                update(ClassifierEvaluationRow)
                .where(
                    ClassifierEvaluationRow.id == evaluation_id,
                    ClassifierEvaluationRow.project_id == project_id,
                    ClassifierEvaluationRow.user_id == user_id,
                    ClassifierEvaluationRow.human_choice.is_(None),
                )
                .values(human_choice=str(outcome), decided_at=datetime.now(UTC))
            )
            if result.rowcount:
                await session.commit()
            else:
                await session.rollback()

            row = await session.get(ClassifierEvaluationRow, evaluation_id)
            if row is None or row.project_id != project_id or row.user_id != user_id:
                return None
            return _serialize(row)

    async def list_evaluations(self, project_id: str, *, limit: int = DEFAULT_LIST_LIMIT) -> list[dict[str, Any]]:
        """Newest first, bounded — this feeds the internal evaluation drawer."""
        bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
        async with self._session_factory() as session:
            result = await session.execute(select(ClassifierEvaluationRow).where(ClassifierEvaluationRow.project_id == project_id).order_by(ClassifierEvaluationRow.created_at.desc(), ClassifierEvaluationRow.id.desc()).limit(bounded))
            return [_serialize(row) for row in result.scalars().all()]

    async def evaluation_stats(self, project_id: str) -> dict[str, int]:
        """The two rates the human exit review has to approve.

        A *false upgrade* is a request the system proposed and the human kept
        ordinary. A *missed cycle* is the reverse: nothing was proposed and the
        human started a cycle anyway. Both are counted from stored rows so the
        review does not depend on anyone's recollection.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    ClassifierEvaluationRow.route_kind,
                    ClassifierEvaluationRow.route_source,
                    ClassifierEvaluationRow.human_choice,
                    func.count().label("total"),
                )
                .where(ClassifierEvaluationRow.project_id == project_id)
                .group_by(
                    ClassifierEvaluationRow.route_kind,
                    ClassifierEvaluationRow.route_source,
                    ClassifierEvaluationRow.human_choice,
                )
            )
            rows = result.all()

        stats = {
            "total": 0,
            "proposed": 0,
            "classifier_ordinary": 0,
            "decided": 0,
            "false_upgrades": 0,
            "missed_cycles": 0,
        }
        for route_kind, route_source, human_choice, total in rows:
            count = int(total)
            stats["total"] += count
            proposed = route_kind == "proposal" and route_source == "classifier"
            classifier_ordinary = route_kind == "ordinary" and route_source == "classifier"
            if proposed:
                stats["proposed"] += count
            if classifier_ordinary:
                stats["classifier_ordinary"] += count
            if human_choice is None:
                continue
            stats["decided"] += count
            if proposed and human_choice in _ORDINARY_CHOICES:
                stats["false_upgrades"] += count
            elif classifier_ordinary and human_choice in _UPGRADE_CHOICES:
                stats["missed_cycles"] += count
        return stats
