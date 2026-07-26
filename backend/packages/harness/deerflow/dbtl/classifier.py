"""Deterministic request classifier for DBTL upgrade proposals (Phase 4).

This module answers one question about one message: *does this look like
multi-step research work?* It never creates, advances, or reads a durable
record — it only produces a structured opinion that a human then accepts or
rejects.

**Why the rules are deterministic.** Two reasons, and both are about the
review rather than about accuracy. Shadow-mode telemetry is only comparable
across runs if the same text classifies the same way every time, and the exit
review has to judge *why* a request was flagged — which means the evidence
must be an inspectable rule hit, not a model's summary of its own reasoning.
A model classifier can be layered on later behind ``ClassifierProtocol``; the
routing layer already evaluates every deterministic signal before it would be
consulted.

**The errors are asymmetric**, so the thresholds are not symmetric either. A
false upgrade interrupts someone's ordinary file work with a card they did not
ask for, and it does so in a project where they will see it constantly. A
missed cycle costs one manual "Start a cycle" click. Ordinary is therefore the
default, and the negative rules are strong enough to veto a positive score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

# The objective is echoed back to the user on the card, so it stays short
# enough to read at a glance and to fit the durable cycle title field.
MAX_OBJECTIVE_LENGTH = 240

# A bounded number of hits keeps the evaluation drawer readable and stops a
# pathological input from producing an unbounded telemetry row.
MAX_RULE_HITS = 16

# Only the leading window of a message is scanned. A pasted logfile should not
# be able to out-vote the sentence the person actually wrote.
MAX_SCANNED_CHARS = 4000


class ClassifierDecision(StrEnum):
    """What the classifier believes about one request."""

    ORDINARY = "ordinary"
    PROPOSE_CYCLE = "propose_cycle"


class ConfidenceBand(StrEnum):
    """Coarse confidence, because a raw score invites false precision."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


HIGH_BAND_THRESHOLD = 0.7
MEDIUM_BAND_THRESHOLD = 0.4


@dataclass(frozen=True, slots=True)
class RuleHit:
    """One matched signal, with the span of text that matched it.

    ``evidence`` is the literal matched text rather than a description, so the
    reviewer in the evaluation drawer sees what the rule saw.
    """

    rule_id: str
    weight: float
    evidence: str


@dataclass(frozen=True, slots=True)
class ClassifierResult:
    """Structured classifier output. Carries no authority to act."""

    decision: ClassifierDecision
    confidence: float
    band: ConfidenceBand
    rule_hits: tuple[RuleHit, ...]
    missing_fields: tuple[str, ...]
    proposed_objective: str
    normalized_text: str = field(default="")


# ── Rule tables ──────────────────────────────────────────────────────────
#
# Each entry is (rule_id, pattern, weight). Weights are hand-set and are the
# thing the human exit review is expected to argue about; they are grouped by
# what the signal actually means so a reviewer can reason about a whole class
# at once.

_RESEARCH_INTENT_RULES: tuple[tuple[str, str, float], ...] = (
    # Verbs that describe producing knowledge rather than producing a file.
    ("intent.design", r"\bdesign(?:ing|ed)?\b", 0.34),
    ("intent.validate", r"\bvalidat(?:e|es|ing|ion)\b", 0.32),
    ("intent.evaluate", r"\bevaluat(?:e|es|ing|ion)\b", 0.26),
    ("intent.compare", r"\bcompar(?:e|es|ing|ison)\b", 0.24),
    ("intent.benchmark", r"\bbenchmark(?:s|ing|ed)?\b", 0.26),
    ("intent.investigate", r"\binvestigat(?:e|es|ing|ion)\b", 0.26),
    ("intent.test_hypothesis", r"\btest(?:ing)?\s+(?:whether|if|the\s+hypothesis)\b", 0.34),
    ("intent.determine", r"\bdetermine\s+(?:whether|if|which)\b", 0.28),
    ("intent.generalize", r"\bgeneraliz(?:e|es|ing|ation)\b", 0.24),
    ("intent.screen", r"\bscreen(?:ing)?\b", 0.18),
)

_RESEARCH_OBJECT_RULES: tuple[tuple[str, str, float], ...] = (
    # Nouns naming a research object. Individually weak — a breeding project
    # mentions these constantly — so they support a verb rather than fire alone.
    ("object.experiment", r"\bexperiment(?:s|al)?\b", 0.22),
    ("object.trial", r"\btrials?\b", 0.14),
    ("object.hypothesis", r"\bhypothes(?:is|es)\b", 0.24),
    ("object.model", r"\b(?:prediction|predictive|response|statistical)\s+models?\b", 0.20),
    ("object.genomic_selection", r"\bgenomic[- ]selection\b", 0.22),
    ("object.accuracy", r"\bpredictive\s+abilit(?:y|ies)\b", 0.16),
    ("object.population_structure", r"\bpopulation\s+structure\b", 0.20),
    ("object.environments", r"\benvironments?\b", 0.12),
    ("object.heritability", r"\bheritabilit(?:y|ies)\b", 0.18),
)

