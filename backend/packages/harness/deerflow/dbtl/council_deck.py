"""The design meeting's outcome as a slide deck a person can present.

The review package answers "is this design approvable?" — it is long, ordered
for an auditor, and read alone. This answers a different question: *what came
out of the meeting, and what does it need from me?* It is the thing put on a
screen in front of a group, so it leads with where the participants disagreed
and ends on the decisions only the project owner can make.

Three rules follow from that, and they are why this is a renderer rather than
another worker prompt:

**It cannot describe a meeting that did not happen.** Every slide is derived
from the recorded chair result. A model asked to "make a deck about the meeting"
can smooth a contested point into a bullet; this cannot, because it has no
sentence of its own to write.

**Disagreement comes before synthesis**, the same ordering the review Markdown
uses. Where the meeting split is what tells a reader whether the synthesis is a
conclusion or an average, and it is worthless once they have already read the
synthesis as settled.

**One file, no network.** Artifacts are served under a strict policy and are
often opened from a downloaded copy, so the deck inlines its own CSS and script
and fetches nothing. A deck that renders as unstyled text on the machine it is
presented from is not a deck.

Pure. No model call, no filesystem, no config — the deck for a given result is a
string, so what a reviewer will see is testable without running a meeting.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from deerflow.dbtl.consensus import Consensus, parse_consensus
from deerflow.dbtl.decision_request import DecisionRequest

#: Bounds. A slide that scrolls is a document, and the point of the deck is that
#: each screen holds one thought.
MAX_BULLETS_PER_SLIDE = 8
MAX_DISAGREEMENTS = 6
MAX_POSITIONS_PER_DISAGREEMENT = 4
MAX_BULLET_CHARS = 400
MAX_SUMMARY_CHARS = 4_000

#: How much of a section goes on one screen. A topic longer than this runs onto
#: another slide rather than being cut: the reader gets fewer things per screen,
#: which is what makes the deck read as a summary, and loses none of them.
BULLETS_PER_SLIDE = 5
CARDS_PER_SLIDE = 2
PARAGRAPHS_PER_SLIDE = 3

#: A section that would run past this is bounded, and says so on the last slide
#: rather than trailing off. Silent truncation reads as "that was all of it".
MAX_SLIDES_PER_SECTION = 4

#: How many entries a section collects before pagination bounds what is shown.
#: Deliberately larger than the display bound: capping at the display size makes
#: the overflow count zero, so the deck could never tell a reader that it was
#: holding something back.
MAX_SECTION_ITEMS = 200


def _text(value: object, *, limit: int = MAX_BULLET_CHARS) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _bullets(values: Sequence[object], *, limit: int = MAX_BULLETS_PER_SLIDE) -> list[str]:
    seen: list[str] = []
    for value in values:
        cleaned = _text(value)
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return seen


def _chair_result(results: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    """The result the deck describes.

    The last result carrying a consensus, falling back to the last result at
    all: the chair runs last, and on a resumed meeting it is the only worker
    that ran.
    """
    usable = [result for result in results if isinstance(result, Mapping)]
    if not usable:
        return None
    with_consensus = [result for result in usable if isinstance(result.get("consensus"), Mapping)]
    return with_consensus[-1] if with_consensus else usable[-1]


def _open_question_boxes(items: Sequence[str]) -> str:
    """An answer box per open question, not a list of things you cannot answer.

    The chair's own question got a control and every other open item rendered
    as an inert bullet, so a slide headed "Needs your decision" offered one
    decision and three read-only reminders. Each box is a
    ``data-deck-note`` control, so the bridge folds them all into the comment
    of whatever decision is taken — one record, and each answer labelled with
    the question it belongs to rather than its position on the slide.
    """
    if not items:
        return ""
    boxes: list[str] = []
    for index, question in enumerate(items, start=1):
        text = _text(question, limit=240)
        if not text:
            continue
        boxes.append(f'<div class="open-question"><p class="open-question-text">{html.escape(text)}</p>{_note_box(f"open-{index}", text)}</div>')
    return f'<div class="open-questions">{"".join(boxes)}</div>' if boxes else ""


def chair_result(results: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    """The chair result a round is described from — deck and chat reply alike.

    Public so the chat summary and the deck resolve the same chair from the
    same rows; two independent notions of "the chair" would eventually describe
    different meetings in the same turn.
    """
    return _chair_result(results)


def _note_box(note_id: str, label: str) -> str:
    """The reader's own box on a slide, shipped inert like every other control.

    Rendered into the persisted bytes rather than injected later for the same
    reason the gate's comment is: an approval binds to this file, and a control
    that appeared afterwards would not be part of what was hashed.

    ``data-note-label`` is what the note is filed under when the bridge folds it
    into the recorded decision, so the label travels with the text instead of
    being reconstructed from slide order — which changes with pagination.
    """
    return (
        f'<div class="note" data-note-for="{html.escape(note_id)}">'
        f'<label for="note-{html.escape(note_id)}">Your note on {html.escape(label.lower())} <span class="option-detail">travels with your decision</span></label>'
        f'<textarea id="note-{html.escape(note_id)}" data-deck-note data-note-label="{html.escape(label)}" rows="2" disabled></textarea>'
        f"</div>"
    )


def _slide(
    *,
    kind: str,
    title: str,
    body: str,
    eyebrow: str = "",
    note_id: str = "",
    note_label: str = "",
) -> str:
    eyebrow_html = f'<p class="eyebrow">{html.escape(eyebrow)}</p>' if eyebrow else ""
    note_html = _note_box(note_id, note_label or title) if note_id else ""
    return f'<section class="slide slide--{kind}">{eyebrow_html}<h2>{html.escape(title)}</h2>{body}{note_html}</section>'


def render_design_deck_slide(*, kind: str, title: str, body: str, eyebrow: str = "", note_id: str = "", note_label: str = "") -> str:
    """Render one slide with the canonical Design-deck structure."""
    return _slide(kind=kind, title=title, body=body, eyebrow=eyebrow, note_id=note_id, note_label=note_label)


def _pages(items: Sequence[object], per_page: int) -> list[list[object]]:
    """Split a section across screens, bounded, keeping the overflow count.

    Returns the pages plus nothing else; the caller asks ``_overflow`` how many
    entries the bound dropped, because a section that quietly stops is
    indistinguishable from one that had no more to say.
    """
    if per_page < 1:
        per_page = 1
    chunks = [list(items[index : index + per_page]) for index in range(0, len(items), per_page)]
    return chunks[:MAX_SLIDES_PER_SECTION]


def _overflow(items: Sequence[object], per_page: int) -> int:
    shown = sum(len(page) for page in _pages(items, per_page))
    return max(0, len(items) - shown)


def _more_note(count: int) -> str:
    if count < 1:
        return ""
    entry = "entry" if count == 1 else "entries"
    return f'<p class="more">{count} further {entry} in the full review package.</p>'


def _paged_slides(
    *,
    kind: str,
    title: str,
    eyebrow: str,
    items: Sequence[str],
    per_page: int,
    body_of,
    empty: str,
    note_label: str,
    note_prefix: str,
) -> list[str]:
    """One section as however many screens it needs, each with its own note box.

    Continuation slides keep the title and mark themselves ``cont.`` rather than
    inventing a new heading: they are the same topic, and a reader flipping back
    needs to see that.
    """
    pages = _pages(items, per_page)
    if not pages:
        return [
            _slide(
                kind=kind,
                eyebrow=eyebrow,
                title=title,
                body=f'<p class="empty">{html.escape(empty)}</p>',
                note_id=note_prefix,
                note_label=note_label,
            )
        ]
    dropped = _overflow(items, per_page)
    slides: list[str] = []
    for index, page in enumerate(pages):
        last = index == len(pages) - 1
        heading = title if index == 0 else f"{title} (cont.)"
        body = body_of(page) + (_more_note(dropped) if last else "")
        slides.append(
            _slide(
                kind=kind,
                eyebrow=eyebrow if index == 0 else f"{eyebrow} · {index + 1} of {len(pages)}",
                title=heading,
                body=body,
                note_id=note_prefix if index == 0 else f"{note_prefix}-{index + 1}",
                note_label=note_label,
            )
        )
    return slides


def _list_body(items: Sequence[str], *, empty: str) -> str:
    if not items:
        return f'<p class="empty">{html.escape(empty)}</p>'
    entries = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f"<ul>{entries}</ul>"


def _disagreement_body(consensus: Consensus) -> str:
    if not consensus.disagreements:
        if consensus.unanimous:
            # Not praise. A debate that recorded no argument is also what a
            # rubber stamp looks like, and the reader deciding on this should be
            # told which one they might be holding.
            return '<p class="empty">The chair recorded no disagreement at all. Every participant agreed, and no argument was written down — worth a second look before approving.</p>'
        return '<p class="empty">The chair recorded no disagreement.</p>'
    return _disagreement_cards(consensus.disagreements[:MAX_DISAGREEMENTS])


def _disagreement_cards(items: Sequence[object]) -> str:
    """A page of contested topics, each keeping both sides and how it stands."""
    cards: list[str] = []
    for item in items:
        positions = "".join(f"<li>{html.escape(_text(value))}</li>" for value in item.positions[:MAX_POSITIONS_PER_DISAGREEMENT])
        if item.resolved:
            verdict = f'<p class="verdict verdict--settled"><span>Settled</span> {html.escape(_text(item.resolution))}</p>'
        else:
            verdict = '<p class="verdict verdict--open"><span>Not resolved</span> still open for the project owner.</p>'
        cards.append(f'<article class="contested"><h3>{html.escape(_text(item.topic, limit=200))}</h3><ul class="positions">{positions}</ul>{verdict}</article>')
    return f'<div class="contested-grid">{"".join(cards)}</div>'


#: What the persisted file says instead of accepting an answer. It is the whole
#: security posture in one sentence: this copy cannot record anything, wherever
#: it was opened from.
INERT_NOTICE = "Open this deck in DeerFlow to respond."

#: The envelope every bridge message carries. A dedicated source string means a
#: page cannot accidentally answer for the deck, and the parent cannot mistake
#: another frame's chatter for it.
DECK_MESSAGE_SOURCE = "deerflow-design-deck"
BRIDGE_PROTOCOL_VERSION = 1


def _decision_cards(request: DecisionRequest) -> str:
    """The chair's options as a real radio group, shipped disabled.

    A fieldset with a legend rather than styled divs, because this is a
    mutually-exclusive choice and assistive technology has to be told that
    rather than shown it. Nothing is preselected — not even the recommended
    option — so the record cannot contain a choice the owner never made.
    """
    cards: list[str] = []
    for option in request.options:
        input_id = f"{request.id}--{option.id}"
        recommended = ""
        if option.id == request.recommended_option_id:
            # Labelled, never checked: the chair's view is evidence the owner
            # reads, not a default that answers for them.
            recommended = '<span class="badge">Recommended</span>'
        description = f'<span class="option-detail">{html.escape(_text(option.description))}</span>' if option.description else ""
        cards.append(
            f'<div class="option">'
            f'<input type="radio" id="{html.escape(input_id)}" name="{html.escape(request.id)}" value="{html.escape(option.id)}" disabled>'
            f'<label for="{html.escape(input_id)}">'
            f'<span class="option-label">{html.escape(_text(option.label, limit=200))}{recommended}</span>'
            f'<span class="option-value">{html.escape(_text(option.value))}</span>'
            f"{description}"
            f"</label>"
            f"</div>"
        )
    recommendation = f'<p class="recommendation"><span>Why the chair leans this way</span> {html.escape(_text(request.recommendation))}</p>' if request.recommendation else ""
    # The comment and submit exist in the persisted file but are disabled. They
    # are rendered here rather than injected by the parent so the reviewed bytes
    # contain the whole surface: an approval binds to this file, and a control
    # that appeared afterwards would not be part of what was hashed.
    comment = f'<div class="comment"><label for="{html.escape(request.id)}--comment">Add a comment (optional)</label><textarea id="{html.escape(request.id)}--comment" data-deck-comment rows="3" disabled></textarea></div>'
    submit = '<div class="submit-row"><button type="button" data-deck-submit disabled>Send to the meeting</button><p class="inert" data-deck-status role="status" aria-live="polite">' + html.escape(INERT_NOTICE) + "</p></div>"
    return f'<fieldset class="decision" disabled><legend>{html.escape(_text(request.question, limit=600))}</legend><div class="options">{"".join(cards)}</div>{comment}</fieldset>{recommendation}{submit}'


def _decision_text(question: str) -> str:
    """Render the exact recorded chair question as an inert text control."""
    return (
        '<fieldset class="decision" disabled>'
        f"<legend>{html.escape(_text(question, limit=600))}</legend>"
        '<div class="comment"><label for="design-chair-answer">Your answer</label>'
        '<textarea id="design-chair-answer" data-deck-comment rows="5" disabled></textarea></div>'
        '<div class="submit-row"><button type="button" data-deck-action="chair_text" disabled>'
        "Send to the meeting</button></div></fieldset>"
        '<p class="inert" data-deck-status role="status" aria-live="polite">' + html.escape(INERT_NOTICE) + "</p>"
    )


def _gate_option(
    *,
    value: str,
    label: str,
    detail: str,
    recommended: bool = False,
    note: str = "",
) -> str:
    """One gate choice as a radio card, shipped disabled like every control.

    ``note`` states a consequence of choosing this option; it never disables it.
    A verdict about the Design is not the same claim as a path edge being open,
    and gating the verdict on the edge is how the gate deadlocks — approving
    Design is exactly what opens the data work the Build edge waits on.
    """
    input_id = f"gate-{value}"
    badge = '<span class="badge">Recommended</span>' if recommended else ""
    note_html = f'<span class="route-reason">{html.escape(note)}</span>' if note else ""
    return (
        f'<div class="option">'
        f'<input type="radio" id="{input_id}" name="design-gate" value="{html.escape(value)}" disabled>'
        f'<label for="{input_id}">'
        f'<span class="option-label">{html.escape(label)}{badge}</span>'
        f'<span class="option-value">{html.escape(detail)}</span>'
        f"</label>{note_html}"
        f"</div>"
    )


def _review_controls(
    consensus: Consensus | None,
    transition_gate: Mapping[str, object] | None = None,
) -> str:
    gate = dict(transition_gate or {})
    if not gate:
        return _legacy_review_controls(consensus)
    assessment = gate.get("assessment")
    assessment = dict(assessment) if isinstance(assessment, Mapping) else {}
    difficulty = _text(assessment.get("difficulty") or "standard", limit=32)
    rationale = _text(assessment.get("rationale") or "", limit=2_000)
    difficulty_label = difficulty.replace("_", " ")
    routes = gate.get("routes")
    route_items = [dict(item) for item in routes if isinstance(item, Mapping)] if isinstance(routes, Sequence) and not isinstance(routes, str) else []
    advance_route = next((route for route in route_items if _text(route.get("slug") or "", limit=64) == "advance"), {})
    advance_blocked_reason = _text(advance_route.get("blocked_reason") or "", limit=600) if advance_route.get("blocked") else ""
    options = (
        _gate_option(
            value="approve",
            label="Approve",
            # What approving actually opens depends on whether the data work is
            # already settled. Promising the Build gate while reconciliation is
            # outstanding describes a stage that will still be locked.
            detail=("Accept this Design and open the Build gate." if not advance_blocked_reason else "Accept this Design and open Data reconciliation."),
            recommended=difficulty == "routine",
            note=advance_blocked_reason,
        )
        + _gate_option(
            value="revise",
            label="Revise",
            detail="Send the meeting back with your comment describing what to change.",
        )
        + _gate_option(
            value="park",
            label="Park",
            detail="Set the gate aside and work on this cycle with the lead agent.",
        )
    )
    comment_hint = "Required for Revise" + (" and for a high-stakes approval" if difficulty == "high_stakes" else "") + "."
    return (
        # The legend is the radio group's accessible name, so it states the
        # question rather than repeating the slide's heading.
        '<fieldset class="review" disabled><legend>What happens to this Design?</legend>'
        f'<div class="assessment" data-assessed-difficulty="{html.escape(difficulty)}">'
        f"<p><strong>Agent assessment: {html.escape(difficulty_label)}</strong></p>"
        f"<p>{html.escape(rationale)}</p>"
        "</div>"
        f'<div class="options">{options}</div>'
        f'<div class="comment"><label for="design-review-comment">Comment <span class="option-detail">{html.escape(comment_hint)}</span></label>'
        '<textarea id="design-review-comment" data-deck-comment rows="4" disabled></textarea></div>'
        '<div class="submit-row"><button type="button" data-deck-gate-submit disabled>Record my decision</button></div>'
        "</fieldset>"
        '<p class="inert" data-deck-status role="status" aria-live="polite">' + html.escape(INERT_NOTICE) + "</p>"
    )


def _legacy_review_controls(consensus: Consensus | None) -> str:
    """The pre-progressive-gate controls, kept for decks without a gate."""
    issues: list[str] = []
    if consensus is not None:
        for index, item in enumerate(consensus.disagreements[:MAX_DISAGREEMENTS]):
            issue_id = f"issue-{index + 1}"
            issues.append(f'<label class="review-issue" for="{issue_id}"><input id="{issue_id}" type="checkbox" value="{issue_id}" data-deck-issue disabled><span>{html.escape(_text(item.topic, limit=240))}</span></label>')
    issue_html = f'<div class="review-issues"><p>Select the points that need another round</p>{"".join(issues)}</div>' if issues else ""
    return (
        '<fieldset class="review" disabled><legend>Move this Design through its human gate</legend>'
        '<p class="option-detail">Submission and the final verdict are separate records. Nothing is approved by opening this deck.</p>'
        f"{issue_html}"
        '<div class="comment"><label for="design-review-comment">Rationale or requested change</label>'
        '<textarea id="design-review-comment" data-deck-comment rows="4" disabled></textarea></div>'
        '<div class="review-actions">'
        '<button type="button" data-deck-action="submit_for_review" disabled>Submit for review</button>'
        '<button type="button" data-deck-action="approve" disabled>Continue to Build</button>'
        '<button type="button" data-deck-action="request_changes" disabled>Revise Design</button>'
        '<button type="button" data-deck-action="reject" disabled>Reject</button>'
        "</div></fieldset>"
        '<p class="inert" data-deck-status role="status" aria-live="polite">' + html.escape(INERT_NOTICE) + "</p>"
    )


def _stage_review_controls(
    stage: str,
    transition_gate: Mapping[str, object] | None,
) -> str:
    """Review controls for Build, Test, and Learn feedback surfaces.

    Design keeps its byte-stable, three-choice control above.  The other
    stages previously rendered that same Design-only form, which meant the
    server could offer a review meeting but the deck had no control capable of
    emitting ``convene_review_meeting``.  Render the stage's real vocabulary;
    every button still ships disabled and the authenticated parent enables only
    the actions returned by the server read model.
    """
    normalized = (stage or "").strip().lower()
    gate = dict(transition_gate or {})
    assessment = gate.get("assessment")
    assessment = dict(assessment) if isinstance(assessment, Mapping) else {}
    difficulty = _text(assessment.get("difficulty") or "standard", limit=32)
    rationale = _text(assessment.get("rationale") or "", limit=2_000)
    assessment_html = (
        f'<div class="assessment" data-assessed-difficulty="{html.escape(difficulty)}"><p><strong>Agent assessment: {html.escape(difficulty.replace("_", " "))}</strong></p><p>{html.escape(rationale)}</p></div>' if assessment else ""
    )

    buttons = [
        '<button type="button" data-deck-action="convene_review_meeting" disabled>Convene review meeting</button>',
        '<button type="button" data-deck-action="submit_for_review" disabled>Submit for review</button>',
    ]
    if normalized in {"build", "learn"}:
        buttons.extend(
            [
                '<button type="button" data-deck-action="approve" disabled>Approve</button>',
                '<button type="button" data-deck-action="request_changes" disabled>Revise here</button>',
                '<button type="button" data-deck-action="reject" disabled>Reject</button>',
            ]
        )
    if normalized == "build":
        # Rendered unconditionally and enabled by the server's read model,
        # like every other control here: the deck is a persisted file that may
        # be opened long after the deployment's rules changed, so it must not
        # be the thing that decides which decisions exist.
        buttons.append('<button type="button" data-deck-action="learn_exploratory" disabled>Keep, but do not validate</button>')
    test_note = '<p class="option-detail">The scientific outcome and route are computed from the structured validity review; the meeting may annotate that pack but cannot choose an outcome.</p>' if normalized == "test" else ""
    stage_label = normalized.title() or "Stage"
    return (
        f'<fieldset class="review" disabled><legend>Move this {html.escape(stage_label)} evidence through its human gate</legend>'
        f"{assessment_html}{test_note}"
        f'<div class="comment"><label for="stage-review-comment">Reviewer comment or meeting brief</label>'
        '<textarea id="stage-review-comment" data-deck-comment rows="4" disabled></textarea></div>'
        f'<div class="review-actions">{"".join(buttons)}</div></fieldset>'
        '<p class="inert" data-deck-status role="status" aria-live="polite">' + html.escape(INERT_NOTICE) + "</p>"
    )


def render_stage_review_controls(
    stage: str,
    transition_gate: Mapping[str, object] | None = None,
) -> str:
    """Return the inert, authenticated-parent-owned controls for a stage deck.

    Result-specific deck renderers (currently Build) use this public boundary
    instead of copying the review vocabulary or bridge selectors.  The controls
    still ship disabled; rendering them grants no authority.
    """
    return _stage_review_controls(stage, transition_gate)


def _bridge_script(surface_id: str) -> str:
    """The deck's half of the handshake, or nothing at all.

    Emitted only for a deck the server registered. A legacy or unregistered deck
    carries no bridge whatsoever — not a disabled one — because the safest
    version of "this file cannot answer" is a file with no code that could.

    The deck holds no endpoint, no token, and no way to reach a server. It
    announces itself and waits; the authenticated parent decides whether
    anything becomes live, and repeats every check server-side regardless.
    """
    if not surface_id:
        return ""
    # json.dumps escapes quotes and backslashes; the ``</`` split additionally
    # prevents a literal ``</script>`` inside the value from closing this block.
    encoded = json.dumps(surface_id).replace("</", "<\\/")
    return _BRIDGE_TEMPLATE.replace("__SURFACE_ID__", encoded).replace("__PROTOCOL__", str(BRIDGE_PROTOCOL_VERSION)).replace("__SOURCE__", json.dumps(DECK_MESSAGE_SOURCE))


def _bridge_script_for_stage(surface_id: str, stage: str) -> str:
    """Keep Design's pinned bytes while making later-stage prompts truthful."""
    script = _bridge_script(surface_id)
    normalized = (stage or "design").strip().lower()
    if normalized == "design" or not script:
        return script
    label = normalized.title()
    replacements = {
        "sending the Design back": f"sending the {label} back",
        "Recording your Design decision...": f"Recording your {label} decision...",
        "Submit this Design when it is ready for human review.": f"Submit this {label} evidence when it is ready for human review.",
        "Choose a Design verdict.": f"Choose a {label} verdict.",
    }
    for before, after in replacements.items():
        script = script.replace(before, after)
    return script


