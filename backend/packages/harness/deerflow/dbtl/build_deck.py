"""Build evidence rendered inside the canonical Design slide template.

The Build renderer owns no result claims: every outcome, figure, phase note,
deviation, limitation, and rerun instruction comes from a validated
``BuildReviewPackage``.  It does own presentation.  Build and Design therefore
share one deck shell, navigation model, typography, motion, print behavior, and
authenticated bridge slot instead of maintaining parallel review channels.

Figures are embedded under explicit per-file and whole-deck limits.  A figure
that cannot be embedded is named with a reason rather than silently dropped.
The final Human gate slide uses the same inert stage controls as Design; only
the authenticated DeerFlow parent can activate them.
"""

from __future__ import annotations

import base64
import html
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from deerflow.dbtl.build_summary import BuildReviewPackage, SelectedFigure
from deerflow.dbtl.council_deck import (
    render_design_deck_shell,
    render_design_deck_slide,
    render_stage_feedback_bridge,
    render_stage_review_controls,
)

MAX_FIGURE_BYTES = 1_500_000
MAX_DECK_FIGURE_BYTES = 6_000_000

#: Part of Build's deterministic feedback-surface id. Bump whenever the
#: bridge-bearing structure changes so newly rendered bytes cannot reuse an id
#: embedded in an older deck.
BUILD_DECK_SURFACE_VERSION = "build-review-surface-v4-exploratory-closeout"

FigureReader = Callable[[str], tuple[bytes, str] | None]

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def figure_mime(path: str) -> str:
    lowered = path.lower()
    return next((mime for suffix, mime in _MIME.items() if lowered.endswith(suffix)), "")


@dataclass(frozen=True, slots=True)
class EmbeddedFigure:
    figure: SelectedFigure
    data_uri: str = ""
    skipped_reason: str = ""

    @property
    def embedded(self) -> bool:
        return bool(self.data_uri)


def embed_figures(
    figures: tuple[SelectedFigure, ...],
    *,
    read: FigureReader,
    max_figure_bytes: int = MAX_FIGURE_BYTES,
    max_total_bytes: int = MAX_DECK_FIGURE_BYTES,
) -> tuple[EmbeddedFigure, ...]:
    """Inline bounded verified figures and explain every omission."""
    embedded: list[EmbeddedFigure] = []
    budget = max_total_bytes
    for selected in figures:
        mime = figure_mime(selected.figure.path)
        if not mime:
            embedded.append(EmbeddedFigure(figure=selected, skipped_reason="This format cannot be embedded — open it from the workspace."))
            continue
        try:
            payload = read(selected.figure.path)
        except OSError:
            payload = None
        if payload is None:
            embedded.append(EmbeddedFigure(figure=selected, skipped_reason="This figure could not be read — open it from the workspace."))
            continue
        raw, resolved_mime = payload
        if len(raw) > max_figure_bytes:
            embedded.append(EmbeddedFigure(figure=selected, skipped_reason="Figure too large to embed — open it from the workspace."))
            continue
        if len(raw) > budget:
            embedded.append(EmbeddedFigure(figure=selected, skipped_reason="The deck reached its embedded-figure limit — open this one from the workspace."))
            continue
        budget -= len(raw)
        data = base64.b64encode(raw).decode("ascii")
        embedded.append(EmbeddedFigure(figure=selected, data_uri=f"data:{resolved_mime or mime};base64,{data}"))
    return tuple(embedded)


def _e(value: str) -> str:
    return html.escape(value or "")


#: The publisher prefixes a governed filename with a short content hash. A
#: reader recognises the figure by its name, not by that prefix.
_PUBLISHED_PREFIX = re.compile(r"^[0-9a-f]{8,}-")


def readable_artifact_name(path: str) -> str:
    """The filename a person would say out loud, not the governed URI.

    Slides used to stamp the full virtual path under every figure — six nested
    directories and a content hash for a file the reader is already looking at.
    The full path stays in the machine record, where a path belongs.
    """
    name = PurePosixPath(str(path or "").strip()).name
    return _PUBLISHED_PREFIX.sub("", name) or str(path or "")


def readable_number(value: str) -> str:
    """Round a worker's raw float for reading, leaving anything else alone.

    Workers emit full float repr — `0.0615427182312` — because that is what
    `str()` gives. Twelve digits on a slide is noise a reviewer has to squint
    past, and it implies a precision the estimate does not have.
    """
    text = str(value or "").strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.4g}"


def _outcomes_body(package: BuildReviewPackage) -> str:
    if not package.key_outcomes:
        # Not noise: Test cannot reach a verdict at all without headline
        # metrics, so a figure-only Build is telling the reviewer something
        # they are going to meet again two stages later.
        return '<p class="empty">This build reported no numeric outcomes.</p>'
    entries = "".join(
        f'<article class="contested"><h3>{_e(outcome.name)}</h3><p class="verdict verdict--settled"><span>Recorded</span>{_e(readable_number(outcome.value))}{(" " + _e(outcome.unit)) if outcome.unit else ""}</p></article>'
        for outcome in package.key_outcomes
    )
    return f'<div class="contested-grid">{entries}</div>'


