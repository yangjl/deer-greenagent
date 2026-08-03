"""Read DBTL Human Input state without trusting client-echoed scope.

Every helper in this module resolves replies against a card the server emitted.
That is the boundary that lets the supervisor recover one-shot project/cycle
intent after browser state has disappeared without accepting forged replies.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage

from deerflow.agents.human_input import read_human_input_response
from deerflow.dbtl.build_control import BuildControlAnswer, resolve_answer
from deerflow.dbtl.council import CouncilDepth
from deerflow.dbtl.council_proposal import CouncilProposal, proposal_from_dict
from deerflow.dbtl.council_settings import ParticipantSettings, parse_participant_settings
from deerflow.dbtl.meeting_intent import wants_new_debate
from deerflow.dbtl.routing import ExplicitChoice
from deerflow.utils.messages import message_content_to_text

from .human_input_protocol import (
    BUILD_CONTROL_PREFIX,
    BUILD_CONTROL_REFUSED_KEY,
    COUNCIL_ADJUST_PREFIX,
    COUNCIL_PREFLIGHT_PREFIX,
    DESIGN_AUTHORING_PREFIX,
    DESIGN_CLARIFICATION_PREFIX,
    SETUP_CLARIFICATION_PREFIX,
    SETUP_CONFIRMATION_PREFIX,
    STAGE_HANDOFF_PREFIX,
    STAGE_HANDOFF_REFUSED_KEY,
)


def latest_user_text(state: dict) -> str:
    """Return the newest visible, user-authored message as plain text."""
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        extra = getattr(message, "additional_kwargs", None) or {}
        if extra.get("hide_from_ui") or extra.get("human_input_response"):
            continue
        return message_content_to_text(message.content) or ""
    return ""


def is_new_conversation(state: dict) -> bool:
    """Return whether this is the checkpoint's first visible user turn."""
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


def latest_cycle_request_text(state: dict) -> str:
    """Recover the request that owns the current Design meeting exchange."""
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        extra = getattr(message, "additional_kwargs", None) or {}
        response = read_human_input_response(extra)
        if response and response.get("source") == "ask_clarification" and str(response.get("request_id") or "").startswith(DESIGN_CLARIFICATION_PREFIX):
            return str(response.get("value") or "")
        if response is not None:
            continue
        if extra.get("dbtl_design_kickoff") is True:
            return message_content_to_text(message.content) or ""
        if extra.get("hide_from_ui"):
            continue
        return message_content_to_text(message.content) or ""
    return ""


def card_answer(state: dict, prefix: str) -> tuple[str, str] | None:
    """Return ``(request_id, answer)`` if the newest message answers a card."""
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if not response or response.get("source") != "ask_clarification":
            return None
        request_id = str(response.get("request_id") or "")
        if not request_id.startswith(prefix):
            return None
        return request_id, str(response.get("value") or "")
    return None


def emitted_card_request(state: dict, request_id: str) -> dict | None:
    """Resolve a Human Input request from the server-emitted ToolMessage."""
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, ToolMessage) or message.tool_call_id != request_id:
            continue
        artifact = getattr(message, "artifact", None)
        request = artifact.get("human_input") if isinstance(artifact, dict) else None
        return request if isinstance(request, dict) else None
    return None


