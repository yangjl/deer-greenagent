"""Append-only stage-graph transition records (progressive-gate Phase 0).

A cycle's path history is the ``dbtl_stage_transitions`` table ordered by
``seq``. Rows are written inside the same session/transaction as the gate
decision they record — a review verdict, a Test validity route, or a cycle
closure — so a committed decision and its path edge cannot disagree. There is
deliberately no update or delete operation: each row is the record of a
decision somebody actually took, and rewriting one would rewrite the audit.

Writes are unconditional (not gated on ``dbtl.progressive_gate``): the flag
gates only the read model and UI, so toggling it never creates an audit gap.
Reconciliation reviews never produce a row — reconciliation is a Build-edge
precondition, not a stage on the path.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from deerflow.dbtl.stage_routes import GRAPH_STAGES, StageRoutesRefused, transition_target
from deerflow.persistence.dbtl.model import DbtlCycleRow, DbtlStageAttemptRow, DbtlStageTransitionRow
from deerflow.utils.time import coerce_iso

logger = logging.getLogger(__name__)


class TransitionOpsMixin:
    """Requires ``self._sf`` (session factory) from the host repository."""

    async def _append_stage_transition(
        self,
        session: AsyncSession,
        *,
        cycle: DbtlCycleRow,
        from_stage: str,
        chosen_route: str,
        decided_by: str,
        stage_attempt: DbtlStageAttemptRow | None = None,
        to_stage: str | None = None,
        evidence_hash: str | None = None,
        decision_surface_id: str | None = None,
        offered_routes: list[str] | None = None,
        assessed_difficulty: str | None = None,
        assessment_rationale: str | None = None,
        human_override: str | None = None,
    ) -> None:
        """Record one decided edge in the caller's open transaction.

        The row commits or rolls back with the decision itself, so the two
        cannot disagree. Only a route this module has no edge for is skipped
        (logged): a non-graph stage or unmapped route is not a path event.
        """
        if from_stage not in GRAPH_STAGES:
            return
        try:
            target = to_stage if to_stage is not None else transition_target(from_stage, chosen_route)
        except StageRoutesRefused:
            logger.warning("No stage-graph edge for route %r from stage %r; transition not recorded", chosen_route, from_stage)
            return
        next_seq = (await session.scalar(select(func.count()).select_from(DbtlStageTransitionRow).where(DbtlStageTransitionRow.cycle_id == cycle.id)) or 0) + 1
        session.add(
            DbtlStageTransitionRow(
                id=f"dst-{cycle.id}-{next_seq}",
                project_id=cycle.project_id,
                cycle_id=cycle.id,
                seq=next_seq,
                from_stage=from_stage,
                from_attempt=stage_attempt.attempt_number if stage_attempt is not None else None,
                stage_attempt_id=stage_attempt.id if stage_attempt is not None else None,
                chosen_route=chosen_route,
                to_stage=target,
                assessed_difficulty=assessed_difficulty,
                assessment_rationale=assessment_rationale,
                human_override=human_override,
                offered_routes=offered_routes,
                decided_by=decided_by,
                decision_surface_id=decision_surface_id,
                evidence_hash=evidence_hash,
                dataset_fingerprint=stage_attempt.approved_dataset_fingerprint if stage_attempt is not None else None,
                stage_spec_version=stage_attempt.stage_spec_key if stage_attempt is not None else None,
                policy_version=cycle.policy_version,
                backfilled=False,
                decided_at=datetime.now(UTC),
            )
        )

    async def list_stage_transitions(self, *, cycle_id: str, project_id: str) -> list[dict[str, Any]]:
        """The cycle's path history, ordered by ``seq``."""
        async with self._sf() as session:  # type: ignore[attr-defined]
            rows = (await session.execute(select(DbtlStageTransitionRow).where(DbtlStageTransitionRow.cycle_id == cycle_id, DbtlStageTransitionRow.project_id == project_id).order_by(DbtlStageTransitionRow.seq))).scalars().all()
            return [_transition_payload(row) for row in rows]


def _transition_payload(row: DbtlStageTransitionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "cycle_id": row.cycle_id,
        "seq": row.seq,
        "from_stage": row.from_stage,
        "from_attempt": row.from_attempt,
        "stage_attempt_id": row.stage_attempt_id,
        "chosen_route": row.chosen_route,
        "to_stage": row.to_stage,
        "assessed_difficulty": row.assessed_difficulty,
        "assessment_rationale": row.assessment_rationale,
        "human_override": row.human_override,
        "offered_routes": row.offered_routes,
        "decided_by": row.decided_by,
        "decision_surface_id": row.decision_surface_id,
        "evidence_hash": row.evidence_hash,
        "dataset_fingerprint": row.dataset_fingerprint,
        "stage_spec_version": row.stage_spec_version,
        "policy_version": row.policy_version,
        "backfilled": row.backfilled,
        "decided_at": coerce_iso(row.decided_at) if isinstance(row.decided_at, datetime) else row.decided_at,
    }
