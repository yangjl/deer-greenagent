"""The thin project supervisor graph (Phase 5).

    START ──▶ (route) ──┬──▶ ordinary ─────────────▶ END   (the real lead agent)
                        ├──▶ clarification ────────▶ END
                        ├──▶ cycle_setup ──────────▶ END
                        └──▶ cycle_continuation ───▶ END

Four properties are worth explaining, because each is load-bearing for the
phase's no-go ("routing must not drop messages, project scope, file access, or
artifact inspection"):

**The ordinary branch *is* the lead agent.** It is the compiled lead-agent graph
added directly as a node, sharing this graph's state schema — not a
reimplementation and not a wrapper that copies fields across. Every middleware,
tool, sandbox mount, and artifact path therefore behaves exactly as it does
today, because it is the same graph. Nothing about ordinary work is re-derived
here, which is the only way "indistinguishable from the pre-supervisor
experience" can be true rather than aspirational.

**Delegation depends on idempotent reducers.** A compiled child used as a node
returns its *entire final state* as its update, which this graph then re-applies
through its own reducers. That is safe only because every ``ThreadState`` channel
merges by id or key (``add_messages`` dedupes by message id, ``merge_artifacts``
by value) rather than blindly accumulating. A channel added later with a naive
accumulator would duplicate the whole conversation on the first delegated turn,
so ``tests/test_dbtl_supervisor.py`` pins that invariant explicitly.

**Routing reads a value, not ambient state.** The selected project and cycle
arrive as an explicit :class:`SupervisorContext` captured per run from the
request's runtime context. They decide which folder and which research record a
request may touch, so they are passed in rather than looked up mid-graph where a
stale or forged value would be invisible.

**Every branch is terminal.** The supervisor routes once and the graph ends. It
never loops between branches, so one request cannot silently become several, and
"thin" stays checkable rather than being a description of the original intent.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from inspect import isawaitable
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from deerflow.agents.dbtl.supervisor_support.card_history import (
    answers_a_server_card as _answers_a_server_card,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    authored_design as _authored_design,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    card_answer as _card_answer,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    confirmation_answer as _confirmation_answer,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    confirmed_council_depth as _confirmed_council_depth,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    confirmed_council_proposal as _confirmed_council_proposal,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    confirmed_participant_settings as _confirmed_participant_settings,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    council_adjustment as _council_adjustment,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    emitted_card_request as _emitted_card_request,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    has_emitted_card as _has_emitted_card,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    is_new_conversation as _is_new_conversation,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    latest_cycle_request_text as _latest_cycle_request_text,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    latest_stage_handoff_state as _latest_stage_handoff_state,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    latest_user_text as _latest_user_text,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    resumed_council_setup as _resumed_council_setup,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    routing_input as _routing_input,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    unanswered_stage_handoff_card as _unanswered_stage_handoff_card,
)
from deerflow.agents.dbtl.supervisor_support.card_history import (
    wants_roster_adjustment as _wants_roster_adjustment,
)
from deerflow.agents.dbtl.supervisor_support.continuation import (
    handle_stage_handoff,
    handle_test_cards,
    represent_pending_stage_handoff,
)
from deerflow.agents.dbtl.supervisor_support.human_input_protocol import (
    COUNCIL_ADJUST_PREFIX,
    COUNCIL_PREFLIGHT_PREFIX,
    DESIGN_AUTHORING_PREFIX,
    DESIGN_CLARIFICATION_PREFIX,
    PRESENT_ARTIFACT_PREFIX,
    SETUP_CLARIFICATION_PREFIX,
    SETUP_CONFIRMATION_PREFIX,
    STAGE_HANDOFF_PREFIX,
    TEST_OUTCOME_PREFIX,
    TEST_REVIEW_PREFIX,
    build_human_input_messages,
    card_request_id,
    receipt_message,
)
from deerflow.agents.dbtl.supervisor_support.human_input_protocol import (
    MAX_CARD_REQUEST_ID_CHARS as _MAX_CARD_REQUEST_ID_CHARS,
)
from deerflow.agents.dbtl.supervisor_support.ports import StageExecutionPort, compatible_stage_port
from deerflow.dbtl.branches import (
    BranchDecision,
    SupervisorBranch,
    SupervisorContext,
    resolve_branch,
)
from deerflow.dbtl.council import (
    COUNCIL_DEPTH_CONTEXT_KEY,
    DEPTH_INTENT_INSTRUCTION,
    CouncilDepth,
    council_depth_from_config,
    depth_policy,
    interpret_depth,
    request_context,
)
from deerflow.dbtl.council_proposal import (
    proposal_as_dict,
    proposal_from_plan,
)
from deerflow.dbtl.council_settings import (
    participants_payload,
)
from deerflow.dbtl.routing import ExplicitChoice
from deerflow.dbtl.setup_questions import (
    SetupQuestion,
    build_questions_prompt,
    fallback_questions,
    parse_questions_response,
    render_questions,
)

logger = logging.getLogger(__name__)

# Compatibility export while external callers migrate to the protocol module.
MAX_CARD_REQUEST_ID_CHARS = _MAX_CARD_REQUEST_ID_CHARS

# Runtime-context keys the frontend's context chip sets for the *next* request
# only. Read from runtime context rather than ``configurable`` because
# ``configurable`` is checkpointed: a per-request selection written there would
# outlive the request and silently apply to later turns.
SELECTED_CYCLE_CONTEXT_KEY = "dbtl_selected_cycle_id"
EXPLICIT_CHOICE_CONTEXT_KEY = "dbtl_explicit_choice"
#: Set only by the authenticated ``convene_review_meeting`` route, from the
#: stage the *server* registered the deck against. A meeting reads a stage's
#: recorded evidence rather than re-running it, so this is the one signal that
#: makes an ``awaiting_review`` stage dispatchable — it must never come from a
#: client, or a deck could convene a meeting over evidence it never saw.
REVIEW_MEETING_STAGE_CONTEXT_KEY = "dbtl_review_meeting_stage"

_REVIEW_INTENT_RE = re.compile(
    r"""
    ^\s*
    (?:(?:ok(?:ay)?|yes)[,!.]?\s+)?
    (?:i\s+)?
    (?P<decision>
        approve
        |reject
        |request\s+changes
    )
    (?:
        \s+
        (?:
            it
            |this
            |the\s+(?:design|artifact|package|review)
            |revision(?:\s+\d+)?
        )
    )?
    \s*[.!]*\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


async def _validate_stage_handoff(
    stage_adapter: Any,
    context: SupervisorContext,
    marker: Mapping[str, Any],
) -> str | None:
    """Fail closed unless the durable cycle still matches the emitted card."""
    validator = getattr(stage_adapter, "validate_stage_handoff", None)
    if not callable(validator):
        return "This runtime cannot validate the next-stage prompt against current cycle state. Nothing was started."
    try:
        expected_revision = int(marker.get("cycle_revision") or 0)
    except (TypeError, ValueError):
        return "The next-stage prompt has no valid cycle revision. Nothing was started."
    result = validator(
        project_id=context.project_id,
        cycle_id=str(marker.get("cycle_id") or ""),
        expected_db_revision=expected_revision,
        expected_stage=str(marker.get("next_stage") or ""),
    )
    if isawaitable(result):
        result = await result
    return str(result) if isinstance(result, str) and result.strip() else None


