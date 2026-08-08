"""The structured result a stage worker must return (Phase 6).

The design states the constraint plainly: "free-form text alone cannot satisfy a
stage contract". That is not a formatting preference. A stage attempt ends in a
human review, and a reviewer asked to approve a paragraph has no way to tell
which claims are backed by which artifact, what the worker could not do, or
whether it ran out of budget halfway and summarised anyway. This boundary keeps
those semantic guarantees strict while normalizing lossless JSON variations —
field aliases, exact boolean spellings, and equivalent path objects — so a
model's envelope choice does not discard work whose meaning is unambiguous.

``stop_reason`` matters as much as ``status``. ``SubagentExecutor`` reports a
turn, token, or loop cap as a *completed* run carrying a partial answer
(``stop_reason=token_capped`` and friends), so a stage that only read ``status``
would file a truncated investigation as finished work. :meth:`StageWorkerResult.
is_trustworthy` folds the two together so callers cannot forget.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from deerflow.dbtl.build_execution import BuildFigure, KeyOutcome, parse_figures, parse_key_outcomes
from deerflow.dbtl.consensus import Consensus, parse_consensus
from deerflow.dbtl.decision_request import DecisionRequest, parse_decision_request

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 4_000
MAX_ITEM_CHARS = 1_000
MAX_ITEMS = 50


class WorkerStatus(StrEnum):
    """How a worker's own account of its run ended."""

    COMPLETED = "completed"
    #: Ran cleanly but could not answer without something only a human has.
    NEEDS_INPUT = "needs_input"
    #: Ran cleanly and found a real obstacle in the data or the design.
    BLOCKED = "blocked"
    #: Did not run cleanly. Never satisfies a stage requirement.
    FAILED = "failed"


#: Guardrail caps ``SubagentExecutor`` reports on an otherwise-``completed`` run.
#: Listed here rather than imported so this module keeps no dependency on the
#: executor; ``tests/test_dbtl_worker_result.py`` pins the two together.
CAPPED_STOP_REASONS: frozenset[str] = frozenset({"token_capped", "turn_capped", "loop_capped"})


class WorkerResultRejected(ValueError):
    """A worker's output does not satisfy the structured contract."""


