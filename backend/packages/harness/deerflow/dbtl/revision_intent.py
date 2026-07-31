"""What a reviewer's "request changes" actually asks the meeting to do.

Requesting changes used to reconvene the whole meeting: a fresh roster, a red
team, and a chair, dispatched the moment the reviewer clicked. Most objections
do not need that. "Use 4,000 individuals rather than 1,000" is a correction the
chair can fold into the synthesis it already wrote, over positions that are
already recorded; re-arguing the design from scratch spends a second meeting's
budget re-litigating the parts the reviewer accepted, and does it silently.

So the objection is read first, and the reading chooses between two routes:

- ``chair_only`` — the chair revises its synthesis over the recorded positions.
  Cheap, and the common case.
- ``reconvene`` — the objection attacks the argument rather than the write-up
  ("nobody considered population structure", "get a statistician's view"), so
  new positions genuinely have to be heard.

Two rules make this safe to run on a model's opinion. **The cheap route is the
default**: an absent interpreter, a provider outage, or a reply this module
cannot parse all yield ``chair_only``, because spending a meeting nobody asked
for is the exact failure this exists to fix, and a chair-only round that turns
out to be too narrow costs one more click. And **the reviewer's words are never
paraphrased** — the verdict decides who runs, while the objection itself is
carried verbatim into whichever prompt runs, the same rule the refinement round
already followed.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

#: How much of the reviewer's objection and each recorded position is shown to
#: the reader. Generous enough to judge the objection, bounded so one enormous
#: comment cannot become an unbounded prompt.
MAX_OBJECTION_CHARS = 4_000
MAX_POSITION_CHARS = 600
MAX_POSITIONS_SHOWN = 6
MAX_REASON_CHARS = 400
MAX_ROSTER_NOTE_CHARS = 600

RevisionInterpreter = Callable[[str], Awaitable[str]]


class RevisionRoute(StrEnum):
    """Who has to run to answer this objection."""

    CHAIR_ONLY = "chair_only"
    RECONVENE = "reconvene"


@dataclass(frozen=True, slots=True)
class RevisionVerdict:
    """The route, why it was chosen, and what a new roster should focus on."""

    route: RevisionRoute
    reason: str = ""
    #: Only meaningful for ``reconvene``: what the reviewer's objection says the
    #: next roster should argue about. Fed to the roster writer as an
    #: adjustment note, never as a replacement for the objection itself.
    roster_note: str = ""
    #: True when this verdict came from a reading rather than from the
    #: fail-soft default, so a caller can say which it was.
    interpreted: bool = False

    @property
    def reconvenes(self) -> bool:
        return self.route is RevisionRoute.RECONVENE

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "reason": self.reason,
            "roster_note": self.roster_note,
            "interpreted": self.interpreted,
        }


#: The default, used whenever nothing could be read. Stated once so every
#: failure path returns the same value rather than three similar ones.
def _default_verdict(reason: str) -> RevisionVerdict:
    return RevisionVerdict(route=RevisionRoute.CHAIR_ONLY, reason=reason, interpreted=False)


REVISION_INTENT_INSTRUCTION = """You decide how a research design meeting should respond to a reviewer who asked for changes.

The meeting already produced a written design. Independent participants argued
positions, a red team attacked them, and a chair synthesized the result. A human
reviewer has now read that design and asked for changes.

Choose exactly one route:

- "chair_only": the objection can be answered by the chair revising its
  synthesis over the positions already argued. Choose this for corrections,
  clarifications, parameter or threshold changes, scope trims, missing detail,
  or anything the recorded positions already contain enough material to settle.
- "reconvene": the objection attacks the argument itself, not the write-up.
  Choose this only when answering it needs a viewpoint nobody argued — an
  unconsidered confound, a missing discipline, a rejected premise, or a request
  to hear a different kind of expert.

Prefer "chair_only" when the objection could plausibly be settled either way.
Reconvening spends a full meeting's budget, so it must be justified by the
objection needing an argument that was never made.

Reply with JSON only:
{"route": "chair_only" | "reconvene", "reason": "<one sentence>", "roster_note": "<what new participants should argue about, only when reconvening>"}
"""


def _text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _json_object(raw: str) -> Mapping[str, Any] | None:
    """The first JSON object in a reply, tolerating fences and commentary."""
    text = (raw or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def parse_revision_verdict(raw: str) -> RevisionVerdict:
    """Read a verdict, or fall back to the cheap route with a stated reason.

    Deliberately total: every malformed shape returns ``chair_only`` rather than
    raising, because this runs between a reviewer's click and the round that
    answers it, and an exception there would cost them the revision entirely.
    """
    payload = _json_object(raw)
    if payload is None:
        return _default_verdict("The revision reader returned no readable verdict, so the chair revises its own synthesis.")

    route_text = str(payload.get("route") or "").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        route = RevisionRoute(route_text)
    except ValueError:
        return _default_verdict("The revision reader named no known route, so the chair revises its own synthesis.")

    reason = _text(payload.get("reason"), limit=MAX_REASON_CHARS)
    roster_note = _text(payload.get("roster_note"), limit=MAX_ROSTER_NOTE_CHARS) if route is RevisionRoute.RECONVENE else ""
    return RevisionVerdict(route=route, reason=reason, roster_note=roster_note, interpreted=True)


def build_revision_prompt(objection: str, *, positions: tuple[str, ...] = ()) -> str:
    """The reviewer's words verbatim, plus what the meeting already argued.

    The positions are summaries rather than full reports: the reader is judging
    whether the material to answer this objection already exists, which their
    subjects answer, and pasting four full worker payloads would crowd the
    objection itself out of the reading.
    """
    lines = [
        "A human reviewer read the design this meeting produced and asked for changes.",
        "",
        "The reviewer's objection, verbatim (it may contain typos):",
        "",
        _text(objection, limit=MAX_OBJECTION_CHARS),
    ]
    shown = [item for item in (_text(text, limit=MAX_POSITION_CHARS) for text in positions[:MAX_POSITIONS_SHOWN]) if item]
    if shown:
        lines += ["", "The positions this meeting already argued:", ""]
        lines += [f"- {item}" for item in shown]
    else:
        lines += ["", "No prior positions were recorded for this meeting."]
    return "\n".join(lines)


async def interpret_revision(
    objection: str,
    *,
    positions: tuple[str, ...] = (),
    interpreter: RevisionInterpreter | None,
) -> RevisionVerdict:
    """Read the objection, or take the cheap route.

    Fail-soft on every axis — no interpreter, empty objection, provider error,
    unreadable reply — because the reviewer has already clicked and is owed a
    round. The failure is visible in the returned ``reason`` rather than
    silent, so the chat message can say the reading did not happen.
    """
    text = (objection or "").strip()
    if not text:
        return _default_verdict("No written objection was recorded, so the chair revises its own synthesis.")
    if interpreter is None:
        return _default_verdict("No revision reader is configured, so the chair revises its own synthesis.")
    try:
        raw = await interpreter(build_revision_prompt(text, positions=positions))
    except Exception:  # noqa: BLE001 - a reading must never cost the revision
        logger.warning("The revision reader failed; the chair will revise its own synthesis.", exc_info=True)
        return _default_verdict("The revision reader could not be reached, so the chair revises its own synthesis.")
    return parse_revision_verdict(raw)