def _figure_slide(embedded: EmbeddedFigure) -> str:
    figure = embedded.figure.figure
    caption = figure.caption or figure.shows or readable_artifact_name(figure.path)
    visual = f'<img alt="{_e(caption)}" src="{embedded.data_uri}" style="display:block;max-width:100%;max-height:58vh;margin:auto">' if embedded.embedded else f'<p class="empty">{_e(embedded.skipped_reason)}</p>'
    reading = f'<p class="lede">{_e(embedded.figure.reading)}</p>' if embedded.figure.reading else ""
    return render_design_deck_slide(
        kind="figure",
        eyebrow="Figure",
        title=caption,
        body=f'<div class="contested">{visual}</div>{reading}<p class="stamp">{_e(readable_artifact_name(figure.path))}</p>',
    )


def _list_slide(
    title: str,
    eyebrow: str,
    entries: list[str],
    *,
    empty: str,
    note_id: str = "",
) -> str:
    """One list section, deduplicated.

    An empty section still renders. That looks like noise and is not: each of
    these slides is a comment anchor, and the absence is reviewable information
    — "no limitations recorded" is a thing a reviewer may want to object to, and
    "no rerun procedure" tells them the `structured_rerun_spec` gate is about to
    bite. Dropping the slide would take away the only place to say so.

    Exact repeats *are* dropped: workers routinely restate one caveat in two
    phases, and listing it twice adds nothing to read.
    """
    kept = list(dict.fromkeys(entry for entry in entries if entry and entry.strip()))
    body = "".join(f"<li>{_e(entry)}</li>" for entry in kept)
    inner = f"<ul>{body}</ul>" if kept else f'<p class="empty">{_e(empty)}</p>'
    return render_design_deck_slide(kind="detail", eyebrow=eyebrow, title=title, body=inner, note_id=note_id, note_label=title)


def render_build_deck(
    package: BuildReviewPackage,
    *,
    title: str,
    subtitle: str = "",
    package_path: str = "",
    read_figure: FigureReader,
    surface_id: str = "",
    transition_gate: Mapping[str, object] | None = None,
) -> str:
    """Render Build evidence in Design's canonical deck and gate slide."""
    if not package.has_slide_results:
        raise ValueError("A Build result deck requires a verified numeric outcome or figure.")
    embedded = embed_figures(package.selected_figures, read=read_figure)
    slides: list[str] = [
        render_design_deck_slide(
            kind="title",
            eyebrow="Build review",
            title=title,
            body=f'<p class="lede">{_e(package.headline)}</p>' + (f'<p class="stamp">{_e(subtitle)}</p>' if subtitle else ""),
        ),
        render_design_deck_slide(
            kind="outcomes",
            eyebrow="What we got",
            title="Key outcomes",
            body=_outcomes_body(package),
            note_id="build-outcomes",
            note_label="Key outcomes",
        ),
    ]

    if embedded:
        slides.extend(_figure_slide(item) for item in embedded)
        if package.withheld_figure_count:
            shown = {item.figure.figure.path for item in embedded}
            slides.append(
                _list_slide(
                    "Other figures produced",
                    "Not shown here",
                    # The full path stays here: this figure is *not* on screen,
                    # so the reader needs enough to go and find it.
                    [figure.path for figure in package.all_figures if figure.path not in shown],
                    empty="None.",
                )
            )

    if package.phase_notes:
        rows = "".join(f"<li><strong>{_e(note.title)}</strong> — {_e(note.text)}</li>" for note in package.phase_notes)
        slides.append(render_design_deck_slide(kind="phases", eyebrow="How it got there", title="What the build did", body=f"<ul>{rows}</ul>"))

    if package.deliverable_fulfillment is not None:
        slides.append(
            _list_slide(
                "Deliverables",
                "Design contract",
                [f"{item.deliverable_id} — {item.status.value}: {item.notes}" for item in package.deliverable_fulfillment.items],
                empty="No deliverables were declared.",
                note_id="build-deliverables",
            )
        )

    slides.append(
        _list_slide(
            "Deviations and limitations",
            "Read this before the result",
            [f"Deviation: {item}" for item in package.deviations] + [f"Limitation: {item}" for item in package.limitations],
            empty="None reported.",
            note_id="build-limitations",
        )
    )
    rerun = (
        f'<pre style="white-space:pre-wrap;word-break:break-word;border:1px solid var(--line);border-radius:8px;padding:.9rem;background:var(--card)">{_e(package.rerun_procedure)}</pre>'
        if package.rerun_procedure
        else '<p class="empty">The build did not record a rerun procedure.</p>'
    )
    slides.append(
        render_design_deck_slide(
            kind="rerun",
            eyebrow="Reproducing it",
            title="How to re-run it",
            body=rerun + (f'<p class="stamp">Reviewed document: {_e(package_path)}</p>' if package_path else ""),
            note_id="build-rerun",
            note_label="How to re-run it",
        )
    )

    if surface_id:
        slides.append(
            render_design_deck_slide(
                kind="review",
                eyebrow="Human gate",
                title="Review the Build",
                body=render_stage_review_controls("build", transition_gate),
            )
        )

    return render_design_deck_shell(
        title=_e(title),
        slides=slides,
        bridge=render_stage_feedback_bridge(surface_id, "build") if surface_id else "",
    )
