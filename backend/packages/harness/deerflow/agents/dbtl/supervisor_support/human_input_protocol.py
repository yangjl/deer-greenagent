"""Deterministic Human Input message envelopes for DBTL supervisor cards.

The supervisor emits cards directly from graph nodes, so these messages do not
pass through :class:`ClarificationMiddleware`.  Keep the shared transport shape
here while leaving every card's domain payload with its card builder.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from deerflow.runtime.journal import GRAPH_RECEIPT_KEY

# Request-id prefixes are part of the durable Human Input wire protocol.  They
# must remain collision-free under ``startswith`` and valid for every provider's
# tool-call grammar.
SETUP_CLARIFICATION_PREFIX = "dbtl-setup__"
SETUP_CONFIRMATION_PREFIX = "dbtl-setup-confirm__"
DISCOVERY_START_PREFIX = "dbtl-discovery__"
DESIGN_CLARIFICATION_PREFIX = "dbtl-design__"
DESIGN_AUTHORING_PREFIX = "dbtl-design-write__"
TEST_REVIEW_PREFIX = "dbtl-test-review__"
TEST_OUTCOME_PREFIX = "dbtl-test-outcome__"
EVIDENCE_RETRY_PREFIX = "dbtl-evidence-retry__"
STAGE_HANDOFF_PREFIX = "dbtl-stage-handoff__"
COUNCIL_PREFLIGHT_PREFIX = "dbtl-council__"
COUNCIL_ADJUST_PREFIX = "dbtl-council-edit__"
#: A paused Build: confirm the plan, continue past a boundary, or choose what to
#: do after a failure. One prefix for all four kinds, because they share one
#: answer vocabulary and one recovery path.
BUILD_CONTROL_PREFIX = "dbtl-build__"

# Not a card: the id prefix of the ``present_files`` pair that delivers a
# finished package.  It shares the same provider constraints.
PRESENT_ARTIFACT_PREFIX = "dbtl-present__"

# OpenAI's Responses API rejects a call id longer than this.  Keep a short,
# human-correlatable cycle token and derive uniqueness from the full digest.
MAX_CARD_REQUEST_ID_CHARS = 64
_CYCLE_TOKEN_CHARS = 12


def card_request_id(prefix: str, cycle: str, *parts: str) -> str:
    """Build a durable card id accepted by every supported provider."""
    digest = sha256("\x1f".join((cycle, *parts)).encode()).hexdigest()[:16]
    token = "".join(char for char in cycle[:_CYCLE_TOKEN_CHARS] if char.isalnum() or char in "_-")
    request_id = f"{prefix}{token}__{digest}"
    if len(request_id) > MAX_CARD_REQUEST_ID_CHARS:  # pragma: no cover - guarded by test
        request_id = f"{prefix}{digest}"
    return request_id


#: Marks a receipt that explains why an outstanding Start/Hold card can no
#: longer be acted on. Its presence releases the routing fence for that card:
#: the reason is stated once, and the conversation is handed back rather than
#: answering every later message with the same refusal.
STAGE_HANDOFF_REFUSED_KEY = "dbtl_stage_handoff_refused"


#: Marks a receipt that closes an outstanding Build control. Same purpose as
#: ``STAGE_HANDOFF_REFUSED_KEY``: a control that can no longer be answered
#: truthfully states its reason once and hands the conversation back, rather
#: than intercepting every later message with the same refusal.
BUILD_CONTROL_REFUSED_KEY = "dbtl_build_control_refused"


def receipt_message(
    content: str,
    *,
    stage_handoff_refused: str | None = None,
    build_control_refused: str | None = None,
    message_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AIMessage:
    """A deterministic supervisor reply that must survive a page reload.

    These are authored by the graph with no model call behind them, so no LLM
    callback exists to persist them and they would live only in the checkpoint —
    visible while the run streams and gone on refresh. The marker tells
    ``RunJournal``'s root reconciliation pass to write it to the thread's
    durable event feed. It is server-owned: the Gateway strips it from any
    client-supplied message.

    ``stage_handoff_refused`` records which card this receipt closes, so the
    refusal is said once instead of on every subsequent turn.
    """
    extra: dict[str, Any] = {GRAPH_RECEIPT_KEY: True}
    if stage_handoff_refused:
        extra[STAGE_HANDOFF_REFUSED_KEY] = stage_handoff_refused
    if build_control_refused:
        extra[BUILD_CONTROL_REFUSED_KEY] = build_control_refused
    if metadata:
        extra.update(dict(metadata))
    # The id is minted here rather than left to ``add_messages``. Reconciliation
    # identifies a message by id and skips one that has none, so leaving it to
    # the reducer would make durable delivery of this receipt depend on a
    # framework detail — and the whole reason it carries a marker is that
    # nothing else will persist it.
    return AIMessage(id=message_id or f"dbtl-receipt__{uuid4().hex}", content=content, additional_kwargs=extra)


def build_human_input_messages(
    *,
    request_id: str,
    tool_args: Mapping[str, Any],
    request: Mapping[str, Any],
    fallback_content: str,
    message_id: str | None = None,
) -> tuple[AIMessage, ToolMessage]:
    """Return the exact paired AI/tool envelope for one supervisor card.

    ``tool_args`` and ``request`` are accepted separately on purpose.  The
    former is provider-visible tool-call input; the latter is the richer,
    server-owned artifact consumed by the authenticated frontend and history
    recovery.  Inferring either from the other would collapse that boundary.
    """

    # The request id is the durable authority binding.  The message id is only
    # a delivery identity: a waiting control may be presented again in a later
    # run while every answer must still resolve against the original request.
    # Keeping the two separate prevents ``add_messages`` from replacing the old
    # card in place and leaving the later run with nothing new to deliver.
    envelope_id = message_id or request_id
    tool_call = {
        "name": "ask_clarification",
        "args": dict(tool_args),
        "id": request_id,
        "type": "tool_call",
    }
    return (
        AIMessage(
            id=f"{envelope_id}:call",
            content="",
            tool_calls=[tool_call],
        ),
        ToolMessage(
            id=envelope_id,
            name="ask_clarification",
            tool_call_id=request_id,
            content=fallback_content,
            artifact={"human_input": dict(request)},
        ),
    )
