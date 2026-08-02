"""Raising and settling the control a person answers while a Build is paused.

The pure shape lives in `deerflow.dbtl.build_control`; the durable record lives
in the repository. This is the thin seam between them, and it exists so the
adapter does not have to remember three things at every pause: mint the card id
deterministically, write the record *before* the card is shown, and never let
either failure take the Build down with it.

**The id is derived, not random.** A retried turn re-renders the same question,
and a random id would open a second control for it — two cards competing for
one answer, with the durable record unable to say which one was answered. The
id is computed from what the pause *is*: cycle, stage attempt, kind, step, and
the digests the paused work was computed against. Two genuinely different
pauses cannot collide; the same pause twice cannot fork.

**The record is written before the card is shown.** A card with no record is a
question nobody can later prove was asked, and worse, an answer to it has
nothing to bind against. A record with no card is inert.

**Neither failure ends the Build.** This is a control surface attached to a
governance record. If the repository refuses, the person loses the card and
sees the reason in chat — they do not lose an hour of sandbox work, and they do
not get a crash.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from deerflow.agents.dbtl.supervisor_support.human_input_protocol import BUILD_CONTROL_PREFIX, card_request_id
from deerflow.dbtl.build_control import BuildControlAction, BuildControlAnswer, BuildControlRequest

logger = logging.getLogger(__name__)


class _CollaborationRepository(Protocol):
    """The slice of `DbtlCycleRepository` this seam needs."""

    async def open_build_collaboration(self, **kwargs: Any) -> dict[str, Any]: ...

    async def answer_build_collaboration(self, **kwargs: Any) -> dict[str, Any]: ...

    async def latest_build_collaboration(self, **kwargs: Any) -> dict[str, Any] | None: ...

    async def list_build_collaborations(self, **kwargs: Any) -> list[dict[str, Any]]: ...


@dataclass(slots=True)
class BuildControlGate:
    """One Build attempt's pause surface."""

    repo: _CollaborationRepository | None
    project_id: str
    cycle_id: str
    stage_attempt_id: str
    thread_id: str = ""
    run_id: str = ""
    responder_user_id: str = ""
    enabled: bool = True

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.repo is not None and self.project_id and self.cycle_id and self.stage_attempt_id)

    def request_id_for(self, request: BuildControlRequest) -> str:
        return card_request_id(
            BUILD_CONTROL_PREFIX,
            self.cycle_id,
            self.stage_attempt_id,
            request.kind.value,
            # A choice card and the free-text follow-up it raises are two
            # different questions about the same pause. Without this they derive
            # one id, the second collapses onto the first's already-answered
            # record, and the words the person typed reach nothing.
            "text" if request.free_text else "choice",
            request.step_key,
            request.plan_digest,
            request.input_digest,
            request.error_code,
        )

    async def raise_control(self, request: BuildControlRequest) -> dict[str, Any]:
        """Record the control and return the card payload to render.

        Returns the card even when the record could not be written: the person
        can still answer, and the answer is still resolved against the emitted
        card, which is the security boundary. What is lost is the audit row, and
        losing that is better than losing the ability to act.
        """
        card = request.bound_to(self.request_id_for(request)).as_card()
        if not self.available:
            return card
        try:
            await self.repo.open_build_collaboration(  # type: ignore[union-attr]
                project_id=self.project_id,
                cycle_id=self.cycle_id,
                stage_attempt_id=self.stage_attempt_id,
                request=card,
                originating_thread_id=self.thread_id or None,
                parent_run_id=self.run_id or None,
            )
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.warning("Could not record the Build control for stage attempt %s.", self.stage_attempt_id, exc_info=True)
        return card

    async def record_answer(
        self,
        answer: BuildControlAnswer,
        *,
        resumed_step_run_id: str | None = None,
        meeting_id: str | None = None,
    ) -> None:
        """Attach the human decision to the control it answered."""
        if not self.available or not answer.request_id:
            return
        try:
            await self.repo.answer_build_collaboration(  # type: ignore[union-attr]
                project_id=self.project_id,
                request_id=answer.request_id,
                action=answer.action.value,
                response_text=answer.comment,
                responder_user_id=self.responder_user_id or None,
                client_submission_id=answer.request_id,
                resumed_step_run_id=resumed_step_run_id,
                meeting_id=meeting_id,
            )
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.warning("Could not record the answer to Build control %s.", answer.request_id, exc_info=True)

    async def plan_is_confirmed(self, plan_digest: str) -> bool:
        """Has a person already agreed to run *this* plan?

        Scoped to the plan digest rather than to the stage attempt, because a
        replan produces a different plan and agreeing to the previous one says
        nothing about it. A `hold` is deliberately not a confirmation: it is the
        opposite decision, and treating the presence of any answer as consent
        would start a build somebody stopped.
        """
        if not self.available or not plan_digest:
            return False
        try:
            rows = await self.repo.list_build_collaborations(project_id=self.project_id, stage_attempt_id=self.stage_attempt_id)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.warning("Could not read Build controls for stage attempt %s.", self.stage_attempt_id, exc_info=True)
            return False
        return any(row.get("plan_digest") == plan_digest and row.get("lifecycle") == "answered" and str(row.get("action") or "") in _CONFIRMING for row in rows)

    async def open_control(self) -> dict[str, Any] | None:
        """The control still waiting for an answer on this Build, if any."""
        if not self.available:
            return None
        try:
            return await self.repo.latest_build_collaboration(project_id=self.project_id, stage_attempt_id=self.stage_attempt_id)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.warning("Could not read the open Build control for stage attempt %s.", self.stage_attempt_id, exc_info=True)
            return None


#: Answers that mean "run this plan". A hold, a change request, and a meeting
#: request are all answers, and none of them is consent to start.
_CONFIRMING = frozenset(
    {
        BuildControlAction.START_BUILD.value,
        BuildControlAction.CONTINUE_BUILD.value,
        BuildControlAction.RETRY_STEP.value,
        BuildControlAction.ANSWER_DIRECTLY.value,
    }
)


DISABLED_GATE = BuildControlGate(repo=None, project_id="", cycle_id="", stage_attempt_id="", enabled=False)