def render_stage_feedback_bridge(surface_id: str, stage: str) -> str:
    """Return the authenticated iframe bridge for a registered stage surface."""
    return _bridge_script_for_stage(surface_id, stage)


_BRIDGE_TEMPLATE = """
<script>
(function () {
  'use strict';
  var SURFACE_ID = __SURFACE_ID__;
  var PROTOCOL = __PROTOCOL__;
  var SOURCE = __SOURCE__;

  // Opened directly rather than framed: there is no parent to authenticate, so
  // the deck stays exactly as it was persisted.
  if (window.parent === window) { return; }

  var channel = null;
  var allowed = [];
  var submitting = false;
  // Latched once the surface is answered or replaced. The parent already
  // refuses to re-initialize a settled surface, but a deck that would happily
  // re-arm on a later 'initialize' leaves that as the parent's promise rather
  // than the file's property — and this file is the part that travels.
  var settled = false;

  var fieldset = document.querySelector('fieldset.decision');
  var review = document.querySelector('fieldset.review');
  var submit = document.querySelector('[data-deck-submit]');
  var gateSubmit = document.querySelector('[data-deck-gate-submit]');
  var actionButtons = Array.prototype.slice.call(document.querySelectorAll('[data-deck-action]'));
  var status = document.querySelector('[data-deck-status]');
  var comment = document.querySelector('[data-deck-comment]');
  var assessment = document.querySelector('[data-assessed-difficulty]');
  // The chair's options, the gate's choice radios, and the legacy review's
  // contested-topic checkboxes: every one is rendered disabled, and the submit
  // paths read them back via :checked.
  var choices = Array.prototype.slice.call(
    document.querySelectorAll('fieldset.decision input[type="radio"], fieldset.review input[type="radio"], [data-deck-issue]')
  );
  // One box per content slide. They are drafts, not a second record: whatever
  // is in them is folded into the comment of the decision actually taken, so a
  // note can never be stored as a verdict nobody gave.
  var notes = Array.prototype.slice.call(document.querySelectorAll('[data-deck-note]'));
  var NOTE_LIMIT = 1000;
  var GATE_KINDS = ['advance', 'approve', 'request_changes', 'park'];

  function say(text) { if (status) { status.textContent = text; } }

  function withNotes(text) {
    var parts = [];
    notes.forEach(function (note) {
      var value = (note.value || '').trim();
      if (!value) { return; }
      var label = note.getAttribute('data-note-label') || 'Note';
      parts.push('[' + label + '] ' + value.slice(0, NOTE_LIMIT));
    });
    if (!parts.length) { return text; }
    // Notes first, then the verdict's own words: the reader of the record
    // works through the deck in the order the reviewer did.
    return text ? parts.join('\\n') + '\\n---\\n' + text : parts.join('\\n');
  }

  function clearNotes() {
    notes.forEach(function (note) { note.value = ''; });
  }

  function send(type, extra) {
    var payload = { source: SOURCE, protocol: PROTOCOL, surfaceId: SURFACE_ID, type: type };
    if (channel) { payload.channel = channel; }
    if (extra) { for (var key in extra) { if (Object.prototype.hasOwnProperty.call(extra, key)) { payload[key] = extra[key]; } } }
    // No secret crosses this boundary, so a wildcard target is acceptable; the
    // parent authenticates by source window, channel, and its own server call.
    window.parent.postMessage(payload, '*');
  }

  function gateAllowed() {
    for (var i = 0; i < GATE_KINDS.length; i += 1) {
      if (allowed.indexOf(GATE_KINDS[i]) !== -1) { return true; }
    }
    return false;
  }

  function setEnabled(on) {
    if (fieldset) { fieldset.disabled = !on; }
    if (submit) { submit.disabled = !on || allowed.indexOf('chair_option') === -1; }
    if (review) { review.disabled = !on; }
    if (gateSubmit) { gateSubmit.disabled = !on || !gateAllowed(); }
    actionButtons.forEach(function (button) {
      var kind = button.dataset.deckAction;
      button.disabled = !on || button.hasAttribute('data-route-blocked')
        || allowed.indexOf(kind) === -1;
    });
    if (comment) { comment.disabled = !on; }
    // A note box carries its own `disabled` for the same reason every other
    // control does, so activation has to clear each one individually.
    notes.forEach(function (note) { note.disabled = !on; });
    // Every option ships individually disabled so the persisted file is inert
    // wherever it is opened. An enabled fieldset does not re-enable a control
    // that carries its own `disabled`, so activation has to clear each one --
    // otherwise the submit button comes alive over a choice nobody can make.
    // A route-blocked choice stays disabled: the reason is printed beside it.
    choices.forEach(function (choice) {
      choice.disabled = !on || choice.hasAttribute('data-route-blocked');
    });
  }

  function selected() {
    var checked = document.querySelector('fieldset.decision input[type="radio"]:checked');
    return checked ? checked.value : '';
  }

  if (submit) {
    submit.addEventListener('click', function () {
      if (submitting || !channel || allowed.indexOf('chair_option') === -1) { return; }
      var option = selected();
      if (!option) { say('Choose one option first.'); return; }
      if (option.toLowerCase() === 'other' && (!comment || !comment.value.trim())) {
        say('Add a comment for the Other option.'); return;
      }
      submitting = true;
      setEnabled(false);
      say('Sending your decision...');
      // The draft is kept in the DOM, so a failure can re-enable exactly what
      // the person had typed rather than asking them to retype it.
      send('submit_intent', {
        action: { kind: 'chair_option', optionIds: [option] },
        comment: withNotes(comment ? comment.value : '')
      });
    });
  }

  if (gateSubmit) {
    gateSubmit.addEventListener('click', function () {
      if (submitting || !channel || !gateAllowed()) { return; }
      var checked = document.querySelector('fieldset.review input[type="radio"]:checked');
      if (!checked) { say('Choose Approve, Revise, or Park first.'); return; }
      var value = checked.value;
      // Approve is one recorded gate action: the submit+approve route when the
      // stage is still open, the plain verdict once it is awaiting review.
      var kind = value === 'revise' ? 'request_changes'
        : value === 'park' ? 'park'
        : (allowed.indexOf('advance') !== -1 ? 'advance' : 'approve');
      if (allowed.indexOf(kind) === -1) { say('That choice is not available right now.'); return; }
      // Validate what is actually recorded. A reviewer who wrote their reasons
      // on the slide the reasons are about has written them down; refusing the
      // verdict because the last box is empty would be asking twice.
      var text = withNotes(comment ? comment.value.trim() : '').trim();
      var effective = assessment ? assessment.dataset.assessedDifficulty : 'standard';
      if (kind === 'request_changes' && !text) {
        say('Describe what should change before sending the Design back.'); return;
      }
      if ((kind === 'advance' || kind === 'approve') && effective === 'high_stakes' && !text) {
        say('A high-stakes approval needs your written rationale.'); return;
      }
      submitting = true;
      setEnabled(false);
      say('Recording your decision...');
      send('submit_intent', {
        action: { kind: kind, optionIds: [], difficultyOverride: '' },
        comment: text
      });
    });
  }

  actionButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      var kind = button.dataset.deckAction;
      if (submitting || !channel || allowed.indexOf(kind) === -1) { return; }
      var optionIds = [];
      if (kind === 'request_changes') {
        optionIds = Array.prototype.slice.call(document.querySelectorAll('[data-deck-issue]:checked')).map(function (item) { return item.value; });
      }
      var text = withNotes(comment ? comment.value.trim() : '').trim();
      if ((kind === 'chair_text' || kind === 'reject') && !text) {
        say('Add a rationale before recording this decision.'); return;
      }
      if (kind === 'request_changes' && !optionIds.length && !text) {
        say('Select a contested point or describe the change needed.'); return;
      }
      submitting = true;
      setEnabled(false);
      say('Recording your Design decision...');
      send('submit_intent', {
        action: {
          kind: kind,
          optionIds: optionIds,
          difficultyOverride: ''
        },
        comment: text
      });
    });
  });

  window.addEventListener('message', function (event) {
    if (event.source !== window.parent) { return; }
    var data = event.data;
    if (!data || typeof data !== 'object') { return; }
    if (data.source !== SOURCE) { return; }
    if (data.protocol !== PROTOCOL) { return; }
    if (data.surfaceId !== SURFACE_ID) { return; }
    // Every message after the handshake must carry the channel the parent
    // issued for this mount. Convenience against cross-talk, not authority.
    if (data.type !== 'initialize' && data.channel !== channel) { return; }

    if (data.type === 'initialize') {
      // A surface that has been answered or superseded is not re-openable.
      if (settled) { return; }
      channel = typeof data.channel === 'string' && data.channel ? data.channel : null;
      allowed = Array.isArray(data.allowedActions) ? data.allowedActions.slice(0, 8) : [];
      if (Array.isArray(data.selectedOptionIds)) {
        choices.forEach(function (choice) {
          choice.checked = data.selectedOptionIds.indexOf(choice.value) !== -1;
        });
      }
      if (comment && typeof data.comment === 'string') {
        comment.value = data.comment;
        // A restored comment is one this deck already folded its notes into,
        // so the boxes are cleared rather than merged a second time. The
        // reviewer's words are not lost -- they are in the comment now, which
        // is the field the record actually keeps.
        if (data.comment) { clearNotes(); }
      }
      submitting = false;
      var live = !!channel && allowed.length > 0;
      setEnabled(live);
      var prompt = gateSubmit && gateAllowed()
        ? 'Choose Approve, Revise, or Park, then record your decision.'
        : (allowed.indexOf('submit_for_review') !== -1
          ? 'Submit this Design when it is ready for human review.'
          : (allowed.indexOf('approve') !== -1
            ? 'Choose a Design verdict.'
            : (allowed.indexOf('chair_text') !== -1
              ? 'Answer the chair, then send it to the meeting.'
              : 'Choose an option, then send it to the meeting.')));
      say(live ? prompt : (typeof data.note === 'string' && data.note ? data.note : 'This round is read-only.'));
      return;
    }
    if (data.type === 'pending') { submitting = true; setEnabled(false); say('Sending your decision...'); return; }
    if (data.type === 'accepted') {
      submitting = false;
      settled = true;
      setEnabled(false);
      say(typeof data.note === 'string' && data.note ? data.note : 'Recorded. The meeting is resuming in the conversation it started in.');
      return;
    }
    if (data.type === 'stale') {
      submitting = false;
      settled = true;
      setEnabled(false);
      say(typeof data.note === 'string' && data.note ? data.note : 'A newer round has replaced this one. Open the latest deck to respond.');
      return;
    }
    if (data.type === 'failed') {
      // Re-enable rather than clear: the draft is still the person's, and the
      // parent reuses the same submission id when they try again.
      submitting = false;
      setEnabled(true);
      say(typeof data.note === 'string' && data.note ? data.note : 'That did not go through. Your choice is still here — try again.');
      return;
    }
  });

  send('ready');
})();
</script>
"""


