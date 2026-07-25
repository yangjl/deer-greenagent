"""Transactional repository for DBTL governance records."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.dbtl.model import (
    DbtlArtifactRow,
    DbtlCutoverDecisionRow,
    DbtlCycleRow,
    DbtlEventRow,
    DbtlReviewRow,
    DbtlStageAttemptRow,
    DbtlValidationRow,
)
from deerflow.utils.time import coerce_iso


class DbtlReviewReplay(ValueError):
    """An idempotency key has already consumed a review gate."""


class DbtlReviewStale(ValueError):
    """A review targets a stale cycle, stage, artifact, or policy revision."""


class DbtlProjectionMismatch(ValueError):
    """The durable projection hash does not match the bound projection."""


class DbtlCutoverBlocked(ValueError):
    """Technical validation has not passed, so cutover cannot be approved."""


def projection_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evidence_hash(payload: dict[str, Any]) -> str:
    return projection_hash(payload)


def _serialize(row) -> dict[str, Any]:
    data = row.to_dict()
    for key, value in tuple(data.items()):
        if isinstance(value, datetime):
            data[key] = coerce_iso(value)
    return data


class DbtlGovernanceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create_foundation_fixture(
        self,
        *,
        cycle_id: str,
        project_id: str,
        title: str,
        created_by: str,
        policy_version: str,
    ) -> dict[str, Any]:
        """Create a pre-graph fixture for migration/tests.

        Phase 3 will own user-facing cycle creation. Keeping this repository
        method out of the HTTP API prevents Phase 1 from becoming a hidden
        manual-cycle implementation.
        """
        stage_id = f"stage-{cycle_id}-design-1"
        artifact_id = f"artifact-{cycle_id}-design-1"
        projection = {
            "cycle_id": cycle_id,
            "project_id": project_id,
            "state": "awaiting-design-review",
            "policy_version": policy_version,
            "db_revision": 1,
            "last_review_id": None,
        }
        cycle = DbtlCycleRow(
            id=cycle_id,
            project_id=project_id,
            title=title,
            cycle_class="computational",
            state="awaiting-design-review",
            policy_version=policy_version,
            db_revision=1,
            projection_json=projection,
            projection_hash=projection_hash(projection),
            created_by=created_by,
        )
        stage = DbtlStageAttemptRow(
            id=stage_id,
            project_id=project_id,
            cycle_id=cycle_id,
            stage="design",
            attempt_number=1,
            status="awaiting-review",
            db_revision=1,
        )
        artifact = DbtlArtifactRow(
            id=artifact_id,
            project_id=project_id,
            cycle_id=cycle_id,
            stage_attempt_id=stage_id,
            artifact_type="design-package",
            revision=1,
            content_hash="0" * 64,
            uri=f"dbtl://{project_id}/{cycle_id}/design-package/1",
            created_by=created_by,
        )
        async with self._sf() as session:
            session.add(cycle)
            await session.flush()
            session.add(stage)
            await session.flush()
            session.add(artifact)
            await session.commit()
            await session.refresh(cycle)
            await session.refresh(stage)
            await session.refresh(artifact)
        return {
            "cycle": _serialize(cycle),
            "stage_attempt": _serialize(stage),
            "artifact": _serialize(artifact),
        }

    async def get_cycle(self, cycle_id: str, *, project_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            cycle = await session.get(DbtlCycleRow, cycle_id)
            if cycle is None or cycle.project_id != project_id:
                return None
            return _serialize(cycle)

    async def submit_review(
        self,
        *,
        review_id: str,
        project_id: str,
        cycle_id: str,
        stage_attempt_id: str,
        artifact_id: str,
        artifact_revision: int,
        expected_db_revision: int,
        expected_stage_revision: int,
        expected_projection_hash: str,
        policy_version: str,
        idempotency_key: str,
        decision: str,
        rationale: str,
        reviewer_user_id: str,
        reviewer_project_role: str,
        authorization_reference: str,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            existing = await session.scalar(
                select(DbtlReviewRow).where(
                    DbtlReviewRow.project_id == project_id,
                    DbtlReviewRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                raise DbtlReviewReplay(idempotency_key)

            cycle = await session.get(DbtlCycleRow, cycle_id)
            if cycle is None or cycle.project_id != project_id:
                raise LookupError(cycle_id)
            stage = await session.get(DbtlStageAttemptRow, stage_attempt_id)
            artifact = await session.get(DbtlArtifactRow, artifact_id)
            if stage is None or artifact is None or stage.project_id != project_id or stage.cycle_id != cycle_id or artifact.project_id != project_id or artifact.cycle_id != cycle_id or artifact.stage_attempt_id != stage_attempt_id:
                raise LookupError("Review target not found")

            computed_hash = projection_hash(cycle.projection_json)
            if cycle.projection_hash != computed_hash:
                raise DbtlProjectionMismatch(cycle_id)
            if cycle.db_revision != expected_db_revision or stage.db_revision != expected_stage_revision or artifact.revision != artifact_revision or cycle.policy_version != policy_version:
                raise DbtlReviewStale(cycle_id)
            if expected_projection_hash != computed_hash:
                raise DbtlProjectionMismatch(cycle_id)

            new_revision = expected_db_revision + 1
            new_projection = dict(cycle.projection_json)
            new_projection.update(
                {
                    "db_revision": new_revision,
                    "last_review_id": review_id,
                }
            )
            new_hash = projection_hash(new_projection)
            cycle_result = await session.execute(
                update(DbtlCycleRow)
                .where(
                    DbtlCycleRow.id == cycle_id,
                    DbtlCycleRow.project_id == project_id,
                    DbtlCycleRow.db_revision == expected_db_revision,
                    DbtlCycleRow.projection_hash == expected_projection_hash,
                )
                .values(
                    db_revision=new_revision,
                    projection_json=new_projection,
                    projection_hash=new_hash,
                    updated_at=datetime.now(UTC),
                )
            )
            stage_result = await session.execute(
                update(DbtlStageAttemptRow)
                .where(
                    DbtlStageAttemptRow.id == stage_attempt_id,
                    DbtlStageAttemptRow.db_revision == expected_stage_revision,
                )
                .values(
                    db_revision=expected_stage_revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            if cycle_result.rowcount != 1 or stage_result.rowcount != 1:
                await session.rollback()
                raise DbtlReviewStale(cycle_id)

            review = DbtlReviewRow(
                id=review_id,
                project_id=project_id,
                cycle_id=cycle_id,
                stage_attempt_id=stage_attempt_id,
                artifact_id=artifact_id,
                artifact_revision=artifact_revision,
                bound_db_revision=expected_db_revision,
                bound_stage_revision=expected_stage_revision,
                bound_projection_hash=expected_projection_hash,
                policy_version=policy_version,
                idempotency_key=idempotency_key,
                decision=decision,
                rationale=rationale,
                reviewer_user_id=reviewer_user_id,
                reviewer_project_role=reviewer_project_role,
                authorization_reference=authorization_reference,
            )
            event = DbtlEventRow(
                id=f"event-{review_id}",
                project_id=project_id,
                cycle_id=cycle_id,
                sequence=new_revision,
                event_type="human-review-recorded",
                actor_user_id=reviewer_user_id,
                payload={
                    "review_id": review_id,
                    "decision": decision,
                    "artifact_id": artifact_id,
                    "artifact_revision": artifact_revision,
                    "policy_version": policy_version,
                },
            )
            session.add_all([review, event])
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DbtlReviewReplay(idempotency_key) from exc
            await session.refresh(review)
            return _serialize(review)

    async def force_projection_hash_for_test(self, cycle_id: str, value: str) -> None:
        async with self._sf() as session:
            await session.execute(update(DbtlCycleRow).where(DbtlCycleRow.id == cycle_id).values(projection_hash=value))
            await session.commit()

    async def list_projection_mismatches(self) -> list[dict[str, str]]:
        async with self._sf() as session:
            rows = (await session.execute(select(DbtlCycleRow).order_by(DbtlCycleRow.id))).scalars()
            mismatches = []
            for row in rows:
                computed = projection_hash(row.projection_json)
                if computed != row.projection_hash:
                    mismatches.append(
                        {
                            "cycle_id": row.id,
                            "project_id": row.project_id,
                            "stored_hash": row.projection_hash,
                            "computed_hash": computed,
                        }
                    )
            return mismatches

    async def schema_snapshot(self) -> dict[str, Any]:
        expected = {
            "dbtl_cycles",
            "dbtl_stage_runs",
            "dbtl_artifacts",
            "dbtl_reviews",
            "activity_events",
            "dbtl_transition_intents",
            "dbtl_transitions",
            "dbtl_gate_evaluations",
            "work_items",
            "memory_candidates",
            "knowledge_claims",
            "knowledge_promotions",
            "knowledge_links",
            "dbtl_validations",
            "dbtl_cutover_decisions",
        }
        async with self._sf() as session:
            connection = await session.connection()

            def inspect_schema(sync_connection):
                from sqlalchemy import inspect

                inspector = inspect(sync_connection)
                tables = set(inspector.get_table_names())
                review_columns = {column["name"] for column in inspector.get_columns("dbtl_reviews")} if "dbtl_reviews" in tables else set()
                revision = None
                if "alembic_version" in tables:
                    revision = sync_connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
                return tables, review_columns, revision

            tables, review_columns, revision = await connection.run_sync(inspect_schema)
        return {
            "revision": revision,
            "tables_present": sorted(expected & tables),
            "tables_missing": sorted(expected - tables),
            "identity_binding": {
                "reviewer_user_id",
                "reviewer_project_role",
                "authorization_reference",
                "bound_db_revision",
                "bound_stage_revision",
            }
            <= review_columns,
        }

    async def save_validation(
        self,
        *,
        validation_id: str,
        database_backend: str,
        technical_ready: bool,
        report: dict[str, Any],
        created_by: str,
    ) -> dict[str, Any]:
        row = DbtlValidationRow(
            id=validation_id,
            database_backend=database_backend,
            technical_ready=technical_ready,
            evidence_hash=evidence_hash(report),
            report=report,
            created_by=created_by,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _serialize(row)

    async def get_validation(self, validation_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(DbtlValidationRow, validation_id)
            return _serialize(row) if row is not None else None

    async def latest_validation(self) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.scalar(select(DbtlValidationRow).order_by(DbtlValidationRow.created_at.desc()).limit(1))
            return _serialize(row) if row is not None else None

    async def approve_cutover(
        self,
        *,
        decision_id: str,
        validation_id: str,
        approved_by: str,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            validation = await session.get(DbtlValidationRow, validation_id)
            if validation is None or not validation.technical_ready or validation.database_backend != "postgres":
                raise DbtlCutoverBlocked(validation_id)
            row = DbtlCutoverDecisionRow(
                id=decision_id,
                validation_id=validation_id,
                status="approved",
                evidence_hash=validation.evidence_hash,
                approved_by=approved_by,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DbtlCutoverBlocked("Validation was already consumed") from exc
            await session.refresh(row)
            return _serialize(row)

    async def latest_cutover(self) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.scalar(select(DbtlCutoverDecisionRow).where(DbtlCutoverDecisionRow.status == "approved").order_by(DbtlCutoverDecisionRow.created_at.desc()).limit(1))
            return _serialize(row) if row is not None else None
