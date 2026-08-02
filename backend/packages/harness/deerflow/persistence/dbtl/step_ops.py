"""Append-only Build workflow step attempts.

Mixed into `DbtlCycleRepository` rather than given a sibling repository, for the
same reason reconciliation is: step attempts belong to the same cycle aggregate
as the stage run they hang from, and a second repository would need its own copy
of the revision and idempotency machinery. The first divergence between the two
copies would show up as a lost or double-run step.

Three rules live here rather than in prose:

**Attempts append; they never overwrite.** A retry adds a row. A failed attempt
stays exactly as it was recorded, because it is what a reviewer reads to
understand why a retry happened, and `supersedes_step_run_id` is what links
them.

**A terminal attempt is immutable.** `needs_input` in particular is terminal:
it records the exact question, releases worker ownership, and leaves the stage
paused. A human answer creates a *new* attempt bound to the paused one; nothing
mutates the old row back to running.

**A committed step replays instead of re-running.** `open_step_attempt` looks
for an existing success against the same input digest first, so a duplicate
dispatch — a retried turn, a double-clicked Retry, a redelivered message —
returns the committed work rather than spending a sandbox run to recompute it.
The one-running-attempt rule underneath is a database constraint, not a check
here: two concurrent dispatches can both pass a check-then-write.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from deerflow.dbtl.build_workflow import (
    MAX_BUILD_PHASES,
    BuildErrorCode,
    BuildStepKey,
    StepAttempt,
    StepState,
    build_step_material,
    project_workflow,
    resolve_build_workflow,
    resolve_build_workflow_by_key,
)
from deerflow.dbtl.stage_spec import StageSpecNotFound, resolve_stage_spec
from deerflow.persistence.dbtl.model import DbtlArtifactRow, DbtlBuildCollaborationRow, DbtlCycleRow, DbtlStageAttemptRow, DbtlStageStepRunRow
from deerflow.utils.time import coerce_iso

logger = logging.getLogger(__name__)

#: The partial unique index that enforces "one running attempt per step".
_RUNNING_INDEX = "uq_dbtl_stage_step_running"

#: How long a `running` attempt from a **different** run may sit before a later
#: run may reclaim it.
#:
#: The one-running-attempt rule is what stops two dispatches doing the same
#: work, and it has no expiry — so a Gateway killed mid-phase leaves a row that
#: blocks that step forever, and every later Build silently stops recording
#: rather than colliding with a process that no longer exists. The window is
#: deliberately far longer than any real step: reclaiming early would settle a
#: live worker's row and let a second dispatch run beside it, which is worse
#: than a stuck step somebody can see. A same-run row is never reclaimed, since
#: that is this process's own work.
_ORPHAN_RECLAIM_SECONDS = 6 * 60 * 60

#: Free-text and metadata caps, applied at the **write** boundary.
#:
#: The read model is served through an authenticated endpoint whose contract is
#: "bounded metadata, never raw prompts, secrets, host paths, or shell logs".
#: Bounding on the way out would still have stored the unbounded value, and the
#: next reader of the table — a support bundle, a migration, a debug query —
#: would find it. `execution` accepts scalars only for the same reason: a nested
#: object is where an unreviewed payload rides in.
_MAX_ERROR_SUMMARY_CHARS = 2_000
_MAX_EXECUTION_KEYS = 24
_MAX_EXECUTION_VALUE_CHARS = 512


def _bounded_summary(text: str) -> str:
    value = (text or "").strip()
    if len(value) <= _MAX_ERROR_SUMMARY_CHARS:
        return value
    return f"{value[: _MAX_ERROR_SUMMARY_CHARS - 1]}\u2026"


def _bounded_execution(execution: Any) -> dict[str, Any]:
    """Keep small, scalar, server-owned execution metadata and nothing else.

    Silently dropping is deliberate: this is instrumentation attached to a
    durable governance record, and refusing the whole step because a caller
    attached one oversized value would fail a Build for a logging mistake.
    """
    if not isinstance(execution, dict):
        return {}
    bounded: dict[str, Any] = {}
    for key, value in execution.items():
        if len(bounded) >= _MAX_EXECUTION_KEYS:
            break
        if not isinstance(key, str) or not key or len(key) > 64:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
            bounded[key] = value
        elif isinstance(value, str):
            bounded[key] = value if len(value) <= _MAX_EXECUTION_VALUE_CHARS else f"{value[: _MAX_EXECUTION_VALUE_CHARS - 1]}\u2026"
    return bounded


class DbtlStepConflict(RuntimeError):
    """Another attempt at this step is already running."""


class DbtlStepImmutable(RuntimeError):
    """A settled attempt cannot be settled again or reopened."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _payload(row: DbtlStageStepRunRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "cycle_id": row.cycle_id,
        "stage_attempt_id": row.stage_attempt_id,
        "workflow_spec_key": row.workflow_spec_key,
        "step_key": row.step_key,
        "attempt": row.attempt,
        "status": row.status,
        "phase_index": row.phase_index,
        "phase_key": row.phase_key,
        "plan_digest": row.plan_digest,
        "capability": row.capability,
        "agent_name": row.agent_name,
        "via_generalist": bool(row.via_generalist),
        "input_digest": row.input_digest,
        "output_digest": row.output_digest,
        "predecessor_step_run_ids": list(row.predecessor_step_run_ids or []),
        "parent_run_id": row.parent_run_id,
        "task_id": row.task_id,
        "error_code": row.error_code,
        "error_summary": row.error_summary or "",
        "execution": dict(row.execution or {}),
        "human_input_request_id": row.human_input_request_id,
        "meeting_id": row.meeting_id,
        "supersedes_step_run_id": row.supersedes_step_run_id,
        "started_at": coerce_iso(row.started_at),
        "completed_at": coerce_iso(row.completed_at),
    }