_SCOPE_RULES: tuple[tuple[str, str, float], ...] = (
    # Multi-step or multi-population scope — the thing a cycle exists to track.
    ("scope.conjunction", r"\b(?:design|evaluate|compare|benchmark|investigate)\b[^.]{0,80}\band\b[^.]{0,40}\bvalidat", 0.30),
    ("scope.across", r"\bacross\s+(?:\w+\s+){0,2}(?:environments?|sites?|seasons?|years?|populations?)\b", 0.24),
    ("scope.held_out", r"\bheld[- ]out\b|\bcross[- ]validat", 0.22),
    ("scope.then", r"\bthen\s+(?:validate|evaluate|compare|test)\b", 0.24),
    (
        "scope.research_plan",
        r"\bdesign\b[^.]{0,100}\b(?:genomic[- ]selection|breeding|experiment(?:al)?)\b[^.]{0,60}\b(?:plan|workflow|strategy)\b",
        0.18,
    ),
    ("scope.simulated_test", r"\btest\b[^.]{0,80}\bsimulat(?:e|ed|ion)\b", 0.22),
)

# "make me a chart / plot / script" — one deliverable, named up front.
_MAKE_ARTIFACT_VERBS = r"create|make|draw|plot|generate|write|build|add"
_MAKE_ARTIFACT_NOUNS = r"chart|plot|graph|figure|table|script|file|report|note|summary|readme|csv|bar\s+plot"
_MAKE_ARTIFACT_PATTERN = rf"^\s*(?:please\s+)?(?:{_MAKE_ARTIFACT_VERBS})\s+(?:me\s+)?(?:a|an|the|some)?\s*(?:small\s+|simple\s+|quick\s+)?(?:{_MAKE_ARTIFACT_NOUNS})"

# Negative rules veto rather than subtract, because they identify a *request
# shape* (one artifact, one file, one answer) rather than a weak signal.
_ORDINARY_SHAPE_RULES: tuple[tuple[str, str], ...] = (
    ("ordinary.explain", r"^\s*(?:please\s+)?(?:explain|describe|summari[sz]e|what|why|how|who|when|where)\b"),
    ("ordinary.read", r"^\s*(?:please\s+)?(?:read|open|show|list|display|print|cat|find|search)\b"),
    ("ordinary.file_edit", r"^\s*(?:please\s+)?(?:fix|rename|move|copy|delete|convert|format|refactor|clean\s+up|update)\b"),
    ("ordinary.make_artifact", _MAKE_ARTIFACT_PATTERN),
)

# A single strong research phrase overrides an ordinary opening. "Design an
# experiment to test whether ..." starts with a make-artifact-ish verb but is
# unambiguously research.
_OVERRIDE_RULES: tuple[tuple[str, str], ...] = (
    ("override.design_experiment", r"\bdesign(?:ing)?\s+(?:a|an|the)?\s*\w*\s*experiment\b"),
    ("override.design_and_validate", r"\bdesign\b[^.]{0,60}\bvalidat"),
    ("override.test_whether", r"\btest(?:ing)?\s+whether\b"),
)

_DECISION_THRESHOLD = 0.45

# ── Clarification fields ─────────────────────────────────────────────────
#
# What a Design stage needs before it can start. Each is only requested when
# the request does not already answer it — asking for something the user just
# told you reads as the system not listening.

