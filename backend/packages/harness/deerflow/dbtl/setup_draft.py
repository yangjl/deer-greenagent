"""Draft a DBTL cycle's setup fields from the request the scientist typed.

The setup form used to open blank, which made "start a cycle" a data-entry
chore. The model can propose most of it from context — but a drafted scientific
record carries a specific risk: a confirmation click turns the draft into a
durable research record, so a value the model *invented* must never be
presentable as a value the scientist *stated*.

That is the whole reason this module exists separately from the LLM call. Two
rules are enforced here rather than left to the prompt:

1. Every drafted value carries a `grounded` flag, and anything not explicitly
   grounded is reported as an **assumption** (see {@link SetupDraft.assumed}).
   The unsafe default — treating a silent model as authoritative — is not
   reachable.
2. Nothing here can fail loudly. A malformed or missing reply degrades to
   {@link empty_draft}, which is exactly the blank form the human had before, so
   a model outage can never block cycle setup.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Cap on one drafted value. Generous for a phrase, short enough that a runaway
#: generation cannot push an unreviewable wall of text into a form field.
SETUP_DRAFT_MAX_VALUE_CHARS = 240
SETUP_DRAFT_MAX_TITLE_CHARS = 120

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SetupDraft:
    """A proposed setup form, with its provenance."""

    title: str = ""
    fields: Mapping[str, str] = field(default_factory=dict)
    #: Fields whose drafted value the request did not support. These are the
    #: model's suggestions, and the UI must present them as such.
    assumed: frozenset[str] = frozenset()


def empty_draft() -> SetupDraft:
    """The blank form. Every failure path resolves here."""
    return SetupDraft(title="", fields={}, assumed=frozenset())


def build_draft_prompt(
    *,
    request_text: str,
    project_name: str,
    fields: Iterable[str],
) -> str:
    """The drafting instruction.

    Only the proposal's missing fields are asked for, so the model cannot
    volunteer values for parts of the record already settled.
    """
    wanted = [f for f in fields if f]
    field_lines = "\n".join(f"- {name}" for name in wanted)
    return f"""You are helping a plant scientist open a DBTL research cycle in the project "{project_name}".

Their request, verbatim:
\"\"\"{request_text}\"\"\"

Draft a short cycle title and a proposed value for each field below:
{field_lines}

Rules:
- Do not invent dataset specifics. Never state counts, sample sizes, environment
  numbers, years, locations, or accession names unless the request states them.
- For each field, set "grounded" to true ONLY if the request itself supports the
  value. If you are proposing a reasonable default the scientist did not state,
  set "grounded" to false. A wrong "grounded" flag is worse than a blank field,
  because this form becomes a durable research record.
- Prefer a defensible generic proposal over a specific-sounding guess.
- Keep each value under {SETUP_DRAFT_MAX_VALUE_CHARS} characters, phrased as a
  form answer rather than a sentence.
- If you cannot propose anything defensible for a field, use an empty value.

Reply with JSON only, in exactly this shape:
{{"title": "...", "fields": {{"<field name>": {{"value": "...", "grounded": true}}}}}}"""


def _strip_fence(raw: str) -> str:
    text = (raw or "").strip()
    if "```" not in text:
        return text
    # Only the outer fence is removed; inner braces are left to the JSON parser.
    return _FENCE.sub("", text).strip()


def _clean(value: object, cap: int) -> str:
    if not isinstance(value, str):
        return ""
    collapsed = " ".join(value.split())
    return collapsed[:cap].strip()


def parse_draft_response(raw: str, *, fields: Iterable[str]) -> SetupDraft:
    """Parse a model reply into a draft, degrading to a blank form."""
    wanted = {name for name in fields if name}

    try:
        payload = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        logger.debug("DBTL setup draft: reply was not JSON; using a blank form")
        return empty_draft()

    if not isinstance(payload, dict):
        return empty_draft()

    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, dict):
        # A title without fields is not worth half-populating a form with.
        return empty_draft()

    values: dict[str, str] = {}
    assumed: set[str] = set()

    for name, entry in raw_fields.items():
        if name not in wanted:
            # Silently drop: the model does not get to widen the record's shape.
            continue

        if isinstance(entry, str):
            # A bare string carries no provenance, so it cannot claim grounding.
            value, grounded = entry, False
        elif isinstance(entry, dict):
            value = entry.get("value")
            # Anything other than an explicit true is an assumption. A missing
            # flag must not read as "the scientist said this".
            grounded = entry.get("grounded") is True
        else:
            continue

        cleaned = _clean(value, SETUP_DRAFT_MAX_VALUE_CHARS)
        if not cleaned:
            continue

        values[name] = cleaned
        if not grounded:
            assumed.add(name)

    return SetupDraft(
        title=_clean(payload.get("title"), SETUP_DRAFT_MAX_TITLE_CHARS),
        fields=values,
        assumed=frozenset(assumed),
    )
