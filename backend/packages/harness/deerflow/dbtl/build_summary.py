"""The canonical Build review package: what we got, not what files exist.

The summarizer is a **bounded synthesis** step. It cannot invoke Bash, cannot
modify the execution bundle, and cannot cite evidence the server did not verify
— deterministic code owns schema validation, path checks, and every
gate-relevant field, so the model's only job is to say what the numbers and
plots mean.

Two boundaries are worth stating plainly because they are easy to erode.

**It says what a figure shows; it does not say whether the result is good.**
That judgement belongs to Test (compliance) and to the reviewer (meaning). A
summarizer permitted to grade would eventually grade a disappointing result as a
failure and withhold it.

**Selecting a subset never hides one.** The package records every figure the
Build declared and separately records the few chosen for the deck, so a reviewer
can always ask what was not shown to them.

**It writes prose, never evidence.** Key outcomes come from the execution and
only from it, and recorded deviations, limitations, and the rerun procedure
survive whatever the summarizer says — its own caveats are appended after them.
Every one of those fields is read downstream as something the work reported
about itself, so a step whose entire job is to describe must not be able to
restate a number, drop a caveat, or claim a procedure that was never run.

Parsing is fail-closed for *this step only*: a malformed summary fails
`summarize_results` and leaves every successful phase pinned and reusable. Two
deterministic normalization passes run first, because the common failure is a
model wrapping valid JSON in a fence, not a model inventing a schema.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from deerflow.dbtl.build_execution import BuildExecutionBundle, BuildFigure, KeyOutcome

#: How many figures the deck may lead with. The rest stay in the record.
MAX_SUMMARY_FIGURES = 8
MAX_PHASE_NOTES = 12
MAX_LINE_CHARS = 600

_FENCE = re.compile(r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)


def _text(value: Any, *, limit: int = MAX_LINE_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    return trimmed if len(trimmed) <= limit else trimmed[: limit - 1] + "…"


def _lines(value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(text for text in (_text(item) for item in value) if text)[:limit]


@dataclass(frozen=True, slots=True)
class SelectedFigure:
    """A figure the summarizer chose, with its one-line reading."""

    figure: BuildFigure
    reading: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {**self.figure.as_dict(), "reading": self.reading}


@dataclass(frozen=True, slots=True)
class PhaseNote:
    """A few lines on what one piece of the build did."""

    title: str
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "text": self.text}


@dataclass(frozen=True, slots=True)
class BuildReviewPackage:
    """The document a Build approval binds to."""

    headline: str
    key_outcomes: tuple[KeyOutcome, ...] = ()
    selected_figures: tuple[SelectedFigure, ...] = ()
    all_figures: tuple[BuildFigure, ...] = ()
    phase_notes: tuple[PhaseNote, ...] = ()
    deviations: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    rerun_procedure: str = ""

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @property
    def withheld_figure_count(self) -> int:
        chosen = {item.figure.path for item in self.selected_figures}
        return sum(1 for figure in self.all_figures if figure.path not in chosen)

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "key_outcomes": [outcome.as_dict() for outcome in self.key_outcomes],
            "selected_figures": [item.as_dict() for item in self.selected_figures],
            "all_figures": [figure.as_dict() for figure in self.all_figures],
            "phase_notes": [note.as_dict() for note in self.phase_notes],
            "deviations": list(self.deviations),
            "limitations": list(self.limitations),
            "rerun_procedure": self.rerun_procedure,
        }


@dataclass(frozen=True, slots=True)
class SummaryParse:
    """A parsed package, a question, or a reason it could not be read."""

    package: BuildReviewPackage | None = None
    clarification_question: str = ""
    refusal: str = ""
    #: The normalization passes that were needed. Recorded because a summary
    #: that only parses after unwrapping a fence is worth knowing about when a
    #: contract is being tuned.
    repaired: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.package is not None

    @property
    def needs_input(self) -> bool:
        return bool(self.clarification_question)


def _load(raw: str) -> tuple[Mapping[str, Any] | None, tuple[str, ...]]:
    """Two deterministic passes, then give up.

    The passes exist because the common failure is presentational — a fenced
    block, or prose wrapped around otherwise valid JSON — and re-running an
    expensive Build to recover from a code fence would be absurd. Anything
    beyond that is a contract failure and is reported as one.
    """
    attempts: list[str] = []
    candidate = raw.strip()
    try:
        loaded = json.loads(candidate)
        return (loaded if isinstance(loaded, Mapping) else None), ()
    except (ValueError, TypeError):
        pass

    fenced = _FENCE.match(candidate)
    if fenced:
        attempts.append("unwrapped_code_fence")
        try:
            loaded = json.loads(fenced.group("body"))
            return (loaded if isinstance(loaded, Mapping) else None), tuple(attempts)
        except (ValueError, TypeError):
            pass

    start, end = candidate.find("{"), candidate.rfind("}")
    if 0 <= start < end:
        attempts.append("extracted_outer_object")
        try:
            loaded = json.loads(candidate[start : end + 1])
            return (loaded if isinstance(loaded, Mapping) else None), tuple(attempts)
        except (ValueError, TypeError):
            pass
    return None, tuple(attempts)


def parse_build_summary(raw: str, *, bundle: BuildExecutionBundle) -> SummaryParse:
    """Read a summarizer's answer against the bundle it was given.

    Refuses rather than repairs when the model cites evidence that does not
    exist: a package naming a plot nobody produced is worse than no package,
    because the reviewer has no way to discover the citation is empty.
    """
    payload, repaired = _load(raw or "")
    if payload is None:
        return SummaryParse(refusal="The Build summary was not valid JSON.", repaired=repaired)

    question = _text(payload.get("clarification_question"))
    if _text(payload.get("status"), limit=32) == "needs_input" or question:
        if not question:
            return SummaryParse(refusal="The Build summary asked for input without stating a question.", repaired=repaired)
        return SummaryParse(clarification_question=question, repaired=repaired)

    headline = _text(payload.get("headline") or payload.get("summary"))
    if not headline:
        return SummaryParse(refusal="The Build summary did not state what the build produced.", repaired=repaired)

    selected: list[SelectedFigure] = []
    for raw_figure in payload.get("figures") or []:
        if not isinstance(raw_figure, Mapping):
            continue
        path = _text(raw_figure.get("path") or raw_figure.get("reference"), limit=1024)
        figure = bundle.figure_for(path)
        if figure is None:
            # Fail closed. This is the one thing a summarizer must not be able
            # to do: describe a plot the server never verified.
            return SummaryParse(refusal=f"The Build summary cited a figure the server did not verify: {path or '(unnamed)'}.", repaired=repaired)
        if any(item.figure.path == path for item in selected):
            continue
        selected.append(SelectedFigure(figure=figure, reading=_text(raw_figure.get("reading") or raw_figure.get("shows"))))
        if len(selected) >= MAX_SUMMARY_FIGURES:
            break

    notes = tuple(PhaseNote(title=_text(item.get("title"), limit=160), text=_text(item.get("text") or item.get("summary"))) for item in (payload.get("phases") or []) if isinstance(item, Mapping) and _text(item.get("title"), limit=160))[
        :MAX_PHASE_NOTES
    ]

    package = BuildReviewPackage(
        headline=headline,
        # **The numbers are the execution's, not the summarizer's.** A key
        # outcome is a measured value that Test will weigh and a reviewer will
        # read as fact; taking the model's list whenever it supplied one let it
        # restate a metric with a different number, a different unit, or a
        # figure that supports something else, and nothing downstream compares
        # the two. Highlighting belongs in the headline, where it reads as
        # commentary.
        key_outcomes=bundle.key_outcomes,
        selected_figures=tuple(selected),
        # Every declared figure, always. Choosing a subset for the deck must
        # never remove one from the record a reviewer can ask about.
        all_figures=bundle.figures,
        phase_notes=notes,
        # Additive, in that order. The execution's own caveats lead because a
        # reviewer must see what the work recorded about itself before what a
        # later reader made of it, and a summarizer that supplied its own list
        # used to replace them outright — the one edit that turns a recorded
        # deviation into a deviation nobody mentioned.
        deviations=_merge(bundle.deviations, _lines(payload.get("deviations"), limit=MAX_PHASE_NOTES)),
        limitations=_merge(bundle.limitations, _lines(payload.get("limitations"), limit=MAX_PHASE_NOTES)),
        # The recorded procedure wins outright: it is what was actually run, and
        # a rewrite is a claim about reproducibility that only Test may make.
        rerun_procedure=bundle.rerun_procedure or _text(payload.get("rerun_procedure"), limit=1200),
    )
    return SummaryParse(package=package, repaired=repaired)


def _merge(recorded: Sequence[str], added: Sequence[str]) -> tuple[str, ...]:
    """Recorded entries first, then anything new the summarizer added."""
    merged = list(recorded)
    seen = {item.strip().casefold() for item in merged}
    for item in added:
        key = item.strip().casefold()
        if key and key not in seen:
            seen.add(key)
            merged.append(item)
    return tuple(merged[: MAX_PHASE_NOTES * 2])


def render_summary_markdown(package: BuildReviewPackage, *, title: str) -> str:
    """The reviewed document. Ordered by what a reader needs first.

    Key outcomes lead, figures follow, and deviations/limitations come *before*
    the rerun notes — the same reason the Design deck puts disagreement before
    synthesis: a reader who has accepted the result will not go back for the
    caveat.
    """
    lines: list[str] = [f"# {title}", "", package.headline, ""]

    lines.append("## Key outcomes")
    if package.key_outcomes:
        lines.extend(f"- **{outcome.name}**: {outcome.value}{(' ' + outcome.unit) if outcome.unit else ''}" for outcome in package.key_outcomes)
    else:
        lines.append("- None reported.")
    lines.append("")

    lines.append("## Figures worth looking at")
    if package.selected_figures:
        for item in package.selected_figures:
            lines.append(f"- `{item.figure.path}` — {item.figure.caption or item.figure.shows or 'no caption'}")
            if item.reading:
                lines.append(f"  - {item.reading}")
        if package.withheld_figure_count:
            lines.append(f"- {package.withheld_figure_count} further figure(s) were produced and are listed in the record below.")
    else:
        # Legitimate: some builds produce none. Saying so beats an empty header.
        lines.append("- This build produced no figures.")
    lines.append("")

    if package.phase_notes:
        lines.append("## What the build did")
        for note in package.phase_notes:
            lines.append(f"- **{note.title}** — {note.text}")
        lines.append("")

    lines.append("## Deviations and limitations")
    if package.deviations or package.limitations:
        lines.extend(f"- Deviation: {item}" for item in package.deviations)
        lines.extend(f"- Limitation: {item}" for item in package.limitations)
    else:
        lines.append("- None reported.")
    lines.append("")

    lines.append("## How to re-run it")
    lines.append(package.rerun_procedure or "The build did not record a rerun procedure.")
    lines.append("")

    if package.all_figures:
        lines.append("## Every figure produced")
        lines.extend(f"- `{figure.path}` — {figure.caption or figure.shows or 'no caption'}" for figure in package.all_figures)
        lines.append("")

    return "\n".join(lines)
