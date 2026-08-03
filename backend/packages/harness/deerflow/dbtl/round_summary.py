"""One sentence saying what the meeting actually decided.

The reply that follows a Design round used to be three fixed sentences: the
cycle id, "here is where the meeting got to", and a line about a "server-owned
human review record". None of them said anything about *this* meeting — a
reader learned the same thing whether the council converged or split four ways,
and the one instruction they were given named an internal record rather than
the deck sitting in front of them.

So the sentence is written from the recorded chair result, and two rules keep
that safe:

1. **It reports, it does not decide.** The summary may say what was agreed and
   what is still open; it may not say the design is approved, sound, or ready,
   because approval is a human record and a sentence in chat is not it.
   :func:`parse_round_summary` refuses a verdict claim rather than trimming it,
   since a trimmed sentence still reads as the model's view of the outcome.
2. **Nothing here fails loudly.** A missing model, an outage, or an unusable
   reply degrades to :func:`fallback_summary`, which states the counts the
   chair recorded and nothing else. A worse sentence is a fair price; losing
   the reply to a meeting that already ran is not.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from deerflow.dbtl.consensus import Consensus

logger = logging.getLogger(__name__)

#: Long enough for a real finding, short enough to read at a glance. A summary
#: that runs on is the wall of text this replaced.
MAX_SUMMARY_CHARS = 320

#: Claims a chat reply may not make. The gate is a durable human record; a
#: sentence that says the design is approved or sound is describing a decision
#: nobody took, and a reader who believes it stops looking for the control.
_VERDICT_CLAIMS = re.compile(
    r"\b(?:approved|approve[sd]?\s+the\s+design|rejected|ready\s+for\s+build|"
    r"passes?\s+the\s+gate|gate\s+is\s+(?:passed|satisfied)|sign(?:ed)?[-\s]?off|"
    r"gre?en[-\s]?lit|cleared\s+for)\b",
    re.IGNORECASE,
)


def build_round_summary_prompt(
    *,
    research_question: str,
    chair_summary: str,
    consensus: Consensus | None,
    clarification_question: str = "",
) -> str:
    """The instruction that produces the reply's opening sentence."""
    agreements = list(consensus.agreements) if consensus is not None else []
    open_topics = [item.topic for item in consensus.disagreements if not item.resolved] if consensus is not None else []

    def _block(label: str, values: Sequence[str]) -> str:
        if not values:
            return f"{label}: (none recorded)"
        lines = "\n".join(f"- {str(value).strip()[:300]}" for value in values[:6])
        return f"{label}:\n{lines}"

    pending = f'\n\nThe chair paused and asked: "{clarification_question.strip()[:400]}"' if clarification_question.strip() else ""
    return f"""A design meeting just finished a round on this research question:

\"\"\"{research_question.strip()[:600] or "(not recorded)"}\"\"\"

The chair's own synthesis:
\"\"\"{chair_summary.strip()[:2000] or "(none recorded)"}\"\"\"

{_block("What the participants agreed", agreements)}

{_block("What is still contested", open_topics)}{pending}

Write ONE sentence, for the scientist who owns this project, saying what this
round actually established and what is still open. Rules:
- Be specific to this meeting. A sentence that would fit any meeting is useless.
- Report only. Do NOT say the design is approved, sound, ready, or cleared —
  that is a decision the owner has not made yet.
- Do not mention slide decks, review records, gates, or what to do next; the
  reply says that separately.
- Plain language, under {MAX_SUMMARY_CHARS} characters, no preamble, no quotes.

Reply with the sentence and nothing else."""


def fallback_summary(consensus: Consensus | None, *, clarification_question: str = "") -> str:
    """What the chair recorded, counted — used when no model wrote a sentence.

    Deliberately states quantities rather than substance: this path runs when
    nothing readable came back, and a fallback that characterised the meeting
    would be inventing the summary it is standing in for.
    """
    if consensus is None:
        if clarification_question.strip():
            return "The meeting finished this round and the chair has a question for you before it can go further."
        return "The meeting finished this round; the chair recorded no structured consensus, so the written synthesis is the record."
    agreed = len(consensus.agreements)
    open_count = sum(1 for item in consensus.disagreements if not item.resolved)
    settled = sum(1 for item in consensus.disagreements if item.resolved)
    parts = [f"{agreed} point{'' if agreed == 1 else 's'} agreed"]
    if settled:
        parts.append(f"{settled} settled in discussion")
    if open_count:
        parts.append(f"{open_count} still contested")
    tail = " and the chair has a question for you." if clarification_question.strip() else "."
    return "The meeting recorded " + ", ".join(parts) + tail


def parse_round_summary(
    raw: str,
    *,
    consensus: Consensus | None,
    clarification_question: str = "",
) -> str:
    """Clean a model sentence, or fall back rather than pass on a bad one."""
    text = " ".join(str(raw or "").split()).strip().strip('"').strip()
    if not text:
        return fallback_summary(consensus, clarification_question=clarification_question)
    if _VERDICT_CLAIMS.search(text):
        # Refused whole rather than edited: a sentence with the verdict cut out
        # still carries the judgement that produced it.
        logger.debug("DBTL round summary: reply claimed a verdict; using the recorded counts instead")
        return fallback_summary(consensus, clarification_question=clarification_question)
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"
    return text
