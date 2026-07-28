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
from collections.abc import Sequence
from dataclasses import replace
from hashlib import sha256
from inspect import isawaitable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from deerflow.dbtl.branches import (
    BranchDecision,
    SupervisorBranch,
    SupervisorContext,
    resolve_branch,
)
from deerflow.dbtl.council import (
    COUNCIL_DEPTH_CONTEXT_KEY,
    CouncilDepth,
    council_depth_from_config,
    depth_policy,
    recommend_depth,
    request_context,
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

# Runtime-context keys the frontend's context chip sets for the *next* request
# only. Read from runtime context rather than ``configurable`` because
# ``configurable`` is checkpointed: a per-request selection written there would
# outlive the request and silently apply to later turns.
SELECTED_CYCLE_CONTEXT_KEY = "dbtl_selected_cycle_id"
EXPLICIT_CHOICE_CONTEXT_KEY = "dbtl_explicit_choice"

# Request-id prefixes, one per clarification the supervisor can raise. They are
# what a resuming turn matches on, so the two must stay distinguishable: a
# Design-council answer feeds a running stage, a setup answer re-routes a
# request that has not started anything yet.
# The separator is ``__`` rather than ``:`` because these strings become
# ``tool_use.id`` values, and Anthropic validates those against
# ``^[a-zA-Z0-9_-]+$``. A colon is accepted locally and written to the
# checkpoint, then rejects *every later turn in the thread* when the history is
# replayed — one turn after the card, on an unrelated request, naming message 0.
#
# ``__`` also keeps the set collision-free under ``startswith``: the pairs that
# share a stem (``dbtl-setup`` / ``dbtl-setup-confirm``, ``dbtl-design`` /
# ``dbtl-design-write``) diverge at ``_`` versus ``-``, so a reply to the longer
# card cannot be consumed by the branch waiting on the shorter one.
SETUP_CLARIFICATION_PREFIX = "dbtl-setup__"
SETUP_CONFIRMATION_PREFIX = "dbtl-setup-confirm__"
DESIGN_CLARIFICATION_PREFIX = "dbtl-design__"
# Distinct from the clarification prefix because the two answers do opposite
# things: a clarification answer feeds a council that already ran, this one
# *is* the design, submitted where no council ran at all.
DESIGN_AUTHORING_PREFIX = "dbtl-design-write__"
# Not a card: the id of the ``present_files`` pair that delivers a finished
# package. Same provider constraint, same failure mode.
PRESENT_ARTIFACT_PREFIX = "dbtl-present__"

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


def _latest_user_text(state: dict) -> str:
    """The newest visible user message, as plain text.

    Hidden ``HumanMessage``s are skipped: goal continuations, human-input card
    replies, and injected context blocks are machine-authored, and routing on
    one would let internal plumbing steer a research decision.
    """
    from deerflow.utils.messages import message_content_to_text

    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        extra = getattr(message, "additional_kwargs", None) or {}
        if extra.get("hide_from_ui") or extra.get("human_input_response"):
            continue
        return message_content_to_text(message.content) or ""
    return ""


def _is_new_conversation(state: dict) -> bool:
    """Whether this request is the first visible user turn in the checkpoint."""
    visible_user_turns = 0
    for message in state.get("messages") or []:
        if not isinstance(message, HumanMessage):
            continue
        extra = getattr(message, "additional_kwargs", None) or {}
        if extra.get("hide_from_ui") or extra.get("human_input_response"):
            continue
        visible_user_turns += 1
        if visible_user_turns > 1:
            return False
    return visible_user_turns == 1


def _latest_cycle_request_text(state: dict) -> str:
    """Prefer a Design-council card response over the preceding visible prompt."""
    from deerflow.agents.human_input import read_human_input_response
    from deerflow.utils.messages import message_content_to_text

    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        additional_kwargs = getattr(message, "additional_kwargs", None) or {}
        response = read_human_input_response(additional_kwargs)
        if response and response.get("source") == "ask_clarification" and str(response.get("request_id") or "").startswith(DESIGN_CLARIFICATION_PREFIX):
            return str(response.get("value") or "")
        if additional_kwargs.get("dbtl_design_kickoff") is True:
            return message_content_to_text(message.content) or ""
        break
    return _latest_user_text(state)


def _card_answer(state: dict, prefix: str) -> tuple[str, str] | None:
    """``(request_id, answer)`` when the newest message answers *prefix*'s card."""
    from deerflow.agents.human_input import read_human_input_response

    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if not response or response.get("source") != "ask_clarification":
            return None
        request_id = str(response.get("request_id") or "")
        if not request_id.startswith(prefix):
            return None
        return (request_id, str(response.get("value") or ""))
    return None


def _emitted_card_request(state: dict, request_id: str) -> dict | None:
    """The human-input request the server itself sent for *request_id*.

    Resolving the card from thread state rather than trusting the reply is what
    makes the recovered intent server-owned: a reply naming a card that was
    never emitted matches nothing and routes as ordinary text.
    """
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, ToolMessage) or message.tool_call_id != request_id:
            continue
        artifact = getattr(message, "artifact", None)
        request = artifact.get("human_input") if isinstance(artifact, dict) else None
        return request if isinstance(request, dict) else None
    return None