def _decision_items(consensus: Consensus | None, clarification_question: str) -> list[str]:
    """What the meeting is asking a person for, most urgent first."""
    items: list[str] = []
    if clarification_question.strip():
        items.append(_text(clarification_question))
    if consensus is not None:
        items.extend(_text(item.topic) for item in consensus.disagreements if not item.resolved)
        items.extend(_text(value) for value in consensus.open_questions)
    return _bullets(items)


def _brief_slides(*, research_question: str, objective: str, success_criteria: Sequence[str] | str) -> list[str]:
    """Background and Objectives, read off the cycle record rather than written.

    These two sections are the only ones on the deck that do not come from the
    meeting, and they are quoted verbatim from what the cycle was opened with.
    The renderer has no sentence of its own here either: a "background" it
    composed would be an account of the science that nobody reviewed.
    """
    slides: list[str] = []
    question = _text(research_question, limit=MAX_SUMMARY_CHARS)
    if question:
        slides.append(
            _slide(
                kind="background",
                eyebrow="Why this cycle exists",
                title="Background",
                body=f'<p class="statement">{html.escape(question)}</p><p class="source">The question this cycle was opened with.</p>',
                note_id="background",
                note_label="Background",
            )
        )
    goals = [item for item in ([_text(objective, limit=MAX_SUMMARY_CHARS)] if objective else []) if item]
    # A str is itself a Sequence[str], so iterating one yields characters and
    # would render a criterion as a column of single letters.
    raw_criteria: Sequence[object] = [success_criteria] if isinstance(success_criteria, str) else list(success_criteria or ())
    criteria = _bullets(raw_criteria, limit=BULLETS_PER_SLIDE)
    if goals or criteria:
        objective_html = f'<p class="statement">{html.escape(goals[0])}</p>' if goals else ""
        criteria_html = f'<p class="source">What would count as success</p>{_list_body(criteria, empty="")}' if criteria else ""
        slides.append(
            _slide(
                kind="objective",
                eyebrow="What it has to achieve",
                title="Objectives",
                body=objective_html + criteria_html,
                note_id="objectives",
                note_label="Objectives",
            )
        )
    return slides


