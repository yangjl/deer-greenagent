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


class BuildControlNotRecorded(RuntimeError):
    """A chain-moving decision could not be written, so it did not happen."""


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
            recorded = await self.repo.open_build_collaboration(  # type: ignore[union-attr]
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
        # The card names the row it came from. Without it a redelivered answer
        # to an *earlier* emission would settle the control now open — and for
        # Replan and Restart that moves the digest chain a second time, silently
        # discarding committed phases nobody asked to discard again.
        card["collaboration_id"] = str(recorded.get("id") or "")
        return card

    async def record_answer(
        self,
        answer: BuildControlAnswer,
        *,
        resumed_step_run_id: str | None = None,
        meeting_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Attach the human decision to the control it answered.

        Returns the settled record, which is how a caller recovers the question
        the person was answering: the reply carries their words and the option
        they chose, never the sentence that was put to them.
        """
        if not self.available or not answer.request_id:
            return None
        try:
            return await self.repo.answer_build_collaboration(  # type: ignore[union-attr]
                project_id=self.project_id,
                request_id=answer.request_id,
                action=answer.action.value,
                collaboration_id=answer.collaboration_id or None,
                response_text=answer.comment,
                responder_user_id=self.responder_user_id or None,
                resumed_step_run_id=resumed_step_run_id,
                meeting_id=meeting_id,
            )
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.warning("Could not record the answer to Build control %s.", answer.request_id, exc_info=True)
            if answer.action.discards_work:
                # Fail-soft is right for a lost audit row and wrong here: Replan
                # and Restart move the digest chain *through* this record, so a
                # swallowed write is not a missing note, it is a button that did
                # nothing while reporting that it worked.
                raise BuildControlNotRecorded(f"The decision to {answer.action.value.replace('_', ' ')} could not be recorded, so nothing was changed. Try again.") from None
            return None

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

    async def boundary_released(self, plan_digest: str) -> bool:
        """Has a person already told *this* plan to carry on past a boundary?

        A `pause_after` boundary is answered once, and the answer is durable —
        but the only thing consulted was the control riding on the current
        request, so a plan continued in one turn and stopped by a later failure
        paused again at the finished phase on every retry, with no way through.
        The record is what a boundary was crossed *in*, so the record is what
        this reads.

        Scoped to the plan digest for the same reason `plan_is_confirmed` is: a
        replan draws different work, and agreeing to run the previous plan says
        nothing about it. `hold` is excluded — it is the opposite decision.
        """
        if not self.available or not plan_digest:
            return False
        try:
            rows = await self.repo.list_build_collaborations(project_id=self.project_id, stage_attempt_id=self.stage_attempt_id)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.warning("Could not read Build controls for stage attempt %s.", self.stage_attempt_id, exc_info=True)
            return False
        return any(row.get("plan_digest") == plan_digest and row.get("lifecycle") == "answered" and str(row.get("action") or "") in _RESUMING for row in rows)

    async def reopen_card(self) -> dict[str, Any] | None:
        """Rebuild the card for the control still open on this Build, if any.

        The refusal path needs it: a reply is marked answered the moment it
        *resolves*, not when the durable write lands, so a failed Replan told
        the person to try again while the routing fence had already stood down
        and their next words reached ordinary chat. Re-presenting the same
        control from its own durable row makes that instruction reachable.
        """
        row = await self.open_control()
        if not isinstance(row, dict):
            return None
        return {
            "clarification_type": "dbtl_build_control",
            "request_id": str(row.get("request_id") or ""),
            "collaboration_id": str(row.get("id") or ""),
            "build_control_kind": str(row.get("kind") or ""),
            "dbtl_cycle_id": str(row.get("cycle_id") or ""),
            "cycle_revision": int(row.get("bound_cycle_revision") or 0),
            "stage_attempt_id": str(row.get("stage_attempt_id") or ""),
            "workflow_spec_key": str(row.get("workflow_spec_key") or ""),
            "step_key": str(row.get("step_key") or ""),
            "step_run_id": str(row.get("step_run_id") or ""),
            "plan_digest": str(row.get("plan_digest") or ""),
            "input_digest": str(row.get("input_digest") or ""),
            "error_code": "",
            "question": str(row.get("question") or ""),
            "rationale": str(row.get("rationale") or ""),
            "assumptions": [],
            "open_questions": [],
            "build_plan_rows": [],
            "recommended_option_id": str(row.get("recommended_option_id") or ""),
            "input_mode": str(row.get("response_format") or "single_choice"),
            "options": list(row.get("options") or []),
        }

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
    }
)

#: Answers that mean "keep going past where this stopped". `start_build` is
#: deliberately absent: it agrees to run the plan, and the first boundary in
#: that plan has not been reached, let alone shown to anybody.
_RESUMING = frozenset(
    {
        BuildControlAction.CONTINUE_BUILD.value,
        BuildControlAction.RETRY_STEP.value,
    }
)


DISABLED_GATE = BuildControlGate(repo=None, project_id="", cycle_id="", stage_attempt_id="", enabled=False)