#: Runtime-context key carrying the read-only DBTL status the ordinary branch
#: hands to the lead agent. Written per request and never checkpointed, like the
#: parked-design brief beside it.
DBTL_STATUS_CONTEXT_KEY = "dbtl_status_snapshot"

#: A status block is orientation, not a directory. More than this and it stops
#: being read.
_MAX_STATUS_CYCLES = 5


async def _dbtl_status_snapshot(
    stage_adapter: Any,
    context: SupervisorContext,
    state: dict,
) -> dict[str, Any] | None:
    """Assemble what the lead agent must know before it answers in a project.

    The observed takeover began with the lead agent having no idea a governed
    cycle existed: it read "start to build following the approved design" as an
    ordinary request and produced a convincing, ungoverned Build. Handing it the
    active cycles, their stage statuses, and any control still waiting lets it
    explain the boundary instead of walking through it.

    This is awareness only. It grants no authority, and the block the middleware
    renders says so explicitly — the routing and execution fences, not the
    prompt, are what actually stop an ordinary run from taking a stage.
    """
    if not context.project_id:
        return None
    cycles: list[dict[str, Any]] = []
    reader = getattr(stage_adapter, "active_cycle_status", None)
    if callable(reader):
        try:
            read = reader(project_id=context.project_id)
            if isawaitable(read):
                read = await read
            if isinstance(read, list):
                cycles = [item for item in read if isinstance(item, dict)][:_MAX_STATUS_CYCLES]
        except Exception:  # noqa: BLE001 - orientation must never fail a reply
            logger.warning("Could not read DBTL status for project %s.", context.project_id, exc_info=True)
    handoff = _latest_stage_handoff_state(state)
    pending_control = None
    if handoff is not None:
        request, answer = handoff
        pending_control = {
            "kind": "stage_handoff",
            "cycle_id": str(request.get("dbtl_cycle_id") or ""),
            "approved_stage": str(request.get("approved_stage") or ""),
            "next_stage": str(request.get("next_stage") or ""),
            "answered_with": answer,
        }
    if not cycles and pending_control is None:
        return None
    return {
        "project_id": context.project_id,
        "cycles": cycles,
        "pending_control": pending_control,
    }