def render_council_deck(
    *,
    cycle_title: str,
    stage_title: str,
    round_number: int,
    results: Sequence[Mapping[str, object]],
    package_path: str = "",
    clarification_question: str = "",
    decision_request: DecisionRequest | None = None,
    surface_id: str = "",
    surface_mode: str = "",
    transition_gate: Mapping[str, object] | None = None,
    stage: str = "design",
    research_question: str = "",
    objective: str = "",
    success_criteria: Sequence[str] | str = (),
    generated_at: datetime | None = None,
) -> str:
    """The meeting's outcome as one self-contained HTML slide deck."""
    chair = _chair_result(results) or {}
    consensus = parse_consensus(chair.get("consensus"))
    summary = _text(chair.get("summary"), limit=MAX_SUMMARY_CHARS)
    decisions = _decision_items(consensus, clarification_question)
    stamp = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")

    slides: list[str] = []
    slides.append(
        _slide(
            kind="title",
            eyebrow=f"{stage_title} · Round {round_number}",
            title=cycle_title or "Design meeting",
            body=f'<p class="lede">What the participants agreed, where they split, and what still needs your decision.</p><p class="stamp">{html.escape(stamp)}</p>',
        )
    )
    slides.extend(_brief_slides(research_question=research_question, objective=objective, success_criteria=success_criteria))
    slides.extend(
        _paged_slides(
            kind="agree",
            eyebrow="Where the meeting agreed",
            title="Agreed",
            items=_bullets(consensus.agreements, limit=MAX_SECTION_ITEMS) if consensus is not None else [],
            per_page=BULLETS_PER_SLIDE,
            body_of=lambda page: _list_body([str(item) for item in page], empty=""),
            empty="The chair recorded no agreement.",
            note_label="Agreed",
            note_prefix="agreed",
        )
    )
    if consensus is None:
        slides.append(
            _slide(
                kind="contest",
                eyebrow="Where the meeting split",
                title="Contested",
                body='<p class="empty">The chair reported no structured consensus, so nothing can be shown here without inventing it. The written synthesis is the record.</p>',
                note_id="contested",
                note_label="Contested",
            )
        )
    elif not consensus.disagreements:
        slides.append(
            _slide(
                kind="contest",
                eyebrow="Where the meeting split",
                title="Contested",
                body=_disagreement_body(consensus),
                note_id="contested",
                note_label="Contested",
            )
        )
    else:
        slides.extend(
            _paged_slides(
                kind="contest",
                eyebrow="Where the meeting split",
                title="Contested",
                items=list(consensus.disagreements[:MAX_DISAGREEMENTS]),
                per_page=CARDS_PER_SLIDE,
                body_of=_disagreement_cards,
                empty="The chair recorded no disagreement.",
                note_label="Contested",
                note_prefix="contested",
            )
        )
    cards = _decision_cards(decision_request) if decision_request is not None and decision_request.renders_as_cards else ""
    free_text = _decision_text(clarification_question) if surface_mode == "chair_feedback" and clarification_question.strip() and not cards else ""
    if cards or free_text or decisions:
        # The options replace the question's own bullet, not the rest of the
        # list: a contested topic the chair left open still needs settling
        # whether or not this one question came with choices.
        remaining = [item for item in decisions if item != _text(clarification_question)] if cards or free_text else decisions
        slides.append(
            _slide(
                kind="decide",
                eyebrow="Only you can settle these",
                title="Needs your decision",
                body=cards + free_text + _open_question_boxes(remaining),
                # The chair's own question already carries a control, and every
                # other open item now carries its own; a slide-level box on top
                # would ask the same person the same thing twice.
                note_id="",
            )
        )
    if summary:
        paragraphs = [part.strip() for part in summary.split("\n") if part.strip()]
        slides.extend(
            _paged_slides(
                kind="synthesis",
                eyebrow="The chair's synthesis",
                title="Conclusions",
                items=paragraphs,
                per_page=PARAGRAPHS_PER_SLIDE,
                body_of=lambda page: "".join(f"<p>{html.escape(str(part))}</p>" for part in page),
                empty="",
                note_label="Conclusions",
                note_prefix="conclusions",
            )
        )
    limitations = _bullets(list(chair.get("limitations") or []), limit=MAX_SECTION_ITEMS)
    if limitations:
        slides.extend(
            _paged_slides(
                kind="limits",
                eyebrow="Read the conclusions against these",
                title="Limitations",
                items=limitations,
                per_page=BULLETS_PER_SLIDE,
                body_of=lambda page: _list_body([str(item) for item in page], empty=""),
                empty="",
                note_label="Limitations",
                note_prefix="limitations",
            )
        )
    next_actions = _bullets(list(chair.get("recommended_next_actions") or []), limit=MAX_SECTION_ITEMS)
    footer = f'<p class="stamp">Full review package: {html.escape(package_path)}</p>' if package_path else ""
    slides.extend(
        _paged_slides(
            kind="next",
            eyebrow="Recommended, not decided",
            title="Next",
            items=next_actions,
            per_page=BULLETS_PER_SLIDE,
            body_of=lambda page: _list_body([str(item) for item in page], empty=""),
            empty="The chair recommended no next action.",
            note_label="Next steps",
            note_prefix="next",
        )
    )
    # The deck must say what it is not, on the way to the one slide that
    # records something. Losing this sentence when the gate moved would have
    # left a reader to infer that reaching the end is itself an approval.
    closing = '<p class="gate">Nothing here approves anything merely by opening the deck. The final slide is where a verdict is recorded.</p>' + footer
    # Ahead of the reader's own box, not after it: the box is the last thing on
    # the slide because it is the reader's turn.
    anchor = '<div class="note"' if '<div class="note"' in slides[-1] else "</section>"
    slides[-1] = slides[-1].replace(anchor, closing + anchor, 1)
    # The gate goes last, after everything it is a verdict on. A reviewer who
    # reaches it has passed every slide their notes are attached to, and the
    # deck ends on the one screen that records something.
    if surface_mode == "stage_review":
        normalized_stage = (stage or "design").strip().lower()
        slides.append(
            _slide(
                kind="review",
                eyebrow="Human gate",
                title=f"Review the {normalized_stage.title()}",
                body=(
                    '<p class="gate">Your notes from the earlier slides are sent with this decision.</p>'
                    + (_review_controls(consensus, transition_gate) if normalized_stage == "design" else _stage_review_controls(normalized_stage, transition_gate))
                ),
            )
        )

    return render_design_deck_shell(
        title=html.escape(f"{cycle_title or 'Design meeting'} — {stage_title}"),
        slides=slides,
        bridge=_bridge_script_for_stage(surface_id, stage),
    )


