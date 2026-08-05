"""Let the model write the setup questions, and bound what it may show.

The setup card used to list the classifier's unmatched regex fields — "target
trait", "season range" — as bullets. Those are rule names, not questions, and
they are the same four in every conversation regardless of what was asked. A
person reading them has to work out what is actually wanted and then type it
out in full.

So the model writes the questions instead, and answers each one with its own
recommendation, leaving the human to accept or correct rather than compose.
That trade is only safe with two rules enforced here rather than in the prompt,
for the same reason every DBTL model-authored setup value is revalidated:

1. **A recommendation carries provenance.** ``grounded`` is true only when the
   request itself supports the value; anything else renders as a suggestion.
   The unsafe default — a silent model read as authoritative — is unreachable,
   because only an explicit ``true`` counts.
2. **Nothing here fails loudly.** A malformed, empty, or missing reply degrades
   to :func:`fallback_questions`, which asks the deterministic gaps as plain
   questions. A model outage costs a nicer card, never the ability to set up a
   cycle.

The deterministic ``missing_fields`` are passed to the model as a hint rather
than a script: the rules noticed something real, but they are not entitled to
dictate the wording, and a request may deserve a question no rule can express.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Enough to pin a design, few enough to answer in one sitting. A card that
#: asks twelve questions gets abandoned, which is worse than asking four.
MAX_SETUP_QUESTIONS = 5

#: Choices per question. Beyond a handful the reader is doing research to
#: answer a question that exists to save them research, and "Other" — which the
#: card always adds — covers the tail anyway.
MAX_OPTIONS_PER_QUESTION = 4

MAX_OPTION_LABEL_CHARS = 80
MAX_OPTION_DESCRIPTION_CHARS = 160

#: Caps on model-supplied text. Generous for a sentence, short enough that a
#: runaway generation cannot push an unreviewable wall of text onto the card.
MAX_QUESTION_CHARS = 200
MAX_RECOMMENDATION_CHARS = 240
MAX_WHY_CHARS = 160

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

ACCEPT_HINT = "Reply “accept” to take every suggestion, or answer only the ones you want to change."


@dataclass(frozen=True, slots=True)
class SetupOption:
    """One selectable answer, with the line that makes it choosable.

    ``description`` is what turns a menu into a decision: "DESeq2" alone asks
    the reader to already know; "most widely used, robust for most designs"
    lets them choose without leaving the card.
    """

    id: str
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class SetupQuestion:
    """One question, and the answers worth offering for it.

    A question may carry ``options`` (answered by picking) or a free-text
    ``recommendation`` (answered by editing). Both are proposals; neither is
    the scientist's word until they say so, which is what ``grounded`` and the
    card's own "Other" field are for.
    """

    id: str
    question: str
    why: str = ""
    options: tuple[SetupOption, ...] = ()
    #: Which option the model would pick. Empty when it declined to.
    recommended_option_id: str = ""
    recommendation: str = ""
    #: Whether the request itself supports the proposal. False means the model
    #: proposed it, and the card must say so.
    grounded: bool = False


def build_questions_prompt(
    *,
    request_text: str,
    project_name: str,
    missing_fields: Iterable[str],
) -> str:
    """The instruction that produces the card's questions."""
    gaps = [name for name in missing_fields if name]
    gap_lines = "\n".join(f"- {name}" for name in gaps) or "- (the rules found no specific gap)"
    return f"""A scientist in the project "{project_name}" has just approved starting a DBTL research cycle (Design → Build → Test → Learn) for this request:

\"\"\"{request_text}\"\"\"

Write the questions the Design stage needs answered before work can start, and offer the answers worth choosing between.

A deterministic rule pass flagged these as unstated. Treat it as a hint, not a script — reword them, merge them, drop one that does not apply to this request, or ask something better:
{gap_lines}

Rules:
- Ask at most {MAX_SETUP_QUESTIONS} questions, fewest that genuinely pin the design. Each must be a real question a person can answer in one step.
- Ask only what this request actually needs. A software or data task has no "target trait"; a breeding experiment does.
- Give each question 2 to {MAX_OPTIONS_PER_QUESTION} concrete "options". Every option needs a short "label" and a
  one-line "description" saying when it is the right choice, so the reader can decide without looking anything up.
- Mark exactly one option "recommended": true — your pick. Do not mark more than one.
- Do NOT add an "other" or "not sure" option; the card always offers one.
- If a question genuinely has no discrete choices (a number, a name, a free description), omit "options" and give a "recommendation" string instead.
- Set "grounded" to true ONLY if the request itself supports your pick. If you are proposing a sensible default they did not state, set it to false.
- A wrong "grounded" flag is worse than no recommendation, because these answers become a durable research record.
- Never invent dataset specifics — counts, sample sizes, years, locations, accession names — unless the request states them. Prefer a defensible generic option over a specific-sounding guess.
- Keep each question under {MAX_QUESTION_CHARS} characters, each label under {MAX_OPTION_LABEL_CHARS}, each description under {MAX_OPTION_DESCRIPTION_CHARS}.
- "why" is one short clause on what the answer decides. Omit it if it adds nothing.

Reply with JSON only, in exactly this shape:
{{"questions": [{{"id": "short-slug", "question": "...", "why": "...", "options": [{{"id": "slug", "label": "...", "description": "...", "recommended": true}}], "grounded": false}}]}}"""


