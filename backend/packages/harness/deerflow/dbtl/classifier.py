"""Deterministic request classifier for DBTL upgrade proposals (Phase 4).

This module answers one question about one message: *does this look like work
that needs a plan and a way to tell whether it worked?* It never creates,
advances, or reads a durable record — it only produces a structured opinion
that a human then accepts or rejects.

**DBTL is a way of working, not a vocabulary.** Design/Build/Test/Learn fits a
breeding experiment, but it fits a data pipeline, a benchmark, a refactor, or a
root-cause investigation just as well: each one needs a plan, produces
artifacts (data, code, figures, reports) along the way, and is finished only
when something confirms it worked. So the rules come in two layers — a
domain-neutral layer that recognizes that *shape*, and a breeding layer that
recognizes this deployment's subject matter. Neither layer is required; a
request that has the shape without the vocabulary is still a cycle, which is
the whole point of the split. The clarification questions follow the same
split, because asking a caching-layer job for its "target trait" reads as the
system not listening.

**Why the rules are deterministic.** Two reasons, and both are about the
review rather than about accuracy. Shadow-mode telemetry is only comparable
across runs if the same text classifies the same way every time, and the exit
review has to judge *why* a request was flagged — which means the evidence
must be an inspectable rule hit, not a model's summary of its own reasoning.
A model classifier can be layered on later behind ``ClassifierProtocol``; the
routing layer already evaluates every deterministic signal before it would be
consulted.

**Data work is cycle-worthy by policy.** A request that names structured data,
an analytical operation, or a tabular data artifact is a high-confidence DBTL
candidate even when it asks for only one plot, conversion, or explanation.
This deliberately overrides the otherwise conservative single-file and
single-artifact vetoes: losing the governed design/test path for data work is
more costly than offering the human a proposal they can decline. Ordinary
remains the default for requests without a data signal.
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
    """One matched signal, with the evidence that produced it.

    Text rules carry the literal matched span. Context priors carry a concise
    server-derived lifecycle fact, so the evaluation drawer can distinguish a
    textual signal from the reason its proposal prior was raised.
    """

    rule_id: str
    weight: float
    evidence: str


@dataclass(frozen=True, slots=True)
class ClassifierContext:
    """Durable lifecycle context that may raise a research proposal prior.

    ``None`` means the server could not establish a project fact. Unknown
    context never earns a prior; only affirmative, server-derived facts do.
    """

    is_new_conversation: bool = False
    project_cycle_count: int | None = None
    has_unfinished_cycles: bool | None = None


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


# ── Shared vocabulary ────────────────────────────────────────────────────
#
# Used by both rule layers and by the clarification fields, so "what counts as
# stating a test" is defined once. Two copies would drift, and the drift would
# show up as a request that the rules flag but the questions ignore.

# The verbs that open a *plan*, deliberately excluding "create"/"make"/"write".
# Those three open single-deliverable requests far more often than they open
# projects, and letting them anchor the plan-and-verify rule would turn "make a
# chart and check it renders" into a research record.
_PLAN_VERBS = r"plan|design|build|develop|implement|refactor|migrate|optimi[sz]e|analy[sz]e|investigate|benchmark|train|tune|automate"

# What "we will know it worked" sounds like when nobody says "validate".
_VERIFY_VERBS = r"test|verif|validat|evaluat|measur|benchmark|confirm|check|compar|reproduc"

# The strongest domain-neutral signal there is: a plan joined to the thing that
# will check it. This is Design→Test in one sentence.
_PLAN_AND_VERIFY_PATTERN = rf"\b(?:{_PLAN_VERBS})\w*\b[^.]{{0,140}}\b(?:and|then|,)\b[^.]{{0,80}}\b(?:{_VERIFY_VERBS})\w*"

# Naming what a result is checked *against* is the Test contract in one phrase,
# and it is the one thing an errand almost never says.
_CHECKED_AGAINST_PATTERN = rf"\b(?:{_VERIFY_VERBS})\w*\b[^.]{{0,40}}\b(?:against|versus|vs\.?|relative\s+to|baseline)\b"

_SUCCESS_CRITERIA_PATTERN = r"\b(?:success|acceptance|pass(?:ing)?)\s+criteri\w*|\bcriteri\w+\s+for\s+success\b|\bdefinition\s+of\s+done\b"

# Data work is intentionally a first-class policy signal rather than an
# accumulation of weak research vocabulary. The match covers structured data
# assets, tabular concepts, and common analytical operations. It avoids bare
# JSON because application/config JSON is usually ordinary software work;
# JSONL is included because it is conventionally a record-oriented dataset.
_DATA_TASK_PATTERN = (
    r"\b(?:data|datasets?|databases?|dataframes?|tabular|csv|tsv|spreadsheet|"
    r"workbooks?|worksheets?|parquet|jsonl|arrow|feather|rows?|columns?|"
    r"features?|predictors?|outcomes?|regressions?|correlations?|"
    r"cross[- ]validat\w*|predict(?:ion|ions|ive|or|ors|s|ed|ing)?|"
    r"phenotypes?|genotypes?|traits?|yield)\b"
    r"|(?:^|[/\\\s])[\w.-]+\.(?:csv|tsv|xlsx?|parquet|jsonl|arrow|feather)\b"
)

# 1 - exp(-1.25) ~= 0.7135, safely inside the HIGH band on this signal alone.
_DATA_TASK_RULES: tuple[tuple[str, str, float], ...] = (("intent.data_task", _DATA_TASK_PATTERN, 1.25),)

# ── Rule tables ──────────────────────────────────────────────────────────
#
# Each entry is (rule_id, pattern, weight). Weights are hand-set and are the
# thing the human exit review is expected to argue about; they are grouped by
# what the signal actually means so a reviewer can reason about a whole class
# at once.

_RESEARCH_INTENT_RULES: tuple[tuple[str, str, float], ...] = (
    # Verbs that describe producing knowledge rather than producing a file.
    ("intent.design", r"\bdesign(?:ing|ed)?\b", 0.34),
    # Accept the common "similate" misspelling without making the classifier
    # generally fuzzy or weakening the surrounding research-shape rules.
    ("intent.simulate", r"\b(?:simulat|similat)(?:e|es|ed|ing|ion)\b", 0.20),
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
    ("object.genotype_markers", r"\b(?:snps?|genotyp(?:e|es|ic)|genetic\s+markers?|marker\s+(?:matrix|data))\b", 0.16),
    ("object.phenotype_data", r"\bphenotyp(?:e|es|ic)\b", 0.14),
)

_SCOPE_RULES: tuple[tuple[str, str, float], ...] = (
    # Multi-step or multi-population scope — the thing a cycle exists to track.
    ("scope.conjunction", r"\b(?:design|evaluate|compare|benchmark|investigate)\b[^.]{0,80}\band\b[^.]{0,40}\bvalidat", 0.30),
    ("scope.across", r"\bacross\s+(?:\w+\s+){0,2}(?:environments?|sites?|seasons?|years?|populations?)\b", 0.24),
    ("scope.held_out", r"\bheld[- ]out\b|\bcross[- ]validat", 0.22),
    # Broadened past the four research verbs it started with: "then verify",
    # "then measure", and "then check" describe the same follow-on test, and a
    # rule that only recognized the research wording missed every engineering
    # request that has the same structure.
    ("scope.then", rf"\bthen\s+(?:{_VERIFY_VERBS})\w*\b", 0.24),
    (
        "scope.research_plan",
        r"\bdesign\b[^.]{0,100}\b(?:genomic[- ]selection|breeding|experiment(?:al)?)\b[^.]{0,60}\b(?:plan|workflow|strategy)\b",
        0.18,
    ),
    ("scope.simulated_test", r"\btest\b[^.]{0,80}\bsimulat(?:e|ed|ion)\b", 0.22),
    (
        "scope.genotype_phenotype_simulation",
        (
            r"\b(?:snps?|genotyp(?:e|es|ic)|genetic\s+markers?|marker\s+(?:matrix|data))\b"
            r"[^.]{0,180}\bphenotyp(?:e|es|ic)\b"
            r"|\bphenotyp(?:e|es|ic)\b[^.]{0,180}"
            r"\b(?:snps?|genotyp(?:e|es|ic)|genetic\s+markers?|marker\s+(?:matrix|data))\b"
        ),
        0.14,
    ),
)

# ── Domain-neutral rules ─────────────────────────────────────────────────
#
# The same signals as above, expressed without a subject matter. A request that
# fires only these is still a cycle: it has the DBTL shape.

_GENERAL_INTENT_RULES: tuple[tuple[str, str, float], ...] = (
    # Building a *system* rather than a file — the noun is what carries the
    # weight, so "create a chart" is untouched by this rule.
    ("intent.build_system", r"\b(?:build|develop|implement|create|set\s+up|stand\s+up)\b[^.]{0,60}\b(?:pipelines?|workflows?|systems?|services?|frameworks?|harness|dashboards?|tools?|end[- ]to[- ]end)\b", 0.30),
    ("intent.optimize", r"\boptimi[sz](?:e|es|ed|ing|ation)\b|\btun(?:e|es|ed|ing)\b", 0.22),
    ("intent.analyze", r"\banaly[sz](?:e|es|ed|ing|is|sis)\b", 0.22),
    ("intent.diagnose", r"\b(?:diagnos(?:e|es|ing|is)|troubleshoot(?:ing)?|root[- ]cause)\b", 0.26),
    ("intent.reproduce", r"\breproduc(?:e|es|ed|ing|ibility|ible)\b|\breplicat(?:e|es|ed|ing|ion)\b", 0.22),
    ("intent.quantify", r"\b(?:quantif(?:y|ies|ying)|profil(?:e|es|ing))\b", 0.20),
)

_GENERAL_OBJECT_RULES: tuple[tuple[str, str, float], ...] = (
    ("object.pipeline", r"\b(?:pipelines?|workflows?|end[- ]to[- ]end)\b", 0.16),
    ("object.metric", r"\b(?:latenc(?:y|ies)|throughput|accurac(?:y|ies)|precision|recall|error\s+rates?|runtimes?|memory\s+(?:use|usage|footprint)|performance)\b", 0.14),
    ("object.baseline", r"\bbaselines?\b", 0.16),
)

# One hit per *kind* of artifact the request would produce. Individually these
# are near-noise — every project mentions a figure eventually — but a request
# that names data and code and a figure is describing a body of work, not an
# errand. Keeping them separate rather than counting inside one rule is what
# lets each hit carry the literal span it matched, which the evaluation drawer
# needs; a synthetic "3 deliverables" hit would have no evidence to show.
_DELIVERABLE_RULES: tuple[tuple[str, str, float], ...] = (
    # Bare "data" is excluded on purpose: it appears in nearly every request in
    # a data-heavy project and would tip ordinary work over the line.
    ("deliverable.dataset", r"\bdata\s?sets?\b", 0.08),
    ("deliverable.code", r"\b(?:scripts?|codebase|modules?|packages?|notebooks?)\b", 0.08),
    ("deliverable.figure", r"\b(?:figures?|plots?|charts?|graphs?|visuali[sz]ations?)\b", 0.08),
    ("deliverable.table", r"\btables?\b", 0.08),
    ("deliverable.report", r"\b(?:reports?|write[- ]?ups?|manuscripts?|papers?)\b", 0.08),
    ("deliverable.model", r"\bmodels?\b", 0.08),
)

_GENERAL_SCOPE_RULES: tuple[tuple[str, str, float], ...] = (
    ("scope.plan_and_verify", _PLAN_AND_VERIFY_PATTERN, 0.36),
    ("scope.checked_against", _CHECKED_AGAINST_PATTERN, 0.28),
    ("scope.success_criteria", _SUCCESS_CRITERIA_PATTERN, 0.30),
    ("scope.multi_step", r"\bfirst\b[^.]{0,120}\bthen\b|\bstep\s*\d\b|\bend[- ]to[- ]end\b", 0.20),
    ("scope.iterate_until", r"\biterat\w+\s+until\b|\buntil\s+(?:it|they|the\s+\w+)\s+(?:pass\w*|work\w*|converge\w*)\b", 0.24),
)

# "make me a chart / plot / script" — one deliverable, named up front.
_MAKE_ARTIFACT_VERBS = r"create|make|draw|plot|generate|write|build|add"
_MAKE_ARTIFACT_NOUNS = r"chart|plot|graph|figure|table|script|file|report|note|summary|readme|csv|bar\s+plot"
_MAKE_ARTIFACT_PATTERN = rf"^\s*(?:please\s+)?(?:{_MAKE_ARTIFACT_VERBS})\s+(?:me\s+)?(?:a|an|the|some)?\s*(?:small\s+|simple\s+|quick\s+)?(?:{_MAKE_ARTIFACT_NOUNS})"

# One-slip misspellings of the question and read openers, enumerated the same
# narrow way "similate" is above. These rules veto, so a slip costs the veto and
# promotes a read-only question into cycle work — with a selected cycle that can
# spend a whole council's budget answering something the lead agent should have
# read out. Typos of a *negative* rule are therefore the safe direction to
# absorb: recognizing one can only make the classifier more conservative.
_EXPLAIN_TYPOS = r"explian|explaine|expalin|descrbie|descibe|desribe|summarise|summrise|summarize|waht|wat|hwat|wht|hwo|hwy|whi|whos|whne|wehre"
_READ_TYPOS = r"raed|read|opne|oepn|sohw|shwo|lsit|lits|dispaly|pritn|prnit|fnid|serach|sercha"

# Negative rules veto rather than subtract, because they identify a *request
# shape* (one artifact, one file, one answer) rather than a weak signal.
_ORDINARY_SHAPE_RULES: tuple[tuple[str, str], ...] = (
    ("ordinary.explain", r"^\s*(?:please\s+)?(?:explain|describe|summari[sz]e|what|why|how|who|when|where|" + _EXPLAIN_TYPOS + r")\b"),
    ("ordinary.read", r"^\s*(?:please\s+)?(?:read|open|show|list|display|print|cat|find|search|" + _READ_TYPOS + r")\b"),
    ("ordinary.file_edit", r"^\s*(?:please\s+)?(?:fix|rename|move|copy|delete|convert|format|refactor|clean\s+up|update)\b"),
    ("ordinary.make_artifact", _MAKE_ARTIFACT_PATTERN),
)

# A single strong phrase overrides an ordinary opening. "Design an experiment
# to test whether ..." starts with a make-artifact-ish verb but is unambiguously
# research, and "write a script ... then verify ..." is a plan with a test in it
# rather than the one-deliverable errand its opening resembles.
#
# An override only lifts the veto; the score still has to clear the decision
# threshold on its own. That is what makes this list safe to extend: "fix the
# path then check it runs" stops being vetoed and is then rejected on its
# merits, which is the correct outcome for both rules.
_OVERRIDE_RULES: tuple[tuple[str, str], ...] = (
    ("override.data_task", _DATA_TASK_PATTERN),
    ("override.design_experiment", r"\bdesign(?:ing)?\s+(?:a|an|the)?\s*\w*\s*experiment\b"),
    ("override.design_and_validate", r"\bdesign\b[^.]{0,60}\bvalidat"),
    ("override.test_whether", r"\btest(?:ing)?\s+whether\b"),
    ("override.plan_and_verify", _PLAN_AND_VERIFY_PATTERN),
    ("override.then_verify", rf"\bthen\s+(?:{_VERIFY_VERBS})\w*\b"),
    ("override.success_criteria", _SUCCESS_CRITERIA_PATTERN),
)

_DECISION_THRESHOLD = 0.45

# Context is deliberately weaker than explicit research language. It may move
# a borderline research request over the line, but cannot propose a cycle when
# the text produced no positive research signal at all.
_NEW_CONVERSATION_WEIGHT = 0.10
_NO_UNFINISHED_CYCLES_WEIGHT = 0.10
_NEW_PROJECT_WEIGHT = 0.08

# ── Clarification fields ─────────────────────────────────────────────────
#
# What a Design stage needs before it can start. Each is only requested when
# the request does not already answer it — asking for something the user just
# told you reads as the system not listening.
#
# Which set applies is decided by the subject matter, for the same reason: a
# caching-layer job asked for its "target trait" and its "season range" reads
# as a form built for somebody else. Both sets ask the same three questions
# underneath — what goes in, what comes out, how we will know it worked — and
# the breeding set names two of them in this deployment's own vocabulary.

# Deliberately NOT a bare "validate": saying you will validate is intent, not
# an expectation. The field is answered only by a concrete criterion — what it
# is checked against — which is what Design actually needs.
_VALIDATION_EXPECTATION_PATTERN = (
    r"\bheld[- ]out\b|\bcross[- ]validat|\bpreregister"
    r"|\bvalidat\w*\s+(?:on|against|across|using|with)\b"
    r"|\bindependent\s+(?:set|trial|environment)"
    rf"|{_CHECKED_AGAINST_PATTERN}"
    rf"|{_SUCCESS_CRITERIA_PATTERN}"
)

_BREEDING_CLARIFICATION_FIELDS: tuple[tuple[str, str], ...] = (
    ("target trait", r"\b(?:yield|biomass|height|flowering(?:\s+time)?|moisture|protein|starch|disease\s+resistance|drought\s+tolerance|grain\s+yield|trait\s+\w+)\b"),
    ("season range", r"\b(?:19|20)\d{2}\b|\bseasons?\b|\byears?\b"),
    ("validation expectation", _VALIDATION_EXPECTATION_PATTERN),
    ("population scope", r"\b(?:hybrids?|inbreds?|lines?|accessions?|populations?|panel)\b"),
)

_GENERIC_CLARIFICATION_FIELDS: tuple[tuple[str, str], ...] = (
    ("input data", r"\b(?:\S+\.(?:csv|tsv|json|parquet|xlsx?|txt|md|py|log)|data\s?sets?|logs?|tables?|databases?|apis?|endpoints?|files?|spreadsheets?|records?|corpus|samples?|repos?|repositor(?:y|ies))\b"),
    ("expected outputs", r"\b(?:figures?|plots?|charts?|tables?|reports?|dashboards?|data\s?sets?|models?|scripts?|notebooks?|artifacts?|outputs?|deliverables?)\b"),
    ("validation expectation", _VALIDATION_EXPECTATION_PATTERN),
)

# What makes this deployment's subject matter recognizable. Only used to pick
# which clarification vocabulary to ask in — it never contributes to the
# decision, so a wrong guess costs a differently-worded question and nothing
# more.
_BREEDING_DOMAIN_PATTERN = (
    r"\b(?:traits?|phenotyp\w*|genotyp\w*|snps?|genetic\s+markers?|breeding|germplasm|hybrids?|inbreds?"
    r"|heritabilit\w*|agronomic|cultivars?|accessions?|nursery|genomic\w*|alleles?|qtls?|gwas|pedigrees?"
    r"|harvest\w*|planting|seasons?)\b"
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


def _context_hits(context: ClassifierContext | None) -> list[RuleHit]:
    if context is None:
        return []

    hits: list[RuleHit] = []
    if context.is_new_conversation:
        hits.append(
            RuleHit(
                rule_id="context.new_conversation",
                weight=_NEW_CONVERSATION_WEIGHT,
                evidence="first user turn",
            )
        )
    if context.has_unfinished_cycles is False:
        hits.append(
            RuleHit(
                rule_id="context.no_unfinished_cycles",
                weight=_NO_UNFINISHED_CYCLES_WEIGHT,
                evidence="no unfinished DBTL cycles",
            )
        )
    if context.project_cycle_count == 0:
        hits.append(
            RuleHit(
                rule_id="context.new_project",
                weight=_NEW_PROJECT_WEIGHT,
                evidence="project has no DBTL cycles",
            )
        )
    return hits


def _band_for(confidence: float) -> ConfidenceBand:
    if confidence >= HIGH_BAND_THRESHOLD:
        return ConfidenceBand.HIGH
    if confidence >= MEDIUM_BAND_THRESHOLD:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def _clarification_fields(text: str) -> tuple[tuple[str, str], ...]:
    """Which vocabulary to ask this request's gaps in."""
    if re.search(_BREEDING_DOMAIN_PATTERN, text):
        return _BREEDING_CLARIFICATION_FIELDS
    return _GENERIC_CLARIFICATION_FIELDS