_CLARIFICATION_FIELDS: tuple[tuple[str, str], ...] = (
    ("target trait", r"\b(?:yield|biomass|height|flowering(?:\s+time)?|moisture|protein|starch|disease\s+resistance|drought\s+tolerance|grain\s+yield|trait\s+\w+)\b"),
    ("season range", r"\b(?:19|20)\d{2}\b|\bseasons?\b|\byears?\b"),
    # Deliberately NOT a bare "validate": saying you will validate is intent,
    # not an expectation. The field is answered only by a concrete criterion —
    # what it is validated against — which is what Design actually needs.
    ("validation expectation", r"\bheld[- ]out\b|\bcross[- ]validat|\bpreregister|\bvalidat\w*\s+(?:on|against|across|using|with)\b|\bindependent\s+(?:set|trial|environment)"),
    ("population scope", r"\b(?:hybrids?|inbreds?|lines?|accessions?|populations?|panel)\b"),
)


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace, bounded to the leading window."""
    return re.sub(r"\s+", " ", text[:MAX_SCANNED_CHARS].strip().lower())


def _collect(rules: tuple[tuple[str, str, float], ...], text: str) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for rule_id, pattern, weight in rules:
        match = re.search(pattern, text)
        if match:
            hits.append(RuleHit(rule_id=rule_id, weight=weight, evidence=match.group(0)))
    return hits


def _matches_any(rules: tuple[tuple[str, str], ...], text: str) -> RuleHit | None:
    for rule_id, pattern in rules:
        match = re.search(pattern, text)
        if match:
            return RuleHit(rule_id=rule_id, weight=0.0, evidence=match.group(0))
    return None


def _band_for(confidence: float) -> ConfidenceBand:
    if confidence >= HIGH_BAND_THRESHOLD:
        return ConfidenceBand.HIGH
    if confidence >= MEDIUM_BAND_THRESHOLD:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def _missing_fields(text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in _CLARIFICATION_FIELDS if not re.search(pattern, text))


def _proposed_objective(original: str, normalized: str) -> str:
    """The user's own first sentence, trimmed — never a paraphrase.

    The card asks the user to confirm an objective. Rewriting their words into
    something they did not say is how a confirmation dialog stops being a real
    confirmation, so this only cuts and tidies.
    """
    source = original[:MAX_SCANNED_CHARS].strip() or normalized
    sentence = re.split(r"(?<=[.!?])\s+", source, maxsplit=1)[0].strip()
    sentence = re.sub(r"\s+", " ", sentence)
    if len(sentence) <= MAX_OBJECTIVE_LENGTH:
        return sentence
    return sentence[: MAX_OBJECTIVE_LENGTH - 1].rstrip() + "…"


def missing_clarification_fields(text: str) -> tuple[str, ...]:
    """Which Design fields *text* still leaves unanswered.

    Public because the supervisor's clarification branch needs this for a
    deterministically-routed request, where the classifier never ran and so
    produced no ``missing_fields``. Sharing one field list is the point: two
    copies would drift into asking for different things.
    """
    return _missing_fields(_normalize(text or ""))


def derive_objective(text: str) -> str:
    """The objective a confirmation card should show for *text*.

    Same reasoning as :func:`missing_clarification_fields` — a deterministic
    route still has to show the user an objective, and it must be the one the
    classifier path would have shown.
    """
    original = text or ""
    return _proposed_objective(original, _normalize(original))


def classify_request(text: str) -> ClassifierResult:
    """Classify one request. Pure, deterministic, and side-effect free."""
    normalized = _normalize(text or "")
    objective = _proposed_objective(text or "", normalized)

    if not normalized:
        return ClassifierResult(
            decision=ClassifierDecision.ORDINARY,
            confidence=0.0,
            band=ConfidenceBand.LOW,
            rule_hits=(),
            missing_fields=(),
            proposed_objective="",
            normalized_text=normalized,
        )

    hits = [
        *_collect(_RESEARCH_INTENT_RULES, normalized),
        *_collect(_RESEARCH_OBJECT_RULES, normalized),
        *_collect(_SCOPE_RULES, normalized),
    ]
    score = sum(hit.weight for hit in hits)
    # Saturating rather than clipping: the tenth weak signal should not read
    # as more certain than the third strong one.
    confidence = round(1.0 - pow(2.718281828459045, -score), 4)

    override = _matches_any(_OVERRIDE_RULES, normalized)
    ordinary_shape = None if override else _matches_any(_ORDINARY_SHAPE_RULES, normalized)

    if ordinary_shape is not None:
        hits.append(ordinary_shape)
        decision = ClassifierDecision.ORDINARY
        # The veto is a statement about this request's shape, not evidence
        # that research signals were absent — so the reported confidence is
        # the confidence in "ordinary", which the veto makes high.
        confidence = 0.0
    else:
        if override is not None:
            hits.append(RuleHit(rule_id=override.rule_id, weight=0.0, evidence=override.evidence))
        decision = ClassifierDecision.PROPOSE_CYCLE if confidence >= _DECISION_THRESHOLD else ClassifierDecision.ORDINARY

    return ClassifierResult(
        decision=decision,
        confidence=confidence,
        band=_band_for(confidence),
        rule_hits=tuple(hits[:MAX_RULE_HITS]),
        missing_fields=_missing_fields(normalized) if decision is ClassifierDecision.PROPOSE_CYCLE else (),
        proposed_objective=objective,
        normalized_text=normalized,
    )
