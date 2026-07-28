"""A council roster proposed for the question, then validated before it seats.

:mod:`deerflow.dbtl.council` computes a roster from capability selection. That
is deterministic and auditable, and it has one failure that shows up in every
deployment which has not registered specialists: an undeclared agent covers
nothing, ``general-purpose`` covers everything, so every seat resolves to the
same agent running the same prompt lineage. Three copies of one model is not a
debate, and the review package cannot tell you it wasn't one.

This module lets the roster be *proposed* for the actual question. The seats
carry a **focus** and a **brief** — what this seat argues from — so three seats
disagree even when all three are the same agent. That is the part that works
today, without anyone registering a specialist first.

What must not come with it is a roster a model can invent. Two rules:

**Fail closed, per seat.** An agent nobody registered or a model nobody
configured is refused with a reason, never swapped for the generalist. That
silent swap is precisely the bug the roster work exists to prevent, and it would
be far harder to spot arriving through a feature meant to fix it. One bad seat
does not discard the good ones — a partly-refused roster is still better than
the identical-triplet fallback.

**Degrade, never raise.** An unusable reply costs the deterministic council, not
the cycle. The caller falls back to capability selection, which is what ran
before proposals existed: a worse council, not a failed one.

Nothing here calls a model or dispatches anything. The proposal is values, so
what a council would be composed of is testable without a model or a registry.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from deerflow.dbtl.capabilities import Capability

logger = logging.getLogger(__name__)

#: A hard ceiling independent of the depth the caller passes. Depth is a human's
#: dial and this is a safety limit; keeping them separate means a bug in depth
#: handling cannot dispatch forty workers.
MAX_PROPOSED_POSITIONS = 6

MAX_FOCUS_CHARS = 80
MAX_BRIEF_CHARS = 600
_MAX_SEATS_READ = 40


@dataclass(frozen=True, slots=True)
class ProposedSeat:
    """One seat, described by what it will argue rather than by its job title."""

    focus: str
    brief: str
    agent_name: str
    capability: Capability
    model: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "focus": self.focus,
            "brief": self.brief,
            "agent_name": self.agent_name,
            "capability": self.capability.value,
            "model": self.model,
        }


@dataclass(frozen=True, slots=True)
class CouncilProposal:
    """A validated roster, plus everything that was refused and why."""

    positions: tuple[ProposedSeat, ...] = ()
    chair: ProposedSeat | None = None
    rejected: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        """Whether this is worth seating instead of falling back.

        One surviving position is enough: it still carries a brief written for
        this question, which is the whole advantage over generic selection.
        """
        return bool(self.positions)

    def as_dict(self) -> dict[str, object]:
        return {
            "positions": [seat.as_dict() for seat in self.positions],
            "chair": self.chair.as_dict() if self.chair is not None else None,
            "rejected": list(self.rejected),
            "notes": list(self.notes),
            "usable": self.usable,
        }


#: Registered subagents that cannot hold a council seat.
#:
#: Being registered is not the same as being able to debate. ``bash`` is a
#: real subagent, so the fail-closed "is this a known agent?" check accepted
#: it — and a live council duly seated it as an independent position, where it
#: spent its entire turn budget running commands and returned no argument. An
#: execution specialist has no position to take.
#:
#: Deliberately a short list of *built-in* executors rather than a rule like
#: "must declare ``dbtl_capabilities``": most deployments register no
#: capabilities at all, and requiring them would collapse every council back to
#: the single generalist this whole proposal path exists to escape.
NON_DELIBERATIVE_AGENTS = frozenset({"bash"})


def is_deliberative_agent(agent_name: str) -> bool:
    """Whether *agent_name* can argue a position rather than only act."""
    return str(agent_name or "").strip().lower() not in NON_DELIBERATIVE_AGENTS


def seatable_agents(agent_names: Sequence[str]) -> tuple[str, ...]:
    """The subset of *agent_names* that may be offered a council seat.

    Applied to the prompt's agent list as well as the parser's check: the model
    cannot pick what it was never shown, and refusing afterwards is the
    backstop rather than the mechanism.
    """
    return tuple(name for name in agent_names if is_deliberative_agent(name))


def _extract_object(text: str) -> Mapping[str, object] | None:
    """The outermost JSON object, or ``None``.

    Mirrors ``worker_result.extract_result_payload``'s tolerance for a code
    fence or a sentence of preamble, but returns rather than raises: a roster is
    an optimisation, and refusing the run because the model wrapped its answer
    in prose would trade a better council for no council.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _clean(value: object, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _parse_seat(
    raw: object,
    *,
    known_agents: Sequence[str],
    known_models: Sequence[str],
    label: str,
) -> tuple[ProposedSeat | None, str | None]:
    """One seat, or the reason it was refused."""
    if not isinstance(raw, Mapping):
        return None, f"{label}: expected an object describing one seat."

    focus = _clean(raw.get("focus"), limit=MAX_FOCUS_CHARS)
    brief = _clean(raw.get("brief"), limit=MAX_BRIEF_CHARS)
    if not focus:
        return None, f"{label}: a seat needs a focus naming what it brings."
    if not brief:
        # A seat with no angle is the generic prompt with extra steps, and
        # seating it would spend a worker to reproduce what selection already
        # does for free.
        return None, f"{label} ({focus}): a seat needs a brief saying what it argues from."

    agent_name = _clean(raw.get("agent_name"), limit=120)
    if agent_name not in set(known_agents):
        known = ", ".join(known_agents) or "none"
        return None, f"{label} ({focus}): {agent_name or 'no agent'!r} is not a registered agent (known: {known})."
    if not is_deliberative_agent(agent_name):
        return None, f"{label} ({focus}): {agent_name!r} runs commands rather than arguing a position, so it cannot hold a council seat."

    raw_model = raw.get("model")
    model = _clean(raw_model, limit=120) or None
    if model is not None and model not in set(known_models):
        known = ", ".join(known_models) or "none"
        return None, f"{label} ({focus}): {model!r} is not a configured model (known: {known})."

    raw_capability = _clean(raw.get("capability"), limit=120)
    try:
        capability = Capability(raw_capability)
    except ValueError:
        return None, f"{label} ({focus}): {raw_capability!r} is not a known capability."

    return ProposedSeat(focus=focus, brief=brief, agent_name=agent_name, capability=capability, model=model), None


def parse_council_proposal(
    text: str,
    *,
    known_agents: Sequence[str],
    known_models: Sequence[str],
    max_positions: int,
) -> CouncilProposal:
    """Validate a proposed roster. Never raises; refuses seat by seat."""
    payload = _extract_object(text)
    if payload is None:
        return CouncilProposal(notes=("The roster reply was not a JSON object.",))

    ceiling = max(0, min(int(max_positions), MAX_PROPOSED_POSITIONS))
    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, Sequence) or isinstance(raw_positions, str):
        return CouncilProposal(notes=("The roster reply carried no 'positions' list.",))

    seats: list[ProposedSeat] = []
    rejected: list[str] = []
    notes: list[str] = []
    seen_briefs: set[str] = set()

    for index, raw in enumerate(list(raw_positions)[:_MAX_SEATS_READ], start=1):
        seat, reason = _parse_seat(raw, known_agents=known_agents, known_models=known_models, label=f"position {index}")
        if seat is None:
            if reason:
                rejected.append(reason)
            continue
        # Two seats arguing the same thing are one seat and twice the bill, and
        # they would read to a reviewer as corroboration rather than repetition.
        fingerprint = seat.brief.casefold()
        if fingerprint in seen_briefs:
            notes.append(f"Dropped a duplicate seat: {seat.focus} repeats an earlier brief.")
            continue
        seen_briefs.add(fingerprint)
        seats.append(seat)

    if len(seats) > ceiling:
        notes.append(f"Roster capped at {ceiling} position(s) by the chosen depth; {len(seats) - ceiling} were dropped.")
        seats = seats[:ceiling]

    chair, chair_reason = (None, None)
    if payload.get("chair") is not None:
        chair, chair_reason = _parse_seat(
            payload.get("chair"),
            known_agents=known_agents,
            known_models=known_models,
            label="chair",
        )
        if chair_reason:
            rejected.append(chair_reason)

    return CouncilProposal(
        positions=tuple(seats),
        chair=chair,
        rejected=tuple(rejected),
        notes=tuple(notes),
    )