def _stage_handoff_option(request: dict[str, Any], response: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve a reply only when it selects an offered Start/Hold option."""
    if request.get("clarification_type") != "dbtl_stage_handoff" or response.get("response_kind") != "option":
        return None
    option_id = str(response.get("option_id") or "")
    option = next((item for item in request.get("options", []) if isinstance(item, dict) and item.get("id") == option_id), None)
    if option is None or str(option.get("value") or "") not in {"start_next_stage", "hold_here"}:
        return None
    return option


def answered_cycle_card_id(state: dict) -> str | None:
    """Recover the cycle bound to the newest server-emitted card reply."""
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if not response or response.get("source") != "ask_clarification":
            return None
        request = emitted_card_request(state, str(response.get("request_id") or ""))
        if request is None:
            return None
        cycle_id = request.get("dbtl_cycle_id")
        return str(cycle_id) if cycle_id else None
    return None


def pending_stage_handoff(state: dict) -> dict[str, Any] | None:
    """Return the newest valid hidden post-approval handoff marker."""
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        extra = getattr(message, "additional_kwargs", None) or {}
        if read_human_input_response(extra) is not None:
            return None
        marker = extra.get("dbtl_post_approval_handoff")
        if isinstance(marker, dict):
            required = ("cycle_id", "approved_stage", "next_stage", "surface_id")
            return marker if all(isinstance(marker.get(key), str) and marker.get(key) for key in required) and isinstance(marker.get("cycle_revision"), int) and int(marker["cycle_revision"]) > 0 else None
        if not extra.get("hide_from_ui"):
            return None
    return None


def answered_stage_handoff(state: dict) -> tuple[str, dict[str, Any]] | None:
    """Resolve a stage-handoff option through its server-owned request."""
    answered = card_answer(state, STAGE_HANDOFF_PREFIX)
    if answered is None:
        return None
    request_id, _ = answered
    request = emitted_card_request(state, request_id)
    if request is None or request.get("clarification_type") != "dbtl_stage_handoff":
        return None
    latest = next((message for message in reversed(state.get("messages") or []) if isinstance(message, HumanMessage)), None)
    response = read_human_input_response(getattr(latest, "additional_kwargs", None) or {}) if latest is not None else None
    if response is None:
        return None
    option = _stage_handoff_option(request, response)
    return (str(option.get("value") or ""), request) if option is not None else None


def answers_a_server_card(state: dict) -> bool:
    """Whether the newest message validly answers a card this server emitted."""
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if not response or response.get("source") != "ask_clarification":
            return False
        request = emitted_card_request(state, str(response.get("request_id") or ""))
        if request is None:
            return False
        if request.get("clarification_type") == "dbtl_stage_handoff":
            return _stage_handoff_option(request, response) is not None
        return True
    return False


def unanswered_stage_handoff_card(state: dict) -> dict[str, Any] | None:
    """Return the newest emitted Start/Hold card that nobody has answered.

    This is the thread-bound pending control the routing fence needs. It reads
    only a card the *server* emitted, so it cannot be forged, and it survives
    the two failures the hidden marker does not: the marker is scanned back only
    as far as the first visible user message, and the observed escape is exactly
    a visible user message ("go ahead with build") arriving after it.

    An answered card — including one answered with **Hold** — is not pending.
    Hold is a decision, and re-presenting it would argue with the person who
    made it; the plan reopens a held handoff on an explicit later request
    instead.
    """
    answered: set[str] = set()
    for message in state.get("messages") or []:
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if not response or response.get("source") != "ask_clarification":
            continue
        request_id = str(response.get("request_id") or "")
        request = emitted_card_request(state, request_id)
        if request is not None and _stage_handoff_option(request, response) is not None:
            answered.add(request_id)
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, ToolMessage):
            continue
        artifact = getattr(message, "artifact", None)
        request = artifact.get("human_input") if isinstance(artifact, dict) else None
        if not isinstance(request, dict) or request.get("clarification_type") != "dbtl_stage_handoff":
            continue
        request_id = str(request.get("request_id") or "")
        if not request_id or request_id in answered:
            return None
        required = ("dbtl_cycle_id", "approved_stage", "next_stage", "design_feedback_surface_id")
        if not all(isinstance(request.get(key), str) and request.get(key) for key in required):
            return None
        # Validated here rather than coerced in ``stage_handoff_marker``: the
        # fence forces every later request through that rebuild, so a card
        # carrying a non-integer revision would raise on every turn and take the
        # whole conversation down with it.
        if not isinstance(request.get("cycle_revision"), int) or isinstance(request.get("cycle_revision"), bool) or int(request["cycle_revision"]) <= 0:
            return None
        return request
    return None


def stage_handoff_refusal_recorded(state: dict, request_id: str) -> bool:
    """Whether this card's "no longer actionable" receipt was already given."""
    if not request_id:
        return False
    for message in state.get("messages") or []:
        extra = getattr(message, "additional_kwargs", None) or {}
        if extra.get(STAGE_HANDOFF_REFUSED_KEY) == request_id:
            return True
    return False


def pending_stage_handoff_control(state: dict, *, selected_cycle_id: str | None = None) -> dict[str, Any] | None:
    """The one Start/Hold control this request should be answering, if any.

    Shared by the routing fence and the handler that re-presents the card, so
    the two cannot disagree about whether a control is pending — a fence that
    intercepts a request the handler then declines to answer would fall through
    to stage execution, which is the opposite of what the fence is for.

    Three things make a card *not* pending. It was answered (including with
    Hold, which is a decision). Its refusal was already recorded, so the reason
    has been given and the conversation is released rather than trapped
    repeating it. Or the request explicitly names a different cycle, so this
    card belongs to other work — a card that never names its own cycle must not
    be answered by someone who thinks they are looking at another one.
    """
    if answers_a_server_card(state):
        return None
    request = unanswered_stage_handoff_card(state)
    if request is None:
        return None
    if stage_handoff_refusal_recorded(state, str(request.get("request_id") or "")):
        return None
    if selected_cycle_id and str(request.get("dbtl_cycle_id") or "") != str(selected_cycle_id):
        return None
    return request


def latest_stage_handoff_state(state: dict) -> tuple[dict[str, Any], str | None] | None:
    """Return the newest emitted Start/Hold card and the option chosen, if any.

    Unlike :func:`unanswered_stage_handoff_card` this keeps an answered card, so
    an ordinary run can be told a stage is *open but deliberately not started*.
    That is the state a person is in right after choosing Hold, and it is the
    one the lead agent most needs stated: the stage exists, and nothing the lead
    agent does may start it.
    """
    answers: dict[str, str] = {}
    for message in state.get("messages") or []:
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if response and response.get("source") == "ask_clarification":
            answers[str(response.get("request_id") or "")] = str(response.get("option_id") or response.get("value") or "")
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, ToolMessage):
            continue
        artifact = getattr(message, "artifact", None)
        request = artifact.get("human_input") if isinstance(artifact, dict) else None
        if not isinstance(request, dict) or request.get("clarification_type") != "dbtl_stage_handoff":
            continue
        return request, answers.get(str(request.get("request_id") or "")) or None
    return None