def render_design_deck_shell(*, title: str, slides: Sequence[str], bridge: str = "") -> str:
    """Place evidence slides in the canonical Design navigation and theme."""
    return _DECK_TEMPLATE.format(
        title=title,
        slides="".join(slides),
        count=len(slides),
        bridge=bridge,
    )


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
  .slide.is-active {{ display: block; animation: in .18s ease-out; }}
  @keyframes in {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: none; }} }}
  .eyebrow {{ margin: 0 0 .35rem; color: var(--accent); font: .72rem/1.55 ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace; letter-spacing: .16em; text-transform: uppercase; }}
  h2 {{ margin: 0 0 1.25rem; font-size: clamp(1.7rem, 3.6vw, 2.7rem); font-weight: 600; line-height: 1.15; letter-spacing: -.025em; }}
  .slide--title h2 {{ font-size: clamp(2.1rem, 5vw, 3.4rem); font-style: italic; }}
  .lede {{ font-size: 1.15rem; color: var(--muted); max-width: 42rem; }}
  .stamp {{ color: var(--muted); font-size: .82rem; margin-top: 1.75rem; word-break: break-all; }}
  ul {{ margin: 0; padding-left: 1.15rem; }}
  li {{ margin: .55rem 0; font-size: 1.08rem; }}
  .empty {{ color: var(--muted); font-style: italic; }}
  /* Fewer things per screen, each one larger: what makes the deck read as a
     summary is the pacing, not a shorter sentence the renderer invented. */
  .slide--agree li, .slide--limits li, .slide--next li {{ font-size: 1.16rem; margin: .95rem 0; padding-left: .2rem; }}
  .slide--agree li::marker {{ color: var(--accent); }}
  .statement {{ font-size: 1.32rem; line-height: 1.45; max-width: 46rem; margin: 0 0 1rem; }}
  .source {{ margin: 0 0 .6rem; color: var(--muted); font: .72rem/1.6 ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
    letter-spacing: .12em; text-transform: uppercase; }}
  .slide--synthesis p {{ font-size: 1.14rem; line-height: 1.62; max-width: 46rem; margin: 0 0 1rem; }}
  .more {{ margin: 1rem 0 0; color: var(--muted); font-size: .88rem; font-style: italic; }}
  /* The reader's own box. Dashed while inert so an untouched deck does not
     look like a form somebody abandoned half-filled. */
  .note {{ display: grid; gap: .3rem; margin: 1.9rem 0 0; padding-top: 1.1rem; border-top: 1px solid var(--line); }}
  .note label {{ font: .72rem/1.6 ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
    letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }}
  .note textarea {{ width: 100%; resize: vertical; border: 1px dashed var(--line); border-radius: 8px;
    padding: .6rem .7rem; color: var(--fg); background: transparent; font: inherit; }}
  .note textarea:not([disabled]) {{ border-style: solid; background: var(--bg); }}
  .note textarea:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 3px; }}
  .note textarea[disabled] {{ opacity: .6; }}
  .open-questions {{ display: grid; gap: 1.4rem; margin-top: .4rem; }}
  .open-question-text {{ margin: 0 0 .1rem; font-size: 1.1rem; font-weight: 600; line-height: 1.4; }}
  /* Its own box sits under its own question, so the two read as one item
     rather than as a list followed by a form. */
  .open-question .note {{ margin-top: .5rem; padding-top: 0; border-top: 0; }}
  .contested-grid {{ display: grid; gap: .9rem; }}
  .contested {{ border: 1px solid var(--line); border-left: 3px solid var(--accent); border-radius: 0 6px 6px 0; padding: 1rem 1.15rem; background: var(--card); }}
  .contested h3 {{ margin: 0 0 .5rem; font-size: 1.05rem; }}
  .positions {{ color: var(--muted); }}
  .positions li {{ font-size: .96rem; margin: .3rem 0; }}
  .verdict {{ margin: .6rem 0 0; font-size: .95rem; }}
  .verdict span {{ display: inline-block; margin-right: .5rem; padding: .08rem .5rem; border-radius: 999px; font-size: .74rem;
    letter-spacing: .05em; text-transform: uppercase; border: 1px solid currentColor; }}
  .verdict--open {{ color: var(--warn); }}
  .verdict--open span {{ font-weight: 600; }}
  .verdict--settled span {{ color: var(--accent); }}
  .assessment {{ margin: 1rem 0; padding: .9rem; border: 1px solid var(--line); border-radius: .7rem; background: var(--card); }}
  .assessment p {{ margin: .2rem 0 .55rem; }}
  .assessment label {{ display: block; margin: .75rem 0 .3rem; font-weight: 650; }}
  .assessment select {{ width: 100%; padding: .55rem; border: 1px solid var(--line); border-radius: .45rem; background: var(--bg); color: var(--fg); }}
  .route-actions {{ display: grid; gap: .45rem; margin: .8rem 0; }}
  .route-reason {{ color: var(--warn); font-size: .82rem; }}
  .slide--decide li {{ font-size: 1.15rem; }}
  .decision {{ margin: 0 0 1.1rem; padding: 0; border: 0; }}
  .review {{ margin: 0; padding: 0; border: 0; }}
  .decision legend {{ padding: 0; margin-bottom: .9rem; font-size: 1.2rem; font-weight: 600; }}
  .options {{ display: grid; gap: .65rem; }}
  .option {{ display: flex; gap: .7rem; align-items: flex-start; border: 1px solid var(--line); border-radius: 10px;
    padding: .8rem .95rem; background: var(--card); }}
  .option input {{ margin: .3rem 0 0; flex: none; width: 1.05rem; height: 1.05rem; accent-color: var(--accent); }}
  .option label {{ display: grid; gap: .2rem; }}
  .option input:checked + label {{ outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 6px; }}
  .option-label {{ font-weight: 600; font-size: 1.02rem; }}
  .option-value {{ color: var(--fg); font-size: .97rem; }}
  .option-detail {{ color: var(--muted); font-size: .9rem; }}
  /* Not colour alone: the badge keeps its border and text in every palette and
     in print, where an accent tint is the first thing to disappear. */
  .badge {{ margin-left: .55rem; padding: .08rem .5rem; border: 1px solid currentColor; border-radius: 999px;
    color: var(--accent); font-size: .68rem; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; vertical-align: middle; }}
  .decision[disabled] .option {{ opacity: .92; }}
  .recommendation {{ margin: 0 0 .9rem; color: var(--muted); font-size: .93rem; }}
  .recommendation span {{ display: block; font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; }}
  .review-issues {{ display: grid; gap: .5rem; margin: 1rem 0; }}
  .review-issues > p {{ margin: 0; color: var(--muted); font-size: .86rem; }}
  .review-issue {{ display: flex; gap: .65rem; align-items: flex-start; padding: .65rem .8rem;
    border: 1px solid var(--line); border-radius: 8px; background: var(--card); }}
  .review-issue input {{ margin-top: .25rem; }}
  .comment {{ display: grid; gap: .35rem; margin: .9rem 0; }}
  .comment label {{ font-size: .82rem; color: var(--muted); }}
  .comment textarea {{ width: 100%; resize: vertical; border: 1px solid var(--line); border-radius: 8px;
    padding: .6rem .7rem; color: var(--fg); background: var(--bg); font: inherit; }}
  .submit-row, .review-actions {{ display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; }}
  .submit-row button, .review-actions button {{ border: 1px solid var(--line); border-radius: 7px;
    padding: .5rem .75rem; color: var(--fg); background: var(--card); font: inherit; cursor: pointer; }}
  .submit-row button:focus-visible, .review-actions button:focus-visible, .bar button:focus-visible,
  .option input:focus-visible, .review-issue input:focus-visible, .comment textarea:focus-visible {{
    outline: 3px solid var(--accent); outline-offset: 3px;
  }}
  .submit-row button:disabled, .review-actions button:disabled {{ cursor: not-allowed; opacity: .55; }}
  .inert {{ margin: 0; padding: .55rem .8rem; border: 1px dashed var(--line); border-radius: 8px;
    color: var(--muted); font: .8rem/1.55 ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace; }}
  .gate {{ margin-top: 1.5rem; color: var(--muted); font-size: .9rem; border-left: 2px solid var(--line); padding-left: .85rem; }}
  .bar {{ position: fixed; left: 0; bottom: 0; width: 100%; display: flex; align-items: center; gap: 1rem;
    padding: .7rem 1.4rem; border-top: 1px solid var(--line); background: var(--bg); font-size: .82rem; color: var(--muted); }}
  .bar button {{ font: inherit; color: inherit; background: none; border: 1px solid var(--line); border-radius: 6px;
    padding: .2rem .7rem; cursor: pointer; }}
  .bar button:hover {{ border-color: var(--accent); color: var(--accent); }}
  .track {{ flex: 1; height: 3px; background: var(--line); border-radius: 999px; overflow: hidden; }}
  .track i {{ display: block; height: 100%; background: var(--accent); transition: width .2s ease; }}
  @media (prefers-reduced-motion: reduce) {{
    .track i {{ transition: none; }}
  }}
  @media print {{
    .deck {{ display: block; height: auto; padding: 0; }}
    .slide, .slide.is-active {{ display: block; page-break-after: always; padding: 2.5rem; }}
    .decision .comment, .review .comment, .submit-row, .review-actions, [data-deck-status] {{ display: none !important; }}
    /* An empty box prints as a blank rectangle; on paper it is a place to
       write, so it keeps its border and loses only the disabled styling. */
    .note textarea {{ opacity: 1; min-height: 4.5rem; }}
    .decision input, .review input {{ display: none; }}
    .bar {{ display: none; }}
  }}
