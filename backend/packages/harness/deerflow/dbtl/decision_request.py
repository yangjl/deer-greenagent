"""The structured decision a paused chair asks the project owner to make.

A chair that stops mid-meeting already records one ``clarification_question``,
and a person can answer it in prose. That is enough to resume the chair and not
enough to audit later: "use family-level holdout" and "family holdout, but only
if the sample size allows" are the same free-text field, so a reader a year on
cannot tell which option was chosen from which the owner invented. Structuring
the choice makes the answer a stable identifier the record can bind to, and it
is what lets the feedback deck render real option cards instead of a text box.

Two rules do most of the work here.

**The question is not this object's to state.** ``parse_decision_request`` takes
the authoritative ``clarification_question`` as an argument and overwrites
whatever the payload repeated beside it. The chair asked one question; two
copies of it that can drift are worse than one, and the copy a person answers
must be the copy the audit record keeps.

**A refusal costs the cards, never the question.** Parsing is strict — an
unknown option id, a single option, a recommendation naming nothing — but every
refusal returns a *reason* rather than raising, and the caller keeps the
recorded question and falls back to free text. Failing the whole chair result
over a misshapen sub-object would trade a meeting for a formatting error;
dropping it silently would leave a reviewer unable to tell a chair that offered
no options from one whose options were thrown away.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Card mode needs a real choice. One option is a recommendation, and more than
#: a handful stops being a decision and starts being a form.
MIN_OPTIONS = 2
MAX_OPTIONS = 5

MAX_IDENTIFIER_CHARS = 64
MAX_QUESTION_CHARS = 1_000
MAX_LABEL_CHARS = 120
MAX_VALUE_CHARS = 600
MAX_DESCRIPTION_CHARS = 600
MAX_RECOMMENDATION_CHARS = 600

#: Identifiers are answered back by an untrusted page and stored as the human's
#: choice, so they stay a conservative slug: safe in an HTML attribute, stable
#: across a re-render, and comparable without normalization.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _text(raw: object, limit: int) -> str:
    return str(raw or "").strip()[:limit]


@dataclass(frozen=True, slots=True)
class DecisionOption:
    """One answer the chair is offering."""

    id: str
    label: str
    #: What answering this option means, in the chair's own words. This is what
    #: the resumed chair is told; the label is only what the card shows.
    value: str
    description: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "value": self.value, "description": self.description}


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    """One focused decision, offered as a bounded set of options."""

    id: str
    question: str
    options: tuple[DecisionOption, ...] = field(default_factory=tuple)
    #: An option the chair recommends. Recorded as evidence and deliberately
    #: never preselected: a default that becomes the answer is a decision
    #: nobody made.
    recommended_option_id: str = ""
    recommendation: str = ""
    version: int = 1

    @property
    def renders_as_cards(self) -> bool:
        return MIN_OPTIONS <= len(self.options) <= MAX_OPTIONS

    def option(self, option_id: str) -> DecisionOption | None:
        for option in self.options:
            if option.id == option_id:
                return option
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "id": self.id,
            "question": self.question,
            "options": [option.as_dict() for option in self.options],
            "recommended_option_id": self.recommended_option_id,
            "recommendation": self.recommendation,
        }


@dataclass(frozen=True, slots=True)
class DecisionParse:
    """What a payload yielded, and why it did not.

    ``refusal`` is empty both when parsing succeeded and when there was nothing
    to parse; ``request`` distinguishes them. Callers that care about the
    difference — the audit record does, the renderer does not — read both.
    """

    request: DecisionRequest | None = None
    refusal: str = ""

    def __bool__(self) -> bool:
        return self.request is not None


def _parse_options(raw: object) -> tuple[tuple[DecisionOption, ...], str]:
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        return (), "'options' must be a list of option objects."

    options: list[DecisionOption] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            return (), "Each option must be an object with 'id', 'label', and 'value'."

        option_id = _text(entry.get("id"), MAX_IDENTIFIER_CHARS + 1)
        if not _IDENTIFIER_PATTERN.match(option_id) or len(option_id) > MAX_IDENTIFIER_CHARS:
            # Truncating an over-long id would silently merge two distinct
            # options into one answer.
            return (), f"Option identifier {entry.get('id')!r} is not a stable bounded identifier."
        if option_id in seen:
            return (), f"Option identifiers must be unique; {option_id!r} appears twice."
        seen.add(option_id)

        label = _text(entry.get("label"), MAX_LABEL_CHARS)
        if not label:
            return (), f"Option {option_id!r} needs a label."
        value = _text(entry.get("value"), MAX_VALUE_CHARS)
        if not value:
            return (), f"Option {option_id!r} needs a value saying what choosing it means."

        options.append(
            DecisionOption(
                id=option_id,
                label=label,
                value=value,
                description=_text(entry.get("description"), MAX_DESCRIPTION_CHARS),
            )
        )

    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        return (), f"A decision needs between {MIN_OPTIONS} and {MAX_OPTIONS} options; {len(options)} were offered."
    return tuple(options), ""


def parse_decision_request(raw: object, *, question: str) -> DecisionParse:
    """Read a chair's structured decision. Never raises.

    ``question`` is the recorded ``clarification_question`` and always wins: the
    payload may repeat it, but a copy that can drift from the audit record is
    not the question anyone answered.
    """
    recorded_question = _text(question, MAX_QUESTION_CHARS)
    if raw is None:
        return DecisionParse()
    if not isinstance(raw, Mapping):
        return DecisionParse(refusal="A decision request must be an object.")
    if not recorded_question:
        return DecisionParse(refusal="A decision request needs the chair's recorded question.")

    request_id = _text(raw.get("id"), MAX_IDENTIFIER_CHARS + 1)
    if not _IDENTIFIER_PATTERN.match(request_id) or len(request_id) > MAX_IDENTIFIER_CHARS:
        return DecisionParse(refusal=f"Decision identifier {raw.get('id')!r} is not a stable bounded identifier.")

    options, refusal = _parse_options(raw.get("options"))
    if refusal:
        return DecisionParse(refusal=refusal)

    recommended = _text(raw.get("recommended_option_id"), MAX_IDENTIFIER_CHARS)
    if recommended and all(option.id != recommended for option in options):
        return DecisionParse(refusal=f"The recommended option {recommended!r} is not one of the options offered.")

    return DecisionParse(
        request=DecisionRequest(
            id=request_id,
            question=recorded_question,
            options=options,
            recommended_option_id=recommended,
            recommendation=_text(raw.get("recommendation"), MAX_RECOMMENDATION_CHARS),
        )
    )


DECISION_REQUEST_CONTRACT = """\
When your status is "needs_input", you may add a "decision_request" object beside
"clarification_question" so the project owner is offered real choices rather than
a blank box. Add it only when the decision genuinely has a small set of distinct
answers; a question that needs a number, a name, or an explanation should stay
free text.

"decision_request": {
  "version": 1,
  "id": "short-stable-slug",
  "question": "repeat your clarification_question",
  "options": [
    {
      "id": "stable_slug",
      "label": "Short name for the card",
      "value": "What choosing this means, stated as you would tell the council",
      "description": "The tradeoff this option accepts"
    }
  ],
  "recommended_option_id": "stable_slug",
  "recommendation": "why you lean this way, and what it costs"
}

Between two and five options, each with a unique slug identifier. Recommend one
if you have a view — it is recorded as your reasoning, and it is never
preselected for the owner. Do not offer options you would refuse; every option
listed is one you can proceed with.
"""