def _missing_fields(text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in _clarification_fields(text) if not re.search(pattern, text))


def _bounded_hits(hits: list[RuleHit], decisive: RuleHit | None) -> tuple[RuleHit, ...]:
    """Cap the hit list without discarding the rule that decided the outcome.

    A veto or override is the *reason* for the decision, and it is appended
    last, so a plain tail-truncation can drop exactly the evidence the
    evaluation drawer exists to show. Leading with it also reads better: the
    reason first, then what it outranked.
    """
    if decisive is None:
        return tuple(hits[:MAX_RULE_HITS])
    others = [hit for hit in hits if hit is not decisive]
    return (decisive, *others[: MAX_RULE_HITS - 1])


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


def classify_request(
    text: str,
    *,
    context: ClassifierContext | None = None,
) -> ClassifierResult:
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

    text_hits = [
        *_collect(_DATA_TASK_RULES, normalized),
        *_collect(_RESEARCH_INTENT_RULES, normalized),
        *_collect(_RESEARCH_OBJECT_RULES, normalized),
        *_collect(_SCOPE_RULES, normalized),
        *_collect(_GENERAL_INTENT_RULES, normalized),
        *_collect(_GENERAL_OBJECT_RULES, normalized),
        *_collect(_DELIVERABLE_RULES, normalized),
        *_collect(_GENERAL_SCOPE_RULES, normalized),
    ]
    hits = [*text_hits, *(_context_hits(context) if text_hits else [])]
    score = sum(hit.weight for hit in hits)
    # Saturating rather than clipping: the tenth weak signal should not read
    # as more certain than the third strong one.
    confidence = round(1.0 - pow(2.718281828459045, -score), 4)

    override = _matches_any(_OVERRIDE_RULES, normalized)
    ordinary_shape = _matches_any(_ORDINARY_SHAPE_RULES, normalized)

    decisive: RuleHit | None = None
    if ordinary_shape is not None and override is None:
        decisive = ordinary_shape
        hits.append(decisive)
        decision = ClassifierDecision.ORDINARY
        # The veto is a statement about this request's shape, not evidence
        # that research signals were absent — so the reported confidence is
        # the confidence in "ordinary", which the veto makes high.
        confidence = 0.0
    else:
        # Keep the request shape visible even when an override wins. Selected-
        # cycle routing uses this evidence to send read/explain questions to
        # ordinary chat instead of convening stage work; the same unscoped
        # request remains a data-cycle proposal because the override decides
        # the classifier outcome below.
        if ordinary_shape is not None:
            hits.append(ordinary_shape)
        if override is not None:
            decisive = RuleHit(rule_id=override.rule_id, weight=0.0, evidence=override.evidence)
            hits.append(decisive)
        decision = ClassifierDecision.PROPOSE_CYCLE if confidence >= _DECISION_THRESHOLD else ClassifierDecision.ORDINARY

    return ClassifierResult(
        decision=decision,
        confidence=confidence,
        band=_band_for(confidence),
        rule_hits=_bounded_hits(hits, decisive),
        missing_fields=_missing_fields(normalized) if decision is ClassifierDecision.PROPOSE_CYCLE else (),
        proposed_objective=objective,
        normalized_text=normalized,
    )