</style>
</head>
<body>
<main class="deck">{slides}</main>
<nav class="bar">
  <button type="button" data-step="-1" aria-label="Previous slide">&larr;</button>
  <button type="button" data-step="1" aria-label="Next slide">&rarr;</button>
  <span class="track"><i></i></span>
  <span class="count"><span class="at">1</span> / {count}</span>
</nav>
<script>
(function () {{
  var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
  var at = document.querySelector('.at');
  var fill = document.querySelector('.track i');
  var index = 0;
  function show(next) {{
    index = Math.max(0, Math.min(next, slides.length - 1));
    slides.forEach(function (slide, i) {{ slide.classList.toggle('is-active', i === index); }});
    at.textContent = String(index + 1);
    fill.style.width = ((index + 1) / slides.length * 100) + '%';
  }}
  // Space selects a focused radio and an arrow key moves between them, so a
  // deck that always paged would make the choice unusable by keyboard the
  // moment these controls are live.
  var INTERACTIVE = /^(input|textarea|select|button|a)$/i;
  function inControl(target) {{
    if (!target) {{ return false; }}
    // The deck's own arrows are buttons; keeping the keyboard working after
    // someone clicks one is the whole point of the navigation bar.
    if (target.closest && target.closest('.bar')) {{ return false; }}
    return INTERACTIVE.test(target.nodeName || '') || target.isContentEditable;
  }}
  document.addEventListener('keydown', function (event) {{
    if (inControl(event.target)) {{ return; }}
    if (event.key === 'ArrowRight' || event.key === 'PageDown' || event.key === ' ') {{ show(index + 1); }}
    else if (event.key === 'ArrowLeft' || event.key === 'PageUp') {{ show(index - 1); }}
    else if (event.key === 'Home') {{ show(0); }}
    else if (event.key === 'End') {{ show(slides.length - 1); }}
    else {{ return; }}
    event.preventDefault();
  }});
  Array.prototype.forEach.call(document.querySelectorAll('.bar button'), function (button) {{
    button.addEventListener('click', function () {{ show(index + Number(button.dataset.step)); }});
  }});
  show(0);
}})();
</script>
{bridge}
</body>
</html>
"""