def _as_attempt(row: DbtlStageStepRunRow) -> StepAttempt:
    """The durable row as the pure projection layer understands it."""
    try:
        step = BuildStepKey(row.step_key)
    except ValueError:  # pragma: no cover - a step key retired by a later version
        raise LookupError(f"Unknown Build workflow step {row.step_key!r}.") from None
    try:
        state = StepState(row.status)
    except ValueError:  # pragma: no cover - guarded on the write path
        raise LookupError(f"Unknown step state {row.status!r}.") from None
    error_code: BuildErrorCode | None = None
    if row.error_code:
        try:
            error_code = BuildErrorCode(row.error_code)
        except ValueError:
            # An error code this deployment no longer recognizes still means the
            # attempt failed; losing the whole row over its label would be worse.
            error_code = None
    return StepAttempt(
        step=step,
        attempt=row.attempt,
        state=state,
        input_digest=row.input_digest,
        output_digest=row.output_digest,
        phase_index=row.phase_index,
        phase_key=row.phase_key,
        plan_digest=row.plan_digest,
        capability=row.capability,
        agent_name=row.agent_name,
        via_generalist=bool(row.via_generalist),
        error_code=error_code,
        error_summary=row.error_summary or "",
    )


class StepOpsMixin:
    """Requires ``self._sf`` (session factory) from the host repository."""

    async def _step_rows(
        self,
        session: AsyncSession,
        *,
        stage_attempt_id: str,
        project_id: str,
    ) -> list[DbtlStageStepRunRow]:
        result = await session.execute(
            select(DbtlStageStepRunRow)
            .where(
                DbtlStageStepRunRow.stage_attempt_id == stage_attempt_id,
                DbtlStageStepRunRow.project_id == project_id,
            )
            .order_by(DbtlStageStepRunRow.started_at, DbtlStageStepRunRow.attempt)
        )
        return list(result.scalars().all())

    async def _step_material(
        self,
        session: AsyncSession,
        *,
        stage_run: DbtlStageAttemptRow | None,
        workflow_spec_key: str,
    ) -> dict[BuildStepKey, dict[str, str]]:
        """Assemble the current server-owned material for every step.

        Read from durable state at the moment of the read, which is the whole
        point: a Design approved against a different artifact, a bumped policy
        version, or a new cycle revision must produce different expected
        digests, or the chain detects nothing.
        """
        if stage_run is None:
            return {}
        cycle = await session.get(DbtlCycleRow, stage_run.cycle_id)
        design_artifact: DbtlArtifactRow | None = None
        design_run = await session.execute(
            select(DbtlStageAttemptRow)
            .where(
                DbtlStageAttemptRow.cycle_id == stage_run.cycle_id,
                DbtlStageAttemptRow.stage == "design",
            )
            .order_by(DbtlStageAttemptRow.attempt_number.desc())
            .limit(1)
        )
        design_stage = design_run.scalar_one_or_none()
        if design_stage is not None:
            found = await session.execute(select(DbtlArtifactRow).where(DbtlArtifactRow.stage_attempt_id == design_stage.id).order_by(DbtlArtifactRow.revision.desc()).limit(1))
            design_artifact = found.scalar_one_or_none()

        # The **currently resolved** contract, not the one stored on the row.
        # `record_worker_runs` writes `stage_run.stage_spec_key` partway through
        # the very run whose steps are being recorded, so reading it made the
        # material change mid-Build and every earlier step read as invalidated
        # the moment execution committed. Resolving it answers the question that
        # actually matters — "would today's contract produce this?" — and is
        # stable within a run, moving only on a real contract upgrade.
        try:
            stage_spec_key = resolve_stage_spec(stage_run.stage).spec_key
        except StageSpecNotFound:
            stage_spec_key = stage_run.stage_spec_key

        # Counted here, in the same session and from the same rows the read
        # model uses, because a restart or a replan a person asked for has to
        # move the chain: a restart that recomputed the same digest would replay
        # the committed success it exists to discard.
        answered = await session.execute(
            select(DbtlBuildCollaborationRow.action).where(
                DbtlBuildCollaborationRow.stage_attempt_id == stage_run.id,
                DbtlBuildCollaborationRow.project_id == stage_run.project_id,
                DbtlBuildCollaborationRow.lifecycle == "answered",
            )
        )
        actions = [str(value or "") for value in answered.scalars().all()]
        restart_epoch = sum(1 for value in actions if value == "restart_build")
        replan_epoch = restart_epoch + sum(1 for value in actions if value == "replan_build")

        return build_step_material(
            workflow_spec_key=workflow_spec_key,
            design_artifact_id=design_artifact.id if design_artifact else None,
            design_content_hash=design_artifact.content_hash if design_artifact else None,
            design_review_id=None,
            policy_version=stage_run.approved_policy_version or (cycle.policy_version if cycle else None),
            stage_spec_key=stage_spec_key,
            dataset_fingerprint=stage_run.approved_dataset_fingerprint,
            restart_epoch=restart_epoch,
            replan_epoch=replan_epoch,
        )

    async def build_step_material_for(
        self,
        *,
        project_id: str,
        stage_attempt_id: str,
        workflow_spec_key: str,
    ) -> dict[str, dict[str, str]]:
        """The current material, for the caller that is about to *open* a step.

        Public because the writer and the read model must agree: a step opened
        against material the projection then recomputes differently would be
        reported stale the moment it succeeded. One query, one function, two
        callers — the alternative is two implementations and a drift nobody sees
        until good work is reported as invalid.

        Keyed by the plain step name so a caller need not import the enum.
        """
        async with self._sf() as session:
            stage_run = await session.get(DbtlStageAttemptRow, stage_attempt_id)
            if stage_run is None or stage_run.project_id != project_id:
                return {}
            material = await self._step_material(session, stage_run=stage_run, workflow_spec_key=workflow_spec_key)
        return {step.value: dict(values) for step, values in material.items()}

    async def open_step_attempt(
        self,
        *,
        project_id: str,
        cycle_id: str,
        stage_attempt_id: str,
        workflow_spec_key: str,
        step_key: str,
        input_digest: str,
        predecessor_step_run_ids: list[str] | None = None,
        phase_index: int | None = None,
        phase_key: str | None = None,
        plan_digest: str | None = None,
        capability: str | None = None,
        agent_name: str | None = None,
        via_generalist: bool = False,
        parent_run_id: str | None = None,
        task_id: str | None = None,
        execution: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Start an attempt, or replay a committed one.

        Returns ``(attempt, dispatched)``. ``dispatched`` is ``False`` when an
        existing success against this exact `input_digest` was returned instead
        — the resume algorithm's "replay an already-committed key without
        dispatch". Raises :class:`DbtlStepConflict` when another attempt at the
        same step is already running; that refusal comes from the database's
        partial unique index rather than from a check here, because two
        concurrent dispatches can both pass a check.
        """
        # Resolving the workflow validates the key before anything is written;
        # an attempt that cannot name its contract is unreviewable later.
        resolve_build_workflow_by_key(workflow_spec_key)
        step = BuildStepKey(step_key)

        # The database compares `phase_slot`, never the nullable `phase_key`.
        phase_slot = phase_key or ""

        async with self._sf() as session:
            async with session.begin():
                # The stage run must exist and belong to this project *and*
                # cycle. The foreign key alone proves neither: it accepts a
                # stage run from a different project's cycle, which would file
                # a step attempt under a Build nobody is looking at.
                stage_run = await session.get(DbtlStageAttemptRow, stage_attempt_id)
                if stage_run is None or stage_run.project_id != project_id or stage_run.cycle_id != cycle_id:
                    raise LookupError(f"No stage run {stage_attempt_id!r} in project {project_id!r} cycle {cycle_id!r}.")

                existing = await session.execute(
                    select(DbtlStageStepRunRow).where(
                        DbtlStageStepRunRow.stage_attempt_id == stage_attempt_id,
                        DbtlStageStepRunRow.step_key == step.value,
                        DbtlStageStepRunRow.phase_slot == phase_slot,
                    )
                )
                rows = list(existing.scalars().all())

                # An evidence chain that names a predecessor which does not
                # exist, or belongs to another stage run, is not a chain. The
                # whole point of recording predecessors is that a later reader
                # can walk them.
                predecessors = list(predecessor_step_run_ids or [])
                if predecessors:
                    found = await session.execute(
                        select(DbtlStageStepRunRow.id).where(
                            DbtlStageStepRunRow.id.in_(predecessors),
                            DbtlStageStepRunRow.stage_attempt_id == stage_attempt_id,
                            DbtlStageStepRunRow.project_id == project_id,
                            DbtlStageStepRunRow.status == StepState.SUCCEEDED.value,
                        )
                    )
                    known = set(found.scalars().all())
                    missing = [entry for entry in predecessors if entry not in known]
                    if missing:
                        raise LookupError(f"Unknown or unusable predecessor step attempts for stage run {stage_attempt_id}: {missing}.")

                committed = [row for row in rows if row.status == StepState.SUCCEEDED.value and row.input_digest == input_digest]
                if committed:
                    # Newest wins, matching the projection's selection rule.
                    return _payload(max(committed, key=lambda row: row.attempt)), False

                # A step left running by a process that is gone. Settled as
                # `cancelled` rather than `failed`: work taken away and work
                # gone wrong are different things to a reader, and nobody
                # observed this one fail.
                now = _utc_now()
                for stale in rows:
                    if stale.status != StepState.RUNNING.value:
                        continue
                    if parent_run_id and stale.parent_run_id == parent_run_id:
                        continue
                    started = stale.started_at
                    if started is None:
                        continue
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=UTC)
                    if (now - started).total_seconds() < _ORPHAN_RECLAIM_SECONDS:
                        continue
                    stale.status = StepState.CANCELLED.value
                    stale.error_code = BuildErrorCode.CANCELLED.value
                    stale.error_summary = _bounded_summary("The run that owned this attempt ended without settling it, so a later run reclaimed the step.")
                    stale.completed_at = now
                await session.flush()

                next_attempt = max((row.attempt for row in rows), default=0) + 1
                superseded = max(rows, key=lambda row: row.attempt) if rows else None
                row = DbtlStageStepRunRow(
                    id=f"dss_{uuid.uuid4().hex[:24]}",
                    project_id=project_id,
                    cycle_id=cycle_id,
                    stage_attempt_id=stage_attempt_id,
                    workflow_spec_key=workflow_spec_key,
                    step_key=step.value,
                    attempt=next_attempt,
                    status=StepState.RUNNING.value,
                    phase_index=phase_index,
                    phase_key=phase_key,
                    phase_slot=phase_slot,
                    plan_digest=plan_digest,
                    capability=capability,
                    agent_name=agent_name,
                    via_generalist=bool(via_generalist),
                    input_digest=input_digest,
                    output_digest=None,
                    predecessor_step_run_ids=predecessors,
                    parent_run_id=parent_run_id,
                    task_id=task_id,
                    error_summary="",
                    execution=_bounded_execution(execution),
                    supersedes_step_run_id=superseded.id if superseded else None,
                    started_at=_utc_now(),
                )
                # A fast path with an honest message. The index below is still
                # the arbiter — two concurrent dispatches can both pass this.
                if any(existing_row.status == StepState.RUNNING.value for existing_row in rows):
                    raise DbtlStepConflict(f"An attempt at {step.value!r} is already running for stage attempt {stage_attempt_id}.")

                session.add(row)
                try:
                    await session.flush()
                except IntegrityError as exc:
                    # Only the one-running-attempt index means "conflict".
                    # Reporting every integrity failure that way would present a
                    # missing stage run or a dangling project as a busy step,
                    # sending whoever debugs it to look at concurrency.
                    if _RUNNING_INDEX in str(getattr(exc, "orig", exc)):
                        raise DbtlStepConflict(f"An attempt at {step.value!r} is already running for stage attempt {stage_attempt_id}.") from exc
                    raise
                return _payload(row), True

    async def settle_step_attempt(
        self,
        *,
        step_run_id: str,
        project_id: str,
        status: str,
        output_digest: str | None = None,
        error_code: str | None = None,
        error_summary: str = "",
        human_input_request_id: str | None = None,
        meeting_id: str | None = None,
        execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record an attempt's terminal outcome, once.

        Refuses a non-terminal status and refuses to settle an already-settled
        attempt: a second write would rewrite what a reviewer already read, and
        reopening a `needs_input` row in place would erase the question it is
        holding.
        """
        state = StepState(status)
        if not state.is_terminal:
            raise ValueError(f"{status!r} is not a terminal step state.")
        # A success is what every downstream step's identity is computed from,
        # so one without an output digest would be selected as a predecessor
        # and contribute nothing to the chain that makes staleness detectable.
        if state is StepState.SUCCEEDED and not (output_digest or "").strip():
            raise ValueError("A succeeded step attempt must record an output digest.")

        async with self._sf() as session:
            async with session.begin():
                row = await session.get(DbtlStageStepRunRow, step_run_id, with_for_update=True)
                if row is None or row.project_id != project_id:
                    raise LookupError(f"No step attempt {step_run_id!r} in project {project_id!r}.")
                if row.status != StepState.RUNNING.value:
                    raise DbtlStepImmutable(f"Step attempt {step_run_id} already settled as {row.status!r}.")

                row.status = state.value
                row.output_digest = output_digest if state is StepState.SUCCEEDED else None
                row.error_code = error_code
                row.error_summary = _bounded_summary(error_summary)
                row.human_input_request_id = human_input_request_id
                row.meeting_id = meeting_id
                if execution:
                    row.execution = _bounded_execution({**dict(row.execution or {}), **execution})
                row.completed_at = _utc_now()
                await session.flush()
                return _payload(row)

    async def list_step_attempts(
        self,
        *,
        project_id: str,
        stage_attempt_id: str,
    ) -> list[dict[str, Any]]:
        """Every attempt at every step of one stage run, oldest first."""
        async with self._sf() as session:
            rows = await self._step_rows(session, stage_attempt_id=stage_attempt_id, project_id=project_id)
            return [_payload(row) for row in rows]

    async def build_workflow_view(
        self,
        *,
        project_id: str,
        stage_attempt_id: str,
        workflow_spec_key: str | None = None,
        expected_inputs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """The workflow read model: ordered steps, what is selected, what is stale.

        The projection is computed rather than stored, for the same reason a
        feedback surface derives `is_current`: a stored "selected" flag could
        disagree with the digests it was derived from, and the disagreement
        would surface as a reviewer reading evidence the system no longer
        considers valid.
        """
        async with self._sf() as session:
            rows = await self._step_rows(session, stage_attempt_id=stage_attempt_id, project_id=project_id)
            stage_run = await session.get(DbtlStageAttemptRow, stage_attempt_id)
            if stage_run is not None and stage_run.project_id != project_id:
                stage_run = None
            material = await self._step_material(
                session,
                stage_run=stage_run,
                workflow_spec_key=workflow_spec_key or (rows[0].workflow_spec_key if rows else None) or resolve_build_workflow().spec_key,
            )

        spec_key = workflow_spec_key or (rows[0].workflow_spec_key if rows else None)
        spec = resolve_build_workflow_by_key(spec_key) if spec_key else resolve_build_workflow()

        by_id = {row.id: row for row in rows}
        attempts = [_as_attempt(row) for row in rows]
        # Map each pure attempt back to its durable row so the view can report
        # ids, timestamps, and task ids the projection does not carry.
        row_for = {(attempt.step, attempt.phase_key, attempt.attempt): row for attempt, row in zip(attempts, rows, strict=True)}

        typed_expected = {BuildStepKey(key): value for key, value in (expected_inputs or {}).items()}
        # Real, current, server-owned material — not the empty mapping that
        # would make every expected digest synthetic and every staleness
        # judgement meaningless in both directions.
        projection = project_workflow(attempts, spec=spec, expected_inputs=typed_expected or None, step_material=material)

        steps: list[dict[str, Any]] = []
        for step_spec in spec.steps:
            selected = projection.selected.get(step_spec.key)
            selected_row = row_for.get((selected.step, selected.phase_key, selected.attempt)) if selected else None
            step_attempts = [row for row in rows if row.step_key == step_spec.key.value and not row.phase_slot]
            latest = max(step_attempts, key=lambda row: row.attempt) if step_attempts else None
            steps.append(
                {
                    "key": step_spec.key.value,
                    "label": step_spec.label,
                    "executor": step_spec.executor.value,
                    "retryable": step_spec.retryable,
                    "expected_input_digest": projection.expected_inputs.get(step_spec.key, ""),
                    "selected_step_run_id": selected_row.id if selected_row else None,
                    "status": selected.state.value if selected else _observed_status(step_spec.key, latest, projection),
                    "error_code": None if selected else (latest.error_code if latest else None),
                    "attempts": [_payload(row) for row in step_attempts],
                    "invalidated_attempts": [attempt.attempt for attempt in projection.invalidated.get(step_spec.key, ())],
                }
            )

        return {
            "workflow_spec_key": spec.spec_key,
            "stage_attempt_id": stage_attempt_id,
            # The recorded plan, so a reader can name the phases that have not
            # started. Derived from the plan step's own row rather than stored
            # twice: a second copy is a second thing that can be stale, and the
            # symptom would be a rail describing work the Build never planned.
            "plan": _recorded_plan(_selected_plan_row(rows, projection)),
            # Whether the digests above were compared against current durable
            # state. False means the stage run could not be resolved, so the
            # view reports what was attempted without claiming any of it is
            # still valid — a claim it has no material to support.
            "staleness_checked": stage_run is not None,
            "steps": steps,
            "next_step": projection.next_step.value if projection.next_step else None,
            "is_complete": projection.is_complete,
            "phases": [_payload(by_id[row.id]) for row in rows if row.phase_index is not None],
        }


def _observed_status(step: BuildStepKey, latest: DbtlStageStepRunRow | None, projection: Any) -> str:
    """What to show for a step with no selected success.

    **What actually happened outranks where the walk stopped.** Reporting the
    position in the walk alone made a step that was running, that failed, that
    is waiting on a person, or that was cancelled all read as `queued` — so the
    one question the read model exists to answer, "where did this Build stop and
    why", had no answer. The newest attempt's own state is reported when there
    is one, with `invalidated` as the exception: a success the digest chain
    rejected is not a state the attempt recorded, it is a judgement about it,
    and the projection is the authority on that.

    With no attempt at all, `queued` is the step work resumes at and `waiting`
    is everything behind it — a UI projection rather than a stored state,
    because "not started" and "blocked behind something that has not started"
    look identical in a status column and mean different things to a reader.
    """
    if latest is not None:
        if latest.attempt in {entry.attempt for entry in projection.invalidated.get(step, ())}:
            return StepState.INVALIDATED.value
        return latest.status
    if projection.next_step is None or step is projection.next_step:
        return StepState.QUEUED.value
    order = projection.spec.step_order
    return "waiting" if order.index(step) > order.index(projection.next_step) else StepState.QUEUED.value


def _selected_plan_row(rows: list[DbtlStageStepRunRow], projection: Any) -> DbtlStageStepRunRow | None:
    """The `plan_build` attempt the projection selected, or the newest one.

    Falls back to the newest attempt because a plan that is running, or that
    failed, is still the plan a reader is looking at — reporting no phases at
    all while one is visibly executing would be worse than reporting a plan the
    chain has since invalidated, which the step's own status already says.
    """
    selected = projection.selected.get(BuildStepKey.PLAN_BUILD)
    candidates = [row for row in rows if row.step_key == BuildStepKey.PLAN_BUILD.value and not row.phase_slot]
    if not candidates:
        return None
    if selected is not None:
        for row in candidates:
            if row.attempt == selected.attempt:
                return row
    return max(candidates, key=lambda row: row.attempt)


def _recorded_plan(row: DbtlStageStepRunRow | None) -> dict[str, Any] | None:
    """The plan's shape as the writer flattened it onto the step row."""
    if row is None:
        return None
    execution = dict(row.execution or {})
    phases: list[dict[str, Any]] = []
    for index in range(1, MAX_BUILD_PHASES + 1):
        key = execution.get(f"phase_{index}_key")
        if not isinstance(key, str) or not key:
            continue
        phases.append({"index": index, "phase_key": key, "title": str(execution.get(f"phase_{index}_title") or key)})
    return {
        "feasibility": execution.get("feasibility"),
        "degraded": bool(execution.get("degraded")),
        "phase_count": int(execution.get("phases") or len(phases)),
        "phases": phases,
    }