#: Where a piece of evidence lives. Module-level rather than a class attribute
#: because ``slots=True`` turns an annotated class attribute into a field and an
#: unannotated one into a descriptor — either way it stops being the constant it
#: looks like.
EVIDENCE_KINDS: frozenset[str] = frozenset({"artifact", "workspace_file", "dataset", "external"})


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A pointer from a claim to the thing that supports it.

    ``kind`` distinguishes an artifact the cycle already stores from a file in
    the project workspace or an external source, because the reviewer's next
    action differs: an artifact opens in the inspector, a workspace path is on
    disk, and an external reference has to be judged on its own.
    """

    kind: str
    reference: str
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            allowed = ", ".join(sorted(EVIDENCE_KINDS))
            raise WorkerResultRejected(f"Unknown evidence kind {self.kind!r}; expected one of: {allowed}")
        if not self.reference.strip():
            raise WorkerResultRejected("An evidence reference cannot be empty.")

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "reference": self.reference, "description": self.description}


@dataclass(frozen=True, slots=True)
class QualityCheck:
    """One thing the worker verified about its own output, and the verdict.

    Separate from ``claims`` on purpose: a claim is about the science, a quality
    check is about whether the work was done properly. Collapsing them is how a
    passing pipeline starts reading like a supported hypothesis.
    """

    name: str
    passed: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise WorkerResultRejected("A quality check needs a name.")

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class StageWorkerResult:
    """What one worker returns from one stage work unit."""

    status: WorkerStatus
    summary: str
    capability: str
    agent_name: str
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    claims: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    quality_checks: tuple[QualityCheck, ...] = ()
    recommended_next_actions: tuple[str, ...] = ()
    clarification_question: str | None = None
    stop_reason: str | None = None
    #: Server-collected provider usage for this worker. The worker cannot
    #: author or alter it; ``collect_results`` attaches it after validation.
    token_usage: Mapping[str, int] = field(default_factory=dict)
    #: Where the council converged and where it did not. Only a chair fills
    #: this in; every other worker leaves it ``None``. Optional rather than
    #: required because the contract is shared by all five stages, and a Build
    #: worker has no council to report on.
    consensus: Consensus | None = None
    #: The bounded choice behind ``clarification_question``, when the chair
    #: offered one. Always optional: a question that needs a number or an
    #: explanation has no options to offer, and a malformed one is dropped
    #: rather than allowed to take the question down with it.
    decision_request: DecisionRequest | None = None
    #: Figures and numeric outcomes this worker declared, when its stage asks
    #: for them. Optional and stage-specific for the same reason ``consensus``
    #: is: the contract is shared by all five stages, and a Design chair has no
    #: plot to declare. Both are *presentational* declarations layered on
    #: artifacts the contract already validated, so both parse fail-soft — a
    #: missing caption must not discard verified execution.
    figures: tuple[BuildFigure, ...] = ()
    key_outcomes: tuple[KeyOutcome, ...] = ()

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise WorkerResultRejected("A stage worker result needs a summary.")
        # A claim with nothing behind it is the exact shape this contract exists
        # to prevent: it reads as a finding and survives into the reviewer's
        # summary without ever naming what would have to be wrong for it to be
        # false.
        if self.claims and not self.evidence_refs:
            raise WorkerResultRejected("A result that makes claims must reference the evidence behind them.")
        if self.status is WorkerStatus.NEEDS_INPUT and not (self.clarification_question and self.clarification_question.strip()):
            raise WorkerResultRejected("A needs_input result must provide one non-empty 'clarification_question'.")
        # Options with no question behind them would render as a decision the
        # chair never asked for, and a person would answer it.
        if self.decision_request is not None and not (self.clarification_question and self.clarification_question.strip()):
            raise WorkerResultRejected("A decision request must accompany the question it offers options for.")

    @property
    def was_capped(self) -> bool:
        """Whether a guardrail budget cut the run short."""
        return self.stop_reason in CAPPED_STOP_REASONS

    @property
    def is_trustworthy(self) -> bool:
        """Whether this result may count toward a stage's required output.

        A capped run is excluded even when its ``status`` is ``completed``,
        because the worker stopped mid-investigation and then summarised what it
        had. Its content is still worth showing a reviewer — it is just not
        evidence that the work finished.
        """
        return self.status is WorkerStatus.COMPLETED and not self.was_capped

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "capability": self.capability,
            "agent_name": self.agent_name,
            "artifact_refs": list(self.artifact_refs),
            "evidence_refs": [item.as_dict() for item in self.evidence_refs],
            "claims": list(self.claims),
            "limitations": list(self.limitations),
            "provenance": dict(self.provenance),
            "quality_checks": [item.as_dict() for item in self.quality_checks],
            "recommended_next_actions": list(self.recommended_next_actions),
            "clarification_question": self.clarification_question,
            "stop_reason": self.stop_reason,
            "token_usage": dict(self.token_usage),
            "was_capped": self.was_capped,
            "is_trustworthy": self.is_trustworthy,
            # Omitted rather than serialized as null for the four workers in
            # five that have no council to report on: an explicit null in a
            # Build package invites a reader to wonder what went missing.
            **({"consensus": self.consensus.as_dict()} if self.consensus is not None else {}),
            **({"decision_request": self.decision_request.as_dict()} if self.decision_request is not None else {}),
            **({"figures": [item.as_dict() for item in self.figures]} if self.figures else {}),
            **({"key_outcomes": [item.as_dict() for item in self.key_outcomes]} if self.key_outcomes else {}),
        }


def _string_tuple(
    raw: object,
    field_name: str,
    *,
    object_text_fields: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        text = raw.strip()
        return (text[:MAX_ITEM_CHARS],) if text else ()
    if not isinstance(raw, Sequence):
        raise WorkerResultRejected(f"{field_name!r} must be a list of strings.")
    items: list[str] = []
    for entry in raw[:MAX_ITEMS]:
        if isinstance(entry, Mapping) and object_text_fields:
            candidates = {value.strip() for name in object_text_fields if isinstance((value := entry.get(name)), str) and value.strip()}
            if len(candidates) != 1:
                expected = ", ".join(repr(name) for name in object_text_fields)
                raise WorkerResultRejected(f"Each {field_name!r} object needs exactly one unambiguous text field: {expected}.")
            entry = next(iter(candidates))
        if not isinstance(entry, str):
            raise WorkerResultRejected(f"{field_name!r} must contain only strings.")
        text = entry.strip()
        if text:
            items.append(text[:MAX_ITEM_CHARS])
    return tuple(items)


def _artifact_tuple(
    raw: object,
    *,
    stage: str | None = None,
    capability: str = "",
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Normalize canonical paths or explicit named-path objects.

    The identifier is only an alias inside this one worker result; the server
    still validates and remaps the resulting workspace path later. Requiring
    a recognized ``path`` avoids stringifying arbitrary metadata objects into
    paths that look reviewable. ``id`` and ``name`` are optional local aliases;
    when supplied, they are equivalent only if they do not conflict.
    """
    if raw is None:
        return (), {}
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise WorkerResultRejected("'artifact_refs' must be a list of path strings or named path objects.")
    refs: list[str] = []
    aliases: dict[str, str] = {}
    for entry in raw[:MAX_ITEMS]:
        if isinstance(entry, str):
            path = entry.strip()
            alias = ""
        elif isinstance(entry, Mapping):
            raw_id = entry.get("id")
            raw_name = entry.get("name")
            raw_path = entry.get("path")
            # A Design chair sometimes preserves the evidence-reference shape
            # when copying a project file into ``artifact_refs``:
            # ``{"kind": "workspace_file", "reference": "/mnt/user-data/..."}``.
            # The locator has exactly the same meaning as ``path`` and the
            # server still validates the virtual path later.  Recover only that
            # one meaning-preserving shape, only for the chair, and only for a
            # canonical project-virtual path; logical ids and other stages stay
            # strict so this cannot manufacture an artifact from prose.
            raw_reference = entry.get("reference")
            if (not isinstance(raw_path, str) or not raw_path.strip()) and stage == "design" and capability == "design_council_chair" and isinstance(raw_reference, str) and raw_reference.strip().startswith("/mnt/user-data/"):
                raw_path = raw_reference
            artifact_id = raw_id.strip() if isinstance(raw_id, str) else ""
            artifact_name = raw_name.strip() if isinstance(raw_name, str) else ""
            if raw_id is not None and not artifact_id:
                raise WorkerResultRejected("An artifact reference 'id' must be a non-empty string when provided.")
            if raw_name is not None and not artifact_name:
                raise WorkerResultRejected("An artifact reference 'name' must be a non-empty string when provided.")
            if artifact_id and artifact_name and artifact_id != artifact_name:
                raise WorkerResultRejected("An artifact reference cannot provide conflicting 'id' and 'name' aliases.")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise WorkerResultRejected("Each artifact reference object must contain a non-empty string 'path'.")
            alias = (artifact_id or artifact_name)[:MAX_ITEM_CHARS]
            path = raw_path.strip()
        else:
            raise WorkerResultRejected("'artifact_refs' must contain only path strings or named path objects.")
        if path:
            bounded = path[:MAX_ITEM_CHARS]
            refs.append(bounded)
            if alias:
                if alias in aliases:
                    raise WorkerResultRejected(f"Duplicate artifact reference id {alias!r}.")
                aliases[alias] = bounded
    return tuple(dict.fromkeys(refs)), aliases