def _routing_input(state: dict) -> tuple[str, ExplicitChoice | None]:
    """The text routing reads, plus any intent recovered from a card answer.

    A setup clarification is only ever raised for an explicit start request, and
    the choice that produced it applies to that one request by design — so it is
    already gone when the answer arrives. Recovering it here is what keeps an
    answered clarification on the branch that asked the question; routing the
    answer on its own merits lands it in ordinary work, where the lead agent
    absorbs the reply and the user never reaches the confirmation.

    The originating request is carried too. Routing needs both halves: the
    answer supplies the missing fields, while the original request is what
    still says a cycle was being started at all.
    """
    confirmed = _card_answer(state, SETUP_CONFIRMATION_PREFIX)
    if confirmed is not None:
        request_id, _answer = confirmed
        request = _emitted_card_request(state, request_id)
        if request is not None:
            return (str(request.get("source_request") or ""), ExplicitChoice.START_CYCLE)

    answered = _card_answer(state, SETUP_CLARIFICATION_PREFIX)
    if answered is None:
        return (_latest_user_text(state), None)

    request_id, answer = answered
    request = _emitted_card_request(state, request_id)
    if request is None:
        return (_latest_user_text(state), None)

    source_request = str(request.get("source_request") or "")
    combined = f"{source_request}\n\n{answer}".strip()
    return (combined, ExplicitChoice.START_CYCLE)


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
            "No council workers ran, no new artifact was created, and the existing review package remains unchanged.",
        ]
    )


