"""The Build review deck: a renderer, not a worker.

It converts an already-validated `BuildReviewPackage` into one self-contained
HTML file. It has no sentence of its own — every string it prints comes from the
package — which is the property that stops a renderer from quietly improving a
result. It cannot invent a claim, change an outcome, smooth away a deviation, or
show evidence the package does not contain.

**Slide order is a judgement about readers.** Key outcomes → figures → what the
build did → deviations and limitations → how to re-run it. Deviations come
*before* the rerun notes for the same reason the Design deck puts disagreement
before synthesis: a reader who has already accepted a result will not go back
looking for the caveat.

**Figures are embedded, not linked.** A registered deck is self-contained under
a strict no-network rule, so a linked plot is a broken image the moment the file
moves. Embedding forces two things to be designed rather than discovered: a
raster figure has to be size-bounded, and a format the renderer cannot inline is
*named and skipped* rather than silently dropped — a missing figure a reader
cannot see is missing is worse than one labelled as unavailable.

The stylesheet deliberately duplicates the Design deck's design tokens rather
than importing them: `council_deck`'s exact bytes are pinned by a fixture test,
and sharing a constant would make an edit here silently rewrite a deck people
have already been shown.
"""

from __future__ import annotations

import base64
import html
from collections.abc import Callable
from dataclasses import dataclass

from deerflow.dbtl.build_summary import BuildReviewPackage, SelectedFigure

#: Per-figure and whole-deck ceilings. A deck is opened in a browser and stored
#: as evidence; an unbounded base64 payload makes it unopenable in exactly the
#: situation somebody most needs to read it.
MAX_FIGURE_BYTES = 1_500_000
MAX_DECK_FIGURE_BYTES = 6_000_000

#: Returns ``(bytes, mime)`` for a verified figure path, or ``None`` when it
#: cannot be read. Injected so this module stays pure and testable without a
#: project on disk.
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
    """One figure as it will appear: inlined, or explained."""

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
    """Inline what fits and say plainly what did not.

    Every skip carries a reason a person can act on, because "open it from the
    workspace" is a useful instruction and a blank space is not.
    """
    embedded: list[EmbeddedFigure] = []
    budget = max_total_bytes
    for selected in figures:
        mime = figure_mime(selected.figure.path)
        if not mime:
            embedded.append(EmbeddedFigure(figure=selected, skipped_reason="This format cannot be embedded — open it from the workspace."))
            continue
        payload = None
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
        embedded.append(EmbeddedFigure(figure=selected, data_uri=f"data:{resolved_mime or mime};base64,{base64.b64encode(raw).decode('ascii')}"))
    return tuple(embedded)


def _e(value: str) -> str:
    return html.escape(value or "")


def _outcomes_slide(package: BuildReviewPackage) -> str:
    if not package.key_outcomes:
        return '<p class="empty">This build reported no numeric outcomes.</p>'
    items = "".join(f'<li><strong>{_e(outcome.name)}</strong><span class="value">{_e(outcome.value)}{(" " + _e(outcome.unit)) if outcome.unit else ""}</span></li>' for outcome in package.key_outcomes)
    return f'<ul class="outcomes">{items}</ul>'


def _figure_slide(embedded: EmbeddedFigure) -> str:
    figure = embedded.figure.figure
    body = f'<img alt="{_e(figure.caption or figure.shows or figure.path)}" src="{embedded.data_uri}">' if embedded.embedded else f'<p class="skipped">{_e(embedded.skipped_reason)}</p>'
    caption = _e(figure.caption or figure.shows or figure.path)
    reading = f'<p class="reading">{_e(embedded.figure.reading)}</p>' if embedded.figure.reading else ""
    return f'<section class="slide slide--figure"><p class="eyebrow">Figure</p><h2>{caption}</h2><div class="figure">{body}</div>{reading}<p class="stamp">{_e(figure.path)}</p></section>'


