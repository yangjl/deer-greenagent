"""Durable records of a paused Build and the human answer that released it.

Mixed into `DbtlCycleRepository` for the same reason step attempts are: a
collaboration belongs to the cycle aggregate it paused, and a sibling
repository would need its own copy of the revision and idempotency machinery.

Three rules live here rather than in prose.

**One control is open per stage attempt.** A Build pauses in exactly one place.
Opening a second control supersedes the first rather than leaving two cards
competing for the same answer — and the row that is superseded is kept, because
it is the record of what somebody was actually shown.

**A response is idempotent per open control**, not per card id. The card id is
derived so a retried turn re-renders the same question, which means the same
pause recurring mints the same id again on a second row — so keying the answer
on the id alone made every later answer to a recurring pause collide with the
first and be swallowed. A retried delivery replays; a *different* answer under
an explicitly supplied submission id is a conflict, not a silent overwrite.

**An answer is never attributed to an agent.** `responder_user_id` comes from
the authenticated run, and `response_text` is stored exactly as written. A
paraphrase here would put a planner's sentence behind a person's name in the
one record that says who decided to discard finished work.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from deerflow.dbtl.build_control import BuildControlAction
from deerflow.persistence.dbtl.model import DbtlBuildCollaborationRow, DbtlStageAttemptRow
from deerflow.utils.time import coerce_iso

logger = logging.getLogger(__name__)

#: Lifecycle vocabulary, closed. `open` is the only non-terminal value.
OPEN = "open"
ANSWERED = "answered"
HELD = "held"
SUPERSEDED = "superseded"
STALE = "stale"
CANCELLED = "cancelled"

_TERMINAL = frozenset({ANSWERED, HELD, SUPERSEDED, STALE, CANCELLED})

_MAX_TEXT_CHARS = 4_000


class DbtlCollaborationConflict(RuntimeError):
    """The same submission id already carries a different answer."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _bounded(text: str) -> str:
    value = (text or "").strip()
    return value if len(value) <= _MAX_TEXT_CHARS else f"{value[: _MAX_TEXT_CHARS - 1]}…"


def _payload(row: DbtlBuildCollaborationRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "cycle_id": row.cycle_id,
        "stage_attempt_id": row.stage_attempt_id,
        "workflow_spec_key": row.workflow_spec_key,
        "kind": row.kind,
        "step_key": row.step_key,
        "step_run_id": row.step_run_id,
        "plan_digest": row.plan_digest,
        "input_digest": row.input_digest,
        "bound_cycle_revision": int(row.bound_cycle_revision or 0),
        "request_id": row.request_id,
        "response_format": row.response_format,
        "question": row.question,
        "rationale": row.rationale,
        "options": list(row.options or []),
        "recommended_option_id": row.recommended_option_id,
        "lifecycle": row.lifecycle,
        "action": row.action,
        "response_text": row.response_text or "",
        "responder_user_id": row.responder_user_id,
        "client_submission_id": row.client_submission_id,
        "originating_thread_id": row.originating_thread_id,
        "parent_run_id": row.parent_run_id,
        "resumed_step_run_id": row.resumed_step_run_id,
        "meeting_id": row.meeting_id,
        "created_at": coerce_iso(row.created_at),
        "responded_at": coerce_iso(row.responded_at),
    }


def count_control_epochs(actions: Sequence[str]) -> dict[str, int]:
    """How many times a person has asked to replan or restart, from raw actions.

    A free function because **two** callers must agree exactly: whoever opens a
    step against this material, and the read model that recomputes it. A second
    implementation would drift, and the symptom would be perfectly good work
    reported as stale — or, worse, stale work reported as current.

    A restart replans too: it begins again at reading the Design, so every step
    below it is redrawn. Counting it here as well keeps the plan's material
    moving with the design read rather than leaving a restarted Build to reuse
    the plan it was restarted away from.
    """
    restart = sum(1 for value in actions if value == BuildControlAction.RESTART_BUILD.value)
    return {"restart": restart, "replan": restart + sum(1 for value in actions if value == BuildControlAction.REPLAN_BUILD.value)}


