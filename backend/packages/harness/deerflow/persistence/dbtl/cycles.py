"""Durable DBTL cycle workflow (Phase 3).

Humans drive a cycle through the same contracts the later Supervisor Graph
will call. Three separate mechanisms keep that safe, because they defend
against three different failures:

*revision*      — ``expected_db_revision`` loses a race between two reviewers
                  looking at the same screen;
*idempotency*   — a replayed request returns the prior result instead of
                  applying twice;
*constraint*    — the partial unique index, not an application check, is what
                  stops two concurrent "Start a cycle" clicks.

Every mutation writes an ``activity_events`` row carrying the actor and the
revision it committed, which is what the human exit review inspects.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.dbtl.cycle_state import (
    STAGE_ORDER,
    TERMINAL_CYCLE_STATES,
    ReviewDecision,
    StageStatus,
    TransitionRefused,
    apply_review,
    can_enter_stage,
    initial_stage_statuses,
    is_terminal,
    next_cycle_state,
    stage_for_state,
    validate_cycle_class,
)
from deerflow.dbtl.stage_routes import GRAPH_STAGES
from deerflow.persistence.dbtl.build_test_ops import BuildTestOpsMixin
from deerflow.persistence.dbtl.design_feedback_ops import DesignFeedbackOpsMixin
from deerflow.persistence.dbtl.knowledge_ops import KnowledgeOpsMixin
from deerflow.persistence.dbtl.model import (
    DbtlArtifactRow,
    DbtlCycleRow,
    DbtlEventRow,
    DbtlReviewRow,
    DbtlStageAttemptRow,
    WorkItemRow,
)
from deerflow.persistence.dbtl.reconciliation_ops import RECONCILIATION_KIND, ReconciliationOpsMixin
from deerflow.persistence.dbtl.sql import projection_hash
from deerflow.persistence.dbtl.transition_ops import TransitionOpsMixin
from deerflow.utils.time import coerce_iso

logger = logging.getLogger(__name__)

WORK_ITEM_KINDS = frozenset({"blocker", "task", "question"})
CYCLE_WEIGHTS = frozenset({"full", "light", "retroactive"})

# Descriptive fields stored alongside the hashed projection. They describe the
# research record rather than its state, so every revision must carry them.
_DETAIL_KEYS = ("research_question", "objective", "success_criteria", "cycle_weight", "idempotency_key")


class DbtlWorkflowRefused(ValueError):
    """The requested workflow action is not legal for this cycle."""


class DbtlRevisionConflict(ValueError):
    """The caller acted on a revision that is no longer current."""


def _iso(value: Any) -> Any:
    return coerce_iso(value) if isinstance(value, datetime) else value


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DbtlCycleRepository(KnowledgeOpsMixin, BuildTestOpsMixin, DesignFeedbackOpsMixin, ReconciliationOpsMixin, TransitionOpsMixin):
    """Read and mutate durable DBTL cycles for one deployment.

    Phase 6's data-readiness operations live in
    :class:`~deerflow.persistence.dbtl.reconciliation_ops.ReconciliationOpsMixin`
    rather than a sibling repository: declared datasets, matrix rows, and worker
    runs belong to the same cycle aggregate and are guarded by the same
    ``db_revision`` and the same activity-event idempotency ledger. Splitting
    them across two repositories would mean two copies of that machinery, and
    the first divergence between them would show up as a lost review.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # -- projection ------------------------------------------------------

    @staticmethod
    def _stage_payload(row: DbtlStageAttemptRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "stage": row.stage,
            "status": row.status,
            "attempt_number": row.attempt_number,
            "db_revision": row.db_revision,
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _statuses(stages: list[DbtlStageAttemptRow]) -> dict[str, StageStatus]:
        return {row.stage: StageStatus(row.status) for row in stages}

    @classmethod
    def _cycle_payload(
        cls,
        cycle: DbtlCycleRow,
        stages: list[DbtlStageAttemptRow],
        *,
        artifacts: list[DbtlArtifactRow] | None = None,
        work_items: list[WorkItemRow] | None = None,
    ) -> dict[str, Any]:
        detail = dict(cycle.projection_json or {})
        payload: dict[str, Any] = {
            "id": cycle.id,
            "project_id": cycle.project_id,
            "parent_cycle_id": cycle.parent_cycle_id,
            "title": cycle.title,
            "cycle_class": cycle.cycle_class,
            "cycle_weight": detail.get("cycle_weight", "full"),
            "state": cycle.state,
            "policy_version": cycle.policy_version,
            "db_revision": cycle.db_revision,
            "projection_hash": cycle.projection_hash,
            "research_question": detail.get("research_question", ""),
            "objective": detail.get("objective", ""),
            "success_criteria": detail.get("success_criteria", ""),
            "created_by": cycle.created_by,
            "created_at": _iso(cycle.created_at),
            "updated_at": _iso(cycle.updated_at),
            "stages": [cls._stage_payload(row) for row in sorted(stages, key=lambda item: STAGE_ORDER.index(item.stage))],
        }
        if artifacts is not None:
            payload["artifacts"] = [
                {
                    "id": row.id,
                    "stage_attempt_id": row.stage_attempt_id,
                    "artifact_type": row.artifact_type,
                    "revision": row.revision,
                    "content_hash": row.content_hash,
                    "uri": row.uri,
                    "created_by": row.created_by,
                    "created_at": _iso(row.created_at),
                }
                for row in artifacts
            ]
        if work_items is not None:
            payload["work_items"] = [cls._work_item_payload(row) for row in work_items]
        return payload

    @staticmethod
    def _work_item_payload(row: WorkItemRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "cycle_id": row.cycle_id,
            "title": row.title,
            "status": row.status,
            "owner_role": row.owner_role,
            "payload": dict(row.payload or {}),
            "db_revision": row.db_revision,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _projection(cycle: DbtlCycleRow, statuses: dict[str, StageStatus]) -> dict[str, Any]:
        """The hashed view Phase 1's governance checks compare against."""
        return {
            "cycle_id": cycle.id,
            "state": cycle.state,
            "stages": {stage: str(statuses[stage]) for stage in STAGE_ORDER},
        }

    # -- internal helpers ------------------------------------------------

    async def _load(
        self,
        session: AsyncSession,
        cycle_id: str,
        project_id: str,
        *,
        for_update: bool = False,
    ) -> tuple[DbtlCycleRow, list[DbtlStageAttemptRow]] | None:
        statement = select(DbtlCycleRow).where(
            DbtlCycleRow.id == cycle_id,
            DbtlCycleRow.project_id == project_id,
        )
        if for_update:
            statement = statement.with_for_update()
        cycle = await session.scalar(statement)
        if cycle is None:
            return None
        stages = list((await session.execute(select(DbtlStageAttemptRow).where(DbtlStageAttemptRow.cycle_id == cycle_id))).scalars())
        return cycle, stages

    async def _latest_artifact(self, session: AsyncSession, stage_attempt_id: str) -> DbtlArtifactRow | None:
        """The newest artifact revision on a stage attempt, if any."""
        return await session.scalar(select(DbtlArtifactRow).where(DbtlArtifactRow.stage_attempt_id == stage_attempt_id).order_by(DbtlArtifactRow.revision.desc()).limit(1))

    async def _next_sequence(self, session: AsyncSession, cycle_id: str) -> int:
        current = await session.scalar(select(func.max(DbtlEventRow.sequence)).where(DbtlEventRow.cycle_id == cycle_id))
        return int(current or 0) + 1

    async def _record_event(
        self,
        session: AsyncSession,
        *,
        cycle: DbtlCycleRow,
        event_type: str,
        actor_user_id: str,
        payload: dict[str, Any],
    ) -> None:
        sequence = await self._next_sequence(session, cycle.id)
        session.add(
            DbtlEventRow(
                id=f"event-{cycle.id}-{sequence}",
                project_id=cycle.project_id,
                cycle_id=cycle.id,
                sequence=sequence,
                event_type=event_type,
                actor_user_id=actor_user_id,
                payload={**payload, "db_revision": cycle.db_revision},
            )
        )

    def _commit_revision(self, cycle: DbtlCycleRow, statuses: dict[str, StageStatus]) -> None:
        """Bump the revision and rebuild the hashed projection.

        The descriptive fields are carried across explicitly: rebuilding the
        projection from scratch would silently drop the research question the
        cycle exists to record.
        """
        cycle.db_revision += 1
        stored = dict(cycle.projection_json or {})
        detail = {key: stored[key] for key in _DETAIL_KEYS if key in stored}
        cycle.projection_json = {**self._projection(cycle, statuses), **detail}
        cycle.projection_hash = projection_hash(cycle.projection_json)
        cycle.updated_at = _utc_now()

    @staticmethod
    def _require_revision(cycle: DbtlCycleRow, expected: int) -> None:
        if cycle.projection_hash != projection_hash(dict(cycle.projection_json or {})):
            raise DbtlWorkflowRefused(f"Cycle {cycle.id} has a projection mismatch and cannot be mutated.")
        if cycle.db_revision != expected:
            raise DbtlRevisionConflict(f"Cycle {cycle.id} is at revision {cycle.db_revision}, not {expected}.")

    async def _replay_event(
        self,
        session: AsyncSession,
        cycle_id: str,
        idempotency_key: str,
        *,
        event_type: str,
        expected_payload: dict[str, Any],
    ) -> DbtlEventRow | None:
        existing = await session.scalar(
            select(DbtlEventRow).where(
                DbtlEventRow.cycle_id == cycle_id,
                DbtlEventRow.payload["idempotency_key"].as_string() == idempotency_key,
            )
        )
        if existing is None:
            return None
        payload = dict(existing.payload or {})
        if existing.event_type != event_type or any(payload.get(key) != value for key, value in expected_payload.items()):
            raise DbtlWorkflowRefused("This idempotency key was already used for a different workflow action.")
        return existing

    @staticmethod
    def _require_matching_create_replay(
        cycle: DbtlCycleRow,
        *,
        title: str,
        cycle_class: str,
        cycle_weight: str,
        research_question: str,
        objective: str,
        success_criteria: str,
        created_by: str,
        policy_version: str,
        parent_cycle_id: str | None,
    ) -> None:
        detail = dict(cycle.projection_json or {})
        expected = {
            "title": title.strip(),
            "cycle_class": cycle_class,
            "cycle_weight": cycle_weight,
            "research_question": research_question.strip(),
            "objective": objective.strip(),
            "success_criteria": success_criteria.strip(),
            "created_by": created_by,
            "policy_version": policy_version,
            "parent_cycle_id": parent_cycle_id,
        }
        actual = {
            "title": cycle.title,
            "cycle_class": cycle.cycle_class,
            "cycle_weight": detail.get("cycle_weight", "full"),
            "research_question": detail.get("research_question", ""),
            "objective": detail.get("objective", ""),
            "success_criteria": detail.get("success_criteria", ""),
            "created_by": cycle.created_by,
            "policy_version": cycle.policy_version,
            "parent_cycle_id": cycle.parent_cycle_id,
        }
        if actual != expected:
            raise DbtlWorkflowRefused("This idempotency key was already used to create a different cycle.")

    # -- creation --------------------------------------------------------

    async def create_cycle(
        self,
        *,
        cycle_id: str,
        project_id: str,
        title: str,
        cycle_class: str,
        research_question: str,
        objective: str,
        success_criteria: str,
        created_by: str,
        policy_version: str,
        idempotency_key: str,
        cycle_weight: str = "full",
        parent_cycle_id: str | None = None,
    ) -> dict[str, Any]:
        """Open a durable research record. Refuses rather than guesses."""
        validate_cycle_class(cycle_class)
        if cycle_weight not in CYCLE_WEIGHTS:
            raise ValueError(f"Unknown cycle weight {cycle_weight!r}; expected one of: {', '.join(sorted(CYCLE_WEIGHTS))}")
        if not title.strip():
            raise ValueError("A cycle needs a title.")
        if not research_question.strip():
            raise ValueError("A cycle needs a research question.")

        async with self._sf() as session:
            replay = await session.scalar(
                select(DbtlCycleRow).where(
                    DbtlCycleRow.project_id == project_id,
                    DbtlCycleRow.create_idempotency_key == idempotency_key,
                )
            )
            if replay is not None:
                self._require_matching_create_replay(
                    replay,
                    title=title,
                    cycle_class=cycle_class,
                    cycle_weight=cycle_weight,
                    research_question=research_question,
                    objective=objective,
                    success_criteria=success_criteria,
                    created_by=created_by,
                    policy_version=policy_version,
                    parent_cycle_id=parent_cycle_id,
                )
                _, stages = await self._load(session, replay.id, project_id)  # type: ignore[misc]
                return self._cycle_payload(replay, stages)

            if parent_cycle_id is not None:
                parent = await session.get(DbtlCycleRow, parent_cycle_id)
                if parent is None or parent.project_id != project_id:
                    raise DbtlWorkflowRefused(f"Parent cycle {parent_cycle_id!r} is not part of this project.")
                if cycle_class != "computational":
                    raise DbtlWorkflowRefused("Only a computational cycle may have a parent cycle.")
                if parent.cycle_class != "season/program" or parent.parent_cycle_id is not None:
                    raise DbtlWorkflowRefused("A parent cycle must be a top-level season/program cycle.")
                if is_terminal(parent.state):
                    raise DbtlWorkflowRefused("A completed or abandoned cycle cannot accept a new child cycle.")

            statuses = initial_stage_statuses()
            cycle = DbtlCycleRow(
                id=cycle_id,
                project_id=project_id,
                parent_cycle_id=parent_cycle_id,
                create_idempotency_key=idempotency_key,
                title=title.strip(),
                cycle_class=cycle_class,
                state=STAGE_ORDER[0],
                policy_version=policy_version,
                db_revision=1,
                projection_json={},
                projection_hash="",
                created_by=created_by,
            )
            cycle.projection_json = {
                **self._projection(cycle, statuses),
                "research_question": research_question.strip(),
                "objective": objective.strip(),
                "success_criteria": success_criteria.strip(),
                "cycle_weight": cycle_weight,
                "idempotency_key": idempotency_key,
            }
            cycle.projection_hash = projection_hash(cycle.projection_json)
            session.add(cycle)

            stages = [
                DbtlStageAttemptRow(
                    id=f"{cycle_id}-{stage}-1",
                    project_id=project_id,
                    cycle_id=cycle_id,
                    stage=stage,
                    attempt_number=1,
                    status=str(statuses[stage]),
                    db_revision=1,
                )
                for stage in STAGE_ORDER
            ]
            session.add_all(stages)
            try:
                # Flush before anything issues a SELECT: an autoflush inside a
                # later query would surface the constraint as a raw
                # IntegrityError outside this handler.
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                async with self._sf() as replay_session:
                    replay = await replay_session.scalar(
                        select(DbtlCycleRow).where(
                            DbtlCycleRow.project_id == project_id,
                            DbtlCycleRow.create_idempotency_key == idempotency_key,
                        )
                    )
                    if replay is not None:
                        self._require_matching_create_replay(
                            replay,
                            title=title,
                            cycle_class=cycle_class,
                            cycle_weight=cycle_weight,
                            research_question=research_question,
                            objective=objective,
                            success_criteria=success_criteria,
                            created_by=created_by,
                            policy_version=policy_version,
                            parent_cycle_id=parent_cycle_id,
                        )
                        loaded = await self._load(replay_session, replay.id, project_id)
                        assert loaded is not None
                        return self._cycle_payload(*loaded)
                # Parallel top-level cycles are allowed (migration 0018), so the
                # only integrity failure left here is a durable-identity clash:
                # the create-idempotency index collapsing a concurrent retry, or
                # a reused key. Either way the caller should reload rather than
                # be told a rule was broken that no longer exists.
                raise DbtlWorkflowRefused("The cycle could not be created because its durable identity conflicts. Reload and try again.") from exc

            await self._record_event(
                session,
                cycle=cycle,
                event_type="cycle.created",
                actor_user_id=created_by,
                payload={"idempotency_key": idempotency_key, "cycle_class": cycle_class},
            )
            await session.commit()
            return self._cycle_payload(cycle, stages)

    # -- reads -----------------------------------------------------------

    async def project_cycle_summary(self, project_id: str) -> dict[str, int | bool]:
        """Return the bounded lifecycle facts used by DBTL request routing."""
        async with self._sf() as session:
            total = await session.scalar(select(func.count(DbtlCycleRow.id)).where(DbtlCycleRow.project_id == project_id))
            unfinished = await session.scalar(
                select(func.count(DbtlCycleRow.id)).where(
                    DbtlCycleRow.project_id == project_id,
                    DbtlCycleRow.state.not_in(TERMINAL_CYCLE_STATES),
                )
            )
            return {
                "project_cycle_count": int(total or 0),
                "has_unfinished_cycles": bool(unfinished),
            }

    async def list_cycles(self, project_id: str) -> list[dict[str, Any]]:
        async with self._sf() as session:
            cycles = list((await session.execute(select(DbtlCycleRow).where(DbtlCycleRow.project_id == project_id).order_by(DbtlCycleRow.created_at.asc(), DbtlCycleRow.id.asc()))).scalars())
            if not cycles:
                return []
            stage_rows = list((await session.execute(select(DbtlStageAttemptRow).where(DbtlStageAttemptRow.cycle_id.in_([row.id for row in cycles])))).scalars())
            by_cycle: dict[str, list[DbtlStageAttemptRow]] = {}
            for row in stage_rows:
                by_cycle.setdefault(row.cycle_id, []).append(row)
            return [self._cycle_payload(cycle, by_cycle.get(cycle.id, [])) for cycle in cycles]

    async def get_cycle(self, cycle_id: str, *, project_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id)
            if loaded is None:
                return None
            cycle, stages = loaded
            artifacts = list((await session.execute(select(DbtlArtifactRow).where(DbtlArtifactRow.cycle_id == cycle_id).order_by(DbtlArtifactRow.created_at.asc(), DbtlArtifactRow.id.asc()))).scalars())
            work_items = list((await session.execute(select(WorkItemRow).where(WorkItemRow.cycle_id == cycle_id).order_by(WorkItemRow.created_at.asc(), WorkItemRow.id.asc()))).scalars())
            return self._cycle_payload(cycle, stages, artifacts=artifacts, work_items=work_items)

    async def list_activity(self, cycle_id: str, *, project_id: str) -> list[dict[str, Any]]:
        async with self._sf() as session:
            rows = list((await session.execute(select(DbtlEventRow).where(DbtlEventRow.cycle_id == cycle_id, DbtlEventRow.project_id == project_id).order_by(DbtlEventRow.sequence.asc()))).scalars())
            return [
                {
                    "id": row.id,
                    "sequence": row.sequence,
                    "event_type": row.event_type,
                    "actor_user_id": row.actor_user_id,
                    "payload": dict(row.payload or {}),
                    "created_at": _iso(row.created_at),
                }
                for row in rows
            ]

    async def abandon_cycle(
        self,
        *,
        cycle_id: str,
        project_id: str,
        expected_db_revision: int,
        actor_user_id: str,
        idempotency_key: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Retire a live cycle without erasing its research record."""
        reason = rationale.strip()
        if not reason:
            raise ValueError("Removing a cycle requires a rationale.")

        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id, for_update=True)
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            expected_payload = {
                "expected_db_revision": expected_db_revision,
                "actor_user_id": actor_user_id,
                "rationale": reason,
            }
            if (
                await self._replay_event(
                    session,
                    cycle_id,
                    idempotency_key,
                    event_type="cycle.abandoned",
                    expected_payload=expected_payload,
                )
                is not None
            ):
                return self._cycle_payload(cycle, stages)

            self._require_revision(cycle, expected_db_revision)
            if is_terminal(cycle.state):
                raise DbtlWorkflowRefused("A completed or abandoned cycle cannot be removed.")
            live_children = await session.scalar(
                select(func.count(DbtlCycleRow.id)).where(
                    DbtlCycleRow.parent_cycle_id == cycle.id,
                    DbtlCycleRow.state.not_in(TERMINAL_CYCLE_STATES),
                )
            )
            if live_children:
                raise DbtlWorkflowRefused("Remove this cycle's active child cycles first.")

            # The stage being worked when the cycle closed; a checkpoint state
            # (ready_for_build) closes from the stage whose gate it follows.
            closing_stage = stage_for_state(cycle.state) or ("build" if cycle.state == "ready_for_build" else None)
            cycle.state = "abandoned"
            self._commit_revision(cycle, self._statuses(stages))
            if closing_stage in GRAPH_STAGES:
                await self._append_stage_transition(
                    session,
                    cycle=cycle,
                    from_stage=closing_stage,
                    chosen_route="close_cycle",
                    decided_by=actor_user_id,
                    stage_attempt=next((row for row in stages if row.stage == closing_stage), None),
                )
            await self._record_event(
                session,
                cycle=cycle,
                event_type="cycle.abandoned",
                actor_user_id=actor_user_id,
                payload={
                    **expected_payload,
                    "idempotency_key": idempotency_key,
                },
            )
            await session.commit()
            return self._cycle_payload(cycle, stages)

    # -- stage workflow --------------------------------------------------

    async def submit_stage_for_review(
        self,
        *,
        cycle_id: str,
        project_id: str,
        stage: str,
        expected_db_revision: int,
        actor_user_id: str,
        idempotency_key: str,
        design_feedback_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Move a workable stage to ``awaiting_review``."""
        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id, for_update=True)
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            if (
                await self._replay_event(
                    session,
                    cycle_id,
                    idempotency_key,
                    event_type="stage.submitted",
                    expected_payload={
                        "stage": stage,
                        "expected_db_revision": expected_db_revision,
                        "actor_user_id": actor_user_id,
                        "design_feedback_binding": design_feedback_binding,
                    },
                )
                is not None
            ):
                return self._cycle_payload(cycle, stages)
            self._require_revision(cycle, expected_db_revision)

            statuses = self._statuses(stages)
            if not can_enter_stage(stage, cycle.state, statuses):
                raise DbtlWorkflowRefused(f"Stage {stage!r} is not open in state {cycle.state!r}.")
            if statuses[stage] not in {StageStatus.IN_PROGRESS, StageStatus.CHANGES_REQUESTED}:
                raise DbtlWorkflowRefused(f"Stage {stage!r} is {statuses[stage]} and cannot be submitted.")

            row = next(item for item in stages if item.stage == stage)
            # A review must have something to review. Without this a stage
            # could be approved on no evidence at all, which is precisely what
            # the durable review record exists to prevent.
            if await self._latest_artifact(session, row.id) is None:
                raise DbtlWorkflowRefused(f"Stage {stage!r} has no artifact to review; attach evidence first.")
            if stage == "reconciliation":
                # The readiness gate is checked *before* a reviewer is asked, not
                # only when they click approve. Sending an unresolved matrix to
                # review invites an approval on a contradiction nobody settled,
                # and the reviewer would have to notice the blocker themselves.
                gate = await self._reconciliation_gate(session, cycle_id, project_id, stages)
                if not gate.ready:
                    reasons = "; ".join(gate.reasons) or "required rows are unresolved"
                    raise DbtlWorkflowRefused(f"Data reconciliation is not ready for review ({gate.outcome.value}): {reasons}")
            if stage == "build" and await self._latest_build_lineage(session, cycle_id) is None:
                raise DbtlWorkflowRefused("Build has no reproducibility lineage to review.")
            if stage == "test" and await self._latest_build_lineage(session, cycle_id) is None:
                raise DbtlWorkflowRefused("Test cannot be reviewed without Build lineage.")
            row.status = str(StageStatus.AWAITING_REVIEW)
            statuses[stage] = StageStatus.AWAITING_REVIEW
            if stage == "build" and cycle.state == "ready_for_build":
                cycle.state = "build"
            self._commit_revision(cycle, statuses)
            row.db_revision = cycle.db_revision
            await self._record_event(
                session,
                cycle=cycle,
                event_type="stage.submitted",
                actor_user_id=actor_user_id,
                payload={
                    "idempotency_key": idempotency_key,
                    "stage": stage,
                    "expected_db_revision": expected_db_revision,
                    "actor_user_id": actor_user_id,
                    "design_feedback_binding": design_feedback_binding,
                },
            )
            await session.commit()
            return self._cycle_payload(cycle, stages)

    async def review_stage(
        self,
        *,
        cycle_id: str,
        project_id: str,
        stage: str,
        decision: str,
        rationale: str,
        expected_db_revision: int,
        reviewer_user_id: str,
        reviewer_project_role: str,
        idempotency_key: str,
        design_feedback_provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply one human verdict, advancing the cycle only when legal."""
        if not rationale.strip():
            raise ValueError("A review decision requires a rationale.")
        if stage == "test":
            raise DbtlWorkflowRefused("Test requires a typed validity assessment; the generic review path is disabled.")
        verdict = ReviewDecision(decision)

        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id, for_update=True)
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            if (
                await self._replay_event(
                    session,
                    cycle_id,
                    idempotency_key,
                    event_type="stage.reviewed",
                    expected_payload={
                        "stage": stage,
                        "decision": str(verdict),
                        "rationale": rationale.strip(),
                        "expected_db_revision": expected_db_revision,
                        "reviewer_user_id": reviewer_user_id,
                        "reviewer_project_role": reviewer_project_role,
                        "design_feedback_provenance": design_feedback_provenance,
                    },
                )
                is not None
            ):
                return self._cycle_payload(cycle, stages)
            self._require_revision(cycle, expected_db_revision)
            bound_projection_hash = cycle.projection_hash

            try:
                updated = apply_review(self._statuses(stages), stage, verdict)
            except TransitionRefused as exc:
                raise DbtlWorkflowRefused(str(exc)) from exc

            for row in stages:
                row.status = str(updated[row.stage])

            # Advance only when the machine says so; an approval that leaves a
            # prerequisite outstanding must leave the cycle where it is.
            if verdict is ReviewDecision.APPROVE and not is_terminal(cycle.state):
                try:
                    cycle.state = next_cycle_state(cycle.state, updated)
                except TransitionRefused:
                    logger.debug("Cycle %s stays in %s after approving %s", cycle.id, cycle.state, stage)

            self._commit_revision(cycle, updated)
            for row in stages:
                row.db_revision = cycle.db_revision

            stage_attempt_id = next(row.id for row in stages if row.stage == stage)
            attempt_row = next(row for row in stages if row.stage == stage)
            if verdict is ReviewDecision.APPROVE:
                # Bind the approval to the world it was granted against. Without
                # this, a dataset that changes afterwards carries the old
                # approval into Build, and nothing can tell that it did.
                await self._bind_stage_approval(session, cycle, attempt_row, stages)

            evidence = await self._latest_artifact(session, stage_attempt_id)
            if evidence is None:
                raise DbtlWorkflowRefused(f"Stage {stage!r} has no artifact to review.")
            provenance = design_feedback_provenance or {}
            session.add(
                DbtlReviewRow(
                    id=f"review-{cycle.id}-{cycle.db_revision}",
                    project_id=project_id,
                    cycle_id=cycle.id,
                    stage_attempt_id=stage_attempt_id,
                    # The verdict is bound to the exact evidence revision it
                    # was given, so a later artifact revision cannot inherit
                    # an approval it was never shown to a reviewer for.
                    artifact_id=evidence.id,
                    artifact_revision=evidence.revision,
                    bound_db_revision=expected_db_revision,
                    bound_stage_revision=expected_db_revision,
                    bound_projection_hash=bound_projection_hash,
                    policy_version=cycle.policy_version,
                    idempotency_key=idempotency_key,
                    decision=str(verdict),
                    rationale=rationale.strip(),
                    reviewer_user_id=reviewer_user_id,
                    reviewer_project_role=reviewer_project_role,
                    authorization_reference=f"manual-workflow:{project_id}:{reviewer_user_id}",
                    input_source=str(provenance.get("input_source") or "design_sheet"),
                    feedback_surface_id=provenance.get("feedback_surface_id"),
                    deck_content_hash=provenance.get("deck_content_hash"),
                    deck_schema_version=provenance.get("deck_schema_version"),
                    selected_action=provenance.get("selected_action"),
                    selected_card_ids=list(provenance.get("selected_card_ids") or []) or None,
                    human_comment=provenance.get("human_comment"),
                    rationale_projection=provenance.get("rationale_projection"),
                    rationale_source=provenance.get("rationale_source"),
                )
            )
            await self._append_stage_transition(
                session,
                cycle=cycle,
                from_stage=stage,
                chosen_route=str(verdict),
                decided_by=reviewer_user_id,
                stage_attempt=attempt_row,
                evidence_hash=evidence.content_hash,
                decision_surface_id=(provenance.get("feedback_surface_id") if provenance else None),
            )
            await self._record_event(
                session,
                cycle=cycle,
                event_type="stage.reviewed",
                actor_user_id=reviewer_user_id,
                payload={
                    "idempotency_key": idempotency_key,
                    "stage": stage,
                    "decision": str(verdict),
                    "rationale": rationale.strip(),
                    "expected_db_revision": expected_db_revision,
                    "reviewer_user_id": reviewer_user_id,
                    "reviewer_project_role": reviewer_project_role,
                    "state": cycle.state,
                    "design_feedback_provenance": design_feedback_provenance,
                },
            )
            await session.commit()
            return self._cycle_payload(cycle, stages)

    # -- artifacts and work items ---------------------------------------

    async def attach_artifact(
        self,
        *,
        cycle_id: str,
        project_id: str,
        stage: str,
        artifact_type: str,
        uri: str,
        content_hash: str,
        created_by: str,
        expected_db_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Record one artifact revision against a stage attempt."""
        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id, for_update=True)
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            replay = await self._replay_event(
                session,
                cycle_id,
                idempotency_key,
                event_type="artifact.attached",
                expected_payload={
                    "stage": stage,
                    "artifact_type": artifact_type,
                    "uri": uri,
                    "content_hash": content_hash,
                    "expected_db_revision": expected_db_revision,
                    "created_by": created_by,
                },
            )
            if replay is not None:
                artifact_id = dict(replay.payload or {}).get("artifact_id")
                if not isinstance(artifact_id, str):
                    raise DbtlWorkflowRefused("The replayed artifact event is malformed.")
                prior = await session.get(DbtlArtifactRow, artifact_id)
                if prior is None:
                    raise DbtlWorkflowRefused("The replayed artifact record is no longer available.")
                return {
                    "id": prior.id,
                    "stage": stage,
                    "artifact_type": prior.artifact_type,
                    "revision": prior.revision,
                    "content_hash": prior.content_hash,
                    "uri": prior.uri,
                    "db_revision": cycle.db_revision,
                    "created_at": _iso(prior.created_at),
                }
            self._require_revision(cycle, expected_db_revision)
            attempt = next((row for row in stages if row.stage == stage), None)
            if attempt is None:
                raise DbtlWorkflowRefused(f"Unknown stage {stage!r}.")
            statuses = self._statuses(stages)
            if not can_enter_stage(stage, cycle.state, statuses):
                raise DbtlWorkflowRefused(f"Stage {stage!r} is not open in state {cycle.state!r}.")
            if statuses[stage] not in {StageStatus.IN_PROGRESS, StageStatus.CHANGES_REQUESTED}:
                raise DbtlWorkflowRefused(f"Stage {stage!r} is {statuses[stage]} and cannot accept new evidence.")

            highest = await session.scalar(
                select(func.max(DbtlArtifactRow.revision)).where(
                    DbtlArtifactRow.stage_attempt_id == attempt.id,
                    DbtlArtifactRow.artifact_type == artifact_type,
                )
            )
            revision = int(highest or 0) + 1
            row = DbtlArtifactRow(
                id=f"artifact-{uuid4()}",
                project_id=project_id,
                cycle_id=cycle_id,
                stage_attempt_id=attempt.id,
                artifact_type=artifact_type,
                revision=revision,
                content_hash=content_hash,
                uri=uri,
                created_by=created_by,
            )
            session.add(row)
            self._commit_revision(cycle, statuses)
            attempt.db_revision = cycle.db_revision
            await self._record_event(
                session,
                cycle=cycle,
                event_type="artifact.attached",
                actor_user_id=created_by,
                payload={
                    "idempotency_key": idempotency_key,
                    "artifact_id": row.id,
                    "stage": stage,
                    "artifact_type": artifact_type,
                    "revision": revision,
                    "uri": uri,
                    "content_hash": content_hash,
                    "expected_db_revision": expected_db_revision,
                    "created_by": created_by,
                },
            )
            await session.commit()
            return {
                "id": row.id,
                "stage": stage,
                "artifact_type": artifact_type,
                "revision": revision,
                "content_hash": content_hash,
                "uri": uri,
                "db_revision": cycle.db_revision,
                "created_at": _iso(row.created_at),
            }

    async def create_work_item(
        self,
        *,
        cycle_id: str,
        project_id: str,
        title: str,
        kind: str,
        created_by: str,
        owner_role: str | None = None,
        expected_db_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Record a blocker, task, or open question against a cycle."""
        if kind not in WORK_ITEM_KINDS:
            raise ValueError(f"Unknown work item kind {kind!r}; expected one of: {', '.join(sorted(WORK_ITEM_KINDS))}")
        if not title.strip():
            raise ValueError("A work item needs a title.")

        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id, for_update=True)
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            replay = await self._replay_event(
                session,
                cycle_id,
                idempotency_key,
                event_type="work_item.opened",
                expected_payload={
                    "kind": kind,
                    "title": title.strip(),
                    "owner_role": owner_role,
                    "expected_db_revision": expected_db_revision,
                    "created_by": created_by,
                },
            )
            if replay is not None:
                work_item_id = dict(replay.payload or {}).get("work_item_id")
                if not isinstance(work_item_id, str):
                    raise DbtlWorkflowRefused("The replayed work-item event is malformed.")
                prior = await session.get(WorkItemRow, work_item_id)
                if prior is None:
                    raise DbtlWorkflowRefused("The replayed work item is no longer available.")
                return self._work_item_payload(prior)
            self._require_revision(cycle, expected_db_revision)
            statuses = self._statuses(stages)
            row = WorkItemRow(
                id=f"work-{uuid4()}",
                project_id=project_id,
                cycle_id=cycle_id,
                title=title.strip(),
                status="open",
                owner_role=owner_role,
                payload={"kind": kind, "created_by": created_by},
                db_revision=cycle.db_revision + 1,
            )
            session.add(row)
            self._commit_revision(cycle, statuses)
            await self._record_event(
                session,
                cycle=cycle,
                event_type="work_item.opened",
                actor_user_id=created_by,
                payload={
                    "idempotency_key": idempotency_key,
                    "work_item_id": row.id,
                    "kind": kind,
                    "title": row.title,
                    "owner_role": owner_role,
                    "expected_db_revision": expected_db_revision,
                    "created_by": created_by,
                },
            )
            await session.commit()
            return self._work_item_payload(row)

    async def resolve_work_item(
        self,
        *,
        work_item_id: str,
        project_id: str,
        resolution: str,
        actor_user_id: str,
        expected_db_revision: int,
        expected_work_item_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Close a work item with a recorded resolution."""
        if not resolution.strip():
            raise ValueError("Resolving a work item requires a resolution.")

        async with self._sf() as session:
            row = await session.get(WorkItemRow, work_item_id)
            if row is None or row.project_id != project_id:
                raise DbtlWorkflowRefused("Work item not found.")
            if not row.cycle_id:
                raise DbtlWorkflowRefused("Work item is not attached to a DBTL cycle.")
            if dict(row.payload or {}).get("kind") == RECONCILIATION_KIND:
                # This path takes no actor type, so it cannot apply the rule that
                # an agent may not close a judgement row. Refusing here is what
                # stops it from being the way around that rule; the reconciliation
                # decision endpoint is the only writer for these.
                raise DbtlWorkflowRefused("This is a reconciliation row; decide it through the reconciliation endpoint so the reviewer is recorded.")
            loaded = await self._load(session, row.cycle_id, project_id, for_update=True)
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            replay = await self._replay_event(
                session,
                cycle.id,
                idempotency_key,
                event_type="work_item.resolved",
                expected_payload={
                    "work_item_id": row.id,
                    "resolution": resolution.strip(),
                    "expected_db_revision": expected_db_revision,
                    "expected_work_item_revision": expected_work_item_revision,
                    "actor_user_id": actor_user_id,
                },
            )
            if replay is not None:
                return self._work_item_payload(row)
            self._require_revision(cycle, expected_db_revision)
            if row.db_revision != expected_work_item_revision:
                raise DbtlRevisionConflict(f"Work item {row.id} is at revision {row.db_revision}, not {expected_work_item_revision}.")
            if row.status == "resolved":
                raise DbtlWorkflowRefused("This work item was already resolved by another action.")

            row.status = "resolved"
            row.payload = {**dict(row.payload or {}), "resolution": resolution.strip()}
            statuses = self._statuses(stages)
            self._commit_revision(cycle, statuses)
            row.db_revision = cycle.db_revision
            await self._record_event(
                session,
                cycle=cycle,
                event_type="work_item.resolved",
                actor_user_id=actor_user_id,
                payload={
                    "idempotency_key": idempotency_key,
                    "work_item_id": row.id,
                    "resolution": resolution.strip(),
                    "expected_db_revision": expected_db_revision,
                    "expected_work_item_revision": expected_work_item_revision,
                    "actor_user_id": actor_user_id,
                },
            )
            await session.commit()
            return self._work_item_payload(row)