def _list_slide(title: str, eyebrow: str, entries: list[str], *, empty: str) -> str:
    body = "".join(f"<li>{_e(entry)}</li>" for entry in entries) if entries else ""
    inner = f"<ul>{body}</ul>" if entries else f'<p class="empty">{_e(empty)}</p>'
    return f'<section class="slide"><p class="eyebrow">{_e(eyebrow)}</p><h2>{_e(title)}</h2>{inner}</section>'


def render_build_deck(
    package: BuildReviewPackage,
    *,
    title: str,
    subtitle: str = "",
    package_path: str = "",
    read_figure: FigureReader,
) -> str:
    """Render the whole deck as one self-contained HTML document."""
    embedded = embed_figures(package.selected_figures, read=read_figure)

    slides: list[str] = [
        f'<section class="slide slide--title is-active"><p class="eyebrow">Build review</p><h2>{_e(title)}</h2><p class="lede">{_e(package.headline)}</p>' + (f'<p class="stamp">{_e(subtitle)}</p>' if subtitle else "") + "</section>",
        f'<section class="slide"><p class="eyebrow">What we got</p><h2>Key outcomes</h2>{_outcomes_slide(package)}</section>',
    ]

    if embedded:
        slides.extend(_figure_slide(item) for item in embedded)
        if package.withheld_figure_count:
            slides.append(
                _list_slide(
                    "Other figures produced",
                    "Not shown here",
                    [figure.path for figure in package.all_figures if figure.path not in {item.figure.figure.path for item in embedded}],
                    empty="None.",
                )
            )
    else:
        # A build with no figures says so plainly rather than rendering an
        # empty gallery, which reads as a rendering failure.
        slides.append('<section class="slide"><p class="eyebrow">Figures</p><h2>No figures</h2><p class="empty">This build produced no figures to show.</p></section>')

    if package.phase_notes:
        slides.append(
            '<section class="slide"><p class="eyebrow">How it got there</p><h2>What the build did</h2><ul>' + "".join(f"<li><strong>{_e(note.title)}</strong> — {_e(note.text)}</li>" for note in package.phase_notes) + "</ul></section>"
        )

    slides.append(
        _list_slide(
            "Deviations and limitations",
            "Read this before the result",
            [f"Deviation: {item}" for item in package.deviations] + [f"Limitation: {item}" for item in package.limitations],
            empty="None reported.",
        )
    )
    slides.append(
        '<section class="slide"><p class="eyebrow">Reproducing it</p><h2>How to re-run it</h2>'
        + (f"<pre>{_e(package.rerun_procedure)}</pre>" if package.rerun_procedure else '<p class="empty">The build did not record a rerun procedure.</p>')
        + (f'<p class="stamp">Reviewed document: {_e(package_path)}</p>' if package_path else "")
        + "</section>"
    )

    return _DECK_TEMPLATE.format(title=_e(title), slides="".join(slides), count=len(slides))