class CollaborationOpsMixin:
    """Requires ``self._sf`` (session factory) from the host repository."""

    async def _open_collaboration(self, session: AsyncSession, *, stage_attempt_id: str, project_id: str) -> DbtlBuildCollaborationRow | None:
        found = await session.execute(
            select(DbtlBuildCollaborationRow).where(
                DbtlBuildCollaborationRow.stage_attempt_id == stage_attempt_id,
                DbtlBuildCollaborationRow.project_id == project_id,
                DbtlBuildCollaborationRow.lifecycle == OPEN,
            )
        )
        return found.scalar_one_or_none()

    async def open_build_collaboration(
        self,
        *,
        project_id: str,
        cycle_id: str,
        stage_attempt_id: str,
        request: dict[str, Any],
        originating_thread_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Record a control the server is about to show, or replay the open one.

        Replays when the already-open control carries the same request id: a
        retried turn re-renders the same card, and minting a second row for it
        would make the audit read as two questions where one was asked. A
        *different* control supersedes the open one, because the Build has moved
        and the old card can no longer be answered truthfully.
        """
        request_id = str(request.get("request_id") or "")
        if not request_id:
            raise ValueError("A Build collaboration must carry the request id of the card it records.")

        async with self._sf() as session:
            async with session.begin():
                stage_run = await session.get(DbtlStageAttemptRow, stage_attempt_id)
                if stage_run is None or stage_run.project_id != project_id or stage_run.cycle_id != cycle_id:
                    raise LookupError(f"No stage run {stage_attempt_id!r} in project {project_id!r} cycle {cycle_id!r}.")

                existing = await self._open_collaboration(session, stage_attempt_id=stage_attempt_id, project_id=project_id)
                if existing is not None and existing.request_id == request_id:
                    return _payload(existing)
                if existing is not None:
                    existing.lifecycle = SUPERSEDED
                    existing.updated_at = _utc_now()
                    await session.flush()

                row = DbtlBuildCollaborationRow(
                    id=f"dbc_{uuid.uuid4().hex[:24]}",
                    project_id=project_id,
                    cycle_id=cycle_id,
                    stage_attempt_id=stage_attempt_id,
                    workflow_spec_key=str(request.get("workflow_spec_key") or ""),
                    kind=str(request.get("build_control_kind") or ""),
                    step_key=str(request.get("step_key") or ""),
                    step_run_id=str(request.get("step_run_id") or "") or None,
                    plan_digest=str(request.get("plan_digest") or "") or None,
                    input_digest=str(request.get("input_digest") or "") or None,
                    bound_cycle_revision=int(request.get("cycle_revision") or 0),
                    request_id=request_id,
                    response_format=str(request.get("input_mode") or "single_choice"),
                    question=_bounded(str(request.get("question") or "")),
                    rationale=_bounded(str(request.get("rationale") or "")),
                    options=list(request.get("options") or []),
                    recommended_option_id=str(request.get("recommended_option_id") or "") or None,
                    lifecycle=OPEN,
                    originating_thread_id=originating_thread_id,
                    parent_run_id=parent_run_id,
                    created_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                session.add(row)
                try:
                    await session.flush()
                except IntegrityError as exc:  # pragma: no cover - the index is the arbiter
                    raise DbtlCollaborationConflict(f"Another Build control is already open for stage attempt {stage_attempt_id}.") from exc
                return _payload(row)

    async def answer_build_collaboration(
        self,
        *,
        project_id: str,
        request_id: str,
        action: str,
        response_text: str = "",
        responder_user_id: str | None = None,
        client_submission_id: str | None = None,
        resumed_step_run_id: str | None = None,
        meeting_id: str | None = None,
    ) -> dict[str, Any]:
        """Record the human answer, once.

        **Idempotency is per open control, not per card id.** The card id is
        derived so a retried turn re-renders the same question — which means the
        *same pause recurring* mints the same id again, on a second row. Keying
        the answer on the id alone therefore made every later answer to a
        recurring pause collide with the first one and be swallowed: Retry then
        Replan wrote nothing, the replan epoch never moved, and the committed
        plan replayed while the person's words went nowhere. That is exactly the
        no-op button the epochs exist to prevent, arriving through the write
        path.

        A caller that supplies no submission id is not asserting an identity, so
        idempotency falls back to the payload: the same action and the same
        words replay, anything else conflicts.
        """
        verdict = BuildControlAction(action)
        async with self._sf() as session:
            async with session.begin():
                found = await session.execute(
                    select(DbtlBuildCollaborationRow)
                    .where(
                        DbtlBuildCollaborationRow.request_id == request_id,
                        DbtlBuildCollaborationRow.project_id == project_id,
                    )
                    .order_by(DbtlBuildCollaborationRow.created_at.desc())
                    .with_for_update()
                )
                rows = list(found.scalars().all())
                # The open control wins over a newer terminal one: a person is
                # answering the question in front of them, and an already
                # answered row is the record of a different exchange.
                row = next((entry for entry in rows if entry.lifecycle == OPEN), rows[0] if rows else None)
                if row is None:
                    raise LookupError(f"No Build control {request_id!r} in project {project_id!r}.")

                if row.lifecycle in _TERMINAL:
                    same = row.action == verdict.value and (row.response_text or "") == _bounded(response_text)
                    if same and client_submission_id is not None:
                        same = (row.client_submission_id or None) == client_submission_id
                    if same:
                        return _payload(row)
                    raise DbtlCollaborationConflict(f"Build control {request_id!r} is already {row.lifecycle!r} and cannot take a different answer.")

                row.lifecycle = HELD if verdict is BuildControlAction.HOLD else ANSWERED
                row.action = verdict.value
                row.response_text = _bounded(response_text)
                row.responder_user_id = responder_user_id
                # Falls back to the row's own id so two rows sharing a derived
                # card id cannot collide on the submission uniqueness rule.
                row.client_submission_id = client_submission_id or row.id
                row.resumed_step_run_id = resumed_step_run_id
                row.meeting_id = meeting_id
                row.responded_at = _utc_now()
                row.updated_at = _utc_now()
                await session.flush()
                return _payload(row)

    async def latest_build_collaboration(
        self,
        *,
        project_id: str,
        stage_attempt_id: str,
        lifecycle: str | None = OPEN,
    ) -> dict[str, Any] | None:
        """The newest control for one stage attempt, open by default."""
        async with self._sf() as session:
            statement = select(DbtlBuildCollaborationRow).where(
                DbtlBuildCollaborationRow.stage_attempt_id == stage_attempt_id,
                DbtlBuildCollaborationRow.project_id == project_id,
            )
            if lifecycle:
                statement = statement.where(DbtlBuildCollaborationRow.lifecycle == lifecycle)
            found = await session.execute(statement.order_by(DbtlBuildCollaborationRow.created_at.desc()).limit(1))
            row = found.scalar_one_or_none()
            return _payload(row) if row is not None else None

    async def list_build_collaborations(
        self,
        *,
        project_id: str,
        stage_attempt_id: str,
    ) -> list[dict[str, Any]]:
        """Every control raised on one stage attempt, oldest first."""
        async with self._sf() as session:
            found = await session.execute(
                select(DbtlBuildCollaborationRow)
                .where(
                    DbtlBuildCollaborationRow.stage_attempt_id == stage_attempt_id,
                    DbtlBuildCollaborationRow.project_id == project_id,
                )
                .order_by(DbtlBuildCollaborationRow.created_at)
            )
            return [_payload(row) for row in found.scalars().all()]

    async def build_control_epochs(
        self,
        *,
        project_id: str,
        stage_attempt_id: str,
    ) -> dict[str, int]:
        """How many times a person has asked to replan or restart this Build.

        These counts are what make **Restart** and **Replan** mean something in
        the digest chain. Without them a restart would recompute the same input
        digest as the run it is restarting, `open_step_attempt` would replay the
        committed success, and the button would do nothing at all. They are
        derived from durable rows rather than held in the running process, so
        the read model recomputes exactly what the writer opened its steps
        against — the writer/reader agreement the whole chain depends on.
        """
        async with self._sf() as session:
            found = await session.execute(
                select(DbtlBuildCollaborationRow.action).where(
                    DbtlBuildCollaborationRow.stage_attempt_id == stage_attempt_id,
                    DbtlBuildCollaborationRow.project_id == project_id,
                    DbtlBuildCollaborationRow.lifecycle == ANSWERED,
                )
            )
            actions = [str(value or "") for value in found.scalars().all()]
        return count_control_epochs(actions)
