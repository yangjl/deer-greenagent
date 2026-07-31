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
from dataclasses import dataclass
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


def _slide(*, kind: str, title: str, body: str, eyebrow: str = "") -> str:
    eyebrow_html = f'<p class="eyebrow">{html.escape(eyebrow)}</p>' if eyebrow else ""
    return f'<section class="slide slide--{kind}">{eyebrow_html}<h2>{html.escape(title)}</h2>{body}</section>'


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
    cards: list[str] = []
    for item in consensus.disagreements[:MAX_DISAGREEMENTS]:
        positions = "".join(f"<li>{html.escape(_text(value))}</li>" for value in item.positions[:MAX_POSITIONS_PER_DISAGREEMENT])
        if item.resolved:
            verdict = f'<p class="verdict verdict--settled"><span>Settled</span> {html.escape(_text(item.resolution))}</p>'
        else:
            verdict = '<p class="verdict verdict--open"><span>Not resolved</span> still open for the project owner.</p>'
        cards.append(f'<article class="contested"><h3>{html.escape(_text(item.topic, limit=200))}</h3><ul class="positions">{positions}</ul>{verdict}</article>')
    return f'<div class="contested-grid">{"".join(cards)}</div>'


#: A theme may restyle the deck; it may never add to it. The bytes of a
#: registered deck are hash-bound as the actionable gate surface, so anything
#: that could introduce markup or script is refused rather than escaped:
#: inside a ``<style>`` element there is no escaping — a closing tag ends the
#: stylesheet and everything after it is document content.
DECK_THEME_MAX_CHARS = 128_000
_THEME_REFUSED_SUBSTRINGS = ("</style", "<script", "<!--", "javascript:")


@dataclass(frozen=True)
class DeckThemeParse:
    """A validated theme, or the reason there isn't one.

    Never raises, mirroring ``parse_decision_request``: a malformed theme costs
    the deck its styling, never the meeting whose results are already recorded.
    """

    css: str = ""
    refusal: str = ""

    @property
    def accepted(self) -> bool:
        return bool(self.css)


def parse_deck_theme(raw: object) -> DeckThemeParse:
    """Accept operator-supplied CSS for the deck, or say why not.

    Only the *shape* is checked here. A theme is trusted operator content in
    the same sense ``extensions.middlewares`` is — CSS can hide any element it
    likes, including the inert notice — which is why the theme is named in
    `config.yaml` rather than discovered from whatever happens to be on disk.
    An agent that can write a skill directory still cannot make the server load
    one.
    """
    if raw is None:
        return DeckThemeParse()
    if not isinstance(raw, str):
        return DeckThemeParse(refusal=f"A deck theme must be CSS text, not {type(raw).__name__}.")
    css = raw.strip()
    if not css:
        return DeckThemeParse()
    if len(css) > DECK_THEME_MAX_CHARS:
        return DeckThemeParse(refusal=f"The deck theme is {len(css)} characters; the cap is {DECK_THEME_MAX_CHARS}.")
    lowered = css.casefold()
    for marker in _THEME_REFUSED_SUBSTRINGS:
        if marker in lowered:
            return DeckThemeParse(refusal=f"A deck theme may not contain {marker!r}; it is a stylesheet, not markup.")
    return DeckThemeParse(css=css)


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
        f'<div class="assessment" data-assessed-difficulty="{html.escape(difficulty)}">'
        f"<p><strong>Agent assessment: {html.escape(difficulty.replace('_', ' '))}</strong></p>"
        f"<p>{html.escape(rationale)}</p></div>"
        if assessment
        else ""
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
    test_note = (
        '<p class="option-detail">The scientific outcome and route are computed from the structured validity review; the meeting may annotate that pack but cannot choose an outcome.</p>'
        if normalized == "test"
        else ""
    )
    stage_label = normalized.title() or "Stage"
    return (
        f'<fieldset class="review" disabled><legend>Move this {html.escape(stage_label)} evidence through its human gate</legend>'
        f"{assessment_html}{test_note}"
        f'<div class="comment"><label for="stage-review-comment">Reviewer comment or meeting brief</label>'
        '<textarea id="stage-review-comment" data-deck-comment rows="4" disabled></textarea></div>'
        f'<div class="review-actions">{"".join(buttons)}</div></fieldset>'
        '<p class="inert" data-deck-status role="status" aria-live="polite">'
        + html.escape(INERT_NOTICE)
        + "</p>"
    )


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
  var GATE_KINDS = ['advance', 'approve', 'request_changes', 'park'];

  function say(text) { if (status) { status.textContent = text; } }

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
        comment: comment ? comment.value : ''
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
      var text = comment ? comment.value.trim() : '';
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
      var text = comment ? comment.value.trim() : '';
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
    theme_css: str = "",
    generated_at: datetime | None = None,
) -> str:
    """The meeting's outcome as one self-contained HTML slide deck.

    ``theme_css`` is appended after the built-in stylesheet so an operator theme
    overrides it by ordinary cascade rather than by fighting specificity. It is
    validated by ``parse_deck_theme`` first; an unthemed render is byte-identical
    to one from before themes existed, which is what keeps a re-rendered deck's
    content hash stable.
    """
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
    slides.append(
        _slide(
            kind="agree",
            eyebrow="Where the meeting agreed",
            title="Agreed",
            body=_list_body(
                _bullets(consensus.agreements) if consensus is not None else [],
                empty="The chair recorded no agreement.",
            ),
        )
    )
    slides.append(
        _slide(
            kind="contest",
            eyebrow="Where the meeting split",
            title="Contested",
            body=(_disagreement_body(consensus) if consensus is not None else '<p class="empty">The chair reported no structured consensus, so nothing can be shown here without inventing it. The written synthesis is the record.</p>'),
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
                body=cards + free_text + (_list_body(remaining, empty="") if remaining else ""),
            )
        )
    if summary:
        paragraphs = "".join(f"<p>{html.escape(part.strip())}</p>" for part in summary.split("\n") if part.strip())
        slides.append(_slide(kind="synthesis", eyebrow="The chair's synthesis", title="Where this lands", body=paragraphs))
    limitations = _bullets(list(chair.get("limitations") or []))
    if limitations:
        slides.append(_slide(kind="limits", eyebrow="Read the synthesis against these", title="Limitations", body=_list_body(limitations, empty="")))
    if surface_mode == "stage_review":
        normalized_stage = (stage or "design").strip().lower()
        slides.append(
            _slide(
                kind="review",
                eyebrow="Human gate",
                title=f"Review the {normalized_stage.title()}",
                body=(
                    _review_controls(consensus, transition_gate)
                    if normalized_stage == "design"
                    else _stage_review_controls(normalized_stage, transition_gate)
                ),
            )
        )
    next_actions = _bullets(list(chair.get("recommended_next_actions") or []))
    footer = f'<p class="stamp">Full review package: {html.escape(package_path)}</p>' if package_path else ""
    slides.append(
        _slide(
            kind="next",
            eyebrow="Recommended, not decided",
            title="Next",
            body=_list_body(next_actions, empty="The chair recommended no next action.") + '<p class="gate">Nothing here approves anything merely by opening the deck. Use the Human gate slide to submit and record a verdict.</p>' + footer,
        )
    )

    theme = parse_deck_theme(theme_css)
    return _DECK_TEMPLATE.format(
        title=html.escape(f"{cycle_title or 'Design meeting'} — {stage_title}"),
        slides="".join(slides),
        count=len(slides),
        bridge=_bridge_script_for_stage(surface_id, stage),
        theme=f"\n<style data-deck-theme>\n{theme.css}\n</style>" if theme.accepted else "",
    )


