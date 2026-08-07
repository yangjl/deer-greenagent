"""Pure contracts for conversational DBTL discovery.

Discovery is the conversation that may precede a DBTL cycle.  It is allowed to
remember a proposed brief, but it is deliberately unable to represent a cycle,
stage, gate, or transition.  Persistence and graph routing are separate layers;
keeping the vocabulary here dependency-free lets those layers share one closed
contract without giving a classifier or model response workflow authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from deerflow.dbtl.classifier import derive_objective, missing_clarification_fields
from deerflow.dbtl.routing import explicit_start_objective_text, is_explicit_start_request


class DiscoveryStatus(StrEnum):
    """Server-owned lifecycle for one thread-bound discovery."""

    GATHERING = "gathering"
    READY = "ready"
    OFFERED = "offered"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


TERMINAL_DISCOVERY_STATUSES = frozenset(
    {
        DiscoveryStatus.CONFIRMED,
        DiscoveryStatus.DECLINED,
        DiscoveryStatus.SUPERSEDED,
        DiscoveryStatus.EXPIRED,
    }
)


class DiscoveryTrigger(StrEnum):
    """What entered discovery; never an authority to create a cycle."""

    EXPLICIT = "explicit"
    CLASSIFIER = "classifier"


class DiscoveryProvenance(StrEnum):
    """Where a proposed value originated."""

    USER_TURN = "user_turn"
    PROJECT_ARTIFACT = "project_artifact"
    PROJECT_HISTORY = "project_history"
    PROJECT_MEMORY = "project_memory"
    SHARED_PROJECT_MEMORY = "shared_project_memory"
    USER_GLOBAL_MEMORY = "user_global_memory"
    PUBLISHED_KNOWLEDGE = "published_knowledge"
    MODEL_SUGGESTION = "model_suggestion"


class DiscoveryAction(StrEnum):
    """Stable ids accepted by the future server-bound card."""

    START = "start_cycle"
    KEEP_DISCUSSING = "keep_discussing"
    CONTINUE_ORDINARY = "continue_ordinary"


DISCOVERY_CARD_TYPE = "dbtl_discovery_start"
DISCOVERY_NO_RECORD_NOTICE = "No cycle has been created yet."
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHOW_OFFER = re.compile(
    r"\b(?:show|review|open|present|see)\s+(?:me\s+|the\s+|my\s+)*(?:cycle\s+)?(?:proposal|brief|draft|plan|start\s+card)\b",
    re.IGNORECASE,
)


def wants_discovery_offer(text: str) -> bool:
    """Whether the owner explicitly asked to review the current proposal."""

    return bool(_SHOW_OFFER.search(text or ""))


@dataclass(frozen=True, slots=True)
class DiscoverySourceRef:
    """A bounded reference to context actually supplied to discovery."""

    provenance: DiscoveryProvenance
    reference: str
    revision: str = ""
    scope: str = ""
    excerpt: str = ""


@dataclass(frozen=True, slots=True)
class DiscoveryValue:
    """One proposed value with origin and human-acceptance kept separate."""

    value: str
    provenance: DiscoveryProvenance
    source_ref: str = ""
    accepted: bool = False
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryDraft:
    """Versioned pre-cycle brief.

    The type has no cycle id or governance status by design.  ``project_id``
    and ``thread_id`` bind the candidate to an authorized conversation; they do
    not grant the draft authority over a cycle.
    """

    discovery_id: str
    project_id: str
    thread_id: str
    user_id: str
    trigger: DiscoveryTrigger
    policy_version: str
    revision: int
    status: DiscoveryStatus = DiscoveryStatus.GATHERING
    proposed_title: DiscoveryValue | None = None
    objective: DiscoveryValue | None = None
    rationale: DiscoveryValue | None = None
    known_inputs: tuple[DiscoveryValue, ...] = ()
    intended_outputs: tuple[DiscoveryValue, ...] = ()
    success_criteria: tuple[DiscoveryValue, ...] = ()
    rejection_criteria: tuple[DiscoveryValue, ...] = ()
    open_questions: tuple[str, ...] = ()
    context_refs: tuple[DiscoverySourceRef, ...] = ()
    offered_revision: int | None = None

    @property
    def creates_record(self) -> bool:
        """Discovery values cannot claim to have created a DBTL record."""

        return False


@dataclass(frozen=True, slots=True)
class DiscoveryReadiness:
    """Deterministic minimum for automatic offering."""

    ready: bool
    missing: tuple[str, ...] = ()


def assess_discovery_readiness(draft: DiscoveryDraft) -> DiscoveryReadiness:
    """Evaluate the plan's minimum readiness without model judgement."""

    missing: list[str] = []
    if not draft.project_id or not draft.thread_id:
        missing.append("project scope")
    if draft.objective is None or not draft.objective.value.strip():
        missing.append("objective")
    if draft.rationale is None or not draft.rationale.value.strip():
        missing.append("DBTL rationale")
    if not any(item.value.strip() for item in draft.intended_outputs):
        missing.append("intended output or decision")
    return DiscoveryReadiness(ready=not missing, missing=tuple(missing))


