"""Durable repository for pre-cycle conversational discovery."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.dbtl.cycle_state import STAGE_ORDER, initial_stage_statuses
from deerflow.dbtl.discovery import (
    TERMINAL_DISCOVERY_STATUSES,
    DiscoveryStatus,
    DiscoveryTrigger,
    can_transition_discovery,
)
from deerflow.persistence.dbtl.model import (
    DbtlCycleRow,
    DbtlDiscoveryOutboxRow,
    DbtlDiscoveryRow,
    DbtlEventRow,
    DbtlStageAttemptRow,
)
from deerflow.persistence.dbtl.sql import projection_hash
from deerflow.utils.time import coerce_iso


class DbtlDiscoveryConflict(ValueError):
    """A discovery revision or lifecycle compare-and-set lost a race."""


def _turn_hash(value: str) -> str:
    """Bind a turn without copying conversation content into the SQL row."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _payload(row: DbtlDiscoveryRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "thread_id": row.thread_id,
        "user_id": row.user_id,
        "trigger": row.trigger,
        "status": row.status,
        "policy_version": row.policy_version,
        "revision": row.revision,
        "draft": dict(row.draft_json or {}),
        "offered_revision": row.offered_revision,
        "package": dict(row.package_json or {}) if row.package_json is not None else None,
        "package_hash": row.package_hash,
        "cycle_id": row.cycle_id,
        "start_submission_id": row.start_submission_id,
        "created_at": coerce_iso(row.created_at) if isinstance(row.created_at, datetime) else row.created_at,
        "updated_at": coerce_iso(row.updated_at) if isinstance(row.updated_at, datetime) else row.updated_at,
        "expires_at": coerce_iso(row.expires_at) if isinstance(row.expires_at, datetime) else row.expires_at,
    }


def _outbox_payload(row: DbtlDiscoveryOutboxRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "discovery_id": row.discovery_id,
        "project_id": row.project_id,
        "thread_id": row.thread_id,
        "event_type": row.event_type,
        "payload": dict(row.payload_json or {}),
        "status": row.status,
        "attempts": row.attempts,
        "created_at": coerce_iso(row.created_at) if isinstance(row.created_at, datetime) else row.created_at,
        "delivered_at": coerce_iso(row.delivered_at) if isinstance(row.delivered_at, datetime) else row.delivered_at,
    }


def _telemetry_payload(row: DbtlDiscoveryRow) -> dict[str, Any]:
    """Lifecycle metadata only; never copy the proposed brief into telemetry."""

    draft = dict(row.draft_json or {})
    return {
        "id": row.id,
        "project_id": row.project_id,
        "thread_id": row.thread_id,
        "trigger": row.trigger,
        "status": row.status,
        "policy_version": row.policy_version,
        "revision": row.revision,
        "turn_count": max(0, int(draft.get("turn_count") or 0)),
        "offered": row.offered_revision is not None,
        "cycle_id": row.cycle_id,
        "created_at": coerce_iso(row.created_at) if isinstance(row.created_at, datetime) else row.created_at,
        "updated_at": coerce_iso(row.updated_at) if isinstance(row.updated_at, datetime) else row.updated_at,
    }


class DbtlDiscoveryRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_active(self, *, project_id: str, thread_id: str, user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.scalar(
                select(DbtlDiscoveryRow).where(
                    DbtlDiscoveryRow.project_id == project_id,
                    DbtlDiscoveryRow.thread_id == thread_id,
                    DbtlDiscoveryRow.user_id == user_id,
                    DbtlDiscoveryRow.status.in_(["gathering", "ready", "offered"]),
                )
            )
            return _payload(row) if row is not None else None

    async def get_latest(self, *, project_id: str, thread_id: str, user_id: str) -> dict[str, Any] | None:
        """Return the newest discovery, including a terminal suppression marker."""

        async with self._sf() as session:
            row = await session.scalar(
                select(DbtlDiscoveryRow)
                .where(
                    DbtlDiscoveryRow.project_id == project_id,
                    DbtlDiscoveryRow.thread_id == thread_id,
                    DbtlDiscoveryRow.user_id == user_id,
                )
                .order_by(DbtlDiscoveryRow.updated_at.desc(), DbtlDiscoveryRow.created_at.desc())
                .limit(1)
            )
            return _payload(row) if row is not None else None

    async def list_project_outcomes(self, project_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Newest-first bounded lifecycle telemetry for the admin drawer."""

        bounded = max(1, min(int(limit), 500))
        async with self._sf() as session:
            rows = list(await session.scalars(select(DbtlDiscoveryRow).where(DbtlDiscoveryRow.project_id == project_id).order_by(DbtlDiscoveryRow.created_at.desc(), DbtlDiscoveryRow.id.desc()).limit(bounded)))
            return [_telemetry_payload(row) for row in rows]

    async def project_outcome_stats(self, project_id: str) -> dict[str, int]:
        """Aggregate lifecycle outcomes across the full project history."""

        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(
                        DbtlDiscoveryRow.trigger,
                        DbtlDiscoveryRow.status,
                        func.count().label("total"),
                    )
                    .where(DbtlDiscoveryRow.project_id == project_id)
                    .group_by(DbtlDiscoveryRow.trigger, DbtlDiscoveryRow.status)
                )
            ).all()
        stats = {
            "total": 0,
            "classifier_entries": 0,
            "confirmed": 0,
            "declined": 0,
            "active": 0,
        }
        for trigger, status, raw_count in rows:
            count = int(raw_count)
            stats["total"] += count
            if trigger == DiscoveryTrigger.CLASSIFIER.value:
                stats["classifier_entries"] += count
            if status == DiscoveryStatus.CONFIRMED.value:
                stats["confirmed"] += count
            elif status == DiscoveryStatus.DECLINED.value:
                stats["declined"] += count
            elif status in {
                DiscoveryStatus.GATHERING.value,
                DiscoveryStatus.READY.value,
                DiscoveryStatus.OFFERED.value,
            }:
                stats["active"] += count
        return stats

    async def get(
        self,
        discovery_id: str,
        *,
        project_id: str,
        thread_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(DbtlDiscoveryRow, discovery_id)
            if row is None or row.project_id != project_id or row.thread_id != thread_id or row.user_id != user_id:
                return None
            return _payload(row)

    async def mark_outbox_delivered(
        self,
        *,
        event_id: str,
        event_type: str,
        project_id: str,
        thread_id: str,
    ) -> dict[str, Any]:
        """Acknowledge only the exact server event recovered from history."""

        async with self._sf() as session:
            row = await session.get(DbtlDiscoveryOutboxRow, event_id)
            if row is None or row.event_type != event_type or row.project_id != project_id or row.thread_id != thread_id:
                raise DbtlDiscoveryConflict("The discovery delivery event is stale or out of scope.")
            if row.status != "delivered":
                row.status = "delivered"
                row.attempts += 1
                row.delivered_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(row)
            return _outbox_payload(row)

    async def begin(
        self,
        *,
        project_id: str,
        thread_id: str,
        user_id: str,
        policy_version: str,
        trigger: DiscoveryTrigger,
        latest_user_turn: str,
        structured_draft: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = await self.get_active(project_id=project_id, thread_id=thread_id, user_id=user_id)
        if existing is not None:
            return existing
        row = DbtlDiscoveryRow(
            id=f"discovery-{uuid4().hex}",
            project_id=project_id,
            thread_id=thread_id,
            user_id=user_id,
            trigger=trigger.value,
            status=DiscoveryStatus.GATHERING.value,
            policy_version=policy_version,
            revision=1,
            draft_json={"latest_turn_hash": _turn_hash(latest_user_turn), "turn_count": 1, **dict(structured_draft or {})},
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                replay = await self.get_active(project_id=project_id, thread_id=thread_id, user_id=user_id)
                if replay is not None:
                    return replay
                raise DbtlDiscoveryConflict("Another request changed this thread's active discovery.") from exc
            await session.refresh(row)
            return _payload(row)

    async def record_turn(
        self,
        *,
        discovery_id: str,
        expected_revision: int,
        latest_user_turn: str,
        structured_draft: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            row = await session.get(DbtlDiscoveryRow, discovery_id)
            if row is None or row.revision != expected_revision or DiscoveryStatus(row.status) in TERMINAL_DISCOVERY_STATUSES:
                raise DbtlDiscoveryConflict("The discovery changed before this turn could be recorded.")
            draft = dict(row.draft_json or {})
            draft["latest_turn_hash"] = _turn_hash(latest_user_turn)
            draft["turn_count"] = max(1, int(draft.get("turn_count") or 0) + 1)
            if structured_draft is not None:
                draft.update(structured_draft)
            result = await session.execute(update(DbtlDiscoveryRow).where(DbtlDiscoveryRow.id == discovery_id, DbtlDiscoveryRow.revision == expected_revision).values(draft_json=draft, revision=expected_revision + 1))
            if result.rowcount != 1:
                await session.rollback()
                raise DbtlDiscoveryConflict("The discovery changed before this turn could be recorded.")
            await session.commit()
            updated = await session.get(DbtlDiscoveryRow, discovery_id)
            if updated is None:
                raise DbtlDiscoveryConflict("The discovery disappeared after recording the turn.")
            return _payload(updated)

    async def mark_ready(self, *, discovery_id: str, expected_revision: int) -> dict[str, Any]:
        return await self.transition(
            discovery_id=discovery_id,
            expected_revision=expected_revision,
            target=DiscoveryStatus.READY,
        )

    async def transition(
        self,
        *,
        discovery_id: str,
        expected_revision: int,
        target: DiscoveryStatus,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            row = await session.get(DbtlDiscoveryRow, discovery_id)
            if row is None or row.revision != expected_revision:
                raise DbtlDiscoveryConflict("The discovery revision is stale.")
            current = DiscoveryStatus(row.status)
            if not can_transition_discovery(current, target):
                raise DbtlDiscoveryConflict(f"Discovery cannot move from {current.value} to {target.value}.")
            result = await session.execute(
                update(DbtlDiscoveryRow).where(DbtlDiscoveryRow.id == discovery_id, DbtlDiscoveryRow.revision == expected_revision, DbtlDiscoveryRow.status == current.value).values(status=target.value, revision=expected_revision + 1)
            )
            if result.rowcount != 1:
                await session.rollback()
                raise DbtlDiscoveryConflict("The discovery changed before the transition committed.")
            await session.commit()
            updated = await session.get(DbtlDiscoveryRow, discovery_id)
            if updated is None:
                raise DbtlDiscoveryConflict("The discovery disappeared after the transition.")
            return _payload(updated)

    async def offer(
        self,
        *,
        discovery_id: str,
        expected_revision: int,
        package: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind a package and enqueue its card without claiming delivery.

        The discovery remains ``ready`` until an answer resolves against the
        emitted server card.  That answer is the first safe local proof that
        the control crossed the graph transport boundary; a missing card can
        therefore be replayed from this row instead of leaving an ``offered``
        discovery with no visible control.
        """

        package_hash = projection_hash(package)
        async with self._sf() as session:
            row = await session.get(DbtlDiscoveryRow, discovery_id)
            if row is None or row.revision != expected_revision or DiscoveryStatus(row.status) is not DiscoveryStatus.READY:
                raise DbtlDiscoveryConflict("Only the current ready discovery can be offered.")
            row.offered_revision = expected_revision
            row.package_json = package
            row.package_hash = package_hash
            event_id = f"discovery-offer:{discovery_id}:{expected_revision}:{package_hash[:16]}"
            event = await session.get(DbtlDiscoveryOutboxRow, event_id)
            if event is None:
                event = DbtlDiscoveryOutboxRow(
                    id=event_id,
                    discovery_id=discovery_id,
                    project_id=row.project_id,
                    thread_id=row.thread_id,
                    event_type="start_card",
                    payload_json={
                        "discovery_revision": expected_revision,
                        "package_hash": package_hash,
                    },
                )
                session.add(event)
            await session.commit()
            await session.refresh(row)
            result = _payload(row)
            result["outbox"] = _outbox_payload(event)
            return result

    async def acknowledge_offer(
        self,
        *,
        discovery_id: str,
        expected_revision: int,
        package_hash: str,
        event_id: str,
        project_id: str,
        thread_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Advance ``ready`` to ``offered`` after its exact card is recovered."""

        async with self._sf() as session:
            row = await session.scalar(select(DbtlDiscoveryRow).where(DbtlDiscoveryRow.id == discovery_id).with_for_update())
            event = await session.get(DbtlDiscoveryOutboxRow, event_id)
            if (
                row is None
                or row.project_id != project_id
                or row.thread_id != thread_id
                or row.user_id != user_id
                or row.revision != expected_revision
                or row.offered_revision != expected_revision
                or row.package_hash != package_hash
                or row.status not in {DiscoveryStatus.READY.value, DiscoveryStatus.OFFERED.value}
                or event is None
                or event.discovery_id != discovery_id
                or event.event_type != "start_card"
                or dict(event.payload_json or {}).get("package_hash") != package_hash
            ):
                raise DbtlDiscoveryConflict("The displayed discovery offer is stale or out of scope.")
            if row.status == DiscoveryStatus.READY.value:
                row.status = DiscoveryStatus.OFFERED.value
            if event.status != "delivered":
                event.status = "delivered"
                event.attempts += 1
                event.delivered_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return _payload(row)

    async def confirm_and_create_cycle(
        self,
        *,
        discovery_id: str,
        expected_revision: int,
        package_hash: str,
        project_id: str,
        thread_id: str,
        user_id: str,
        submission_id: str,
    ) -> dict[str, Any]:
        """Consume an offered revision and create its cycle in one transaction."""

        async with self._sf() as session:
            row = await session.scalar(select(DbtlDiscoveryRow).where(DbtlDiscoveryRow.id == discovery_id).with_for_update())
            if row is None or row.project_id != project_id or row.thread_id != thread_id or row.user_id != user_id:
                raise DbtlDiscoveryConflict("Discovery does not belong to this project conversation.")
            if row.status == DiscoveryStatus.CONFIRMED.value:
                if row.start_submission_id == submission_id and row.cycle_id:
                    result = _payload(row)
                    events = list(
                        await session.scalars(
                            select(DbtlDiscoveryOutboxRow).where(
                                DbtlDiscoveryOutboxRow.discovery_id == discovery_id,
                                DbtlDiscoveryOutboxRow.event_type.in_(["creation_receipt", "design_kickoff"]),
                            )
                        )
                    )
                    result["outbox"] = [_outbox_payload(event) for event in events]
                    return result
                raise DbtlDiscoveryConflict("This discovery has already created a cycle.")
            offer_event_id = f"discovery-offer:{discovery_id}:{expected_revision}:{package_hash[:16]}"
            offer_event = await session.get(DbtlDiscoveryOutboxRow, offer_event_id)
            if (
                row.status != DiscoveryStatus.OFFERED.value
                or row.offered_revision != expected_revision
                or row.revision != expected_revision
                or row.package_hash != package_hash
                or not isinstance(row.package_json, dict)
                or offer_event is None
                or offer_event.event_type != "start_card"
                or offer_event.status != "delivered"
            ):
                raise DbtlDiscoveryConflict("The displayed discovery revision is stale or unbound.")

            package = dict(row.package_json)
            title = str(package.get("proposed_title") or "").strip()
            objective = str(package.get("objective") or "").strip()
            if not title or not objective:
                raise DbtlDiscoveryConflict("The discovery package has no reviewable title or objective.")
            cycle_id = f"cycle-{uuid4().hex}"
            statuses = initial_stage_statuses()
            cycle = DbtlCycleRow(
                id=cycle_id,
                project_id=project_id,
                parent_cycle_id=None,
                create_idempotency_key=f"discovery:{discovery_id}:{expected_revision}",
                originating_thread_id=thread_id,
                title=title,
                cycle_class="computational",
                state=STAGE_ORDER[0],
                policy_version=row.policy_version,
                db_revision=1,
                projection_json={},
                projection_hash="",
                created_by=user_id,
            )
            cycle.projection_json = {
                "cycle_id": cycle_id,
                "project_id": project_id,
                "parent_cycle_id": None,
                "title": title,
                "cycle_class": "computational",
                "state": STAGE_ORDER[0],
                "policy_version": row.policy_version,
                "db_revision": 1,
                "stages": {stage: str(statuses[stage]) for stage in STAGE_ORDER},
                "research_question": objective,
                "objective": objective,
                "success_criteria": str(package.get("success_criteria") or ""),
                "cycle_weight": "full",
                "idempotency_key": f"discovery:{discovery_id}:{expected_revision}",
                "discovery_id": discovery_id,
                "discovery_package_hash": package_hash,
                "discovery_package": package,
            }
            cycle.projection_hash = projection_hash(cycle.projection_json)
            session.add(cycle)
            session.add_all(
                [
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
            )
            session.add(
                DbtlEventRow(
                    id=f"event-{cycle_id}-1",
                    project_id=project_id,
                    cycle_id=cycle_id,
                    sequence=1,
                    event_type="cycle.created",
                    actor_user_id=user_id,
                    payload={
                        "db_revision": 1,
                        "idempotency_key": cycle.create_idempotency_key,
                        "cycle_class": "computational",
                        "originating_thread_id": thread_id,
                        "discovery_id": discovery_id,
                        "discovery_revision": expected_revision,
                        "discovery_package_hash": package_hash,
                    },
                )
            )
            receipt_event = DbtlDiscoveryOutboxRow(
                id=f"discovery-receipt:{discovery_id}:{expected_revision}",
                discovery_id=discovery_id,
                project_id=project_id,
                thread_id=thread_id,
                event_type="creation_receipt",
                payload_json={
                    "cycle_id": cycle_id,
                    "package_hash": package_hash,
                    "message": f"DBTL cycle {cycle_id} was created from the accepted discovery proposal. Design has not run yet.",
                },
            )
            kickoff_event = DbtlDiscoveryOutboxRow(
                id=f"discovery-design-kickoff:{discovery_id}:{expected_revision}",
                discovery_id=discovery_id,
                project_id=project_id,
                thread_id=thread_id,
                event_type="design_kickoff",
                payload_json={
                    "cycle_id": cycle_id,
                    "package_hash": package_hash,
                    "status": "pending",
                },
            )
            session.add_all([receipt_event, kickoff_event])
            row.status = DiscoveryStatus.CONFIRMED.value
            row.cycle_id = cycle_id
            row.start_submission_id = submission_id
            row.revision = expected_revision + 1
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                replay = await session.get(DbtlDiscoveryRow, discovery_id)
                if replay is not None and replay.status == DiscoveryStatus.CONFIRMED.value and replay.start_submission_id == submission_id and replay.cycle_id:
                    result = _payload(replay)
                    events = list(
                        await session.scalars(
                            select(DbtlDiscoveryOutboxRow).where(
                                DbtlDiscoveryOutboxRow.discovery_id == discovery_id,
                                DbtlDiscoveryOutboxRow.event_type.in_(["creation_receipt", "design_kickoff"]),
                            )
                        )
                    )
                    result["outbox"] = [_outbox_payload(event) for event in events]
                    return result
                raise DbtlDiscoveryConflict("The discovery start conflicted with another committed action.") from exc
            await session.refresh(row)
            result = _payload(row)
            result["outbox"] = [_outbox_payload(receipt_event), _outbox_payload(kickoff_event)]
            return result