def _strip_fence(raw: str) -> str:
    text = (raw or "").strip()
    if "```" not in text:
        return text
    return _FENCE.sub("", text).strip()


def _clean(value: object, cap: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:cap].strip()


def fallback_questions(missing_fields: Iterable[str]) -> tuple[SetupQuestion, ...]:
    """The deterministic gaps, asked as questions and answered by nobody.

    Deliberately carries no recommendation: this path runs when the model is
    unavailable or unusable, and a fallback that guessed would be inventing a
    research record with nothing standing behind it.
    """
    return tuple(
        SetupQuestion(
            id=name.replace(" ", "_"),
            question=f"What is the {name} for this cycle?",
            grounded=False,
        )
        for name in missing_fields
        if name
    )


def _parse_options(raw: object) -> tuple[tuple[SetupOption, ...], str]:
    """Options and the single recommended id, dropping anything unusable.

    A malformed option is skipped rather than rendered blank, and only the
    first ``recommended`` flag counts — two recommendations is no
    recommendation, and picking one arbitrarily at least keeps the card
    honest about there being a default.
    """
    if not isinstance(raw, list):
        return ((), "")

    options: list[SetupOption] = []
    recommended = ""
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        label = _clean(entry.get("label"), MAX_OPTION_LABEL_CHARS)
        if not label:
            continue
        option_id = _clean(entry.get("id"), 40) or f"o{index + 1}"
        options.append(
            SetupOption(
                id=option_id,
                label=label,
                description=_clean(entry.get("description"), MAX_OPTION_DESCRIPTION_CHARS),
            )
        )
        if not recommended and entry.get("recommended") is True:
            recommended = option_id
        if len(options) == MAX_OPTIONS_PER_QUESTION:
            break

    if recommended not in {option.id for option in options}:
        recommended = ""
    return (tuple(options), recommended)


def parse_questions_response(raw: str, *, missing_fields: Iterable[str]) -> tuple[SetupQuestion, ...]:
    """Parse a model reply, degrading to :func:`fallback_questions`."""
    gaps = tuple(missing_fields)

    try:
        payload = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        logger.debug("DBTL setup questions: reply was not JSON; asking the deterministic gaps")
        return fallback_questions(gaps)

    if not isinstance(payload, dict):
        return fallback_questions(gaps)

    entries = payload.get("questions")
    if not isinstance(entries, list) or not entries:
        return fallback_questions(gaps)

    questions: list[SetupQuestion] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        text = _clean(entry.get("question"), MAX_QUESTION_CHARS)
        if not text:
            # A blank question cannot be answered, and an empty row on the card
            # reads as a bug rather than as a question nobody wrote.
            continue
        options, recommended_option_id = _parse_options(entry.get("options"))
        questions.append(
            SetupQuestion(
                id=_clean(entry.get("id"), 40) or f"q{index + 1}",
                question=text,
                why=_clean(entry.get("why"), MAX_WHY_CHARS),
                options=options,
                recommended_option_id=recommended_option_id,
                recommendation=_clean(entry.get("recommendation"), MAX_RECOMMENDATION_CHARS),
                # Anything other than an explicit true is an assumption.
                grounded=entry.get("grounded") is True,
            )
        )
        if len(questions) == MAX_SETUP_QUESTIONS:
            break

    if not questions:
        return fallback_questions(gaps)
    return tuple(questions)


def render_questions(questions: Sequence[SetupQuestion]) -> str:
    """The card body: each question, its proposed answer, and where it came from."""
    if not questions:
        return ""

    lines: list[str] = []
    for index, item in enumerate(questions, start=1):
        lines.append(f"{index}. {item.question}")
        if item.why:
            lines.append(f"   {item.why}")
        # The label is the provenance. "Suggested" and "from your request" are
        # the difference between a proposal and a statement, and the scientist
        # is the one who has to tell them apart at a glance.
        origin = "from your request" if item.grounded else "suggested"
        for option in item.options:
            marker = "→" if option.id == item.recommended_option_id else "-"
            suffix = f" ({origin})" if option.id == item.recommended_option_id else ""
            detail = f" — {option.description}" if option.description else ""
            lines.append(f"   {marker} {option.label}{detail}{suffix}")
        if item.recommendation and not item.options:
            lines.append(f"   → {item.recommendation} ({origin})")
        lines.append("")

    if any(item.recommendation or item.options for item in questions):
        lines.append(ACCEPT_HINT)
    return "\n".join(lines).strip()
