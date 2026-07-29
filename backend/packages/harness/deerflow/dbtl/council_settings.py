"""Per-participant settings a person edits on the design-meeting preflight card.

The preflight card shows who will sit in the design meeting. Before this
module, the roster was take-it-or-redraw-it: a person could pick a depth or
send the whole roster back to the writer, but could not say "this seat, on
that model, with deeper reasoning, and push hardest on X". Those three dials —
model, reasoning strength, and owner instructions — are exactly
what a lab head adjusts when assigning people to a group meeting, so they are
now editable per participant, prefilled with what the roster writer proposed.

Three rules, all inherited from the depth-recovery work:

**The card's prefills are the proposal.** ``participants_payload`` is computed
from the same :class:`~deerflow.dbtl.council.CouncilPlan` the preflight shows
and dispatch runs, so the editable values a person sees are the values that
would run if they touched nothing.

**Edits are validated server-side, field by field.** ``parse_participant_settings``
is fail-soft per field rather than per payload: an unknown model is dropped
while the same participant's instructions still apply. A stale client losing
one edit is a smaller failure than a reply that silently discards them all.

**Instructions are carried verbatim.** An owner's note to a seat is quoted
into that worker's prompt exactly as typed — the same rule the roster
adjustment and the reviewer's change request follow, and for the same reason:
a paraphrase is how "compare against last season's controls" becomes a prompt
that does not.

Nothing here dispatches anything; the settings are values.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from deerflow.dbtl.council import CouncilPlan, CouncilSeat

REASONING_STANDARD = "standard"
REASONING_EXTENDED = "extended"
#: The two strengths a participant can run at. ``extended`` maps to the model
#: factory's extended-thinking mode; anything else runs the model as-is.
REASONING_LEVELS: tuple[str, ...] = (REASONING_STANDARD, REASONING_EXTENDED)

#: Legacy bounds retained for old preflight replies. Current council policies
#: meter tokens without enforcing them, so these values are recorded but
#: cannot stop a participant while ``token_limit_enforced`` is false.
MIN_PARTICIPANT_TOKENS = 10_000
MAX_PARTICIPANT_TOKENS = 2_000_000

MAX_INSTRUCTION_CHARS = 2_000
_MAX_PARTICIPANTS_READ = 40

#: A participant id is the seat id with the attempt prefix removed, so the
#: same edits apply whether the roster was previewed (``preview-position-1``)
#: or dispatched (``dbtl-<digest>-position-1``).
_PARTICIPANT_ID = re.compile(r"^(position-\d{1,2}|red-team|chair)$")


@dataclass(frozen=True, slots=True)
class ParticipantSettings:
    """One participant's edited dials. Unset fields keep the proposal's value."""

    participant_id: str
    model: str | None = None
    max_tokens: int | None = None
    reasoning: str | None = None
    instructions: str = ""

    @property
    def is_empty(self) -> bool:
        return self.model is None and self.max_tokens is None and self.reasoning is None and not self.instructions

    def as_dict(self) -> dict[str, object]:
        return {
            "participant_id": self.participant_id,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "reasoning": self.reasoning,
            "instructions": self.instructions,
        }


def participant_id_for_seat(seat_id: str) -> str:
    """The stable id a seat is edited under, independent of the attempt prefix.

    Preview and dispatch mint different attempt ids, so raw seat ids never
    match across the two. The role-shaped tail is what both share.
    """
    for suffix in ("red-team", "chair"):
        if seat_id == suffix or seat_id.endswith(f"-{suffix}"):
            return suffix
    marker = "position-"
    index = seat_id.rfind(marker)
    if index != -1:
        tail = seat_id[index + len(marker) :]
        if tail.isdigit():
            return f"{marker}{tail}"
    return seat_id


def _clamped_tokens(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        tokens = int(value)
    except (TypeError, ValueError):
        return None
    return max(MIN_PARTICIPANT_TOKENS, min(tokens, MAX_PARTICIPANT_TOKENS))


def parse_participant_settings(
    payload: object,
    *,
    known_models: Sequence[str],
) -> dict[str, ParticipantSettings]:
    """Validate the edits a reply carried. Never raises; drops field by field.

    A model nobody configured, a non-numeric budget, or an unknown reasoning
    level is dropped while the participant's other edits survive — refusing
    the whole payload for one stale field would discard the instructions the
    person typed, which are the part that cannot be reconstructed.
    """
    if not isinstance(payload, Mapping):
        return {}
    known = set(known_models)
    settings: dict[str, ParticipantSettings] = {}
    for raw_id, raw in list(payload.items())[:_MAX_PARTICIPANTS_READ]:
        participant_id = str(raw_id or "").strip()
        if not _PARTICIPANT_ID.match(participant_id) or not isinstance(raw, Mapping):
            continue
        raw_model = raw.get("model")
        model = raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() in known else None
        raw_reasoning = raw.get("reasoning")
        reasoning = raw_reasoning.strip().lower() if isinstance(raw_reasoning, str) and raw_reasoning.strip().lower() in REASONING_LEVELS else None
        parsed = ParticipantSettings(
            participant_id=participant_id,
            model=model,
            max_tokens=_clamped_tokens(raw.get("max_tokens")),
            reasoning=reasoning,
            instructions=str(raw.get("instructions") or "").strip()[:MAX_INSTRUCTION_CHARS],
        )
        if not parsed.is_empty:
            settings[participant_id] = parsed
    return settings


def _seat_with_settings(seat: CouncilSeat, override: ParticipantSettings | None) -> CouncilSeat:
    if override is None:
        return seat
    # Instructions prefill with the seat's brief on the card, so an untouched
    # textarea comes back byte-identical to the suggestion. Recording that as
    # an owner instruction would put the writer's words behind the owner's
    # name in the prompt and the review package.
    instructions = override.instructions
    if instructions.strip().casefold() == seat.brief.strip().casefold():
        instructions = ""
    return replace(
        seat,
        model=override.model or seat.model,
        max_tokens=override.max_tokens if override.max_tokens is not None else seat.max_tokens,
        reasoning=override.reasoning or seat.reasoning,
        instructions=instructions or seat.instructions,
    )


def apply_participant_settings(
    plan: CouncilPlan,
    settings: Mapping[str, ParticipantSettings] | None,
) -> CouncilPlan:
    """The same roster, carrying the owner's edits.

    Applied to the plan rather than only to the dispatched work units, because
    the plan is what the review package records: a package describing the
    proposal's budgets while the workers ran on the owner's would misreport
    what actually happened.
    """
    if not settings:
        return plan
    return replace(
        plan,
        seats=tuple(_seat_with_settings(seat, settings.get(participant_id_for_seat(seat.seat_id))) for seat in plan.seats),
    )


def participants_payload(
    plan: CouncilPlan,
    *,
    model_options: Sequence[str] = (),
) -> list[dict[str, object]]:
    """The editable participant cards, prefilled with the proposal.

    One entry per seat, in meeting order. ``instructions`` prefills with the
    seat's brief — the roster writer's suggestion — so the person edits a
    concrete proposal rather than composing from nothing.
    """
    return [
        {
            "id": participant_id_for_seat(seat.seat_id),
            "role": seat.role.value,
            "role_label": seat.role_label,
            "agent_name": seat.agent_name,
            "via_generalist": seat.via_generalist,
            "focus": seat.focus,
            "model": seat.model,
            "model_options": list(dict.fromkeys(model_options)),
            "max_tokens": seat.max_tokens or plan.budget.max_tokens,
            "max_tokens_min": MIN_PARTICIPANT_TOKENS,
            "max_tokens_max": MAX_PARTICIPANT_TOKENS,
            "token_limit_enforced": plan.budget.token_limit_enforced,
            "reasoning": seat.reasoning or REASONING_STANDARD,
            "reasoning_options": list(REASONING_LEVELS),
            "instructions": seat.instructions or seat.brief,
        }
        for seat in plan.seats
    ]


def owner_instruction_lines(instructions: str) -> list[str]:
    """The prompt lines that quote an owner's note to one participant.

    Verbatim, in quotes, attributed — the same shape the reviewer's change
    request uses. The note is the owner's words, and the seat must be able to
    tell them apart from the framework's own briefing.
    """
    note = (instructions or "").strip()
    if not note:
        return []
    return [
        "",
        "The project owner's instructions for you in this meeting, quoted exactly:",
        f'    """{note}"""',
        "Follow them; where they conflict with the general briefing above, the owner's instructions win.",
    ]