_CLAIM_TEXT_FIELDS = ("claim", "statement", "text")


def _claim_tuple(
    raw: object,
    *,
    artifact_aliases: Mapping[str, str] | None = None,
    evidence_ids: Mapping[str, EvidenceRef] | None = None,
    stage: str | None = None,
) -> tuple[tuple[str, ...], tuple[EvidenceRef, ...]]:
    """Normalize strings or explicitly named structured claims.

    Some models make the claim/evidence relationship more explicit than the
    requested flat string array. Rejecting the whole position for that
    information-preserving shape loses completed research over formatting.
    Only recognized textual fields are accepted; arbitrary mappings are never
    stringified into authoritative-looking claims.
    """
    if raw is None:
        return (), ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise WorkerResultRejected("'claims' must be a list of strings or named claim objects.")

    claims: list[str] = []
    nested_evidence: list[EvidenceRef] = []
    indexed_evidence = evidence_ids or {}
    for entry in raw[:MAX_ITEMS]:
        if isinstance(entry, str):
            text = entry.strip()
        elif isinstance(entry, Mapping):
            text = ""
            for field_name in _CLAIM_TEXT_FIELDS:
                candidate = entry.get(field_name)
                if isinstance(candidate, str) and candidate.strip():
                    text = candidate.strip()
                    break
            if not text:
                expected = ", ".join(repr(name) for name in _CLAIM_TEXT_FIELDS)
                raise WorkerResultRejected(f"Each claim object needs a recognized text field: {expected}.")

            # A structured claim may carry typed evidence beside it or link to
            # a named top-level evidence entry. IDs are resolved, never treated
            # as external evidence by implication.
            claim_evidence = entry.get("evidence_refs")
            if claim_evidence is None and stage == "build":
                claim_evidence = entry.get("evidence")
            if isinstance(claim_evidence, Mapping):
                nested_evidence.extend(_evidence_tuple([claim_evidence], artifact_aliases=artifact_aliases, stage=stage))
            elif isinstance(claim_evidence, Sequence) and not isinstance(claim_evidence, str):
                inline: list[Mapping[str, Any]] = []
                for evidence_item in claim_evidence:
                    if isinstance(evidence_item, str):
                        if stage == "build" and _is_build_workspace_path(evidence_item):
                            nested_evidence.append(EvidenceRef(kind="workspace_file", reference=evidence_item.strip()[:MAX_ITEM_CHARS]))
                        else:
                            evidence_id = evidence_item.strip()
                            resolved = indexed_evidence.get(evidence_id)
                            if not evidence_id or resolved is None:
                                raise WorkerResultRejected(f"Claim references unknown evidence id {evidence_id!r}.")
                            nested_evidence.append(resolved)
                    elif isinstance(evidence_item, Mapping):
                        inline.append(evidence_item)
                    else:
                        raise WorkerResultRejected("Claim evidence references must be evidence ids or typed evidence objects.")
                nested_evidence.extend(_evidence_tuple(inline, artifact_aliases=artifact_aliases, stage=stage))
            singular_evidence = entry.get("evidence_ref")
            if isinstance(singular_evidence, Mapping):
                nested_evidence.extend(_evidence_tuple([singular_evidence], artifact_aliases=artifact_aliases, stage=stage))
        else:
            raise WorkerResultRejected("'claims' must contain only strings or named claim objects.")
        if text:
            claims.append(text[:MAX_ITEM_CHARS])
    return tuple(claims), tuple(nested_evidence)


