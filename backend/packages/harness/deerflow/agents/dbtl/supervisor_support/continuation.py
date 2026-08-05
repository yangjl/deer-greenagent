"""Ordered handlers used by the supervisor's cycle-continuation node."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

from deerflow.dbtl.branches import BranchDecision, SupervisorContext
from deerflow.dbtl.build_control import BuildControlAnswer

from .card_history import (
    answered_build_control,
    answered_stage_handoff,
    answered_test_card,
    pending_build_control,
    pending_stage_handoff,
    pending_stage_handoff_control,
    stage_handoff_marker,
)
from .human_input_protocol import TEST_OUTCOME_PREFIX, TEST_REVIEW_PREFIX, receipt_message
from .ports import StageExecutionPort

logger = logging.getLogger(__name__)

StateUpdate = dict[str, Any]


@dataclass(frozen=True, slots=True)
class HandlerResult:
    """A terminal update, or state carried to the next ordered handler."""

    update: StateUpdate | None = None
    handoff_answer: tuple[str, dict[str, Any]] | None = None
    control_answer: BuildControlAnswer | None = None

    @property
    def handled(self) -> bool:
        return self.update is not None


async def handle_stage_handoff(
    *,
    state: dict,
    decision: BranchDecision,
    context: SupervisorContext,
    stage_adapter: StageExecutionPort,
    request_nonce: str,
    validate: Callable[[StageExecutionPort, SupervisorContext, Mapping[str, Any]], Awaitable[str | None]],
    build_card: Callable[..., tuple[BaseMessage, BaseMessage]],
) -> HandlerResult:
    """Render or consume the durable Start/Hold post-approval handoff."""
    pending = pending_stage_handoff(state)
    if pending is not None:
        if str(pending.get("cycle_id") or "") != str(decision.cycle_id or ""):
            return HandlerResult(update={"messages": [receipt_message("The post-approval handoff no longer matches the selected cycle; no stage was started.")]})
        refusal = await validate(stage_adapter, context, pending)
        if refusal is not None:
            return HandlerResult(update={"messages": [receipt_message(refusal)]})
        return HandlerResult(update={"messages": list(build_card(decision, pending, request_nonce=request_nonce))})

    answer = answered_stage_handoff(state)
    if answer is not None and answer[0] == "hold_here":
        next_stage = str(answer[1].get("next_stage") or "the next stage").replace("_", " ").title()
        return HandlerResult(update={"messages": [receipt_message(f"Holding here. {next_stage} remains open, and no stage work was started.")]})
    if answer is not None and answer[0] == "start_next_stage":
        # Validate against the marker shape, not the raw card request. The card
        # names its cycle `dbtl_cycle_id` (the key the frontend reads) while the
        # validator reads `cycle_id`, so passing the request straight through
        # sent an empty cycle id and every Start was refused as "no
        # project-owned cycle" — the button has never worked in production, and
        # the existing test missed it because its fake adapter ignores its
        # arguments.
        refusal = await validate(stage_adapter, context, stage_handoff_marker(answer[1]))
        if refusal is not None:
            return HandlerResult(update={"messages": [receipt_message(refusal)]})
    return HandlerResult(handoff_answer=answer)


async def represent_pending_stage_handoff(
    *,
    state: dict,
    decision: BranchDecision,
    context: SupervisorContext,
    stage_adapter: StageExecutionPort,
    request_nonce: str,
    validate: Callable[[StageExecutionPort, SupervisorContext, Mapping[str, Any]], Awaitable[str | None]],
    build_card: Callable[..., tuple[BaseMessage, BaseMessage]],
) -> StateUpdate | None:
    """Show the outstanding Start/Hold control instead of starting other work.

    The observed escape is a free-text follow-up — "go ahead with build" — that
    arrives with no cycle scope while a Start/Hold card is still unanswered.
    Every existing guard misses it: they fire on a card *answer* or on an
    explicitly scoped request, and this is neither. The card the server emitted
    is the authority here; a request that reaches this point is answered by
    re-presenting it, never by handing the intent to the lead agent.

    Returns ``None`` when :func:`pending_stage_handoff_control` says no control
    is waiting for *this* request — nothing outstanding, an answer to another
    server card, a card belonging to a different cycle, or one whose refusal has
    already been stated.

    A stale card is refused **once**. The receipt records which card it closes,
    which releases the fence: repeating the refusal on every later message would
    make ordinary work unreachable for the life of the thread, which is worse
    than the escape this exists to prevent.
    """
    request = pending_stage_handoff_control(
        state,
        selected_cycle_id=context.selected_cycle_id or decision.cycle_id,
    )
    if request is None:
        return None
    marker = stage_handoff_marker(request)
    refusal = await validate(stage_adapter, context, marker)
    if refusal is not None:
        return {
            "messages": [
                receipt_message(
                    f"{refusal} You can continue working in this conversation.",
                    stage_handoff_refused=str(request.get("request_id") or ""),
                )
            ]
        }
    return {"messages": list(build_card(decision, marker, request_nonce=request_nonce))}


async def handle_test_cards(
    *,
    state: dict,
    config: RunnableConfig,
    context: SupervisorContext,
    decision: BranchDecision,
    stage_adapter: StageExecutionPort,
    request_nonce: str,
    render_continuation: Callable[[BranchDecision, str], str],
    present_artifacts: Callable[..., Sequence[BaseMessage]],
    build_test_card: Callable[..., tuple[BaseMessage, BaseMessage]],
    build_stage_handoff: Callable[..., tuple[BaseMessage, BaseMessage]],
) -> HandlerResult:
    """Handle server-bound Test review and outcome card answers."""
    outcome_answer = answered_test_card(state, TEST_OUTCOME_PREFIX)
    if outcome_answer is not None:
        request_id, recommendation, snapshot = outcome_answer
        try:
            recorded = stage_adapter.record_test_outcome(
                project_id=str(context.project_id or ""),
                cycle_id=str(decision.cycle_id or ""),
                snapshot=snapshot,
                recommendation=recommendation,
                config=config,
                idempotency_key=f"{request_id}:{recommendation}",
            )
            if isawaitable(recorded):
                recorded = await recorded
        except AttributeError:
            return HandlerResult(update={"messages": [AIMessage(content="This runtime cannot record a Test outcome from chat yet; no decision was written.")]})
        except Exception as exc:  # noqa: BLE001 - a write refusal must be visible
            logger.warning("Could not record the Test outcome from chat.", exc_info=True)
            return HandlerResult(update={"messages": [AIMessage(content=f"The Test decision was not recorded: {exc}")]})
        assessment = dict(recorded.get("validity_assessment") or {}) if isinstance(recorded, dict) else {}
        next_state = dict(recorded.get("cycle") or {}).get("state") if isinstance(recorded, dict) else None
        messages: list[BaseMessage] = [
            AIMessage(
                content=(
                    f"Recorded your Test decision as {assessment.get('recommendation', recommendation).replace('_', ' ')} "
                    f"with outcome {str(assessment.get('outcome') or snapshot.get('evaluation', {}).get('outcome') or '').replace('_', ' ')}. "
                    f"The cycle is now at {next_state or 'its recorded next stage'}."
                )
            )
        ]
        cycle = dict(recorded.get("cycle") or {}) if isinstance(recorded, dict) else {}
        if next_state == "learn" and recommendation == "advance_to_learn":
            messages.extend(
                build_stage_handoff(
                    decision,
                    {
                        "cycle_id": str(cycle.get("id") or decision.cycle_id or ""),
                        "cycle_revision": int(cycle.get("db_revision") or 0),
                        "approved_stage": "test",
                        "next_stage": "learn",
                        "surface_id": str(
                            snapshot.get("evidence_hash")
                            or snapshot.get("stage_attempt_id")
                            or request_id
                        ),
                    },
                    request_nonce=request_nonce,
                )
            )
        return HandlerResult(update={"messages": messages})

    review_answer = answered_test_card(state, TEST_REVIEW_PREFIX)
    if review_answer is None:
        return HandlerResult()
    _, choice, snapshot = review_answer
    presented: list[BaseMessage] = []
    result: Any = None
    if choice == "convene_review_meeting":
        result = stage_adapter.execute(
            project_id=context.project_id,
            cycle_id=decision.cycle_id,
            request_text="Convene the Test review meeting for the recorded evidence.",
            state=state,
            config=config,
            review_meeting_stage="test",
        )
        if isawaitable(result):
            result = await result
        if not getattr(result, "produced_usable_evidence", False):
            return HandlerResult(update={"messages": [AIMessage(content=render_continuation(decision, result.note))]})
        if getattr(result, "artifact_uri", None):
            presented.extend(
                present_artifacts(
                    decision,
                    note=result.note,
                    artifact_uri=result.artifact_uri,
                    request_nonce=request_nonce,
                    deck_uri=getattr(result, "deck_uri", None),
                )
            )
    try:
        refreshed = stage_adapter.test_review_snapshot(
            project_id=str(context.project_id or ""),
            cycle_id=str(decision.cycle_id or ""),
        )
        if isawaitable(refreshed):
            refreshed = await refreshed
        if isinstance(refreshed, dict):
            snapshot = refreshed
    except AttributeError:
        pass
    artifacts = [path for path in (result.artifact_uri, getattr(result, "deck_uri", None)) if path] if choice == "convene_review_meeting" and getattr(result, "artifact_uri", None) else []
    return HandlerResult(
        update={
            "messages": [*presented, *build_test_card(decision, snapshot, request_nonce=request_nonce, outcome=True)],
            **({"artifacts": artifacts} if artifacts else {}),
        }
    )


async def handle_build_control(
    *,
    state: dict,
    decision: BranchDecision,
    context: SupervisorContext,
    request_nonce: str,
    build_card: Callable[..., tuple[BaseMessage, BaseMessage]],
) -> HandlerResult:
    """Consume a Build-control answer, or re-present the control still waiting.

    Two directions, one handler, because they are the same question asked from
    opposite sides. A valid answer carries the card's own bindings forward to
    the adapter, which re-validates them. Anything else, while a control the
    server raised is still unanswered, *is* an attempt to answer it — the
    observed shape is a free-text follow-up with no cycle scope — and letting
    that reach ordinary chat turns a paused Build into a conversation with the
    lead agent about a build it has no authority to start.

    A control naming a different cycle is not this request's to answer: a
    project runs several builds at once, and the card names the one it belongs
    to.
    """
    answer = answered_build_control(state)
    if answer is not None:
        if answer.cycle_id and decision.cycle_id and answer.cycle_id != str(decision.cycle_id):
            return HandlerResult(update={"messages": [receipt_message("That build control belongs to a different cycle, so nothing was started.")]})
        return HandlerResult(control_answer=answer)

    pending = pending_build_control(state, selected_cycle_id=context.selected_cycle_id or decision.cycle_id)
    if pending is None:
        return HandlerResult()
    if decision.cycle_id and str(pending.get("dbtl_cycle_id") or "") != str(decision.cycle_id):
        # Refused **once**, and the receipt records which control it closes. The
        # fence forces every later request through here, so repeating the
        # refusal would make ordinary work unreachable for the life of the
        # thread — worse than the escape the fence exists to prevent.
        return HandlerResult(
            update={
                "messages": [
                    receipt_message(
                        "That build control belongs to a different cycle, so nothing was started. You can continue working in this conversation.",
                        build_control_refused=str(pending.get("request_id") or ""),
                    )
                ]
            }
        )
    return HandlerResult(update={"messages": list(build_card(decision, pending, request_nonce=request_nonce))})