def stage_handoff_marker(request: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the handoff marker from the server-emitted card's own bindings."""
    return {
        "version": 1,
        "cycle_id": str(request.get("dbtl_cycle_id") or ""),
        "cycle_revision": int(request.get("cycle_revision") or 0),
        "approved_stage": str(request.get("approved_stage") or ""),
        "next_stage": str(request.get("next_stage") or ""),
        "surface_id": str(request.get("design_feedback_surface_id") or ""),
    }


def routing_input(state: dict) -> tuple[str, ExplicitChoice | None, str | None]:
    """Return routing text and any scope recovered from an answered card."""
    confirmed = card_answer(state, SETUP_CONFIRMATION_PREFIX)
    if confirmed is not None:
        request = emitted_card_request(state, confirmed[0])
        if request is not None:
            return str(request.get("source_request") or ""), ExplicitChoice.START_CYCLE, None
    answered = card_answer(state, SETUP_CLARIFICATION_PREFIX)
    if answered is not None:
        request = emitted_card_request(state, answered[0])
        if request is not None:
            source_request = str(request.get("source_request") or "")
            return f"{source_request}\n\n{answered[1]}".strip(), ExplicitChoice.START_CYCLE, None
    cycle_id = answered_cycle_card_id(state)
    if cycle_id is not None:
        return latest_cycle_request_text(state), ExplicitChoice.CONTINUE_CYCLE, cycle_id
    return latest_user_text(state), None, None


def confirmation_answer(state: dict) -> str | None:
    answered = card_answer(state, SETUP_CONFIRMATION_PREFIX)
    if answered is None or emitted_card_request(state, answered[0]) is None:
        return None
    return answered[1].strip().lower()


def has_emitted_card(state: dict, prefix: str) -> bool:
    for message in state.get("messages") or []:
        artifact = getattr(message, "artifact", None)
        payload = artifact.get("human_input") if isinstance(artifact, dict) else None
        if isinstance(payload, dict) and str(payload.get("request_id") or "").startswith(prefix):
            return True
    return False


def answered_test_card(state: dict, prefix: str) -> tuple[str, str, dict[str, Any]] | None:
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if not response or response.get("source") != "ask_clarification":
            return None
        request_id = str(response.get("request_id") or "")
        if not request_id.startswith(prefix) or response.get("response_kind") != "option":
            return None
        emitted = emitted_card_request(state, request_id)
        snapshot = dict((emitted or {}).get("test_review_snapshot") or {})
        if not snapshot or not (emitted or {}).get("dbtl_cycle_id"):
            return None
        option_id = str(response.get("option_id") or "")
        option = next((item for item in (emitted or {}).get("options", []) if isinstance(item, dict) and item.get("id") == option_id), None)
        if option is None:
            return None
        return request_id, str(option.get("value") or ""), snapshot
    return None


def authored_design(state: dict) -> str | None:
    answered = card_answer(state, DESIGN_AUTHORING_PREFIX)
    if answered is None:
        return None
    request = emitted_card_request(state, answered[0])
    return answered[1] if request is not None and request.get("council_depth") == CouncilDepth.HUMAN_INPUT.value else None


def wants_roster_adjustment(state: dict, adjust_option: str = "adjust") -> bool:
    answered = card_answer(state, COUNCIL_PREFLIGHT_PREFIX)
    return bool(answered and emitted_card_request(state, answered[0]) is not None and answered[1].strip().lower() == adjust_option)


def council_adjustment(state: dict) -> str | None:
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if not response or response.get("source") != "ask_clarification":
            continue
        request_id = str(response.get("request_id") or "")
        if not request_id.startswith(COUNCIL_ADJUST_PREFIX):
            continue
        if emitted_card_request(state, request_id) is None:
            return None
        return str(response.get("value") or "").strip() or None
    return None


def confirmed_council_depth(state: dict) -> CouncilDepth | None:
    answered = card_answer(state, COUNCIL_PREFLIGHT_PREFIX)
    if answered is None or emitted_card_request(state, answered[0]) is None:
        return None
    try:
        return CouncilDepth(answered[1].strip().lower())
    except ValueError:
        return None


def confirmed_participant_settings(state: dict, known_models: Sequence[str]) -> dict[str, ParticipantSettings]:
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        raw = (getattr(message, "additional_kwargs", None) or {}).get("human_input_response")
        if not isinstance(raw, dict):
            return {}
        request_id = str(raw.get("request_id") or "")
        if raw.get("source") != "ask_clarification" or not request_id.startswith(COUNCIL_PREFLIGHT_PREFIX):
            return {}
        if emitted_card_request(state, request_id) is None:
            return {}
        return parse_participant_settings(raw.get("participants"), known_models=known_models)
    return {}


def confirmed_council_proposal(state: dict) -> CouncilProposal | None:
    answered = card_answer(state, COUNCIL_PREFLIGHT_PREFIX)
    request = emitted_card_request(state, answered[0]) if answered is not None else None
    return proposal_from_dict(request.get("council_proposal")) if request is not None else None


def latest_answered_council_preflight(state: dict) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        raw = (getattr(message, "additional_kwargs", None) or {}).get("human_input_response")
        if not isinstance(raw, dict):
            continue
        request_id = str(raw.get("request_id") or "")
        if raw.get("source") != "ask_clarification" or not request_id.startswith(COUNCIL_PREFLIGHT_PREFIX):
            continue
        request = emitted_card_request(state, request_id)
        if request is not None:
            return request, raw
    return None


def rerun_requested_after_preflight(state: dict) -> bool:
    """True when someone asked for another meeting after the last preflight.

    The preflight is once-only per meeting, which is right for every turn that
    belongs to the meeting it opened — re-asking on the answering turn would
    argue with the person who just answered. A deliberate re-run is a *new*
    meeting spending another meeting's budget, and it arrives with that guard
    already tripped, so nobody is asked and none of the confirmed settings can
    be read back.

    Anchored on position rather than on the phrase alone, because the phrase
    stays in history: once the card is emitted it is newer than the request
    that asked for it, so the answering turn dispatches instead of asking the
    same question forever. That also covers an answer the server cannot read —
    a stale client sending an unknown depth must not trap the cycle in a loop.
    """
    messages = list(state.get("messages") or [])
    last_card = -1
    for index, message in enumerate(messages):
        if not isinstance(message, ToolMessage):
            continue
        artifact = getattr(message, "artifact", None)
        payload = artifact.get("human_input") if isinstance(artifact, dict) else None
        if isinstance(payload, dict) and str(payload.get("request_id") or "").startswith(COUNCIL_PREFLIGHT_PREFIX):
            last_card = index
    if last_card < 0:
        return False
    for index in range(len(messages) - 1, last_card, -1):
        message = messages[index]
        if not isinstance(message, HumanMessage):
            continue
        extra = getattr(message, "additional_kwargs", None) or {}
        if extra.get("hide_from_ui") or read_human_input_response(extra) is not None:
            continue
        # Only the newest visible request counts, matching how the stage adapter
        # decides whether a held design should be argued again.
        return wants_new_debate(message_content_to_text(message.content) or "")
    return False


def prior_council_setup(
    state: dict,
    known_models: Sequence[str],
) -> tuple[CouncilDepth | None, CouncilProposal | None, dict[str, ParticipantSettings]]:
    """The setup confirmed for the last meeting, for a re-run to open on.

    Read from the newest *answered* preflight anywhere in history rather than
    from the answering turn, because a re-run answers nothing. Any part that
    cannot be recovered falls back to being derived fresh — an "adjust" reply
    carries no depth, and an older card carried no participant dials at all.
    """
    answered = latest_answered_council_preflight(state)
    if answered is None:
        return None, None, {}
    request, raw = answered
    try:
        depth = CouncilDepth(str(raw.get("value") or "").strip().lower())
    except ValueError:
        depth = None
    return (
        depth,
        proposal_from_dict(request.get("council_proposal")),
        parse_participant_settings(raw.get("participants"), known_models=known_models),
    )


def resumed_council_setup(state: dict, known_models: Sequence[str]) -> tuple[CouncilProposal | None, dict[str, ParticipantSettings]]:
    answered = latest_answered_council_preflight(state)
    if answered is None:
        return None, {}
    request, raw = answered
    return proposal_from_dict(request.get("council_proposal")), parse_participant_settings(raw.get("participants"), known_models=known_models)


def answered_build_control(state: dict) -> BuildControlAnswer | None:
    """Resolve a Build-control reply through the card the server emitted.

    The reply is the one part of this exchange a client authors, so the option
    it names is matched against the options that card actually offered. A forged
    request id resolves to no card and selects nothing; an invented option id
    matches nothing on a real one. Either way the request falls through to
    ordinary routing rather than starting, replanning, or restarting a governed
    Build.
    """
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if not response or response.get("source") != "ask_clarification":
            return None
        request_id = str(response.get("request_id") or "")
        if not request_id.startswith(BUILD_CONTROL_PREFIX):
            return None
        return resolve_answer(emitted_card_request(state, request_id), response)
    return None


def unanswered_build_control_card(state: dict) -> dict[str, Any] | None:
    """The newest Build control the server emitted that nobody has answered.

    Same shape as the Start/Hold fence and for the same reason: while a control
    the server raised is still waiting, a free-text follow-up is *answering it*,
    and letting that reach ordinary chat is how a paused Build silently becomes
    a conversation with the lead agent about a build it cannot start.
    """
    answered: set[str] = set()
    for message in state.get("messages") or []:
        if not isinstance(message, HumanMessage):
            continue
        response = read_human_input_response(getattr(message, "additional_kwargs", None) or {})
        if not response or response.get("source") != "ask_clarification":
            continue
        request_id = str(response.get("request_id") or "")
        if request_id.startswith(BUILD_CONTROL_PREFIX) and resolve_answer(emitted_card_request(state, request_id), response) is not None:
            answered.add(request_id)
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, ToolMessage):
            continue
        artifact = getattr(message, "artifact", None)
        request = artifact.get("human_input") if isinstance(artifact, dict) else None
        if not isinstance(request, dict) or request.get("clarification_type") != "dbtl_build_control":
            continue
        request_id = str(request.get("request_id") or "")
        return None if not request_id or request_id in answered else request
    return None