def _evidence_items(
    raw: object,
    *,
    artifact_aliases: Mapping[str, str] | None = None,
    stage: str | None = None,
) -> tuple[tuple[EvidenceRef, ...], dict[str, EvidenceRef]]:
    if raw is None:
        return (), {}
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise WorkerResultRejected("'evidence_refs' must be a list of objects.")
    refs: list[EvidenceRef] = []
    ids: dict[str, EvidenceRef] = {}
    aliases = artifact_aliases or {}
    for entry in raw[:MAX_ITEMS]:
        if stage == "build" and isinstance(entry, str) and _is_build_workspace_path(entry):
            refs.append(EvidenceRef(kind="workspace_file", reference=entry.strip()[:MAX_ITEM_CHARS]))
            continue
        if not isinstance(entry, Mapping):
            raise WorkerResultRejected("Each evidence reference must be an object with 'kind' and 'reference'.")
        raw_id = entry.get("id")
        evidence_id = raw_id.strip()[:MAX_ITEM_CHARS] if isinstance(raw_id, str) else ""
        if raw_id is not None and not evidence_id:
            raise WorkerResultRejected("An evidence reference 'id' must be a non-empty string when provided.")
        if evidence_id and evidence_id in ids:
            raise WorkerResultRejected(f"Duplicate evidence reference id {evidence_id!r}.")

        raw_kind = entry.get("kind")
        kind = raw_kind.strip() if isinstance(raw_kind, str) else ""
        raw_reference = entry.get("reference")
        reference = raw_reference.strip() if isinstance(raw_reference, str) else ""

        # Models often preserve more linkage than the compact contract asks
        # for. Normalize only explicit, unambiguous locator fields; an unknown
        # ``kind`` is still rejected by EvidenceRef and an arbitrary object is
        # never coerced into evidence.
        locators: list[tuple[str | None, str]] = []
        if reference:
            locators.append((None, reference))
        for field_name, inferred_kind in (
            ("path", "workspace_file"),
            ("artifact_ref", "artifact"),
            ("dataset_ref", "dataset"),
            ("external_ref", "external"),
            ("url", "external"),
        ):
            candidate = entry.get(field_name)
            if isinstance(candidate, str) and candidate.strip():
                locators.append((inferred_kind, candidate.strip()))
        if len(locators) > 1:
            distinct_references = {reference for _kind, reference in locators}
            inferred_kinds = {item_kind for item_kind, _reference in locators if item_kind is not None}
            if len(distinct_references) == 1 and len(inferred_kinds) <= 1:
                inferred = next(iter(inferred_kinds), None)
                locators = [(inferred, next(iter(distinct_references)))]
        if len(locators) != 1:
            raise WorkerResultRejected("Each evidence reference must provide exactly one locator: reference, path, artifact_ref, dataset_ref, external_ref, or url.")

        inferred_kind, reference = locators[0]
        if inferred_kind == "artifact":
            resolved = aliases.get(reference)
            if resolved:
                reference = resolved
                inferred_kind = "workspace_file"
        if inferred_kind is not None:
            if kind and kind != inferred_kind:
                if not (stage == "build" and inferred_kind == "workspace_file" and reference.startswith("/mnt/user-data/")):
                    raise WorkerResultRejected(f"Evidence kind {kind!r} conflicts with its {inferred_kind!r} locator.")
            kind = inferred_kind
        if stage == "build" and not kind and reference.startswith("/mnt/user-data/"):
            kind = "workspace_file"
        # Build workers create several concrete implementation file types and
        # models naturally label them by role (``manifest``, ``execution_log``,
        # ``test_suite``, ``implementation``) even though the shared contract
        # asks where evidence lives.  Losing a completed multi-minute Build over
        # that harmless vocabulary mismatch is worse than the mismatch itself.
        # Normalize only project-virtual file references, and only for Build;
        # unknown logical ids remain rejected and every other stage stays
        # strict.  Publication still validates containment, regular-file type,
        # bytes, and hashes before any result becomes governed evidence.
        if stage == "build" and kind not in EVIDENCE_KINDS and reference.startswith("/mnt/user-data/"):
            kind = "workspace_file"
        parsed = EvidenceRef(
            kind=kind,
            reference=reference[:MAX_ITEM_CHARS],
            description=str(entry.get("description", "") or "").strip()[:MAX_ITEM_CHARS],
        )
        refs.append(parsed)
        if evidence_id:
            ids[evidence_id] = parsed
    return tuple(refs), ids