def _bullets(items: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _review_intent(text: str) -> str | None:
    """Return a narrow, explicit review intent without treating prose as authority."""
    match = _REVIEW_INTENT_RE.fullmatch(text)
    if match is None:
        return None
    return " ".join(match.group("decision").lower().split())


def _render_review_intent_guidance(
    decision: BranchDecision,
    *,
    review_intent: str,
) -> str:
    """Explain the typed review boundary without rerunning stage workers."""
    cycle = decision.cycle_id or "the selected cycle"
    action = {
        "approve": "Approve",
        "reject": "Reject",
        "request changes": "Request changes",
    }[review_intent]
    return "\n".join(
        [
            f"Your message expresses a {review_intent} decision for {cycle}, but chat text cannot record a DBTL review gate.",
            "",
            "Open the current stage in the project review sheet. Submit the stage for review if needed, then choose "
            f"{action} and provide the rationale there. That authenticated action binds your decision to the exact "
            "stage attempt, artifact revision, and project revision you reviewed.",
            "",
            "No meeting participants ran, no new artifact was created, and the existing review package remains unchanged.",
        ]
    )


def _setup_clarification_message(
    decision: BranchDecision,
    context: SupervisorContext,
    *,
    source_request: str,
    request_nonce: str,
    questions: Sequence[SetupQuestion],
) -> tuple[AIMessage, ToolMessage]:
    """Ask the post-approval design questions as a Human Input Card.

    This runs *after* the human approved the cycle, so the framing is "the
    record is being created, here is what Design still needs" rather than a
    gate standing in front of the offer.

    The originating request is stored on the card because the answer has to be
    routed together with it: the answer supplies the fields, the request is what
    says a cycle was being started. Keeping it here rather than re-deriving it
    from message order also means summarization compacting the original turn
    cannot strand the reply.
    """
    note = "I proposed an answer to each — correct the ones that are wrong."
    question = render_questions(questions) or f"Please provide:\n{_bullets(decision.missing_fields)}"
    request_id = card_request_id(SETUP_CLARIFICATION_PREFIX, context.project_id or "", request_nonce, source_request)
    request = {
        "version": 1,
        "kind": "human_input_request",
        "source": "ask_clarification",
        "request_id": request_id,
        "clarification_type": "cycle_setup",
        "title": "Designing this DBTL cycle",
        "question": question,
        "context": note,
        "input_mode": "free_text",
        "source_request": source_request,
        "missing_fields": list(decision.missing_fields),
        # The structured form of what the question text renders, so a later
        # card UI can show per-question fields without emitting them twice.
        "setup_questions": [
            {
                "id": item.id,
                "question": item.question,
                "why": item.why,
                "options": [
                    {
                        "id": option.id,
                        "label": option.label,
                        "description": option.description,
                    }
                    for option in item.options
                ],
                "recommended_option_id": item.recommended_option_id,
                "recommendation": item.recommendation,
                "grounded": item.grounded,
            }
            for item in questions
        ],
    }
    return build_human_input_messages(
        request_id=request_id,
        tool_args={
            "question": question,
            "context": note,
            "clarification_type": "cycle_setup",
        },
        request=request,
        fallback_content=f"{note}\n\n{question}",
    )


def _stage_handoff_message(
    decision: BranchDecision,
    marker: dict[str, Any],
    *,
    request_nonce: str,
) -> tuple[AIMessage, ToolMessage]:
    """Ask the owner before dispatching the stage opened by a deck approval."""
    approved_stage = str(marker["approved_stage"]).strip().lower()
    next_stage = str(marker["next_stage"]).strip().lower()
    cycle_id = str(marker["cycle_id"])
    surface_id = str(marker["surface_id"])
    request_id = card_request_id(
        STAGE_HANDOFF_PREFIX,
        cycle_id,
        request_nonce,
        surface_id,
        approved_stage,
        next_stage,
    )
    approved_label = approved_stage.replace("_", " ").title()
    next_label = next_stage.replace("_", " ").title()
    question = f"{approved_label} is approved. What should happen next?"
    context = f"{next_label} is open for this cycle, but it will not start until you choose. Holding here leaves the approved record unchanged."
    options = [
        {
            "id": "start_next_stage",
            "label": f"Start {next_label}",
            "value": "start_next_stage",
            "description": f"Run the governed {next_label} stage now.",
        },
        {
            "id": "hold_here",
            "label": "Hold here",
            "value": "hold_here",
            "description": "Keep the next stage open without starting work.",
        },
    ]
    request = {
        "version": 1,
        "kind": "human_input_request",
        "source": "ask_clarification",
        "request_id": request_id,
        "clarification_type": "dbtl_stage_handoff",
        "title": f"{approved_label} approved",
        "question": question,
        "context": context,
        "input_mode": "single_choice",
        "options": options,
        "dbtl_cycle_id": decision.cycle_id or cycle_id,
        "cycle_revision": int(marker.get("cycle_revision") or 0),
        "approved_stage": approved_stage,
        "next_stage": next_stage,
        "design_feedback_surface_id": surface_id,
    }
    return build_human_input_messages(
        request_id=request_id,
        tool_args={
            "question": question,
            "context": context,
            "clarification_type": "dbtl_stage_handoff",
            "options": options,
        },
        request=request,
        fallback_content=f"{context}\n\n{question}",
    )


def _render_cycle_setup(decision: BranchDecision, context: SupervisorContext) -> str:
    """One line: what is being offered, and where.

    Everything else has been cut deliberately. The objective is the user's own
    sentence read back to them, the gates and the record effect are the same
    two paragraphs on every card, and the no-record notice restated a promise
    the buttons already make — together they buried a yes/no decision under a
    screen of boilerplate nobody rereads after the first time.

    The gates and the record effect are still the reviewed wording in
    :mod:`deerflow.dbtl.proposal`; the project rail's cycle view and the
    stage-review surfaces remain where a person sees what a cycle commits them
    to. If this card ever needs to carry that weight again, take the strings
    from there rather than retyping them here.
    """
    project = context.project_name or "this project"
    return f"This looks like it could be a DBTL cycle in {project}."


def _setup_confirmation_message(
    decision: BranchDecision,
    context: SupervisorContext,
    *,
    source_request: str,
    request_nonce: str,
) -> tuple[AIMessage, ToolMessage]:
    """Offer the final no-write setup decision through DeerFlow's native card."""
    summary = _render_cycle_setup(decision, context)
    request_id = card_request_id(SETUP_CONFIRMATION_PREFIX, context.project_id or "", request_nonce, source_request, "confirm")
    question = "How should this request proceed?"
    options = [
        {
            "id": "create_cycle",
            "label": "Create this DBTL cycle",
            "value": "create_cycle",
        },
        {
            "id": "keep_ordinary",
            "label": "Keep as ordinary chat",
            "value": "keep_ordinary",
        },
        {
            "id": "not_sure",
            "label": "Not sure",
            "value": "not_sure",
        },
    ]
    objective = decision.objective.strip() or source_request.strip()
    title = objective[:80].strip() or "New DBTL cycle"
    request = {
        "version": 1,
        "kind": "human_input_request",
        "source": "ask_clarification",
        "request_id": request_id,
        "clarification_type": "cycle_setup_confirmation",
        "title": "Review DBTL cycle setup",
        "question": question,
        "context": summary,
        "input_mode": "single_choice",
        "options": options,
        "source_request": source_request,
        "dbtl_cycle_setup": {
            "title": title,
            "objective": objective,
            "success_criteria": source_request[:4_000],
        },
    }
    return build_human_input_messages(
        request_id=request_id,
        tool_args={
            "question": question,
            "context": summary,
            "clarification_type": "cycle_setup_confirmation",
            "options": options,
        },
        request=request,
        fallback_content=f"{summary}\n\n{question}",
    )


def _design_inputs_acknowledgement(state: dict) -> str | None:
    """What to say once the post-approval design questions have been answered.

    Keyed on the questions card rather than on the approval: ``_card_answer``
    reads only the newest message, so by the time these answers arrive the
    approval is no longer the latest reply. That costs nothing, because the
    questions card is only ever raised after an approval — answering it already
    implies one, and the emitted-card check keeps a forged id from faking it.

    Terminal on purpose. The cycle already exists — the authenticated action
    created it at approval — so there is nothing left to confirm, and falling
    through would ask someone to approve what they just approved.
    """
    answered = _card_answer(state, SETUP_CLARIFICATION_PREFIX)
    if answered is None:
        return None
    request_id, _answer = answered
    if _emitted_card_request(state, request_id) is None:
        return None
    return "Recorded your design inputs for this cycle. Open it in the project rail to review the Design stage and submit it for approval — that authenticated review is what satisfies the gate, not this conversation."


def _setup_confirmation_acknowledgement(state: dict) -> str | None:
    """What to say for a confirmation that did *not* approve.

    Approval is deliberately absent here: it is no longer a terminal message
    but the start of the design questions, which the clarification branch
    raises. Returning prose for it would end the turn before they are asked.
    """
    answer = _confirmation_answer(state)
    if answer is None or answer == "create_cycle":
        return None
    if answer == "keep_ordinary":
        return "Kept as ordinary chat. No DBTL cycle was created."
    return "No DBTL cycle was created."


def _render_continuation(decision: BranchDecision, note: str) -> str:
    cycle = decision.cycle_id or "the selected cycle"
    return f"This request is scoped to {cycle}.\n\n{note}\n\nThis run cannot satisfy a review gate. Design and Data reconciliation advance only through the project's human review records."


#: The preflight option that is not a depth. Choosing it asks what should
#: change instead of how much debate to buy, so it is kept out of
#: :class:`CouncilDepth` — a value in that enum is something the council can be
#: run at, and this one is a request to redraw the roster first.
COUNCIL_ADJUST_OPTION = "adjust"

_ADJUST_QUESTION = "What should the meeting do differently? Name the participants to add, drop, or re-aim — your words go to the roster writer exactly as you type them."
_ADJUST_NOTE = "Nothing has been dispatched. The roster is redrawn from what you write here, and you will see it again before anyone runs."


def _render_council_roster(plan) -> str:
    """The roster as a person reads it, not as JSON.

    The card carries the structured payload too, but the text has to stand on
    its own: it is what a reader sees in a plain transcript, in an IM channel,
    and in the run record long after the card stopped being interactive.
    """
    lines = [f"**{plan.seats[0].model}** · {len(plan.seats)} workers"]
    for seat in plan.seats:
        stand_in = " _(no specialist declared — a general-purpose agent is standing in)_" if seat.via_generalist else ""
        tools = "inherits the assistant's tools" if seat.inherits_all_tools else ", ".join(seat.tools)
        lines.append(f"- **{seat.role_label}** — `{seat.agent_name}`{stand_in}  \n  {seat.brief}  \n  Tools: {tools}")
    return "\n".join(lines)


def _council_preflight_message(
    decision: BranchDecision,
    plan,
    recommendation,
    *,
    request_nonce: str,
    model_options: Sequence[str] = (),
) -> tuple[AIMessage, ToolMessage]:
    """Show who will sit in the design meeting, and let the human set it up.

    Emitted before any worker is dispatched, so the choice is real. The depth
    options and the roster ride on the artifact as structured data; the text
    below is the same information for anyone who cannot see the card. The
    roster also rides as ``council_participants`` — one editable card per
    participant, prefilled with the roster writer's suggestions, whose edits
    (model, token budget, reasoning strength, owner instructions) come back on
    the reply and are validated server-side before anyone runs.
    """
    cycle = decision.cycle_id or "selected-cycle"
    request_id = card_request_id(COUNCIL_PREFLIGHT_PREFIX, cycle, request_nonce)
    note = _render_council_roster(plan)
    question = f"How much debate should this design get? {recommendation.reason}"
    options = [
        {
            "id": policy.depth.value,
            "label": policy.label,
            "value": policy.depth.value,
            "description": policy.description,
        }
        for policy in (depth_policy(item) for item in CouncilDepth)
    ]
    # Last, because it is the only option that does not start the council. A
    # roster is a proposal, and a proposal you can only accept or decline is not
    # one — this is how someone says "these seats, but not that one".
    options.append(
        {
            "id": COUNCIL_ADJUST_OPTION,
            "label": "Adjust the roster first",
            "value": COUNCIL_ADJUST_OPTION,
            "description": "Say what should change about who sits and what they argue from. The roster is redrawn and shown again before anyone runs.",
        }
    )
    proposal = proposal_from_plan(plan)
    request = {
        "version": 1,
        "kind": "human_input_request",
        "source": "ask_clarification",
        "request_id": request_id,
        "clarification_type": "council_preflight",
        "dbtl_cycle_id": cycle,
        "title": "Before the design meeting starts",
        "question": question,
        "context": note,
        "input_mode": "single_choice",
        "options": options,
        "council_plan": plan.as_dict(),
        **({"council_proposal": proposal_as_dict(proposal)} if proposal is not None else {}),
        "council_participants": participants_payload(plan, model_options=model_options),
        "recommended_depth": recommendation.depth.value,
        "recommended_option_id": recommendation.depth.value,
    }
    return build_human_input_messages(
        request_id=request_id,
        tool_args={
            "question": question,
            "context": note,
            "clarification_type": "council_preflight",
        },
        request=request,
        fallback_content=f"{note}\n\n{question}",
    )


def _design_clarification_message(
    decision: BranchDecision,
    *,
    note: str,
    question: str,
    request_nonce: str,
    feedback_surface_id: str | None = None,
) -> tuple[AIMessage, ToolMessage]:
    cycle = decision.cycle_id or "selected-cycle"
    request_id = card_request_id(DESIGN_CLARIFICATION_PREFIX, cycle, request_nonce, question)
    request = {
        "version": 1,
        "kind": "human_input_request",
        "source": "ask_clarification",
        "request_id": request_id,
        "clarification_type": "design_decision",
        "dbtl_cycle_id": cycle,
        **({"design_feedback_surface_id": feedback_surface_id} if feedback_surface_id else {}),
        "title": "The design meeting needs your input",
        "question": question,
        "context": note,
        "input_mode": "free_text",
    }
    return build_human_input_messages(
        request_id=request_id,
        tool_args={
            "question": question,
            "context": note,
            "clarification_type": "design_decision",
        },
        request=request,
        fallback_content=f"{note}\n\n{question}",
    )


def _design_authoring_message(
    decision: BranchDecision,
    *,
    note: str,
    question: str,
    request_nonce: str,
) -> tuple[AIMessage, ToolMessage]:
    """Ask the person to write the design, at the depth where nobody else will.

    The card carries its own depth. The client sends a scope with every request
    and falls back to ``ordinary`` for a card it has no special handling for, so
    a reply that carried only the cycle would land back here with no depth, be
    re-planned at the recommended setting, and convene the council the person
    explicitly declined. Reading the depth off the card the *server* emitted
    keeps that decision server-owned, the same rule ``_routing_input`` follows
    for a recovered setup intent.
    """
    cycle = decision.cycle_id or "selected-cycle"
    request_id = card_request_id(DESIGN_AUTHORING_PREFIX, cycle, request_nonce)
    request = {
        "version": 1,
        "kind": "human_input_request",
        "source": "ask_clarification",
        "request_id": request_id,
        "clarification_type": "design_authoring",
        "dbtl_cycle_id": cycle,
        "title": "Write the design yourself",
        "question": question,
        "context": note,
        "input_mode": "free_text",
        "council_depth": CouncilDepth.HUMAN_INPUT.value,
    }
    return build_human_input_messages(
        request_id=request_id,
        tool_args={
            "question": question,
            "context": note,
            "clarification_type": "design_authoring",
        },
        request=request,
        fallback_content=f"{note}\n\n{question}",
    )


def _test_card_messages(
    decision: BranchDecision,
    snapshot: dict[str, Any],
    *,
    request_nonce: str,
    outcome: bool,
) -> tuple[AIMessage, ToolMessage]:
    """Render the Test meeting/route decision in the conversation."""
    evaluation = dict(snapshot.get("evaluation") or {})
    outcome_name = str(evaluation.get("outcome") or "inconclusive")
    meeting = dict(snapshot.get("meeting") or {})
    requirement = str(meeting.get("requirement") or "skipped")
    if outcome:
        prefix = TEST_OUTCOME_PREFIX
        title = "Decide the Test outcome"
        question = f"The server computed Test as {outcome_name.replace('_', ' ')}. What should this cycle do?"
        allowed = {str(item) for item in evaluation.get("allowed_recommendations", [])}
        labels = {
            "advance_to_learn": "Accept outcome and advance to Learn",
            "repeat_test": "Repeat Test",
            "return_to_build": "Return to Build",
            "return_to_design": "Return to Design",
            "close_cycle": "Close this cycle",
        }
        options = [{"id": route, "label": labels[route], "value": route} for route in labels if route in allowed]
    else:
        prefix = TEST_REVIEW_PREFIX
        title = "Test evidence is ready"
        question = "Would you like a Test review meeting before deciding the outcome?"
        options = []
        if requirement in {"optional", "required"}:
            options.append(
                {
                    "id": "convene_review_meeting",
                    "label": "Convene Test review meeting",
                    "value": "convene_review_meeting",
                }
            )
        if requirement != "required":
            options.append(
                {
                    "id": "continue_to_outcome",
                    "label": "Continue to outcome decision",
                    "value": "continue_to_outcome",
                }
            )
    request_id = card_request_id(prefix, decision.cycle_id or "", request_nonce, str(snapshot.get("evidence_hash") or ""))
    context = f"Bound evidence: {snapshot.get('evidence_uri') or 'the recorded Test package'}\nValidity pack: {evaluation.get('validity_pack_key') or 'server default'}"
    request = {
        "version": 1,
        "kind": "human_input_request",
        "source": "ask_clarification",
        "request_id": request_id,
        "clarification_type": "dbtl_test_outcome" if outcome else "dbtl_test_review",
        "title": title,
        "question": question,
        "context": context,
        "input_mode": "single_choice",
        "options": options,
        "dbtl_cycle_id": decision.cycle_id,
        "test_review_snapshot": snapshot,
    }
    return build_human_input_messages(
        request_id=request_id,
        tool_args={
            "question": question,
            "context": context,
            "clarification_type": request["clarification_type"],
            "options": options,
        },
        request=request,
        fallback_content=f"{context}\n\n{question}",
    )


def _council_adjustment_message(
    decision: BranchDecision,
    *,
    request_nonce: str,
) -> tuple[AIMessage, ToolMessage]:
    """Ask what should change about the roster. Free text, deliberately.

    A structured editor would be a form, and the roster's seats are prose —
    what a seat argues *from* is a sentence, not a dropdown. Free text also
    survives a request the option list never anticipated ("nobody who will just
    agree with the agronomist").
    """
    cycle = decision.cycle_id or "selected-cycle"
    request_id = card_request_id(COUNCIL_ADJUST_PREFIX, cycle, request_nonce)
    request = {
        "version": 1,
        "kind": "human_input_request",
        "source": "ask_clarification",
        "request_id": request_id,
        "clarification_type": "council_adjustment",
        "dbtl_cycle_id": cycle,
        "title": "Adjust the design meeting",
        "question": _ADJUST_QUESTION,
        "context": _ADJUST_NOTE,
        "input_mode": "free_text",
    }
    return build_human_input_messages(
        request_id=request_id,
        tool_args={
            "question": _ADJUST_QUESTION,
            "context": _ADJUST_NOTE,
            "clarification_type": "council_adjustment",
        },
        request=request,
        fallback_content=f"{_ADJUST_NOTE}\n\n{_ADJUST_QUESTION}",
    )


def _adapter_known_models(stage_adapter) -> tuple[str, ...]:
    """The configured model names, when the adapter can report them.

    ``getattr`` rather than a protocol requirement so test doubles and older
    adapters keep working; an adapter that cannot name models simply yields a
    card without model pickers and a reply whose model edits are dropped.
    """
    reader = getattr(stage_adapter, "known_models", None)
    if not callable(reader):
        return ()
    try:
        return tuple(str(item) for item in reader() or ())
    except Exception:  # noqa: BLE001 - a broken model listing must not block routing
        logger.debug("Could not read the configured models for the meeting preflight card.", exc_info=True)
        return ()


def _with_council_depth(config: RunnableConfig, depth: CouncilDepth) -> RunnableConfig:
    """A per-request view carrying a depth the server recovered.

    Written into the request ``context`` and nowhere else. ``configurable`` is
    checkpointed, so a depth placed there would keep steering every later turn
    in the thread instead of the one it was recovered for.
    """
    merged = dict(config)
    context = merged.get("context")
    merged["context"] = {
        **(context if isinstance(context, dict) else {}),
        COUNCIL_DEPTH_CONTEXT_KEY: depth.value,
    }
    return merged


def _present_artifact_messages(
    decision: BranchDecision,
    *,
    note: str,
    artifact_uri: str,
    request_nonce: str,
    deck_uri: str | None = None,
) -> tuple[AIMessage, ToolMessage]:
    """Use the same present-files turn shape as the lead agent.

    The reviewed document leads and the slide deck follows it. Order is the
    whole point: the first path is what an approval binds to, and a deck listed
    first would be the one a reader opens and reviews.
    """
    cycle = decision.cycle_id or "selected-cycle"
    filepaths = [path for path in (artifact_uri, deck_uri) if path]
    tool_call_id = card_request_id(PRESENT_ARTIFACT_PREFIX, cycle, request_nonce, *filepaths)
    tool_call = {
        "name": "present_files",
        "args": {"filepaths": filepaths},
        "id": tool_call_id,
        "type": "tool_call",
    }
    return (
        AIMessage(
            id=f"{tool_call_id}:call",
            content=_render_continuation(decision, note),
            tool_calls=[tool_call],
        ),
        ToolMessage(
            id=tool_call_id,
            name="present_files",
            tool_call_id=tool_call_id,
            content="Successfully presented files",
        ),
    )


def make_llm_depth_interpreter():
    """The production depth interpreter: one nostream model call, fail-soft.

    Returns ``None`` when no drafting model is configured, which
    :func:`interpret_depth` reads as "the phrase table is the whole answer".
    Only requests the phrases did not match reach this, and the result is a
    suggestion on a card a person confirms, so a misread costs one dropdown
    change and never a meeting.
    """
    from deerflow.config.app_config import get_app_config

    try:
        app_config = get_app_config()
    except Exception:  # noqa: BLE001 - config trouble keeps the deterministic default
        return None
    model_name = getattr(getattr(app_config, "dbtl", None), "setup_draft_model_name", None)
    if not model_name:
        return None

    async def interpret(prompt: str) -> str:
        from deerflow.utils.oneshot_llm import run_oneshot_llm

        return await run_oneshot_llm(
            system_instruction=DEPTH_INTENT_INSTRUCTION,
            user_content=prompt,
            run_name="dbtl_depth_intent",
            app_config=app_config,
            model_name=model_name,
        )

    return interpret


def _make_llm_question_writer(context: SupervisorContext):
    """The production question writer: one non-graph model call, fail-soft.

    Every failure — drafting disabled, no config, a model outage, a reply that
    is not JSON — resolves to the deterministic gaps. A card that asks plainly
    is a worse card; a turn that raises here would cost the user the cycle they
    just approved, which is not a trade this step is allowed to make.
    """

    async def write(request_text: str, missing_fields: tuple[str, ...]) -> tuple[SetupQuestion, ...]:
        try:
            from deerflow.config.app_config import get_app_config
            from deerflow.utils.oneshot_llm import run_oneshot_llm

            app_config = get_app_config()
            dbtl_config = getattr(app_config, "dbtl", None)
            model_name = getattr(dbtl_config, "setup_draft_model_name", None)
            if not model_name:
                return fallback_questions(missing_fields)

            raw = await run_oneshot_llm(
                system_instruction="You plan research cycles. Reply with JSON only.",
                user_content=build_questions_prompt(
                    request_text=request_text,
                    project_name=context.project_name or "this project",
                    missing_fields=missing_fields,
                ),
                run_name="dbtl_setup_questions",
                app_config=app_config,
                model_name=model_name,
            )
            return parse_questions_response(raw, missing_fields=missing_fields)
        except Exception:  # noqa: BLE001 - a drafting failure must not fail the turn
            logger.warning("DBTL setup questions: drafting failed; asking the deterministic gaps", exc_info=True)
            return fallback_questions(missing_fields)

    return write


def build_supervisor_graph(
    *,
    lead_agent,
    context: SupervisorContext,
    state_schema,
    stage_adapter: StageExecutionPort,
    question_writer=None,
    depth_interpreter=None,
) -> StateGraph:
    """Build (but do not compile) the supervisor graph.

    ``lead_agent`` is the compiled lead-agent graph and ``context`` the run's
    resolved project/cycle selection; both are injected so tests can drive every
    branch without an LLM and without a checkpointed thread.

    ``question_writer`` is the same seam for the post-approval design questions:
    an async callable taking ``(request_text, missing_fields)`` and returning
    :class:`SetupQuestion` values. ``None`` uses the configured model, and any
    failure degrades to the deterministic gaps rather than blocking setup.

    ``depth_interpreter`` is the seam for the preflight card's depth
    recommendation: the phrase table decides first, and only a request it did
    not match is read by the interpreter, so a typo does not silently open the
    card on a depth the owner did not ask for. ``None`` uses the configured
    model and every failure keeps the deterministic default.
    """
    stage_adapter = compatible_stage_port(stage_adapter)
    writer = question_writer or _make_llm_question_writer(context)
    depth_reader = depth_interpreter or make_llm_depth_interpreter()

    def decide(state: dict) -> BranchDecision:
        text, recovered_choice, recovered_cycle_id = _routing_input(state)
        # The recovered choice wins over the request's own. Answering a card is
        # not choosing a scope: the client sends a scope with every request and
        # falls back to "ordinary" for a card it has no special handling for, so
        # honouring it here would route the answer to the lead agent on exactly
        # the turn the server knows what the user is doing. The card is
        # server-emitted and the reply is bound to it, which makes it the better
        # evidence of intent than a field the client always fills in.
        active = replace(
            context,
            selected_cycle_id=recovered_cycle_id or context.selected_cycle_id,
            explicit_choice=recovered_choice if recovered_choice is not None else context.explicit_choice,
            is_new_conversation=_is_new_conversation(state),
        )
        return resolve_branch(text, active)

    async def route(state: dict, config: RunnableConfig) -> str:
        # A review sentence is a control action, not an open-ended prompt. The
        # one-shot cycle selector may already have cleared after the meeting,
        # in which case normal routing would send "I approve the design" to
        # the lead model and leave the UI spinning on unrelated work. Route it
        # to the deterministic review-boundary response in project chat even
        # without a currently selected cycle; it still cannot write a gate.
        if context.project_id and _review_intent(_latest_user_text(state)) is not None:
            return SupervisorBranch.CYCLE_CONTINUATION.value

        # Approval is the hinge of the whole flow, and only the graph can see
        # it: it lives in an answered card rather than in the request text
        # ``resolve_branch`` is given. An approved confirmation moves to the
        # design questions; every other verdict falls through to the setup
        # branch, which says what happened and stops.
        #
        # The emitted-card guard is what keeps this from becoming a loop: an
        # approval stays in the thread's history forever, so without it every
        # later turn — including the answer to the questions themselves — would
        # re-raise the same card.
        if _confirmation_answer(state) == "create_cycle" and not _has_emitted_card(state, SETUP_CLARIFICATION_PREFIX):
            return SupervisorBranch.CLARIFICATION.value

        decision = decide(state)
        if decision.branch is SupervisorBranch.CYCLE_CONTINUATION:
            reader = getattr(stage_adapter, "parked_design_context", None)
            if callable(reader):
                parked = reader(
                    project_id=context.project_id,
                    cycle_id=decision.cycle_id,
                )
                if isawaitable(parked):
                    parked = await parked
                if parked is not None:
                    return SupervisorBranch.ORDINARY.value
        # The routing fence. A request that would otherwise become ordinary work
        # is intercepted when this thread still holds an unanswered Start/Hold
        # card: that card *is* the pending intent, and free text like "go ahead
        # with build" is answering it, not opening a new conversation. Every
        # earlier guard fires only on a card answer or an explicitly scoped
        # request, which is exactly why this path escaped to the lead agent.
        if decision.branch is SupervisorBranch.ORDINARY and context.project_id and _unanswered_stage_handoff_card(state) is not None and not _answers_a_server_card(state):
            return SupervisorBranch.CYCLE_CONTINUATION.value
        logger.debug(
            "dbtl supervisor route: branch=%s source=%s project=%s cycle=%s",
            decision.branch,
            decision.route.source,
            context.project_id,
            decision.cycle_id,
        )
        return decision.branch.value

    async def clarification(state: dict, config: RunnableConfig) -> dict:
        decision = decide(state)
        source_request, _, _ = _routing_input(state)
        raw_context = request_context(config)
        request_nonce = str(raw_context.get("run_id") or "")
        questions = await writer(source_request, decision.missing_fields)
        return {
            "messages": list(
                _setup_clarification_message(
                    decision,
                    context,
                    source_request=source_request,
                    request_nonce=request_nonce,
                    questions=questions,
                )
            )
        }

    def cycle_setup(state: dict, config: RunnableConfig) -> dict:
        decision = decide(state)
        acknowledgement = _setup_confirmation_acknowledgement(state) or _design_inputs_acknowledgement(state)
        if acknowledgement is not None:
            return {"messages": [AIMessage(content=acknowledgement)]}
        source_request, _choice, _cycle_id = _routing_input(state)
        raw_context = request_context(config)
        request_nonce = str(raw_context.get("run_id") or "")
        return {
            "messages": list(
                _setup_confirmation_message(
                    decision,
                    context,
                    source_request=source_request,
                    request_nonce=request_nonce,
                )
            )
        }

    async def cycle_continuation(
        state: dict,
        config: RunnableConfig,
    ) -> dict:
        decision = decide(state)
        raw_context = request_context(config)
        request_nonce = str(raw_context.get("run_id") or "")

        handoff = await handle_stage_handoff(
            state=state,
            decision=decision,
            context=context,
            stage_adapter=stage_adapter,
            request_nonce=request_nonce,
            validate=_validate_stage_handoff,
            build_card=_stage_handoff_message,
        )
        if handoff.handled:
            return handoff.update or {}
        handoff_answer = handoff.handoff_answer

        test_cards = await handle_test_cards(
            state=state,
            config=config,
            context=context,
            decision=decision,
            stage_adapter=stage_adapter,
            request_nonce=request_nonce,
            render_continuation=_render_continuation,
            present_artifacts=_present_artifact_messages,
            build_test_card=_test_card_messages,
        )
        if test_cards.handled:
            return test_cards.update or {}

        request_text = f"Start the governed {str(handoff_answer[1].get('next_stage') or '').replace('_', ' ')} stage now." if handoff_answer is not None and handoff_answer[0] == "start_next_stage" else _latest_cycle_request_text(state)
        review_intent = _review_intent(request_text)
        if review_intent is not None:
            return {
                "messages": [
                    receipt_message(
                        _render_review_intent_guidance(
                            decision,
                            review_intent=review_intent,
                        )
                    )
                ]
            }

        # Nothing above claimed this request, and a Start/Hold control is still
        # waiting. Re-present it rather than dispatching stage work or falling
        # through: the person is answering a question the server already asked,
        # and only the card can carry that answer into a governed dispatch.
        if handoff_answer is None:
            represented = await represent_pending_stage_handoff(
                state=state,
                decision=decision,
                context=context,
                stage_adapter=stage_adapter,
                request_nonce=request_nonce,
                validate=_validate_stage_handoff,
                build_card=_stage_handoff_message,
            )
            if represented is not None:
                return represented
        raw_context = request_context(config)
        # Server-owned, set only by the authenticated convening route. Read here
        # so the Design preflight below is skipped entirely: a review meeting
        # seats its own roster over recorded evidence, and raising the Design
        # council's participant card for it would ask about the wrong meeting.
        requested_meeting = raw_context.get(REVIEW_MEETING_STAGE_CONTEXT_KEY)
        review_meeting_stage = requested_meeting.strip().lower() if isinstance(requested_meeting, str) and requested_meeting.strip() else None
        # Recovered before the preflight check, and it re-supplies the depth the
        # client cannot: without it this answer would look like an ordinary
        # cycle request with no depth set, and the council the person declined
        # would convene on the very turn they submitted their own design.
        authored_design = _authored_design(state)
        if authored_design is not None:
            config = _with_council_depth(config, CouncilDepth.HUMAN_INPUT)
        elif council_depth_from_config(config) is None:
            # The client's value wins when it sent one: it is the same answer
            # arriving by the path that already existed. This only fills the
            # gap when it did not.
            confirmed = _confirmed_council_depth(state)
            if confirmed is not None:
                config = _with_council_depth(config, confirmed)
        request_nonce = str(raw_context.get("run_id") or "")
        adjustment = _council_adjustment(state)

        # Asked for changes rather than a depth: nothing runs, and the question
        # is what to change. Checked before the preflight guard below, which
        # would otherwise see an already-emitted card and fall straight through
        # to dispatching the roster the person just declined.
        if authored_design is None and _wants_roster_adjustment(state):
            return {"messages": list(_council_adjustment_message(decision, request_nonce=request_nonce))}

        # A fresh preflight, or a redraw after an adjustment. The redraw has to
        # bypass the once-only guard: the whole point is to show the roster
        # again, changed.
        answered_adjustment = _card_answer(state, COUNCIL_ADJUST_PREFIX) is not None
        if review_meeting_stage is None and authored_design is None and council_depth_from_config(config) is None and (answered_adjustment or not _has_emitted_card(state, COUNCIL_PREFLIGHT_PREFIX)):
            preview = getattr(stage_adapter, "preview_council", None)
            plan = None
            if callable(preview):
                plan = preview(
                    project_id=context.project_id,
                    cycle_id=decision.cycle_id,
                    request_text=request_text,
                    config=config,
                    adjustment=adjustment,
                )
                if isawaitable(plan):
                    plan = await plan
            if plan is not None and plan.dispatchable:
                return {
                    "messages": list(
                        _council_preflight_message(
                            decision,
                            plan,
                            await interpret_depth(request_text, interpreter=depth_reader),
                            request_nonce=request_nonce,
                            model_options=_adapter_known_models(stage_adapter),
                        )
                    )
                }
        execute_kwargs = {
            "project_id": context.project_id,
            "cycle_id": decision.cycle_id,
            "request_text": request_text,
            "state": state,
            "config": config,
        }
        if handoff_answer is not None and handoff_answer[0] == "start_next_stage":
            execute_kwargs["expected_stage"] = str(handoff_answer[1].get("next_stage") or "")
            execute_kwargs["expected_cycle_revision"] = int(handoff_answer[1].get("cycle_revision") or 0)
        if review_meeting_stage:
            # Convening is its own kind of request: the adapter reads the named
            # stage's recorded evidence rather than deriving a stage from cycle
            # state, so none of the Design council setup below applies to it.
            execute_kwargs["review_meeting_stage"] = review_meeting_stage
            result = stage_adapter.execute(**execute_kwargs)
            if isawaitable(result):
                result = await result
            deck_uri = getattr(result, "deck_uri", None)
            deck_uri = deck_uri if isinstance(deck_uri, str) and deck_uri else None
            artifact_uri = getattr(result, "artifact_uri", None)
            if isinstance(artifact_uri, str) and artifact_uri:
                return {
                    "messages": list(
                        _present_artifact_messages(
                            decision,
                            note=result.note,
                            artifact_uri=artifact_uri,
                            request_nonce=str(request_context(config).get("run_id") or ""),
                            deck_uri=deck_uri,
                        )
                    ),
                    "artifacts": [path for path in (artifact_uri, deck_uri) if path],
                }
            return {"messages": [AIMessage(content=_render_continuation(decision, result.note))]}
        if authored_design is not None:
            execute_kwargs["authored_design"] = authored_design
        if adjustment is not None:
            # The roster the person approved was drawn with this note, so the
            # one that runs has to be drawn with it too.
            execute_kwargs["council_adjustment"] = adjustment
        design_answer = _card_answer(state, DESIGN_CLARIFICATION_PREFIX)
        if design_answer is not None and design_answer[1].strip():
            # The answer to the chair's own question. Passed explicitly rather
            # than inferred from the request text, which is the same string but
            # says nothing about what it is answering: the adapter resumes the
            # paused meeting on it instead of convening a new one.
            execute_kwargs["clarification_answer"] = design_answer[1]
            consumer = getattr(stage_adapter, "consume_feedback_request", None)
            if consumer is not None:
                try:
                    consumed = consumer(
                        project_id=str(context.project_id or ""),
                        human_input_request_id=design_answer[0],
                        answer=design_answer[1],
                    )
                    if isawaitable(consumed):
                        await consumed
                except Exception:  # noqa: BLE001 - never discard a real card answer
                    logger.warning(
                        "Could not mark Design feedback request %s consumed.",
                        design_answer[0],
                        exc_info=True,
                    )
        known_models = _adapter_known_models(stage_adapter)
        participant_settings = _confirmed_participant_settings(state, known_models)
        approved_proposal = _confirmed_council_proposal(state)
        if design_answer is not None:
            # This is not a new meeting. The preflight reply is necessarily an
            # older message now, behind the chair's clarification card and its
            # answer, so the answering-turn-only readers above cannot see it.
            # Recover it only on this resume path; ordinary later Design turns
            # must remain free to convene a different roster.
            resumed_proposal, resumed_settings = _resumed_council_setup(state, known_models)
            if approved_proposal is None:
                approved_proposal = resumed_proposal
            if not participant_settings:
                participant_settings = resumed_settings
        if participant_settings:
            # The dials the person set on the participant cards travel with
            # the same reply as the depth, and like the depth they apply to
            # the meeting that reply convenes — not to every later turn.
            execute_kwargs["participant_settings"] = participant_settings
        if approved_proposal is not None:
            execute_kwargs["approved_council_proposal"] = approved_proposal
        result = stage_adapter.execute(**execute_kwargs)
        if isawaitable(result):
            result = await result
        authoring_request = getattr(result, "authoring_request", None)
        if isinstance(authoring_request, str) and authoring_request:
            return {
                "messages": list(
                    _design_authoring_message(
                        decision,
                        note=result.note,
                        question=authoring_request,
                        request_nonce=str(raw_context.get("run_id") or ""),
                    )
                )
            }
        deck_uri = getattr(result, "deck_uri", None)
        deck_uri = deck_uri if isinstance(deck_uri, str) and deck_uri else None
        clarification_question = getattr(result, "clarification_question", None)
        if isinstance(clarification_question, str) and clarification_question:
            raw_context = request_context(config)
            request_nonce = str(raw_context.get("run_id") or "")
            # The deck goes in front of the question, not after it: what the
            # meeting agreed and where it split is the context the decision is
            # made from, and a person asked to decide first and read second is
            # being asked to guess.
            deck_messages = (
                list(
                    _present_artifact_messages(
                        decision,
                        note="Here is where the meeting got to — what the participants agreed, and where they are still split.",
                        artifact_uri=deck_uri,
                        request_nonce=request_nonce,
                    )
                )
                if deck_uri
                else []
            )
            feedback_surface_id = getattr(result, "feedback_surface_id", None)
            feedback_surface_id = feedback_surface_id if isinstance(feedback_surface_id, str) and feedback_surface_id else None
            if feedback_surface_id:
                try:
                    from deerflow.config.app_config import get_app_config

                    if not get_app_config().dbtl.design_deck_feedback:
                        feedback_surface_id = None
                except Exception:  # noqa: BLE001 - unavailable config keeps rollback UI
                    feedback_surface_id = None
            clarification_messages = list(
                _design_clarification_message(
                    decision,
                    note=result.note,
                    question=clarification_question,
                    request_nonce=request_nonce,
                    feedback_surface_id=feedback_surface_id,
                )
            )
            if feedback_surface_id:
                request_id = clarification_messages[-1].tool_call_id
                binder = getattr(stage_adapter, "bind_feedback_request", None)
                try:
                    if binder is None:
                        raise RuntimeError("The stage adapter cannot bind a Design feedback request.")
                    bound = binder(
                        project_id=str(context.project_id or ""),
                        surface_id=feedback_surface_id,
                        human_input_request_id=request_id,
                    )
                    if isawaitable(bound):
                        await bound
                except Exception:  # noqa: BLE001 - keep the visible card as recovery
                    logger.warning("Could not bind Design feedback deck %s to its Human Input request.", feedback_surface_id, exc_info=True)
                    clarification_messages = list(
                        _design_clarification_message(
                            decision,
                            note=result.note,
                            question=clarification_question,
                            request_nonce=request_nonce,
                        )
                    )
            return {
                "messages": [
                    *deck_messages,
                    *clarification_messages,
                ],
                **({"artifacts": [deck_uri]} if deck_uri else {}),
            }
        artifact_uri = getattr(result, "artifact_uri", None)
        if isinstance(artifact_uri, str) and artifact_uri:
            raw_context = request_context(config)
            request_nonce = str(raw_context.get("run_id") or "")
            presented = list(
                _present_artifact_messages(
                    decision,
                    note=result.note,
                    artifact_uri=artifact_uri,
                    request_nonce=request_nonce,
                    deck_uri=deck_uri,
                )
            )
            if getattr(result, "stage", None) == "test":
                snapshot_reader = getattr(stage_adapter, "test_review_snapshot", None)
                snapshot = None
                if callable(snapshot_reader):
                    snapshot = snapshot_reader(
                        project_id=str(context.project_id or ""),
                        cycle_id=str(decision.cycle_id or ""),
                    )
                    if isawaitable(snapshot):
                        snapshot = await snapshot
                if isinstance(snapshot, dict):
                    requirement = str(dict(snapshot.get("meeting") or {}).get("requirement") or "skipped")
                    presented.extend(
                        _test_card_messages(
                            decision,
                            snapshot,
                            request_nonce=request_nonce,
                            outcome=requirement in {"skipped", "complete"},
                        )
                    )
            return {
                "messages": presented,
                "artifacts": [path for path in (artifact_uri, deck_uri) if path],
            }
        return {"messages": [AIMessage(content=_render_continuation(decision, result.note))]}

    async def ordinary(state: dict, config: RunnableConfig) -> dict:
        """Delegate ordinary work, adding DBTL orientation only for this call.

        Both additions are request-only context: the lead agent is told what
        governed work exists around it *before* it reaches for a tool, so it can
        say where the boundary is instead of building past it. Neither key
        widens what it may do.
        """
        decision = decide(state)
        extra: dict[str, Any] = {}
        if decision.cycle_id:
            reader = getattr(stage_adapter, "parked_design_context", None)
            if callable(reader):
                parked = reader(
                    project_id=context.project_id,
                    cycle_id=decision.cycle_id,
                )
                if isawaitable(parked):
                    parked = await parked
                if parked is not None:
                    extra["dbtl_parked_design_brief"] = parked
        status = await _dbtl_status_snapshot(stage_adapter, context, state)
        if status is not None:
            extra[DBTL_STATUS_CONTEXT_KEY] = status
        if not extra:
            return await lead_agent.ainvoke(state, config=config)
        active_context = {**request_context(config), **extra}
        ordinary_config = dict(config)
        ordinary_config["context"] = active_context
        configurable = dict(ordinary_config.get("configurable") or {})
        configurable["context"] = active_context
        ordinary_config["configurable"] = configurable
        return await lead_agent.ainvoke(state, config=ordinary_config)

    builder = StateGraph(state_schema)
    builder.add_node(SupervisorBranch.ORDINARY.value, ordinary)
    builder.add_node(SupervisorBranch.CLARIFICATION.value, clarification)
    builder.add_node(SupervisorBranch.CYCLE_SETUP.value, cycle_setup)
    builder.add_node(SupervisorBranch.CYCLE_CONTINUATION.value, cycle_continuation)

    builder.add_conditional_edges(START, route, [branch.value for branch in SupervisorBranch])
    for branch in SupervisorBranch:
        builder.add_edge(branch.value, END)
    return builder


def _resolve_explicit_choice(raw: object) -> ExplicitChoice | None:
    """Coerce a client-supplied choice, ignoring anything unrecognized.

    The chip sends this per request, so it is untrusted input. An unknown value
    must fall through to normal routing rather than raising, or a stale frontend
    would break the conversation instead of merely losing a preference.
    """
    if not isinstance(raw, str):
        return None
    try:
        return ExplicitChoice(raw)
    except ValueError:
        logger.debug("ignoring unrecognized DBTL explicit choice %r", raw)
        return None


def supervisor_context_from_config(config: RunnableConfig) -> SupervisorContext:
    """Read the run's project and cycle selection from its runtime context.

    ``project_id`` is server-owned: the Gateway drops any caller-supplied value
    and re-stamps it from the durable membership record, so it is read from the
    merged runtime view.

    The per-request selection is read from ``context`` **only**, never from the
    merged view. ``configurable`` is checkpointed: a selection accepted from
    there would survive into later turns and keep steering them, which is
    exactly the "affects the next request only" guarantee the context chip
    makes to the user. Reading one key from one place is what enforces it.

    ``selected_cycle_id`` is a client preference. The live Phase 6 adapter
    verifies it by loading ``(cycle_id, project_id)`` through the durable
    repository before dispatching any worker or writing any evidence.
    """
    from deerflow.agents.lead_agent.agent import _get_runtime_config

    cfg = _get_runtime_config(config)
    raw_context = request_context(config)

    project_id = cfg.get("project_id")
    selected = raw_context.get(SELECTED_CYCLE_CONTEXT_KEY)
    cycle_count = cfg.get("dbtl_project_cycle_count")
    has_unfinished = cfg.get("dbtl_has_unfinished_cycles")
    return SupervisorContext(
        project_id=str(project_id) if project_id else None,
        project_name=str(cfg.get("project_name") or ""),
        selected_cycle_id=str(selected) if selected else None,
        explicit_choice=_resolve_explicit_choice(raw_context.get(EXPLICIT_CHOICE_CONTEXT_KEY)),
        project_cycle_count=cycle_count if isinstance(cycle_count, int) and cycle_count >= 0 else None,
        has_unfinished_cycles=has_unfinished if isinstance(has_unfinished, bool) else None,
    )


def make_project_supervisor(config: RunnableConfig):
    """LangGraph factory for the ``project_supervisor`` assistant_id.

    Mirrors ``make_lead_agent(config)`` so the run worker drives it identically,
    and freezes/injects the checkpoint channel mode the same way so the
    runtime's checkpoint machinery stays consistent. The compiled graph carries
    no checkpointer; the runtime injects one.
    """
    from deerflow.agents.lead_agent.agent import make_lead_agent
    from deerflow.agents.thread_state import get_thread_state_schema
    from deerflow.config.app_config import AppConfig, get_app_config
    from deerflow.runtime.checkpoint_mode import (
        INTERNAL_CHECKPOINT_MODE_KEY,
        freeze_checkpoint_channel_mode,
        frozen_checkpoint_channel_mode,
        inject_checkpoint_mode,
    )

    configurable = config.get("configurable", {}) or {}
    runtime_app_config = configurable.get("app_config")
    if not isinstance(runtime_app_config, AppConfig):
        runtime_app_config = get_app_config()

    frozen_mode = frozen_checkpoint_channel_mode()
    if frozen_mode is None:
        requested_mode = runtime_app_config.database.checkpoint_channel_mode
    else:
        requested_mode = configurable.get(
            INTERNAL_CHECKPOINT_MODE_KEY,
            runtime_app_config.database.checkpoint_channel_mode,
        )
    mode = freeze_checkpoint_channel_mode(requested_mode)
    inject_checkpoint_mode(config, mode)

    # ``make_lead_agent`` re-freezes the same mode (idempotent) and builds the
    # full middleware chain, so the ordinary branch is the production agent.
    lead_agent = make_lead_agent(config)
    from deerflow.agents.dbtl.stage_execution import (
        LiveStageAdapter,
        make_llm_intent_interpreter,
        make_llm_revision_interpreter,
        make_llm_roster_writer,
        make_llm_transition_assessor,
    )
    from deerflow.persistence.dbtl import DbtlCycleRepository
    from deerflow.persistence.engine import get_session_factory

    session_factory = get_session_factory()
    if session_factory is None:
        raise RuntimeError("DBTL stage execution requires an initialized SQL persistence layer.")

    graph = build_supervisor_graph(
        lead_agent=lead_agent,
        context=supervisor_context_from_config(config),
        state_schema=get_thread_state_schema(mode),
        stage_adapter=LiveStageAdapter(
            repo=DbtlCycleRepository(session_factory),
            app_config=runtime_app_config,
            runtime_config=config,
            roster_writer=make_llm_roster_writer(),
            intent_interpreter=make_llm_intent_interpreter(),
            revision_interpreter=make_llm_revision_interpreter(),
            transition_assessor=make_llm_transition_assessor(),
        ),
    )
    return graph.compile()