_DECK_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #ffffff; --fg: #16181d; --muted: #5d6470; --line: #e3e6ec;
    --accent: #1f6feb; --warn: #b45309; --card: #f7f8fa;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #101216; --fg: #e8eaef; --muted: #9aa2b1; --line: #262b33;
      --accent: #6ea8ff; --warn: #f0b45e; --card: #171a20;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--fg);
    font: 16px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  }}
  .deck {{ height: 100vh; display: flex; align-items: center; justify-content: center; padding: 3rem 2rem 4.5rem; }}
  .slide {{ display: none; width: min(60rem, 100%); max-height: 100%; overflow-y: auto; }}
  .slide.is-active {{ display: block; animation: in .18s ease-out; }}
  @keyframes in {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: none; }} }}
  .eyebrow {{ margin: 0 0 .35rem; color: var(--muted); font-size: .8rem; letter-spacing: .09em; text-transform: uppercase; }}
  h2 {{ margin: 0 0 1.25rem; font-size: clamp(1.7rem, 3.6vw, 2.7rem); line-height: 1.15; letter-spacing: -.02em; }}
  .slide--title h2 {{ font-size: clamp(2.1rem, 5vw, 3.4rem); }}
  .lede {{ font-size: 1.15rem; color: var(--muted); max-width: 42rem; }}
  .stamp {{ color: var(--muted); font-size: .82rem; margin-top: 1.75rem; word-break: break-all; }}
  ul {{ margin: 0; padding-left: 1.15rem; }}
  li {{ margin: .55rem 0; font-size: 1.08rem; }}
  .empty {{ color: var(--muted); font-style: italic; }}
  .contested-grid {{ display: grid; gap: .9rem; }}
  .contested {{ border: 1px solid var(--line); border-radius: 10px; padding: .9rem 1.05rem; background: var(--card); }}
  .contested h3 {{ margin: 0 0 .5rem; font-size: 1.05rem; }}
  .positions {{ color: var(--muted); }}
  .positions li {{ font-size: .96rem; margin: .3rem 0; }}
  .verdict {{ margin: .6rem 0 0; font-size: .95rem; }}
  .verdict span {{ display: inline-block; margin-right: .5rem; padding: .08rem .5rem; border-radius: 999px; font-size: .74rem;
    letter-spacing: .05em; text-transform: uppercase; border: 1px solid currentColor; }}
  .verdict--open {{ color: var(--warn); }}
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
  .option-label {{ font-weight: 600; font-size: 1.02rem; }}
  .option-value {{ color: var(--fg); font-size: .97rem; }}
  .option-detail {{ color: var(--muted); font-size: .9rem; }}
  /* Not colour alone: the badge keeps its border and text in every theme and in
     print, where an accent tint is the first thing to disappear. */
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
    color: var(--muted); font-size: .88rem; }}
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
    .decision input, .review input {{ display: none; }}
    .bar {{ display: none; }}
  }}
</style>{theme}
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