def _evidence_tuple(
    raw: object,
    *,
    artifact_aliases: Mapping[str, str] | None = None,
    stage: str | None = None,
) -> tuple[EvidenceRef, ...]:
    refs, _ids = _evidence_items(raw, artifact_aliases=artifact_aliases, stage=stage)
    return refs


def _dedupe_evidence(refs: Sequence[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    return tuple(dict.fromkeys(refs))


def _quality_tuple(raw: object, *, stage: str | None = None) -> tuple[QualityCheck, ...]:
    if raw is None:
        return ()
    # Build workers frequently return the natural compact shape
    # ``{"tests_written": true, "simulation_executed": false}``.  That says
    # exactly as much as the requested list of named verdict objects, and Build
    # records those verdicts for Test rather than using them as a gate.  Keep
    # the stricter shared contract everywhere else, and do not coerce strings
    # or numbers into booleans.
    if stage == "build" and isinstance(raw, Mapping):
        normalized: list[dict[str, Any]] = []
        for raw_name, value in list(raw.items())[:MAX_ITEMS]:
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise WorkerResultRejected("A Build quality-check map must use non-empty string names.")
            if isinstance(value, bool) or (isinstance(value, str) and value.strip().lower() in {"true", "false"}):
                normalized.append({"name": raw_name, "passed": value})
            elif isinstance(value, Mapping):
                normalized.append({**value, "name": raw_name})
            else:
                raise WorkerResultRejected(f"Build quality check {raw_name!r} must be a boolean or an object.")
        raw = normalized
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise WorkerResultRejected("'quality_checks' must be a list of objects.")
    checks: list[QualityCheck] = []
    for entry in raw[:MAX_ITEMS]:
        if not isinstance(entry, Mapping):
            raise WorkerResultRejected("Each quality check must be an object with 'name' and 'passed'.")
        passed = entry.get("passed")
        raw_status = entry.get("status")
        normalized_status = raw_status.strip().lower() if isinstance(raw_status, str) else ""
        status_passed: bool | None = None
        if raw_status is not None:
            if normalized_status in {"passed", "pass"}:
                status_passed = True
            elif normalized_status in {"failed", "fail", "not_run", "not_completed", "skipped"}:
                status_passed = False
            else:
                raise WorkerResultRejected(f"Quality check {entry.get('name')!r} has an unknown 'status'.")
        if passed is None:
            passed = status_passed
        elif isinstance(passed, str) and passed.strip().lower() in {"true", "false"}:
            passed = passed.strip().lower() == "true"
        if isinstance(passed, bool) and status_passed is not None and passed is not status_passed:
            raise WorkerResultRejected(f"Quality check {entry.get('name')!r} reports conflicting 'passed' and 'status' verdicts.")
        if not isinstance(passed, bool):
            # Refused rather than coerced: a truthy string here would turn an
            # unanswered check into a passing one.
            raise WorkerResultRejected(f"Quality check {entry.get('name')!r} must report 'passed' as a boolean.")
        checks.append(
            QualityCheck(
                name=str(entry.get("name", "")).strip()[:MAX_ITEM_CHARS],
                passed=passed,
                detail=str(entry.get("detail", "") or "").strip()[:MAX_ITEM_CHARS],
            )
        )
    return tuple(checks)


def extract_result_payload(text: str) -> Mapping[str, Any]:
    """Pull the JSON object out of a worker's final message.

    Workers are prompted to answer with a single JSON object, but models add
    prose, a code fence, or a second object (an echoed manifest) often enough
    that refusing on the first stray character would fail runs that did the work
    correctly. So every top-level ``{...}`` object is parsed and the result one
    (the object carrying a ``status`` field, emitted last) is returned. If
    nothing parses, the result is rejected rather than salvaged, because a
    partially recovered result is worse than none.
    """
    if not isinstance(text, str) or not text.strip():
        raise WorkerResultRejected("The worker returned no output.")
    stripped = text.strip()
    # Scan every '{' with raw_decode so trailing prose, a code fence, or a
    # second brace-bearing chunk (e.g. an echoed manifest) can't turn a correct
    # result into an "Extra data" rejection. The result object is the one that
    # carries a 'status' field; workers emit it last, so we scan right-to-left.
    decoder = json.JSONDecoder()
    objects: list[Mapping[str, Any]] = []
    last_error: json.JSONDecodeError | None = None
    index = 0
    while True:
        brace = stripped.find("{", index)
        if brace == -1:
            break
        try:
            parsed, offset = decoder.raw_decode(stripped, brace)
        except json.JSONDecodeError as exc:
            last_error = exc
            index = brace + 1
            continue
        if isinstance(parsed, Mapping):
            objects.append(parsed)
        index = offset
    if not objects:
        if last_error is not None:
            raise WorkerResultRejected(f"The worker's structured result is not valid JSON: {last_error.msg}") from last_error
        raise WorkerResultRejected("The worker returned prose instead of a structured result.")
    for candidate in reversed(objects):
        if "status" in candidate:
            return candidate
    return objects[-1]


def _render_summary_mapping(summary: Mapping[str, Any]) -> str:
    """Render a Build worker's metrics object as one readable line per field.

    Rendered rather than JSON-dumped because a reviewer reads this: the values
    are the worker's own, and nothing is added, dropped, or reinterpreted.
    """
    lines: list[str] = []
    for key, value in summary.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(value, Mapping) or (isinstance(value, Sequence) and not isinstance(value, (str, bytes))):
            rendered = json.dumps(value, ensure_ascii=False, default=str)
        else:
            rendered = str(value)
        lines.append(f"{name}: {rendered}")
    return "\n".join(lines)


def _summary_text(summary: Any, *, stage: str | None) -> str:
    """The worker's summary as prose, or a refusal that says what was wrong.

    Build alone accepts a mapping, for the same reason it already accepts
    descriptive artifact `kind` labels and a compact boolean `quality_checks`
    map: a validation phase that passed every check and reported its findings as
    an object is real work, and discarding it costs far more than rendering it.
    The compatibility cannot manufacture anything -- a summary carries no claim,
    check, or evidence reference, so the worst a rendered one can do is read
    like machine output.

    Every other stage still requires prose. And the refusal distinguishes an
    absent summary from a wrongly typed one, because reporting a field that is
    plainly present as missing sends a reader looking for the wrong thing.
    """
    if isinstance(summary, str):
        return summary
    if summary is None:
        raise WorkerResultRejected("The worker's result must report a 'summary'.")
    if stage == "build" and isinstance(summary, Mapping):
        rendered = _render_summary_mapping(summary)
        if rendered:
            return rendered
        raise WorkerResultRejected("The worker's 'summary' object was empty; report what the phase found.")
    raise WorkerResultRejected(f"The worker's 'summary' must be text, but it was a {type(summary).__name__}.")


_BUILD_FIELD_ALIASES: Mapping[str, tuple[str, ...]] = {
    "summary": ("headline", "result", "rationale", "message"),
    "artifact_refs": ("artifacts", "outputs", "files"),
    "evidence_refs": ("evidence",),
    "claims": ("findings",),
    "limitations": ("caveats",),
    "quality_checks": ("checks", "validation_checks"),
    "recommended_next_actions": ("next_actions", "recommendations"),
}

_STATUS_ALIASES: Mapping[str, WorkerStatus] = {
    "complete": WorkerStatus.COMPLETED,
    "done": WorkerStatus.COMPLETED,
    "ok": WorkerStatus.COMPLETED,
    "passed": WorkerStatus.COMPLETED,
    "success": WorkerStatus.COMPLETED,
    "succeeded": WorkerStatus.COMPLETED,
    "awaiting_input": WorkerStatus.NEEDS_INPUT,
    "needs_clarification": WorkerStatus.NEEDS_INPUT,
    "waiting_for_input": WorkerStatus.NEEDS_INPUT,
    "error": WorkerStatus.FAILED,
    "errored": WorkerStatus.FAILED,
    "failure": WorkerStatus.FAILED,
    "incomplete": WorkerStatus.FAILED,
}


_BUILD_RELATIVE_WORKSPACE_DIRS = frozenset({"artifacts", "config", "logs", "src", "tests"})


def _is_build_workspace_path(value: object) -> bool:
    """Whether a Build string unambiguously names a workspace path.

    Absolute project-virtual references and grant-relative paths rooted in the
    server-created Build directories are locators. Arbitrary prose remains
    prose and cannot be promoted into evidence by this compatibility layer.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.startswith("/mnt/user-data/"):
        return True
    if text.startswith("/") or "://" in text:
        return False
    path = PurePosixPath(text)
    return not path.is_absolute() and ".." not in path.parts and bool(path.parts) and (path.parts[0] in _BUILD_RELATIVE_WORKSPACE_DIRS or path.as_posix() == "README.md")


def _compatible_payload(payload: Mapping[str, Any], *, stage: str | None) -> dict[str, Any]:
    """Normalize only variations that preserve the worker's reported meaning.

    Build is the long-running stage where throwing away a valid result is most
    expensive, so its common field-name aliases are accepted. Status aliases
    are exact vocabulary mappings for every stage. A missing Build status is
    inferred only from the server-required phase completion check (or from an
    explicit clarification question); prose, artifacts, or token spend alone
    can never manufacture completion.
    """
    normalized = dict(payload)
    if stage == "build":
        for canonical, aliases in _BUILD_FIELD_ALIASES.items():
            if canonical in normalized:
                continue
            for alias in aliases:
                if alias in normalized:
                    normalized[canonical] = normalized[alias]
                    break

        phase = normalized.get("phase")
        if isinstance(phase, Mapping):
            if normalized.get("status") is None and isinstance(phase.get("status"), str):
                normalized["status"] = phase["status"]
            if normalized.get("summary") is None:
                for field_name in ("decision_reason", "summary", "reason"):
                    candidate = phase.get(field_name)
                    if isinstance(candidate, str) and candidate.strip():
                        normalized["summary"] = candidate
                        break

        # A Build failure may report the scratch tree as a structured map.
        # Only `created` paths are real partial artifacts; required-but-missing
        # and bound-input sections are deliberately not promoted to outputs.
        raw_artifacts = normalized.get("artifact_refs")
        if isinstance(raw_artifacts, Mapping):
            created = raw_artifacts.get("created")
            if isinstance(created, Mapping):
                normalized["artifact_refs"] = [{"name": str(name), "path": path} for name, path in created.items() if isinstance(name, str) and name.strip() and isinstance(path, str) and path.strip()]

    raw_status = normalized.get("status")
    if isinstance(raw_status, str):
        status_alias = _STATUS_ALIASES.get(raw_status.strip().lower())
        if status_alias is not None:
            normalized["status"] = status_alias.value
    elif raw_status is None and stage == "build":
        question = normalized.get("clarification_question")
        if isinstance(question, str) and question.strip():
            normalized["status"] = WorkerStatus.NEEDS_INPUT.value
        else:
            try:
                checks = _quality_tuple(normalized.get("quality_checks"), stage="build")
            except WorkerResultRejected:
                checks = ()
            completion = next((item for item in checks if item.name == "phase_done_condition"), None)
            if completion is not None:
                normalized["status"] = (WorkerStatus.COMPLETED if completion.passed else WorkerStatus.FAILED).value

    # Some failed Build reports use `evidence` for a nested diagnostic object,
    # not a list of evidence locators. It cannot satisfy a stage either way;
    # retain the failure summary/limitations and discard claims that cannot be
    # evidence-bound rather than misreporting this as a schema crash.
    if stage == "build" and normalized.get("status") == WorkerStatus.FAILED.value and isinstance(normalized.get("evidence_refs"), Mapping):
        normalized["evidence_refs"] = []
        normalized["claims"] = []
    return normalized


def parse_worker_result(
    payload: Mapping[str, Any],
    *,
    capability: str,
    agent_name: str,
    stop_reason: str | None = None,
    stage: str | None = None,
) -> StageWorkerResult:
    """Validate one worker's structured output.

    ``capability``, ``agent_name``, and ``stop_reason`` come from the dispatcher,
    not from the payload: they are facts about the run that the worker is not
    the authority on, and accepting a worker's own account of which capability
    it exercised would let a selection failure look like a satisfied
    requirement.
    """
    payload = _compatible_payload(payload, stage=stage)
    raw_status = payload.get("status")
    if not isinstance(raw_status, str):
        raise WorkerResultRejected("The worker's result must report a 'status'.")
    try:
        status = WorkerStatus(raw_status.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in WorkerStatus)
        raise WorkerResultRejected(f"Unknown worker status {raw_status!r}; expected one of: {allowed}") from exc

    summary = _summary_text(payload.get("summary"), stage=stage)

    provenance = payload.get("provenance") or {}
    if not isinstance(provenance, Mapping):
        raise WorkerResultRejected("'provenance' must be an object.")

    raw_clarification = payload.get("clarification_question")
    if raw_clarification is not None and not isinstance(raw_clarification, str):
        raise WorkerResultRejected("'clarification_question' must be a string.")

    artifact_refs, artifact_aliases = _artifact_tuple(
        payload.get("artifact_refs"),
        stage=stage,
        capability=capability,
    )
    top_level_evidence, evidence_ids = _evidence_items(
        payload.get("evidence_refs"),
        artifact_aliases=artifact_aliases,
        stage=stage,
    )
    claims, claim_evidence = _claim_tuple(
        payload.get("claims"),
        artifact_aliases=artifact_aliases,
        evidence_ids=evidence_ids,
        stage=stage,
    )
    evidence_refs = _dedupe_evidence((*top_level_evidence, *claim_evidence))

    clarification_question = raw_clarification.strip()[:MAX_ITEM_CHARS] if isinstance(raw_clarification, str) else None
    consensus = parse_consensus(payload.get("consensus"))
    # A Design chair cannot call the meeting complete while its structured
    # consensus still names a decision only the owner can make.  Normalize that
    # contradiction into the existing paused-chair protocol so no incomplete
    # Design package can become reviewable merely because the prose says
    # "completed".  Existing durable packages remain readable; new meetings
    # stop and ask before the human review gate.
    if status is WorkerStatus.COMPLETED and capability == "design_council_chair" and consensus is not None and consensus.has_unresolved:
        unresolved = next(iter(consensus.open_questions), "")
        if not unresolved:
            unresolved = next((item.topic for item in consensus.disagreements if not item.resolved), "")
        status = WorkerStatus.NEEDS_INPUT
        clarification_question = clarification_question or unresolved[:MAX_ITEM_CHARS] or "What owner decision is needed to complete this Design?"
    # Only a paused chair is asking, so only a paused chair may offer options.
    # A completed result carrying them is describing a decision already taken.
    decision_request = None
    if status is WorkerStatus.NEEDS_INPUT and clarification_question:
        decision = parse_decision_request(payload.get("decision_request"), question=clarification_question)
        if decision.refusal:
            # Deliberately not fatal: the question survives and the deck falls
            # back to free text. Logged so a reviewer can tell a chair that
            # offered no options from one whose options were rejected.
            logger.warning("Discarding a malformed decision request from %s: %s", agent_name, decision.refusal)
        decision_request = decision.request

    return StageWorkerResult(
        status=status,
        summary=summary.strip()[:MAX_SUMMARY_CHARS],
        capability=capability,
        agent_name=agent_name,
        artifact_refs=artifact_refs,
        evidence_refs=evidence_refs,
        claims=claims,
        limitations=_string_tuple(
            payload.get("limitations"),
            "limitations",
            object_text_fields=(("item", "limitation", "text", "description", "detail") if stage == "build" else ()),
        ),
        provenance=dict(provenance),
        quality_checks=_quality_tuple(payload.get("quality_checks"), stage=stage),
        recommended_next_actions=_string_tuple(payload.get("recommended_next_actions"), "recommended_next_actions"),
        clarification_question=clarification_question,
        stop_reason=stop_reason,
        # Permissive: a malformed consensus costs the structured view, not the
        # result. The chair's prose summary is still the binding synthesis, and
        # rejecting a whole Design attempt over a misshapen sub-object would
        # trade the thing that works for the thing that reads nicely.
        consensus=consensus,
        decision_request=decision_request,
        figures=parse_figures(payload.get("figures")),
        key_outcomes=parse_key_outcomes(payload.get("key_outcomes")),
    )


def failed_result(*, capability: str, agent_name: str, reason: str, stop_reason: str | None = None) -> StageWorkerResult:
    """A result standing in for a worker that did not produce one.

    A failed worker still has to appear in the stage's record. Dropping it would
    make a fan-out where two of three workers crashed look like a clean run with
    fewer workers, which is precisely the thing the reviewer needs to see.
    """
    return StageWorkerResult(
        status=WorkerStatus.FAILED,
        summary=reason.strip()[:MAX_SUMMARY_CHARS] or "The worker did not return a usable result.",
        capability=capability,
        agent_name=agent_name,
        stop_reason=stop_reason,
    )
