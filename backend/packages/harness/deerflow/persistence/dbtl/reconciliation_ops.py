"""Durable data readiness and reconciliation (Phase 6).

Mixed into :class:`~deerflow.persistence.dbtl.cycles.DbtlCycleRepository` rather
than given its own repository, because a reconciliation row, a declared dataset,
and a worker run are all part of the *cycle* aggregate: they are guarded by the
same optimistic ``db_revision``, replayed through the same activity-event
idempotency ledger, and meaningless outside the cycle they belong to. A second
repository over the same rows would need its own copy of that machinery, and the
first time the two disagreed the symptom would be a lost review.

Two rules from the phase's no-go list are enforced *here*, at the write
boundary, rather than in the router:

* An agent cannot close a judgement row. :func:`~deerflow.dbtl.reconciliation.
  apply_resolution` owns the rule; this layer's job is to make sure every path
  that can write a resolution goes through it — including the generic
  ``resolve_work_item`` endpoint, which refuses reconciliation rows outright so
  it cannot be used as a way around the check.
* An approval binds the dataset fingerprint it was granted against, so a later
  change is detected rather than assumed absent.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from deerflow.dbtl.cycle_state import STAGE_ORDER, StageStatus
from deerflow.dbtl.reconciliation import (
    ActorType,
    ApprovalBinding,
    BlockerKind,
    DatasetBinding,
    GateEvaluation,
    ReconciliationCheck,
    ReconciliationRefused,
    ReconciliationRow,
    RowStatus,
    apply_resolution,
    check_approval_still_valid,
    dataset_fingerprint,
    evaluate_gate,
    summarize_matrix,
)
from deerflow.dbtl.stage_spec import StageSpecNotFound, resolve_stage_spec
from deerflow.persistence.dbtl.model import (
    DbtlArtifactRow,
    DbtlDatasetRow,
    DbtlEventRow,
    DbtlStageAttemptRow,
    DbtlStageWorkerRunRow,
    WorkItemRow,
)

logger = logging.getLogger(__name__)

#: The work-item kind that carries a reconciliation matrix row. Distinct from
#: ``blocker``/``task``/``question`` so the generic resolve path can recognise
#: and refuse one.
RECONCILIATION_KIND = "reconciliation"

DATASET_ROLES = frozenset({"raw", "derived", "reference"})


def _is_sha256(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


class ReconciliationOpsMixin:
    """Dataset declarations, the reconciliation matrix, and the readiness gate."""

    # -- projection ------------------------------------------------------

    @staticmethod
    def _dataset_payload(row: DbtlDatasetRow) -> dict[str, Any]:
        from deerflow.persistence.dbtl.cycles import _iso

        return {
            "id": row.id,
            "source_key": row.source_key,
            "uri": row.uri,
            "content_hash": row.content_hash,
            "declared_immutable": row.declared_immutable,
            "role": row.role,
            "recorded_by": row.recorded_by,
            "db_revision": row.db_revision,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _binding_from_row(row: DbtlDatasetRow) -> DatasetBinding:
        return DatasetBinding(
            source_key=row.source_key,
            uri=row.uri,
            content_hash=row.content_hash,
            declared_immutable=row.declared_immutable,
            role=row.role,
        )

    @staticmethod
    def _matrix_row(row: WorkItemRow) -> ReconciliationRow | None:
        """Rebuild the pure row from its work item, or ``None`` if it is not one.

        A payload that no longer parses returns ``None`` rather than raising: a
        single malformed row must not make the whole matrix — and therefore the
        gate — unreadable. It stays invisible to the gate, which is the
        conservative direction only because :meth:`reconciliation_view` reports
        the discrepancy alongside it.
        """
        payload = dict(row.payload or {})
        if payload.get("kind") != RECONCILIATION_KIND:
            return None
        try:
            return ReconciliationRow(
                row_id=row.id,
                check=ReconciliationCheck(payload["check"]),
                field_name=row.title,
                source_a_label=str(payload.get("source_a_label") or ""),
                source_a_value=str(payload.get("source_a_value") or ""),
                source_b_label=str(payload.get("source_b_label") or ""),
                source_b_value=str(payload.get("source_b_value") or ""),
                required=bool(payload.get("required", True)),
                status=RowStatus(payload.get("row_status", RowStatus.OPEN.value)),
                resolution=str(payload.get("resolution") or ""),
                resolved_by_actor=ActorType(payload["resolved_by_actor"]) if payload.get("resolved_by_actor") else None,
                resolved_by_user_id=payload.get("resolved_by_user_id"),
                blocker_kind=BlockerKind(payload["blocker_kind"]) if payload.get("blocker_kind") else None,
                evidence_refs=tuple(payload.get("evidence_refs") or ()),
            )
        except (KeyError, ValueError, ReconciliationRefused) as exc:
            logger.warning("dbtl reconciliation row %s could not be read: %s", row.id, exc)
            return None

    @staticmethod
    def _row_payload(row: ReconciliationRow, *, created_by: str) -> dict[str, Any]:
        return {
            "kind": RECONCILIATION_KIND,
            "created_by": created_by,
            "check": row.check.value,
            "source_a_label": row.source_a_label,
            "source_a_value": row.source_a_value,
            "source_b_label": row.source_b_label,
            "source_b_value": row.source_b_value,
            "required": row.required,
            "row_status": row.status.value,
            "resolution": row.resolution,
            "resolved_by_actor": row.resolved_by_actor.value if row.resolved_by_actor else None,
            "resolved_by_user_id": row.resolved_by_user_id,
            "blocker_kind": row.blocker_kind.value if row.blocker_kind else None,
            "evidence_refs": list(row.evidence_refs),
        }

    @staticmethod
    def _work_item_status(row: ReconciliationRow) -> str:
        """Map the matrix status onto the work item's own two-state vocabulary.

        The rail counts open work items, and a row waiting on a reviewer is open
        work no matter which of the three unsettled states it is in.
        """
        return "resolved" if row.status in {RowStatus.RESOLVED, RowStatus.WAIVED} else "open"

    @staticmethod
    def _reopen_reconciliation_after_input_change(cycle, stages: list[DbtlStageAttemptRow]) -> bool:
        """Invalidate an approved bridge before a changed input can reach Build.

        The approval binding is retained so the read model can explain exactly
        what changed, but the authoritative workflow is moved back to
        reconciliation and every downstream stage is locked. Merely displaying
        an invalidation banner while leaving ``cycle.state=ready_for_build``
        would be fail-open.
        """
        from deerflow.persistence.dbtl.cycles import DbtlWorkflowRefused

        attempt = next((item for item in stages if item.stage == "reconciliation"), None)
        if attempt is None or attempt.status != StageStatus.APPROVED.value:
            return False
        if cycle.state != "ready_for_build":
            raise DbtlWorkflowRefused("Declared inputs cannot change after Build has started; open a new cycle or an explicit rework path.")

        cycle.state = "reconciliation"
        for item in stages:
            if item.stage == "reconciliation":
                item.status = StageStatus.CHANGES_REQUESTED.value
            elif item.stage in {"build", "test", "learn"}:
                item.status = StageStatus.LOCKED.value
        return True

    @staticmethod
    def _require_reconciliation_rows_mutable(stages: list[DbtlStageAttemptRow]) -> None:
        """Keep an approved matrix immutable until an input change reopens it."""
        from deerflow.persistence.dbtl.cycles import DbtlWorkflowRefused

        attempt = next((item for item in stages if item.stage == "reconciliation"), None)
        if attempt is not None and attempt.status == StageStatus.APPROVED.value:
            raise DbtlWorkflowRefused("The reconciliation matrix is approved and immutable. Change a declared input to invalidate and rerun reconciliation.")

    # -- datasets --------------------------------------------------------

    async def declare_dataset(
        self,
        *,
        cycle_id: str,
        project_id: str,
        source_key: str,
        uri: str,
        content_hash: str,
        recorded_by: str,
        declared_immutable: bool = True,
        role: str = "raw",
        expected_db_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Declare (or redeclare) one input to this cycle.

        Redeclaring the same ``source_key`` with a different hash is the normal
        way a dataset changes, and it deliberately succeeds: the point is not to
        prevent the change but to make it *visible*, which the fingerprint
        comparison against a bound approval then does.
        """
        from deerflow.persistence.dbtl.cycles import DbtlWorkflowRefused

        key = source_key.strip()
        if not key:
            raise ValueError("A dataset declaration needs a source key.")
        if not uri.strip():
            raise ValueError("A dataset declaration needs a URI.")
        if not _is_sha256(content_hash):
            raise ValueError("A dataset declaration needs a lowercase SHA-256 content hash.")
        if role not in DATASET_ROLES:
            raise ValueError(f"Unknown dataset role {role!r}; expected one of: {', '.join(sorted(DATASET_ROLES))}")

        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id, for_update=True)
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            replay = await self._replay_event(
                session,
                cycle_id,
                idempotency_key,
                event_type="dataset.declared",
                expected_payload={
                    "source_key": key,
                    "content_hash": content_hash,
                    "expected_db_revision": expected_db_revision,
                    "recorded_by": recorded_by,
                },
            )
            if replay is not None:
                dataset_id = dict(replay.payload or {}).get("dataset_id")
                if not isinstance(dataset_id, str):
                    raise DbtlWorkflowRefused("The replayed dataset event is malformed.")
                prior = await session.get(DbtlDatasetRow, dataset_id)
                if prior is None:
                    raise DbtlWorkflowRefused("The replayed dataset record is no longer available.")
                return self._dataset_payload(prior)

            self._require_revision(cycle, expected_db_revision)
            existing = await session.scalar(
                select(DbtlDatasetRow).where(
                    DbtlDatasetRow.cycle_id == cycle_id,
                    DbtlDatasetRow.source_key == key,
                )
            )
            previous_hash = existing.content_hash if existing else None
            previous_binding = self._binding_from_row(existing) if existing else None
            next_binding = DatasetBinding(
                source_key=key,
                uri=uri.strip(),
                content_hash=content_hash,
                declared_immutable=declared_immutable,
                role=role,
            )
            input_changed = previous_binding != next_binding
            approval_invalidated = False
            if input_changed:
                approval_invalidated = self._reopen_reconciliation_after_input_change(cycle, stages)
            statuses = self._statuses(stages)
            if existing is None:
                existing = DbtlDatasetRow(
                    id=f"dataset-{uuid4()}",
                    project_id=project_id,
                    cycle_id=cycle_id,
                    source_key=key,
                    uri=uri.strip(),
                    content_hash=content_hash,
                    declared_immutable=declared_immutable,
                    role=role,
                    recorded_by=recorded_by,
                    db_revision=cycle.db_revision + 1,
                )
                session.add(existing)
            else:
                existing.uri = uri.strip()
                existing.content_hash = content_hash
                existing.declared_immutable = declared_immutable
                existing.role = role
                existing.recorded_by = recorded_by
                existing.db_revision = cycle.db_revision + 1

            self._commit_revision(cycle, statuses)
            await self._record_event(
                session,
                cycle=cycle,
                event_type="dataset.declared",
                actor_user_id=recorded_by,
                payload={
                    "idempotency_key": idempotency_key,
                    "dataset_id": existing.id,
                    "source_key": key,
                    "content_hash": content_hash,
                    "previous_content_hash": previous_hash,
                    "role": role,
                    "declared_immutable": declared_immutable,
                    "approval_invalidated": approval_invalidated,
                    "expected_db_revision": expected_db_revision,
                    "recorded_by": recorded_by,
                },
            )
            await session.commit()
            await session.refresh(existing)
            return self._dataset_payload(existing)

    async def list_datasets(self, cycle_id: str, *, project_id: str) -> list[dict[str, Any]]:
        async with self._sf() as session:
            rows = (await session.execute(select(DbtlDatasetRow).where(DbtlDatasetRow.cycle_id == cycle_id, DbtlDatasetRow.project_id == project_id).order_by(DbtlDatasetRow.source_key.asc()))).scalars()
            return [self._dataset_payload(row) for row in rows]

    # -- matrix rows -----------------------------------------------------

    async def open_reconciliation_row(
        self,
        *,
        cycle_id: str,
        project_id: str,
        check: str,
        field_name: str,
        created_by: str,
        source_a_label: str = "",
        source_a_value: str = "",
        source_b_label: str = "",
        source_b_value: str = "",
        required: bool = True,
        expected_db_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Add one line to the reconciliation matrix."""
        from deerflow.persistence.dbtl.cycles import DbtlWorkflowRefused

        try:
            parsed_check = ReconciliationCheck(check)
        except ValueError as exc:
            allowed = ", ".join(sorted(item.value for item in ReconciliationCheck))
            raise ValueError(f"Unknown reconciliation check {check!r}; expected one of: {allowed}") from exc
        if not field_name.strip():
            raise ValueError("A reconciliation row needs a field name.")

        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id, for_update=True)
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            replay = await self._replay_event(
                session,
                cycle_id,
                idempotency_key,
                event_type="reconciliation.row_opened",
                expected_payload={
                    "check": parsed_check.value,
                    "field_name": field_name.strip(),
                    "expected_db_revision": expected_db_revision,
                    "created_by": created_by,
                },
            )
            if replay is not None:
                row_id = dict(replay.payload or {}).get("row_id")
                if not isinstance(row_id, str):
                    raise DbtlWorkflowRefused("The replayed reconciliation event is malformed.")
                prior = await session.get(WorkItemRow, row_id)
                if prior is None:
                    raise DbtlWorkflowRefused("The replayed reconciliation row is no longer available.")
                return self._work_item_payload(prior)

            self._require_revision(cycle, expected_db_revision)
            statuses = self._statuses(stages)
            self._require_reconciliation_rows_mutable(stages)

            # Built through the pure type first so an illegal shape is refused
            # before anything is written.
            pure = ReconciliationRow(
                row_id=f"recon-{uuid4()}",
                check=parsed_check,
                field_name=field_name.strip(),
                source_a_label=source_a_label.strip(),
                source_a_value=source_a_value.strip(),
                source_b_label=source_b_label.strip(),
                source_b_value=source_b_value.strip(),
                required=required,
            )
            row = WorkItemRow(
                id=pure.row_id,
                project_id=project_id,
                cycle_id=cycle_id,
                title=pure.field_name,
                status="open",
                owner_role=None,
                payload=self._row_payload(pure, created_by=created_by),
                db_revision=cycle.db_revision + 1,
            )
            session.add(row)
            self._commit_revision(cycle, statuses)
            await self._record_event(
                session,
                cycle=cycle,
                event_type="reconciliation.row_opened",
                actor_user_id=created_by,
                payload={
                    "idempotency_key": idempotency_key,
                    "row_id": row.id,
                    "check": parsed_check.value,
                    "field_name": pure.field_name,
                    "required": required,
                    "expected_db_revision": expected_db_revision,
                    "created_by": created_by,
                },
            )
            await session.commit()
            await session.refresh(row)
            return self._work_item_payload(row)

    async def decide_reconciliation_row(
        self,
        *,
        row_id: str,
        project_id: str,
        status: str,
        resolution: str,
        actor_type: str,
        actor_user_id: str | None,
        blocker_kind: str | None = None,
        evidence_refs: list[str] | None = None,
        expected_db_revision: int,
        expected_work_item_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Record a decision on one matrix row.

        The authority rule lives in :func:`apply_resolution`, which this method
        calls *before* writing anything. An agent attempting to close a
        judgement row therefore fails with the pure layer's own message and
        leaves no partial write behind.
        """
        from deerflow.persistence.dbtl.cycles import DbtlRevisionConflict, DbtlWorkflowRefused

        try:
            parsed_status = RowStatus(status)
            parsed_actor = ActorType(actor_type)
            parsed_blocker = BlockerKind(blocker_kind) if blocker_kind else None
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        async with self._sf() as session:
            work_row = await session.get(WorkItemRow, row_id)
            if work_row is None or work_row.project_id != project_id:
                raise DbtlWorkflowRefused("Reconciliation row not found.")
            if not work_row.cycle_id:
                raise DbtlWorkflowRefused("Reconciliation row is not attached to a DBTL cycle.")
            current = self._matrix_row(work_row)
            if current is None:
                raise DbtlWorkflowRefused("This work item is not a reconciliation row.")

            loaded = await self._load(session, work_row.cycle_id, project_id, for_update=True)
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded

            replay = await self._replay_event(
                session,
                cycle.id,
                idempotency_key,
                event_type="reconciliation.row_decided",
                expected_payload={
                    "row_id": row_id,
                    "row_status": parsed_status.value,
                    "expected_db_revision": expected_db_revision,
                    "actor_type": parsed_actor.value,
                },
            )
            if replay is not None:
                return self._work_item_payload(work_row)

            self._require_revision(cycle, expected_db_revision)
            if work_row.db_revision != expected_work_item_revision:
                raise DbtlRevisionConflict(f"Reconciliation row {work_row.id} is at revision {work_row.db_revision}, not {expected_work_item_revision}.")
            self._require_reconciliation_rows_mutable(stages)

            updated = apply_resolution(
                current,
                status=parsed_status,
                resolution=resolution,
                actor=parsed_actor,
                actor_user_id=actor_user_id,
                blocker_kind=parsed_blocker,
                evidence_refs=evidence_refs or (),
            )

            statuses = self._statuses(stages)
            payload = dict(work_row.payload or {})
            payload.update(self._row_payload(updated, created_by=str(payload.get("created_by") or "")))
            work_row.payload = payload
            work_row.status = self._work_item_status(updated)
            self._commit_revision(cycle, statuses)
            work_row.db_revision = cycle.db_revision
            await self._record_event(
                session,
                cycle=cycle,
                event_type="reconciliation.row_decided",
                actor_user_id=actor_user_id or f"agent:{parsed_actor.value}",
                payload={
                    "idempotency_key": idempotency_key,
                    "row_id": row_id,
                    "check": updated.check.value,
                    "field_name": updated.field_name,
                    "row_status": updated.status.value,
                    "resolution": updated.resolution,
                    "actor_type": parsed_actor.value,
                    "blocker_kind": updated.blocker_kind.value if updated.blocker_kind else None,
                    "expected_db_revision": expected_db_revision,
                    "expected_work_item_revision": expected_work_item_revision,
                },
            )
            await session.commit()
            await session.refresh(work_row)
            return self._work_item_payload(work_row)

    # -- worker runs -----------------------------------------------------

    async def get_stage_execution_replay(
        self,
        cycle_id: str,
        *,
        project_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        """Return a completed live-stage event so retries never rerun workers."""
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(DbtlEventRow).where(
                        DbtlEventRow.cycle_id == cycle_id,
                        DbtlEventRow.project_id == project_id,
                        DbtlEventRow.event_type == "stage.workers_recorded",
                    )
                )
            ).scalars()
            for row in rows:
                payload = dict(row.payload or {})
                if payload.get("idempotency_key") == idempotency_key:
                    return payload
        return None

    async def record_worker_runs(
        self,
        *,
        cycle_id: str,
        project_id: str,
        stage: str,
        stage_spec_key: str,
        results: list[dict[str, Any]],
        actor_user_id: str,
        expected_db_revision: int,
        idempotency_key: str,
        artifact_type: str | None = None,
        artifact_uri: str | None = None,
        artifact_content_hash: str | None = None,
        reviewed_artifact_id: str | None = None,
        reviewed_artifact_revision: int | None = None,
        reviewed_artifact_content_hash: str | None = None,
    ) -> list[dict[str, Any]]:
        """Persist one stage fan-out's structured results.

        Failed workers are stored too. A fan-out where two of three workers
        crashed must not read afterwards as a tidy run with one worker.
        """
        from deerflow.dbtl.stage_spec import StageSpecNotFound, resolve_spec_by_key
        from deerflow.persistence.dbtl.cycles import DbtlWorkflowRefused

        if stage not in STAGE_ORDER:
            raise ValueError(f"Unknown stage {stage!r}.")
        artifact_values = (artifact_type, artifact_uri, artifact_content_hash)
        if any(artifact_values) and not all(artifact_values):
            raise ValueError("A stage execution artifact requires type, URI, and content hash together.")
        if artifact_content_hash is not None and not _is_sha256(artifact_content_hash):
            raise ValueError("A stage execution artifact requires a lowercase SHA-256 hash.")
        reviewed_values = (
            reviewed_artifact_id,
            reviewed_artifact_revision,
            reviewed_artifact_content_hash,
        )
        if any(value is not None for value in reviewed_values) and not all(value is not None for value in reviewed_values):
            raise ValueError("A review meeting must bind the reviewed artifact id, revision, and content hash together.")
        if reviewed_artifact_content_hash is not None and not _is_sha256(reviewed_artifact_content_hash):
            raise ValueError("Reviewed evidence requires a lowercase SHA-256 hash.")
        if reviewed_artifact_id is not None and artifact_type != f"{stage}_review_meeting":
            raise ValueError("Only a stage review-meeting artifact may carry a reviewed-evidence binding.")
        results_digest = hashlib.sha256(
            json.dumps(
                results,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        try:
            spec = resolve_spec_by_key(stage_spec_key)
        except StageSpecNotFound as exc:
            raise ValueError(str(exc)) from exc
        if spec.stage != stage:
            raise ValueError(f"Stage spec {stage_spec_key!r} belongs to {spec.stage!r}, not {stage!r}.")

        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id, for_update=True)
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            replay = await self._replay_event(
                session,
                cycle_id,
                idempotency_key,
                event_type="stage.workers_recorded",
                expected_payload={
                    "stage": stage,
                    "stage_spec_key": stage_spec_key,
                    "results_digest": results_digest,
                    "artifact_type": artifact_type,
                    "artifact_uri": artifact_uri,
                    "artifact_content_hash": artifact_content_hash,
                    "reviewed_artifact_id": reviewed_artifact_id,
                    "reviewed_artifact_revision": reviewed_artifact_revision,
                    "reviewed_artifact_content_hash": reviewed_artifact_content_hash,
                    "expected_db_revision": expected_db_revision,
                },
            )
            if replay is not None:
                return await self._worker_runs_for(session, cycle_id, stage)

            self._require_revision(cycle, expected_db_revision)
            attempt = next((item for item in stages if item.stage == stage), None)
            if attempt is None:
                raise DbtlWorkflowRefused(f"Unknown stage {stage!r}.")
            if attempt.status not in {
                StageStatus.IN_PROGRESS.value,
                StageStatus.CHANGES_REQUESTED.value,
            }:
                raise DbtlWorkflowRefused(f"Worker evidence cannot be recorded while {stage!r} is {attempt.status!r}.")

            if reviewed_artifact_id is not None:
                reviewed = await session.scalar(
                    select(DbtlArtifactRow).where(
                        DbtlArtifactRow.id == reviewed_artifact_id,
                        DbtlArtifactRow.project_id == project_id,
                        DbtlArtifactRow.cycle_id == cycle_id,
                        DbtlArtifactRow.stage_attempt_id == attempt.id,
                    )
                )
                if reviewed is None or reviewed.revision != reviewed_artifact_revision or reviewed.content_hash != reviewed_artifact_content_hash:
                    raise DbtlWorkflowRefused("The review meeting's evidence binding no longer matches this stage attempt.")

            attempt.stage_spec_key = stage_spec_key
            # A unit is recorded once per stage attempt — that is what the unique
            # index says, and the Build workflow made it reachable: a run whose
            # phases all replayed from the durable chain arrives here with the
            # same unit ids the earlier run already recorded, and the insert
            # raised an unhandled IntegrityError. Skipping a unit already on this
            # attempt keeps the record truthful (the worker ran once, and once is
            # what is stored) and lets the run go on to attach the artifact it
            # came here to attach.
            already_recorded = set(
                (
                    await session.scalars(
                        select(DbtlStageWorkerRunRow.unit_id).where(
                            DbtlStageWorkerRunRow.stage_attempt_id == attempt.id,
                            DbtlStageWorkerRunRow.project_id == project_id,
                        )
                    )
                ).all()
            )
            for index, result in enumerate(results):
                unit_id = str(result.get("unit_id") or f"{attempt.id}-{index + 1}")
                if unit_id in already_recorded:
                    continue
                already_recorded.add(unit_id)
                session.add(
                    DbtlStageWorkerRunRow(
                        id=f"worker-{uuid4()}",
                        project_id=project_id,
                        cycle_id=cycle_id,
                        stage_attempt_id=attempt.id,
                        unit_id=unit_id,
                        stage_spec_key=stage_spec_key,
                        capability=str(result.get("capability") or ""),
                        agent_name=str(result.get("agent_name") or ""),
                        via_generalist=bool(result.get("via_generalist", False)),
                        status=str(result.get("status") or "failed"),
                        stop_reason=result.get("stop_reason"),
                        result=dict(result),
                    )
                )

            statuses = self._statuses(stages)
            artifact_id = None
            artifact_revision = None
            if artifact_type is not None:
                highest = await session.scalar(
                    select(func.max(DbtlArtifactRow.revision)).where(
                        DbtlArtifactRow.stage_attempt_id == attempt.id,
                        DbtlArtifactRow.artifact_type == artifact_type,
                    )
                )
                artifact_revision = int(highest or 0) + 1
                artifact = DbtlArtifactRow(
                    id=f"artifact-{uuid4()}",
                    project_id=project_id,
                    cycle_id=cycle_id,
                    stage_attempt_id=attempt.id,
                    artifact_type=artifact_type,
                    revision=artifact_revision,
                    content_hash=artifact_content_hash,
                    uri=artifact_uri,
                    created_by=actor_user_id,
                )
                artifact_id = artifact.id
                session.add(artifact)
            self._commit_revision(cycle, statuses)
            attempt.db_revision = cycle.db_revision
            await self._record_event(
                session,
                cycle=cycle,
                event_type="stage.workers_recorded",
                actor_user_id=actor_user_id,
                payload={
                    "idempotency_key": idempotency_key,
                    "stage": stage,
                    "stage_spec_key": stage_spec_key,
                    "results_digest": results_digest,
                    "worker_count": len(results),
                    "trustworthy_count": sum(
                        1
                        for item in results
                        if item.get(
                            "counts_toward_stage_output",
                            item.get("is_trustworthy"),
                        )
                        and item.get("is_trustworthy")
                    ),
                    "artifact_id": artifact_id,
                    "artifact_type": artifact_type,
                    "artifact_revision": artifact_revision,
                    "artifact_uri": artifact_uri,
                    "artifact_content_hash": artifact_content_hash,
                    "reviewed_artifact_id": reviewed_artifact_id,
                    "reviewed_artifact_revision": reviewed_artifact_revision,
                    "reviewed_artifact_content_hash": reviewed_artifact_content_hash,
                    "expected_db_revision": expected_db_revision,
                },
            )
            await session.commit()
            return await self._worker_runs_for(session, cycle_id, stage)

    async def _worker_runs_for(self, session, cycle_id: str, stage: str) -> list[dict[str, Any]]:
        from deerflow.persistence.dbtl.cycles import _iso

        rows = (
            await session.execute(
                select(DbtlStageWorkerRunRow)
                .join(DbtlStageAttemptRow, DbtlStageAttemptRow.id == DbtlStageWorkerRunRow.stage_attempt_id)
                .where(DbtlStageWorkerRunRow.cycle_id == cycle_id, DbtlStageAttemptRow.stage == stage)
                # Several workers commonly commit in one flush and therefore
                # share a timestamp. UUID order would make the review surface
                # shuffle across reads; unit IDs preserve the stage plan order.
                .order_by(DbtlStageWorkerRunRow.created_at.asc(), DbtlStageWorkerRunRow.unit_id.asc())
            )
        ).scalars()
        return [
            {
                "id": row.id,
                "unit_id": row.unit_id,
                "stage_spec_key": row.stage_spec_key,
                "capability": row.capability,
                "agent_name": row.agent_name,
                "via_generalist": row.via_generalist,
                "status": row.status,
                "stop_reason": row.stop_reason,
                "result": dict(row.result or {}),
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ]

    async def list_worker_runs(self, cycle_id: str, *, project_id: str, stage: str) -> list[dict[str, Any]]:
        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id)
            if loaded is None:
                return []
            return await self._worker_runs_for(session, cycle_id, stage)

    # -- the gate --------------------------------------------------------

    async def _reconciliation_gate(
        self,
        session,
        cycle_id: str,
        project_id: str,
        stages: list[DbtlStageAttemptRow],
    ) -> GateEvaluation:
        """Evaluate the gate inside a caller's session.

        Session-scoped because the submission path already holds the cycle row
        ``FOR UPDATE``; opening a second session there would read around its own
        uncommitted work and, on PostgreSQL, wait on the lock it is holding.
        """
        design = next((item for item in stages if item.stage == "design"), None)
        design_approved = design is not None and design.status == StageStatus.APPROVED.value

        work_rows = list(
            (
                await session.execute(
                    select(WorkItemRow).where(
                        WorkItemRow.cycle_id == cycle_id,
                        WorkItemRow.project_id == project_id,
                    )
                )
            ).scalars()
        )
        parsed_rows = [(item, self._matrix_row(item)) for item in work_rows]
        rows = [parsed for _, parsed in parsed_rows if parsed is not None]
        unreadable = [item.id for item, parsed in parsed_rows if parsed is None and dict(item.payload or {}).get("kind") == RECONCILIATION_KIND]

        dataset_rows = (await session.execute(select(DbtlDatasetRow).where(DbtlDatasetRow.cycle_id == cycle_id))).scalars()
        datasets = [self._binding_from_row(item) for item in dataset_rows]

        return evaluate_gate(
            rows,
            datasets,
            design_approved=design_approved,
            unreadable_row_ids=unreadable,
        )

    async def _bind_stage_approval(
        self,
        session,
        cycle,
        attempt: DbtlStageAttemptRow,
        stages: list[DbtlStageAttemptRow],
    ) -> None:
        """Record what an approval was granted against.

        Applied to every stage, not only reconciliation: a Design approval is
        equally a judgement about a particular set of declared inputs, and
        binding only the bridge would leave the earlier gate uncheckable.

        The spec key is filled in only when this attempt does not already carry
        one. An attempt that ran a fan-out recorded the contract it actually
        used, and overwriting that with today's current version would make a
        stale approval look current — the exact comparison this exists to
        support.
        """
        dataset_rows = (await session.execute(select(DbtlDatasetRow).where(DbtlDatasetRow.cycle_id == cycle.id))).scalars()
        attempt.approved_dataset_fingerprint = dataset_fingerprint([self._binding_from_row(item) for item in dataset_rows])
        attempt.approved_policy_version = cycle.policy_version
        if not attempt.stage_spec_key:
            try:
                attempt.stage_spec_key = resolve_stage_spec(attempt.stage).spec_key
            except StageSpecNotFound:
                # Build, Test, and Learn have no spec yet. Leaving the key unset
                # is honest: there is no contract to compare against later.
                attempt.stage_spec_key = None

    async def evaluate_reconciliation(self, cycle_id: str, *, project_id: str) -> GateEvaluation:
        """Compute the readiness gate from durable state.

        The same pure function answers this for the reviewer looking at the
        matrix and for the repository about to accept a submission, so the two
        cannot disagree about whether Build may start.
        """
        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id)
            if loaded is None:
                return evaluate_gate([], [], design_approved=False)
            _, stages = loaded
            return await self._reconciliation_gate(session, cycle_id, project_id, stages)

    async def reconciliation_view(self, cycle_id: str, *, project_id: str) -> dict[str, Any]:
        """Everything the reconciliation matrix needs, in one read."""
        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id)
            if loaded is None:
                return {}
            cycle, stages = loaded
            design = next((item for item in stages if item.stage == "design"), None)
            design_approved = design is not None and design.status == StageStatus.APPROVED.value

            work_rows = list((await session.execute(select(WorkItemRow).where(WorkItemRow.cycle_id == cycle_id, WorkItemRow.project_id == project_id).order_by(WorkItemRow.created_at.asc()))).scalars())
            parsed = [(item, self._matrix_row(item)) for item in work_rows]
            rows = [row for _, row in parsed if row is not None]
            # The row's *own* revision, which is not the cycle's: a decision
            # sends both, and conflating them would make every second decision
            # in a session fail its optimistic check.
            row_revisions = {item.id: item.db_revision for item, row in parsed if row is not None}
            unreadable = [item.id for item, row in parsed if row is None and dict(item.payload or {}).get("kind") == RECONCILIATION_KIND]

            dataset_rows = list((await session.execute(select(DbtlDatasetRow).where(DbtlDatasetRow.cycle_id == cycle_id).order_by(DbtlDatasetRow.source_key.asc()))).scalars())
            datasets = [self._binding_from_row(item) for item in dataset_rows]
            gate = evaluate_gate(
                rows,
                datasets,
                design_approved=design_approved,
                unreadable_row_ids=unreadable,
            )

            attempt = next((item for item in stages if item.stage == "reconciliation"), None)
            binding = None
            if attempt is not None and attempt.approved_dataset_fingerprint:
                binding = ApprovalBinding(
                    dataset_fingerprint=attempt.approved_dataset_fingerprint,
                    stage_spec_key=attempt.stage_spec_key or "",
                    policy_version=attempt.approved_policy_version or "",
                )
            invalidation = None
            if binding is not None:
                try:
                    current_key = resolve_stage_spec("reconciliation").spec_key
                except StageSpecNotFound:  # pragma: no cover - registry always has it
                    current_key = attempt.stage_spec_key or ""
                invalidation = check_approval_still_valid(
                    binding,
                    current_fingerprint=dataset_fingerprint(datasets),
                    current_spec_key=current_key,
                    current_policy_version=cycle.policy_version,
                ).as_dict()

            return {
                "cycle_id": cycle_id,
                "design_approved": design_approved,
                "rows": [{**row.as_dict(), "db_revision": row_revisions[row.row_id]} for row in rows],
                "unreadable_row_ids": unreadable,
                "datasets": [self._dataset_payload(item) for item in dataset_rows],
                "gate": gate.as_dict(),
                "summary": summarize_matrix(rows),
                "approval_binding": binding.as_dict() if binding else None,
                "approval_invalidation": invalidation,
                "db_revision": cycle.db_revision,
            }