def proposal_as_dict(proposal: CouncilProposal) -> dict[str, object]:
    """The roster as plain data, for the card that shows it to a person."""

    def _seat(seat: ProposedSeat) -> dict[str, object]:
        return {
            "focus": seat.focus,
            "brief": seat.brief,
            "agent_name": seat.agent_name,
            "capability": seat.capability.value,
            "model": seat.model,
        }

    return {
        "positions": [_seat(seat) for seat in proposal.positions],
        "chair": _seat(proposal.chair) if proposal.chair is not None else None,
    }


def proposal_from_plan(plan) -> CouncilProposal | None:
    """Recover the proposal represented by a previewed council plan.

    The preflight shows a ``CouncilPlan``, while dispatch needs the proposal
    that gave that plan its question-specific focuses, briefs, agents, and
    models. Serializing this value on the server-emitted card lets the answer
    turn replay those exact seats instead of asking the roster writer twice.

    ``plan`` remains untyped here to avoid importing ``council``, which already
    calls into this module.
    """

    def _seat(raw, *, role: str) -> ProposedSeat | None:
        try:
            capability = Capability(raw.capability)
        except (AttributeError, ValueError):
            return None
        focus = _clean(
            getattr(raw, "focus", "") or (capability.value.replace("_", " ") if role == "position" else "synthesis"),
            limit=MAX_FOCUS_CHARS,
        )
        brief = _clean(getattr(raw, "brief", ""), limit=MAX_BRIEF_CHARS)
        agent_name = _clean(getattr(raw, "agent_name", ""), limit=120)
        if not focus or not brief or not agent_name or not is_deliberative_agent(agent_name):
            return None
        return ProposedSeat(
            focus=focus,
            brief=brief,
            agent_name=agent_name,
            capability=capability,
            model=_clean(getattr(raw, "model", ""), limit=120) or None,
        )

    positions: list[ProposedSeat] = []
    chair: ProposedSeat | None = None
    for raw in tuple(getattr(plan, "seats", ()) or ()):
        role = str(getattr(getattr(raw, "role", None), "value", getattr(raw, "role", "")))
        if role not in {"position", "chair"}:
            continue
        seat = _seat(raw, role=role)
        if seat is None:
            return None
        if role == "position":
            positions.append(seat)
        else:
            chair = seat
    if not positions or chair is None:
        return None
    return CouncilProposal(positions=tuple(positions), chair=chair)


