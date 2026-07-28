"""The structured result a stage worker must return (Phase 6).

The design states the constraint plainly: "free-form text alone cannot satisfy a
stage contract". That is not a formatting preference. A stage attempt ends in a
human review, and a reviewer asked to approve a paragraph has no way to tell
which claims are backed by which artifact, what the worker could not do, or
whether it ran out of budget halfway and summarised anyway. Every one of those
is a separate field here, and a result missing them is *rejected* rather than
coerced — a half-parsed result that still reads fluently is the failure mode
worth preventing.

``stop_reason`` matters as much as ``status``. ``SubagentExecutor`` reports a
turn, token, or loop cap as a *completed* run carrying a partial answer
(``stop_reason=token_capped`` and friends), so a stage that only read ``status``
would file a truncated investigation as finished work. :meth:`StageWorkerResult.
is_trustworthy` folds the two together so callers cannot forget.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from deerflow.dbtl.consensus import Consensus, parse_consensus

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
    #: Where the council converged and where it did not. Only a chair fills
    #: this in; every other worker leaves it ``None``. Optional rather than
    #: required because the contract is shared by all five stages, and a Build
    #: worker has no council to report on.
    consensus: Consensus | None = None

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
            "was_capped": self.was_capped,
            "is_trustworthy": self.is_trustworthy,
            # Omitted rather than serialized as null for the four workers in
            # five that have no council to report on: an explicit null in a
            # Build package invites a reader to wonder what went missing.
            **({"consensus": self.consensus.as_dict()} if self.consensus is not None else {}),
        }


def _string_tuple(raw: object, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise WorkerResultRejected(f"{field_name!r} must be a list of strings.")
    items: list[str] = []
    for entry in raw[:MAX_ITEMS]:
        if not isinstance(entry, str):
            raise WorkerResultRejected(f"{field_name!r} must contain only strings.")
        text = entry.strip()
        if text:
            items.append(text[:MAX_ITEM_CHARS])
    return tuple(items)


def _evidence_tuple(raw: object) -> tuple[EvidenceRef, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise WorkerResultRejected("'evidence_refs' must be a list of objects.")
    refs: list[EvidenceRef] = []
    for entry in raw[:MAX_ITEMS]:
        if not isinstance(entry, Mapping):
            raise WorkerResultRejected("Each evidence reference must be an object with 'kind' and 'reference'.")
        refs.append(
            EvidenceRef(
                kind=str(entry.get("kind", "")).strip(),
                reference=str(entry.get("reference", "")).strip()[:MAX_ITEM_CHARS],
                description=str(entry.get("description", "") or "").strip()[:MAX_ITEM_CHARS],
            )
        )
    return tuple(refs)


def _quality_tuple(raw: object) -> tuple[QualityCheck, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise WorkerResultRejected("'quality_checks' must be a list of objects.")
    checks: list[QualityCheck] = []
    for entry in raw[:MAX_ITEMS]:
        if not isinstance(entry, Mapping):
            raise WorkerResultRejected("Each quality check must be an object with 'name' and 'passed'.")
        passed = entry.get("passed")
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
    prose or a code fence often enough that refusing on the first stray
    character would fail runs that did the work correctly. So the outermost
    ``{...}`` span is extracted — and if that does not parse, the result is
    rejected rather than salvaged, because a partially recovered result is worse
    than none.
    """
    if not isinstance(text, str) or not text.strip():
        raise WorkerResultRejected("The worker returned no output.")
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        raise WorkerResultRejected("The worker returned prose instead of a structured result.")
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise WorkerResultRejected(f"The worker's structured result is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, Mapping):
        raise WorkerResultRejected("The worker's structured result must be a JSON object.")
    return payload


def parse_worker_result(
    payload: Mapping[str, Any],
    *,
    capability: str,
    agent_name: str,
    stop_reason: str | None = None,
) -> StageWorkerResult:
    """Validate one worker's structured output.

    ``capability``, ``agent_name``, and ``stop_reason`` come from the dispatcher,
    not from the payload: they are facts about the run that the worker is not
    the authority on, and accepting a worker's own account of which capability
    it exercised would let a selection failure look like a satisfied
    requirement.
    """
    raw_status = payload.get("status")
    if not isinstance(raw_status, str):
        raise WorkerResultRejected("The worker's result must report a 'status'.")
    try:
        status = WorkerStatus(raw_status.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in WorkerStatus)
        raise WorkerResultRejected(f"Unknown worker status {raw_status!r}; expected one of: {allowed}") from exc

    summary = payload.get("summary")
    if not isinstance(summary, str):
        raise WorkerResultRejected("The worker's result must report a 'summary'.")

    provenance = payload.get("provenance") or {}
    if not isinstance(provenance, Mapping):
        raise WorkerResultRejected("'provenance' must be an object.")

    raw_clarification = payload.get("clarification_question")
    if raw_clarification is not None and not isinstance(raw_clarification, str):
        raise WorkerResultRejected("'clarification_question' must be a string.")

    return StageWorkerResult(
        status=status,
        summary=summary.strip()[:MAX_SUMMARY_CHARS],
        capability=capability,
        agent_name=agent_name,
        artifact_refs=_string_tuple(payload.get("artifact_refs"), "artifact_refs"),
        evidence_refs=_evidence_tuple(payload.get("evidence_refs")),
        claims=_string_tuple(payload.get("claims"), "claims"),
        limitations=_string_tuple(payload.get("limitations"), "limitations"),
        provenance=dict(provenance),
        quality_checks=_quality_tuple(payload.get("quality_checks")),
        recommended_next_actions=_string_tuple(payload.get("recommended_next_actions"), "recommended_next_actions"),
        clarification_question=(raw_clarification.strip()[:MAX_ITEM_CHARS] if isinstance(raw_clarification, str) else None),
        stop_reason=stop_reason,
        # Permissive: a malformed consensus costs the structured view, not the
        # result. The chair's prose summary is still the binding synthesis, and
        # rejecting a whole Design attempt over a misshapen sub-object would
        # trade the thing that works for the thing that reads nicely.
        consensus=parse_consensus(payload.get("consensus")),
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
