"""Where the council converged, where it did not, and what is still open.

The chair's synthesis already existed, as prose in a `summary`. The problem with
prose is that a reviewer cannot check the one rule the chair is given: *do not
average incompatible positions, explain the tradeoff*. A genuine convergence and
a disagreement smoothed away read exactly the same in a paragraph, and the
reader has no way to tell which they are looking at.

Structuring it makes both visible. A disagreement keeps **both** positions and
how it was settled — or that it was not. The entries the chair could not resolve
are the most useful part of the document, so they are kept rather than tidied
away; a synthesis that reads as complete while the contested point has quietly
vanished is worse than one that admits the gap.

:attr:`Consensus.unanimous` exists for the opposite failure. Three independent
positions and a red team that recorded no disagreement at all is possible, and
it is also the shape a rubber-stamped debate leaves behind. The flag does not
claim the synthesis is wrong — it says nobody wrote down an argument, and puts
that in front of the person deciding.

Pure values. Nothing here calls a model, and an unusable payload yields ``None``
rather than an empty scaffold: rendering "Agreements / Disagreements" with
nothing under either implies the chair considered the question and found none.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

MAX_CONSENSUS_ITEMS = 12
MAX_ITEM_CHARS = 600
MAX_POSITIONS_PER_TOPIC = 6


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One thing the council did not agree on, and what became of it."""

    topic: str
    positions: tuple[str, ...]
    #: How the chair settled it. Empty means it was **not** settled, which is a
    #: legitimate and important outcome rather than a parse failure.
    resolution: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.resolution.strip())

    def as_dict(self) -> dict[str, object]:
        return {
            "topic": self.topic,
            "positions": list(self.positions),
            "resolution": self.resolution,
            "resolved": self.resolved,
        }


@dataclass(frozen=True, slots=True)
class Consensus:
    """The shape of the council's agreement, for a person to judge."""

    agreements: tuple[str, ...] = field(default_factory=tuple)
    disagreements: tuple[Disagreement, ...] = field(default_factory=tuple)
    open_questions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_unresolved(self) -> bool:
        """Whether anything is still outstanding for a person to settle."""
        return bool(self.open_questions) or any(not item.resolved for item in self.disagreements)

    @property
    def unanimous(self) -> bool:
        """Agreed on something, and recorded no argument about anything.

        Requires at least one agreement on purpose: an empty consensus is
        silence, not unanimity, and labelling it unanimous would turn a chair
        that said nothing into a chair that reported total accord.
        """
        return bool(self.agreements) and not self.disagreements and not self.open_questions

    @property
    def is_empty(self) -> bool:
        return not (self.agreements or self.disagreements or self.open_questions)

    def as_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "agreements": list(self.agreements),
            "disagreements": [item.as_dict() for item in self.disagreements],
            "open_questions": list(self.open_questions),
            "has_unresolved": self.has_unresolved,
            "unanimous": self.unanimous,
        }


def _texts(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        return ()
    cleaned = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        text = entry.strip()[:MAX_ITEM_CHARS]
        if text:
            cleaned.append(text)
    return tuple(cleaned[:MAX_CONSENSUS_ITEMS])


def _disagreements(raw: object) -> tuple[Disagreement, ...]:
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        return ()
    items: list[Disagreement] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        topic = str(entry.get("topic") or "").strip()[:MAX_ITEM_CHARS]
        positions = _texts(entry.get("positions"))[:MAX_POSITIONS_PER_TOPIC]
        # One side is a statement, not a disagreement. Recording it as one would
        # inflate the count a reviewer uses to judge whether a real argument
        # happened, which is the number this whole structure exists to supply.
        if not topic or len(positions) < 2:
            continue
        items.append(
            Disagreement(
                topic=topic,
                positions=positions,
                resolution=str(entry.get("resolution") or "").strip()[:MAX_ITEM_CHARS],
            )
        )
        if len(items) >= MAX_CONSENSUS_ITEMS:
            break
    return tuple(items)


def parse_consensus(raw: object) -> Consensus | None:
    """Read a chair's consensus block. Never raises; ``None`` when unusable."""
    if not isinstance(raw, Mapping):
        return None
    consensus = Consensus(
        agreements=_texts(raw.get("agreements")),
        disagreements=_disagreements(raw.get("disagreements")),
        open_questions=_texts(raw.get("open_questions")),
    )
    return None if consensus.is_empty else consensus


CONSENSUS_CONTRACT = """\
Add a "consensus" object beside the fields above. It is what a reviewer reads to
decide whether a real debate happened, so record the argument rather than the
conclusion:

"consensus": {
  "agreements": ["what every position actually converged on"],
  "disagreements": [
    {
      "topic": "what was contested, in a few words",
      "positions": ["one side, stated as its holder would", "the other side"],
      "resolution": "how you settled it and what it costs; leave empty if you could not"
    }
  ],
  "open_questions": ["a decision only the project owner can make"]
}

Both sides of a disagreement are required — a single side is a statement and is
discarded. Leave "resolution" empty rather than inventing one; an unsettled
disagreement is the most useful entry in the document. Do not smooth away a
contested point to produce a tidier synthesis: recording no disagreement at all
is reported to the reviewer as such.
"""
