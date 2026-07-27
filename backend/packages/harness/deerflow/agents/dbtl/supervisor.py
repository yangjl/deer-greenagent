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
SETUP_CLARIFICATION_PREFIX = "dbtl-setup:"
SETUP_CONFIRMATION_PREFIX = "dbtl-setup-confirm:"
DESIGN_CLARIFICATION_PREFIX = "dbtl-design:"

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

    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if response and response.get("source") == "ask_clarification" and str(response.get("request_id") or "").startswith(DESIGN_CLARIFICATION_PREFIX):
            return str(response.get("value") or "")
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
    project = context.project_name or "this project"
    note = "\n\n".join(
        [
            f"Creating the DBTL cycle in {project}. The authenticated project action writes the durable record; this supervisor turn creates nothing.",
            "To design it, I need a few things the request does not settle. I have proposed an answer to each — correct the ones that are wrong.",
        ]
    )
    question = render_questions(questions) or f"Please provide:\n{_bullets(decision.missing_fields)}"
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


def _design_clarification_message(
    decision: BranchDecision,
    *,
    note: str,
    question: str,
    request_nonce: str,
) -> tuple[AIMessage, ToolMessage]:
    cycle = decision.cycle_id or "selected-cycle"
    digest = sha256(f"{cycle}:{request_nonce}:{question}".encode()).hexdigest()[:16]
    request_id = f"dbtl-design:{cycle}:{digest}"
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
    tool_call_id = f"dbtl-present:{cycle}:{digest}"
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
        raw_context = config.get("context") or {}
        request_nonce = str(raw_context.get("run_id") or "") if isinstance(raw_context, dict) else ""
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
        raw_context = config.get("context") or {}
        request_nonce = str(raw_context.get("run_id") or "") if isinstance(raw_context, dict) else ""
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
        result = stage_adapter.execute(
            project_id=context.project_id,
            cycle_id=decision.cycle_id,
            request_text=request_text,
            state=state,
            config=config,
        )
        if isawaitable(result):
            result = await result
        clarification_question = getattr(result, "clarification_question", None)
        if isinstance(clarification_question, str) and clarification_question:
            raw_context = config.get("context") or {}
            request_nonce = str(raw_context.get("run_id") or "") if isinstance(raw_context, dict) else ""
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
            raw_context = config.get("context") or {}
            request_nonce = str(raw_context.get("run_id") or "") if isinstance(raw_context, dict) else ""
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
    raw_context = config.get("context") or {}
    request_context = raw_context if isinstance(raw_context, dict) else {}

    project_id = cfg.get("project_id")
    selected = request_context.get(SELECTED_CYCLE_CONTEXT_KEY)
    cycle_count = cfg.get("dbtl_project_cycle_count")
    has_unfinished = cfg.get("dbtl_has_unfinished_cycles")
    return SupervisorContext(
        project_id=str(project_id) if project_id else None,
        project_name=str(cfg.get("project_name") or ""),
        selected_cycle_id=str(selected) if selected else None,
        explicit_choice=_resolve_explicit_choice(request_context.get(EXPLICIT_CHOICE_CONTEXT_KEY)),
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
    from deerflow.agents.dbtl.stage_execution import LiveStageAdapter
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
        ),
    )
    return graph.compile()