def _setup_question_form_fields(questions: Sequence[SetupQuestion]) -> list[dict]:
    """Project the setup questions onto the native form-field protocol.

    The wizard's contract survives the projection: the model's proposal is a
    prefilled ``default`` the scientist corrects rather than composes, and the
    provenance sentence keeps grounded answers ("from your request") visually
    distinct from invented ones ("suggested"). Option questions become strict
    selects, so a trailing free-text field preserves the wizard's "Other"
    escape hatch — a scientist must never be forced into the model's menu.
    """
    fields: list[dict] = []
    for item in questions:
        provenance = "From your request." if item.grounded else "Suggested — correct it if wrong."
        description = " ".join(part for part in (item.why, provenance) if part)
        field: dict = {
            "name": item.id,
            "label": item.question,
            "required": False,
            "description": description,
        }
        if item.options:
            recommended = next((option for option in item.options if option.id == item.recommended_option_id), None)
            field["type"] = "select"
            field["options"] = [
                {
                    "id": option.id,
                    "label": option.label,
                    "value": option.label,
                    **({"description": option.description} if option.description else {}),
                }
                for option in item.options
            ]
            if recommended is not None:
                field["default"] = recommended.label
        else:
            field["type"] = "textarea"
            if item.recommendation:
                field["default"] = item.recommendation
        fields.append(field)
    if fields and all(field["name"] != "additional_notes" for field in fields):
        fields.append(
            {
                "name": "additional_notes",
                "label": "Anything else to correct or add?",
                "type": "textarea",
                "required": False,
            }
        )
    return fields


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
    form_fields = _setup_question_form_fields(questions)
    digest = sha256(f"{context.project_id}:{request_nonce}:{source_request}".encode()).hexdigest()[:16]
    request_id = f"{SETUP_CLARIFICATION_PREFIX}{digest}"
    tool_call = {
        "name": "ask_clarification",
        "args": {
            "question": question,
            "context": note,
            "clarification_type": "cycle_setup",
        },
        "id": request_id,
        "type": "tool_call",
    }
    return (
        AIMessage(
            id=f"{request_id}:call",
            content="",
            tool_calls=[tool_call],
        ),
        ToolMessage(
            id=request_id,
            name="ask_clarification",
            tool_call_id=request_id,
            content=f"{note}\n\n{question}",
            artifact={
                "human_input": {
                    # Native form mode (version 2) when the questions project
                    # onto typed fields; a card with no questions keeps the
                    # legacy free-text shape. The reply stays a v1 text
                    # summary either way, so setup-branch routing is unchanged.
                    "version": 2 if form_fields else 1,
                    "kind": "human_input_request",
                    "source": "ask_clarification",
                    "request_id": request_id,
                    "clarification_type": "cycle_setup",
                    "title": "Designing this DBTL cycle",
                    "question": question,
                    "context": note,
                    "input_mode": "form" if form_fields else "free_text",
                    **({"fields": form_fields} if form_fields else {}),
                    "source_request": source_request,
                    "missing_fields": list(decision.missing_fields),
                    # The structured form of what the question text renders, so
                    # a later card UI can show per-question fields without the
                    # supervisor having to emit the questions a second way.
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
            },
        ),
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
    digest = sha256(f"{context.project_id}:{request_nonce}:{source_request}:confirm".encode()).hexdigest()[:16]
    request_id = f"{SETUP_CONFIRMATION_PREFIX}{digest}"
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
    tool_call = {
        "name": "ask_clarification",
        "args": {
            "question": question,
            "context": summary,
            "clarification_type": "cycle_setup_confirmation",
            "options": options,
        },
        "id": request_id,
        "type": "tool_call",
    }
    objective = decision.objective.strip() or source_request.strip()
    title = objective[:80].strip() or "New DBTL cycle"
    return (
        AIMessage(
            id=f"{request_id}:call",
            content="",
            tool_calls=[tool_call],
        ),
        ToolMessage(
            id=request_id,
            name="ask_clarification",
            tool_call_id=request_id,
            content=f"{summary}\n\n{question}",
            artifact={
                "human_input": {
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
            },
        ),
    )


def _confirmation_answer(state: dict) -> str | None:
    """The verdict on a confirmation card this supervisor actually emitted.

    The emitted-card check is what makes a forged ``request_id`` inert: a reply
    that matches no card the server raised is not an approval.
    """
    answered = _card_answer(state, SETUP_CONFIRMATION_PREFIX)
    if answered is None:
        return None
    request_id, answer = answered
    if _emitted_card_request(state, request_id) is None:
        return None
    return answer.strip().lower()


def _has_emitted_card(state: dict, prefix: str) -> bool:
    """Whether this thread already raised a card of that kind."""
    for message in state.get("messages") or []:
        request = getattr(message, "artifact", None)
        if isinstance(request, dict):
            payload = request.get("human_input")
            if isinstance(payload, dict) and str(payload.get("request_id") or "").startswith(prefix):
                return True
    return False


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


COUNCIL_PREFLIGHT_PREFIX = "dbtl-council__"
COUNCIL_ADJUST_PREFIX = "dbtl-council-edit__"

#: The preflight option that is not a depth. Choosing it asks what should
#: change instead of how much debate to buy, so it is kept out of
#: :class:`CouncilDepth` — a value in that enum is something the council can be
#: run at, and this one is a request to redraw the roster first.
COUNCIL_ADJUST_OPTION = "adjust"

_ADJUST_QUESTION = "What should the council do differently? Name the seats to add, drop, or re-aim — your words go to the roster writer exactly as you type them."
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
) -> tuple[AIMessage, ToolMessage]:
    """Show who will sit on the council, and let the human set the depth.

    Emitted before any worker is dispatched, so the choice is real. The depth
    options and the roster ride on the artifact as structured data; the text
    below is the same information for anyone who cannot see the card.
    """
    cycle = decision.cycle_id or "selected-cycle"
    digest = sha256(f"{cycle}:{request_nonce}".encode()).hexdigest()[:16]
    request_id = f"{COUNCIL_PREFLIGHT_PREFIX}{cycle}__{digest}"
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
    return (
        AIMessage(
            id=f"{request_id}:call",
            content="",
            tool_calls=[
                {
                    "name": "ask_clarification",
                    "args": {
                        "question": question,
                        "context": note,
                        "clarification_type": "council_preflight",
                    },
                    "id": request_id,
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            id=request_id,
            name="ask_clarification",
            tool_call_id=request_id,
            content=f"{note}\n\n{question}",
            artifact={
                "human_input": {
                    "version": 1,
                    "kind": "human_input_request",
                    "source": "ask_clarification",
                    "request_id": request_id,
                    "clarification_type": "council_preflight",
                    "title": "Before the Design council convenes",
                    "question": question,
                    "context": note,
                    "input_mode": "single_choice",
                    "options": options,
                    "council_plan": plan.as_dict(),
                    "recommended_depth": recommendation.depth.value,
                    "recommended_option_id": recommendation.depth.value,
                }
            },
        ),
    )


def _design_clarification_message(
    decision: BranchDecision,
    *,
    note: str,
    question: str,
    request_nonce: str,
) -> tuple[AIMessage, ToolMessage]:
    cycle = decision.cycle_id or "selected-cycle"
    digest = sha256(f"{cycle}:{request_nonce}:{question}".encode()).hexdigest()[:16]
    request_id = f"{DESIGN_CLARIFICATION_PREFIX}{cycle}__{digest}"
    tool_call = {
        "name": "ask_clarification",
        "args": {
            "question": question,
            "context": note,
            "clarification_type": "design_decision",
        },
        "id": request_id,
        "type": "tool_call",
    }
    return (
        AIMessage(
            id=f"{request_id}:call",
            content="",
            tool_calls=[tool_call],
        ),
        ToolMessage(
            id=request_id,
            name="ask_clarification",
            tool_call_id=request_id,
            content=f"{note}\n\n{question}",
            artifact={
                "human_input": {
                    "version": 1,
                    "kind": "human_input_request",
                    "source": "ask_clarification",
                    "request_id": request_id,
                    "clarification_type": "design_decision",
                    "title": "Design council needs your input",
                    "question": question,
                    "context": note,
                    "input_mode": "free_text",
                }
            },
        ),
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
    digest = sha256(f"{cycle}:{request_nonce}".encode()).hexdigest()[:16]
    request_id = f"{DESIGN_AUTHORING_PREFIX}{cycle}__{digest}"
    tool_call = {
        "name": "ask_clarification",
        "args": {
            "question": question,
            "context": note,
            "clarification_type": "design_authoring",
        },
        "id": request_id,
        "type": "tool_call",
    }
    return (
        AIMessage(id=f"{request_id}:call", content="", tool_calls=[tool_call]),
        ToolMessage(
            id=request_id,
            name="ask_clarification",
            tool_call_id=request_id,
            content=f"{note}\n\n{question}",
            artifact={
                "human_input": {
                    "version": 1,
                    "kind": "human_input_request",
                    "source": "ask_clarification",
                    "request_id": request_id,
                    "clarification_type": "design_authoring",
                    "title": "Write the design yourself",
                    "question": question,
                    "context": note,
                    "input_mode": "free_text",
                    "council_depth": CouncilDepth.HUMAN_INPUT.value,
                }
            },
        ),
    )


def _authored_design(state: dict) -> str | None:
    """The design text, only when the server itself asked for it.

    Resolved from the emitted card rather than from the reply, so a forged
    ``request_id`` matches nothing and the text is ignored instead of being
    recorded as a Design package nobody was asked for.
    """
    answered = _card_answer(state, DESIGN_AUTHORING_PREFIX)
    if answered is None:
        return None
    request_id, value = answered
    request = _emitted_card_request(state, request_id)
    if request is None or request.get("council_depth") != CouncilDepth.HUMAN_INPUT.value:
        return None
    return value


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
    digest = sha256(f"{cycle}:{request_nonce}".encode()).hexdigest()[:16]
    request_id = f"{COUNCIL_ADJUST_PREFIX}{cycle}__{digest}"
    return (
        AIMessage(
            id=f"{request_id}:call",
            content="",
            tool_calls=[
                {
                    "name": "ask_clarification",
                    "args": {
                        "question": _ADJUST_QUESTION,
                        "context": _ADJUST_NOTE,
                        "clarification_type": "council_adjustment",
                    },
                    "id": request_id,
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            id=request_id,
            name="ask_clarification",
            tool_call_id=request_id,
            content=f"{_ADJUST_NOTE}\n\n{_ADJUST_QUESTION}",
            artifact={
                "human_input": {
                    "version": 1,
                    "kind": "human_input_request",
                    "source": "ask_clarification",
                    "request_id": request_id,
                    "clarification_type": "council_adjustment",
                    "title": "Adjust the Design council",
                    "question": _ADJUST_QUESTION,
                    "context": _ADJUST_NOTE,
                    "input_mode": "free_text",
                }
            },
        ),
    )


def _wants_roster_adjustment(state: dict) -> bool:
    """Whether the newest message answered the preflight by asking for changes."""
    answered = _card_answer(state, COUNCIL_PREFLIGHT_PREFIX)
    if answered is None:
        return False
    request_id, value = answered
    if _emitted_card_request(state, request_id) is None:
        return False
    return value.strip().lower() == COUNCIL_ADJUST_OPTION


def _council_adjustment(state: dict) -> str | None:
    """The newest roster change a person asked for in this cycle.

    Unlike the depth, this is *not* read only from the newest message: the
    adjustment is answered one turn and the depth the next, so by the time the
    council runs the adjustment is no longer the last thing said. Scanning back
    for the most recent answered adjustment card is what carries it to dispatch.

    Resolved from the emitted card rather than the reply, so a forged
    ``request_id`` matches nothing.
    """
    from deerflow.agents.human_input import read_human_input_response

    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if not response or response.get("source") != "ask_clarification":
            continue
        request_id = str(response.get("request_id") or "")
        if not request_id.startswith(COUNCIL_ADJUST_PREFIX):
            continue
        if _emitted_card_request(state, request_id) is None:
            return None
        return str(response.get("value") or "").strip() or None
    return None


def _confirmed_council_depth(state: dict) -> CouncilDepth | None:
    """The depth a person chose, read off the card the server itself emitted.

    The browser echoes the choice back in the next request's context, but a
    reply that loses it is indistinguishable from one that never carried a
    choice — and the fallback is the server's own recommendation, so the
    council convenes at a depth nobody picked and nothing says so. The answer
    is already in state; recovering it here needs no cooperation from the
    client and, like ``_authored_design``, resolves the card rather than
    trusting the reply, so a forged ``request_id`` matches nothing.

    Scoped to the answering turn by ``_card_answer``, which matches only when
    the newest message is that reply. A preflight answer stays in history
    forever, and re-reading it on every later request would pin the whole cycle
    to one depth with no way to say otherwise.
    """
    answered = _card_answer(state, COUNCIL_PREFLIGHT_PREFIX)
    if answered is None:
        return None
    request_id, value = answered
    if _emitted_card_request(state, request_id) is None:
        return None
    try:
        return CouncilDepth(value.strip().lower())
    except ValueError:
        # A stale client losing a preference is a far smaller failure than a
        # cycle that cannot be designed; fall back to the recommendation.
        return None


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
) -> tuple[AIMessage, ToolMessage]:
    """Use the same present-files turn shape as the lead agent."""
    cycle = decision.cycle_id or "selected-cycle"
    digest = sha256(f"{cycle}:{request_nonce}:{artifact_uri}".encode()).hexdigest()[:16]
    tool_call_id = f"{PRESENT_ARTIFACT_PREFIX}{cycle}__{digest}"
    tool_call = {
        "name": "present_files",
        "args": {"filepaths": [artifact_uri]},
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
    stage_adapter,
    question_writer=None,
) -> StateGraph:
    """Build (but do not compile) the supervisor graph.

    ``lead_agent`` is the compiled lead-agent graph and ``context`` the run's
    resolved project/cycle selection; both are injected so tests can drive every
    branch without an LLM and without a checkpointed thread.

    ``question_writer`` is the same seam for the post-approval design questions:
    an async callable taking ``(request_text, missing_fields)`` and returning
    :class:`SetupQuestion` values. ``None`` uses the configured model, and any
    failure degrades to the deterministic gaps rather than blocking setup.
    """
    writer = question_writer or _make_llm_question_writer(context)

    def decide(state: dict) -> BranchDecision:
        text, recovered_choice = _routing_input(state)
        # The recovered choice wins over the request's own. Answering a card is
        # not choosing a scope: the client sends a scope with every request and
        # falls back to "ordinary" for a card it has no special handling for, so
        # honouring it here would route the answer to the lead agent on exactly
        # the turn the server knows what the user is doing. The card is
        # server-emitted and the reply is bound to it, which makes it the better
        # evidence of intent than a field the client always fills in.
        active = replace(
            context,
            explicit_choice=recovered_choice if recovered_choice is not None else context.explicit_choice,
            is_new_conversation=_is_new_conversation(state),
        )
        return resolve_branch(text, active)

    def route(state: dict) -> str:
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
        source_request, _ = _routing_input(state)
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
        source_request, _choice = _routing_input(state)
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
        request_text = _latest_cycle_request_text(state)
        review_intent = _review_intent(request_text)
        if review_intent is not None:
            return {
                "messages": [
                    AIMessage(
                        content=_render_review_intent_guidance(
                            decision,
                            review_intent=review_intent,
                        )
                    )
                ]
            }
        raw_context = request_context(config)
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
        if authored_design is None and council_depth_from_config(config) is None and (answered_adjustment or not _has_emitted_card(state, COUNCIL_PREFLIGHT_PREFIX)):
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
                            recommend_depth(request_text),
                            request_nonce=request_nonce,
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
        if authored_design is not None:
            execute_kwargs["authored_design"] = authored_design
        if adjustment is not None:
            # The roster the person approved was drawn with this note, so the
            # one that runs has to be drawn with it too.
            execute_kwargs["council_adjustment"] = adjustment
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
        clarification_question = getattr(result, "clarification_question", None)
        if isinstance(clarification_question, str) and clarification_question:
            raw_context = request_context(config)
            request_nonce = str(raw_context.get("run_id") or "")
            return {
                "messages": list(
                    _design_clarification_message(
                        decision,
                        note=result.note,
                        question=clarification_question,
                        request_nonce=request_nonce,
                    )
                )
            }
        artifact_uri = getattr(result, "artifact_uri", None)
        if isinstance(artifact_uri, str) and artifact_uri:
            raw_context = request_context(config)
            request_nonce = str(raw_context.get("run_id") or "")
            return {
                "messages": list(
                    _present_artifact_messages(
                        decision,
                        note=result.note,
                        artifact_uri=artifact_uri,
                        request_nonce=request_nonce,
                    )
                ),
                "artifacts": [artifact_uri],
            }
        return {"messages": [AIMessage(content=_render_continuation(decision, result.note))]}

    builder = StateGraph(state_schema)
    # The ordinary branch is the lead agent itself, unmodified.
    builder.add_node(SupervisorBranch.ORDINARY.value, lead_agent)
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
    from deerflow.agents.dbtl.stage_execution import LiveStageAdapter, make_llm_roster_writer
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
        ),
    )
    return graph.compile()