def build_discovery_package(request_text: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a bounded deterministic starting brief from conversation text.

    Phase 2 deliberately starts with deterministic extraction. A future model
    updater may propose richer fields, but it must produce this same closed
    payload and preserve the provenance entries below.
    """

    prior = dict(previous or {})
    missing = list(missing_clarification_fields(request_text))
    objective_text = request_text
    if is_explicit_start_request(request_text):
        objective_text = explicit_start_objective_text(request_text)
    derived_objective = derive_objective(objective_text).strip() if objective_text.strip() else ""
    objective = derived_objective or str(prior.get("objective") or "").strip()
    title = str(prior.get("proposed_title") or objective[:120]).strip()
    rationale = str(prior.get("rationale") or "").strip()
    intended = list(prior.get("intended_outputs") or [])
    if objective and not rationale:
        rationale = "The objective benefits from explicit Design, Build, Test, and Learn review boundaries."
    if objective and not intended:
        intended = [f"A reviewed result for: {objective}"[:240]]
    if not objective and "research objective" not in missing:
        missing.insert(0, "research objective")
    return {
        "proposed_title": title,
        "objective": objective,
        "rationale": rationale,
        "known_inputs": list(prior.get("known_inputs") or []),
        "intended_outputs": intended,
        "success_criteria": str(prior.get("success_criteria") or ""),
        "rejection_criteria": list(prior.get("rejection_criteria") or []),
        "open_questions": missing,
        "provenance": {
            "objective": {"source": DiscoveryProvenance.USER_TURN.value, "accepted": True},
            "rationale": {"source": DiscoveryProvenance.MODEL_SUGGESTION.value, "accepted": False},
            "intended_outputs": {"source": DiscoveryProvenance.MODEL_SUGGESTION.value, "accepted": False},
        },
    }


# Stable tool name for the model-seeded structured-output package. The discovery
# read-only tool policy must allow this name through, and the Lead call's
# ``response_format`` schema (``discovery_schema.DbtlDiscoveryPackage``) must be
# named to match, because LangChain derives the structured tool name from the
# schema class name.
DISCOVERY_PACKAGE_TOOL_NAME = "DbtlDiscoveryPackage"

# Bounds for a model-seeded package. Kept here (not in the schema) so the pure
# normaliser is the one authority: the model may over- or under-fill, and this
# closes the payload before it can seed a card.
_MAX_TITLE_LEN = 120
_MAX_TEXT_LEN = 2000
_MAX_ITEM_LEN = 280
_MAX_KNOWN_INPUTS = 20
_MAX_INTENDED_OUTPUTS = 10
_MAX_SUCCESS_CRITERIA = 10
_MAX_REJECTION_CRITERIA = 10
_MAX_CONFLICTS = 10
_MAX_OPEN_QUESTIONS = 5
# The deterministic builder's placeholder output; a model that only echoes this
# has added nothing concrete, so it is dropped rather than offered.
_GENERIC_OUTPUT_PREFIX = "a reviewed result for"

_ACCEPTED_PROVENANCE_FIELDS = (
    "objective",
    "rationale",
    "known_inputs",
    "intended_outputs",
    "success_criteria",
    "rejection_criteria",
)


def _clean_str(value: Any, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _clean_list(value: Any, *, limit_items: int, limit_len: int = _MAX_ITEM_LEN) -> list[str]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        value = [value] if value else []
    cleaned: list[str] = []
    for item in value:
        text = str(item or "").strip()[:limit_len]
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= limit_items:
            break
    return cleaned


def normalize_model_discovery_package(
    raw: Any,
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Close a model-emitted discovery package into the deterministic payload shape.

    Returns ``None`` when the structured output is missing, malformed, or lacks a
    concrete objective and intended output. The caller falls back to
    :func:`build_discovery_package` on ``None`` so an invalid model proposal can
    never seed a card. The returned dict is shape-compatible with
    :func:`build_discovery_package` (plus an additive ``conflicts`` list) so the
    rest of the discovery pipeline is unchanged.
    """

    if hasattr(raw, "model_dump"):
        try:
            raw = raw.model_dump()
        except Exception:  # noqa: BLE001 - a schema object that will not dump is not usable
            return None
    if not isinstance(raw, dict):
        return None

    objective = _clean_str(raw.get("objective"), limit=_MAX_TEXT_LEN)
    assistant_response = _clean_str(raw.get("assistant_response"), limit=_MAX_TEXT_LEN)
    if not objective or not assistant_response:
        return None

    outputs = [item for item in _clean_list(raw.get("intended_outputs"), limit_items=_MAX_INTENDED_OUTPUTS) if not item.lower().startswith(_GENERIC_OUTPUT_PREFIX)]
    if not outputs:
        return None

    title = _clean_str(raw.get("proposed_title"), limit=_MAX_TITLE_LEN) or objective[:_MAX_TITLE_LEN]
    rationale = _clean_str(raw.get("rationale"), limit=_MAX_TEXT_LEN)
    known_inputs = _clean_list(raw.get("known_inputs"), limit_items=_MAX_KNOWN_INPUTS)
    success_list = _clean_list(raw.get("success_criteria"), limit_items=_MAX_SUCCESS_CRITERIA)
    rejection = _clean_list(raw.get("rejection_criteria"), limit_items=_MAX_REJECTION_CRITERIA)
    conflicts = _clean_list(raw.get("conflicts"), limit_items=_MAX_CONFLICTS)
    open_questions = _clean_list(raw.get("open_questions"), limit_items=_MAX_OPEN_QUESTIONS)

    accepted = {str(name).strip() for name in (raw.get("accepted_fields") or []) if isinstance(name, str)}
    provenance: dict[str, dict[str, Any]] = {}
    for field in _ACCEPTED_PROVENANCE_FIELDS:
        is_accepted = field in accepted
        provenance[field] = {
            "source": DiscoveryProvenance.USER_TURN.value if is_accepted else DiscoveryProvenance.MODEL_SUGGESTION.value,
            "accepted": is_accepted,
        }

    prior = dict(previous or {})
    return {
        "assistant_response": assistant_response,
        "proposed_title": title,
        "objective": objective,
        "rationale": rationale or str(prior.get("rationale") or "").strip(),
        "known_inputs": known_inputs or list(prior.get("known_inputs") or []),
        "intended_outputs": outputs,
        "success_criteria": "; ".join(success_list),
        "rejection_criteria": rejection,
        "conflicts": conflicts,
        "open_questions": open_questions,
        "provenance": provenance,
    }


_ALLOWED_TRANSITIONS: dict[DiscoveryStatus, frozenset[DiscoveryStatus]] = {
    DiscoveryStatus.GATHERING: frozenset(
        {
            DiscoveryStatus.GATHERING,
            DiscoveryStatus.READY,
            DiscoveryStatus.DECLINED,
            DiscoveryStatus.SUPERSEDED,
            DiscoveryStatus.EXPIRED,
        }
    ),
    DiscoveryStatus.READY: frozenset(
        {
            DiscoveryStatus.GATHERING,
            DiscoveryStatus.READY,
            DiscoveryStatus.OFFERED,
            DiscoveryStatus.DECLINED,
            DiscoveryStatus.SUPERSEDED,
            DiscoveryStatus.EXPIRED,
        }
    ),
    DiscoveryStatus.OFFERED: frozenset(
        {
            DiscoveryStatus.GATHERING,
            DiscoveryStatus.CONFIRMED,
            DiscoveryStatus.DECLINED,
            DiscoveryStatus.SUPERSEDED,
            DiscoveryStatus.EXPIRED,
        }
    ),
    DiscoveryStatus.CONFIRMED: frozenset(),
    DiscoveryStatus.DECLINED: frozenset(),
    DiscoveryStatus.SUPERSEDED: frozenset(),
    DiscoveryStatus.EXPIRED: frozenset(),
}


def can_transition_discovery(current: DiscoveryStatus, target: DiscoveryStatus) -> bool:
    """Whether a compare-and-set may advance between two lifecycle states."""

    return target in _ALLOWED_TRANSITIONS[current]


def discovery_card_request(draft: DiscoveryDraft, *, proposal_hash: str) -> dict[str, Any]:
    """Build the deterministic, revision-bound Human Input request payload.

    Rendering may grow richer later, but the authority-bearing identity and
    action ids are fixed here.  No option is selected by the server.
    """

    normalized_hash = proposal_hash.strip().lower()
    if not _SHA256.fullmatch(normalized_hash):
        raise ValueError("proposal_hash must be a 64-character SHA-256 digest")
    if draft.revision < 1:
        raise ValueError("a discovery card must bind a positive revision")

    return {
        "version": 1,
        "kind": "human_input_request",
        "source": "ask_clarification",
        "request_id": "",
        "clarification_type": DISCOVERY_CARD_TYPE,
        "discovery_id": draft.discovery_id,
        "discovery_revision": draft.revision,
        "project_id": draft.project_id,
        "thread_id": draft.thread_id,
        "policy_version": draft.policy_version,
        "proposal_hash": normalized_hash,
        "title": "Ready to start a DBTL cycle",
        "question": "Start a durable DBTL cycle from this proposal?",
        "context": DISCOVERY_NO_RECORD_NOTICE,
        "input_mode": "single_choice",
        "notice": DISCOVERY_NO_RECORD_NOTICE,
        "options": [
            {"id": DiscoveryAction.START.value, "label": "Start DBTL cycle", "value": DiscoveryAction.START.value},
            {"id": DiscoveryAction.KEEP_DISCUSSING.value, "label": "Keep discussing", "value": DiscoveryAction.KEEP_DISCUSSING.value},
            {"id": DiscoveryAction.CONTINUE_ORDINARY.value, "label": "Continue as ordinary work", "value": DiscoveryAction.CONTINUE_ORDINARY.value},
        ],
    }
