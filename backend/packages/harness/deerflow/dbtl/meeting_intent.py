"""Asking, in words, for a meeting to be held again.

This lived inside the stage adapter, where it answered one question: a request
has reached stage execution, should it convene the council or point at the
review sheet? Routing needs the same answer one step earlier — whether the
request should reach stage execution at all — and two regexes that agree today
drift apart the first time either is edited. So the predicate lives here, below
both, and each imports it.

It stays deterministic for the reason every DBTL routing signal does: convening
several workers is expensive and slow, and the person whose sentence was read
as "convene four workers" deserves to see the words that did it. What the
phrases miss, an injected fail-soft interpreter may still read as a re-run at
the adapter — but only after the request has been routed to the stage, which is
what this predicate decides.
"""

from __future__ import annotations

import re

#: One-slip misspellings of "restart", enumerated as literals rather than
#: matched fuzzily, so the rule stays auditable.
_RESTART_TYPOS = r"restat|restar|restrat|retsart|rstart|resart|retart"

#: Ways of asking for the meeting to be held again.
_NEW_DEBATE_PATTERN = re.compile(
    r"\b(?:re-?run|re-?open|re-?start|re-?try|re-?launch|re-?convene|re-?do|repeat|rehold|" + _RESTART_TYPOS + r")\b[^.\n]{0,40}\b(?:meeting|debate|discussion|council|round)\b"
    r"|\b(?:run|hold|convene|start|open|schedule)\b[^.\n]{0,40}\b(?:another|a new|a second|again)\b[^.\n]{0,20}\b(?:meeting|debate|discussion|council|round)\b"
    r"|\b(?:another|a second|a new|one more)\s+(?:round|meeting|debate|discussion)\b"
    r"|\b(?:meet|debate|discuss|argue)\s+(?:it\s+)?again\b"
    r"|\b(?:meeting|debate|discussion|council)\s+again\b",
    re.IGNORECASE,
)


def wants_new_debate(request_text: str) -> bool:
    """Whether the request asks for the meeting to be convened again.

    The default is *not* to convene. A Design stage stays ``in_progress`` until
    a person submits it for review, so without this rule every later message in
    the cycle re-ran the whole meeting — the owner would answer one question
    and watch four fresh workers argue the design they had just been handed.
    """
    return bool(_NEW_DEBATE_PATTERN.search((request_text or "").strip()))