def proposal_from_dict(payload: object) -> CouncilProposal | None:
    """The roster a person approved, restored from the card that showed it.

    Replaying the approved roster is what makes the preview honest: proposing
    again at dispatch means a second model call, and a second call can return a
    different council — so the seats someone approved and the seats that ran
    would differ, with nothing recording that they had.

    Re-validated on the way back rather than trusted. The card is server-owned,
    so this is not a trust boundary; it keeps one function deciding who may hold
    a seat instead of two that can drift. Anything unusable restores to
    ``None`` — half a council is worse than falling back to selection.
    """
    if not isinstance(payload, Mapping):
        return None

    def _seat(raw: object) -> ProposedSeat | None:
        if not isinstance(raw, Mapping):
            return None
        focus = _clean(raw.get("focus"), limit=MAX_FOCUS_CHARS)
        brief = _clean(raw.get("brief"), limit=MAX_BRIEF_CHARS)
        agent_name = _clean(raw.get("agent_name"), limit=120)
        if not focus or not brief or not agent_name or not is_deliberative_agent(agent_name):
            return None
        try:
            capability = Capability(_clean(raw.get("capability"), limit=120))
        except ValueError:
            return None
        return ProposedSeat(
            focus=focus,
            brief=brief,
            agent_name=agent_name,
            capability=capability,
            model=_clean(raw.get("model"), limit=120) or None,
        )

    chair = _seat(payload.get("chair"))
    raw_positions = payload.get("positions")
    positions = tuple(seat for seat in (_seat(raw) for raw in (raw_positions if isinstance(raw_positions, Sequence) and not isinstance(raw_positions, str) else ())) if seat is not None)
    if chair is None or not positions:
        return None
    return CouncilProposal(positions=positions, chair=chair)


_PROMPT_TEMPLATE = """\
You are assembling a Design council for a DBTL research cycle. Decide who should
sit on it for *this* question, then stop. You are not designing the study.

The request:
{request}

Project context:
{context}
{adjustment}
Propose up to {max_positions} independent positions. What makes this a council
rather than one opinion is that the seats **disagree**: give each a different
place to argue from, so that a reader can tell their positions apart before
reading a word of the reasoning. A seat that would say roughly what another seat
says should not be seated at all.

Rules that are checked, not suggested:
- `agent_name` must be exactly one of: {agents}
- `model` must be omitted, or exactly one of: {models}
- `capability` must be exactly one of: {capabilities}
A seat naming anything else is discarded, and it is not replaced.

Return one JSON object and nothing else:

{{
  "positions": [
    {{
      "focus": "a few words naming what this seat brings",
      "brief": "what this seat argues from, and what it should push hardest on",
      "agent_name": "one of the agents above",
      "model": "one of the models above, or omit to inherit",
      "capability": "one of the capabilities above"
    }}
  ],
  "chair": {{
    "focus": "...", "brief": "...", "agent_name": "...", "capability": "..."
  }}
}}

The chair is optional; omit it to use the default. A red team always sits and
you do not need to propose one.
"""


_ADJUSTMENT_TEMPLATE = """
The person reviewing this roster asked for a change, quoted exactly:
\"\"\"
{adjustment}
\"\"\"
Their words are the requirement. Change what they asked to change and leave the
rest of the roster alone — re-drawing the seats they accepted spends the round
re-arguing what was already settled.
"""


def build_proposal_prompt(
    *,
    request_text: str,
    stage_context: str,
    known_agents: Sequence[str],
    known_models: Sequence[str],
    max_positions: int,
    adjustment: str | None = None,
) -> str:
    """The one-shot prompt that asks for a roster.

    The allowed agents, models, and capabilities are listed explicitly. Parsing
    is fail-closed, and a fail-closed rule applied to a model that was never
    shown the options is a trap rather than a contract.

    An ``adjustment`` is the reviewer's own words, carried verbatim. Restating
    it in the prompt's vocabulary is exactly how "drop the reproducibility
    seat" becomes a roster that keeps it.
    """
    note = (adjustment or "").strip()
    return _PROMPT_TEMPLATE.format(
        request=(request_text or "").strip() or "(no request text)",
        context=(stage_context or "").strip() or "(none)",
        adjustment=_ADJUSTMENT_TEMPLATE.format(adjustment=note) if note else "",
        max_positions=max(1, min(int(max_positions), MAX_PROPOSED_POSITIONS)),
        agents=", ".join(seatable_agents(known_agents)) or "(none registered)",
        models=", ".join(known_models) or "(none configured)",
        capabilities=", ".join(item.value for item in Capability),
    )