_DECK_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #fbf9f4; --fg: #1b1a17; --muted: #6a6459; --line: #e2ddd1;
    --accent: #8a3324; --warn: #96601b; --card: #f3efe5;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #16150f; --fg: #f0ece2; --muted: #a49c8c; --line: #2e2b22;
      --accent: #e0714f; --warn: #d9a441; --card: #1f1d16;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--fg);
    font: 16px/1.55 ui-serif, Georgia, "Iowan Old Style", "Times New Roman", serif;
  }}
  .deck {{ height: 100vh; display: flex; align-items: center; justify-content: center; padding: 3rem 2rem 4.5rem; }}
  .slide {{ display: none; width: min(60rem, 100%); max-height: 100%; overflow-y: auto; }}
  .slide.is-active {{ display: block; }}
  .eyebrow {{ margin: 0 0 .35rem; color: var(--accent); font: .72rem/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .16em; text-transform: uppercase; }}
  h2 {{ margin: 0 0 1.25rem; font-size: clamp(1.7rem, 3.6vw, 2.7rem); font-weight: 600; line-height: 1.15; letter-spacing: -.025em; }}
  .slide--title h2 {{ font-size: clamp(2.1rem, 5vw, 3.4rem); font-style: italic; }}
  .lede {{ font-size: 1.15rem; color: var(--muted); max-width: 42rem; }}
  .stamp {{ color: var(--muted); font-size: .82rem; margin-top: 1.75rem; word-break: break-all; }}
  ul {{ margin: 0; padding-left: 1.15rem; }}
  li {{ margin: .55rem 0; font-size: 1.08rem; }}
  .empty {{ color: var(--muted); font-style: italic; }}
  .outcomes {{ list-style: none; padding: 0; display: grid; gap: .6rem; }}
  .outcomes li {{ display: flex; justify-content: space-between; gap: 1.5rem; align-items: baseline;
    border: 1px solid var(--line); border-left: 3px solid var(--accent); border-radius: 0 6px 6px 0;
    padding: .75rem 1rem; background: var(--card); }}
  .outcomes .value {{ font: 600 1.25rem/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: -.01em; }}
  .figure {{ border: 1px solid var(--line); border-radius: 8px; background: var(--card); padding: .85rem; text-align: center; }}
  .figure img {{ max-width: 100%; max-height: 58vh; height: auto; }}
  .skipped {{ margin: 2rem 0; color: var(--warn); font-style: italic; }}
  .reading {{ margin: .9rem 0 0; font-size: 1.05rem; }}
  pre {{ margin: 0; padding: .9rem 1rem; border: 1px solid var(--line); border-radius: 8px; background: var(--card);
    font: .95rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; word-break: break-word; }}
  .bar {{ position: fixed; inset: auto 0 0 0; display: flex; gap: .5rem; align-items: center; justify-content: center;
    padding: .75rem; background: var(--bg); border-top: 1px solid var(--line); }}
  .bar button {{ font: inherit; padding: .3rem .85rem; border: 1px solid var(--line); border-radius: 999px;
    background: var(--card); color: var(--fg); cursor: pointer; }}
  .bar span {{ color: var(--muted); font-size: .85rem; }}
  @media print {{
    .bar {{ display: none; }}
    .deck {{ height: auto; display: block; padding: 0; }}
    .slide {{ display: block !important; max-height: none; page-break-after: always; padding: 2rem 0; }}
  }}
</style>
</head>
<body>
<main class="deck">{slides}</main>
<nav class="bar" aria-label="Slides">
  <button type="button" data-deck-prev aria-label="Previous slide">&larr;</button>
  <span data-deck-position>1 / {count}</span>
  <button type="button" data-deck-next aria-label="Next slide">&rarr;</button>
</nav>
<script>
(function () {{
  var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
  var position = document.querySelector('[data-deck-position]');
  var index = 0;
  function show(next) {{
    index = Math.max(0, Math.min(slides.length - 1, next));
    slides.forEach(function (slide, i) {{ slide.classList.toggle('is-active', i === index); }});
    if (position) {{ position.textContent = (index + 1) + ' / ' + slides.length; }}
  }}
  document.querySelector('[data-deck-prev]').addEventListener('click', function () {{ show(index - 1); }});
  document.querySelector('[data-deck-next]').addEventListener('click', function () {{ show(index + 1); }});
  document.addEventListener('keydown', function (event) {{
    var target = event.target;
    // Yield to a focused control: space selects a radio, and a deck that
    // always paged would make any future control unusable by keyboard.
    if (target && target.closest && target.closest('input, textarea, select, button, a, [contenteditable]') && !target.closest('.bar')) {{ return; }}
    if (event.key === 'ArrowRight' || event.key === 'PageDown' || event.key === ' ') {{ show(index + 1); event.preventDefault(); }}
    if (event.key === 'ArrowLeft' || event.key === 'PageUp') {{ show(index - 1); event.preventDefault(); }}
  }});
  show(0);
}})();
</script>
</body>
</html>
"""