def build_control_refusal_recorded(state: dict, request_id: str) -> bool:
    """Whether this control's "no longer actionable" receipt was already given."""
    if not request_id:
        return False
    return any((getattr(message, "additional_kwargs", None) or {}).get(BUILD_CONTROL_REFUSED_KEY) == request_id for message in state.get("messages") or [])


def pending_build_control(state: dict, *, selected_cycle_id: str | None = None) -> dict[str, Any] | None:
    """The one Build control this request should be answering, if any.

    Shared by the routing fence and the handler that re-presents the card, so
    the two cannot disagree — a fence that intercepts a request the handler then
    declines would fall straight through to dispatching stage work, which is the
    opposite of what the fence is for.

    Three things make a control *not* pending, matching the Start/Hold fence: it
    was answered, its refusal was already recorded (so a control that can no
    longer be answered truthfully states its reason once and releases the
    conversation rather than trapping it), or the request names a different
    cycle — a project runs several builds at once.
    """
    if answers_a_server_card(state):
        return None
    request = unanswered_build_control_card(state)
    if request is None:
        return None
    if build_control_refusal_recorded(state, str(request.get("request_id") or "")):
        return None
    if selected_cycle_id and str(request.get("dbtl_cycle_id") or "") != str(selected_cycle_id):
        return None
    return request
