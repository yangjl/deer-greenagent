"""Pure, independent Test audit of promised deliverables."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from deerflow.dbtl.build_fulfillment import ArtifactEvidence, BuildFulfillmentRejected, parse_artifact_evidence
from deerflow.dbtl.deliverables import DeliverableManifest, DeliverableSpec

MAX_DETAIL_CHARS = 2_000

DELIVERABLE_AUDIT_CONTRACT = """Test deliverable audit (required in provenance.deliverable_audit):
Return {"version":1,"items":[...]} with one item for every id in build_input_bundle.deliverable_manifest. Each item uses the exact
fields deliverable_id, observed_artifacts, criteria, verdict, and notes. Each observed artifact uses path and content_hash. Each
criterion repeats the manifest text verbatim and uses criterion, verdict (pass, fail, or not_testable), and concrete detail.
Independently inspect every expected path from the server-owned Build lineage. Build prose is not evidence, and a missing or merely
attempted product must remain visible as fail or not_testable. The parser also accepts the equivalent Design-style aliases id,
sha256, acceptance_criteria, and status, but never mix aliases with contradictory values."""


class DeliverableAuditRejected(ValueError):
    """Test did not independently account for every promised product."""


class DeliverableAuditVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_TESTABLE = "not_testable"


class AuditCompleteness(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    NOT_TESTABLE = "not_testable"


@dataclass(frozen=True, slots=True)
class CriterionAudit:
    criterion: str
    verdict: DeliverableAuditVerdict
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"criterion": self.criterion, "verdict": self.verdict.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class DeliverableAuditItem:
    deliverable_id: str
    expected_paths: tuple[str, ...]
    observed_artifacts: tuple[ArtifactEvidence, ...]
    criteria: tuple[CriterionAudit, ...]
    verdict: DeliverableAuditVerdict
    notes: str

    def as_dict(self) -> dict[str, object]:
        return {
            "deliverable_id": self.deliverable_id,
            "expected_paths": list(self.expected_paths),
            "observed_artifacts": [item.as_dict() for item in self.observed_artifacts],
            "criteria": [item.as_dict() for item in self.criteria],
            "verdict": self.verdict.value,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class DeliverableAudit:
    items: tuple[DeliverableAuditItem, ...]

    @property
    def completeness(self) -> AuditCompleteness:
        if any(item.verdict is DeliverableAuditVerdict.FAIL for item in self.items):
            return AuditCompleteness.INCOMPLETE
        if any(item.verdict is DeliverableAuditVerdict.NOT_TESTABLE for item in self.items):
            return AuditCompleteness.NOT_TESTABLE
        return AuditCompleteness.COMPLETE

    def as_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "items": [item.as_dict() for item in self.items],
            "completeness": self.completeness.value,
        }


def _detail(raw: object, *, field: str) -> str:
    if not isinstance(raw, str) or not (value := raw.strip()) or len(value) > MAX_DETAIL_CHARS:
        raise DeliverableAuditRejected(f"{field} must be a nonempty bounded string.")
    return value


def _criteria(raw: object, *, spec: DeliverableSpec) -> tuple[CriterionAudit, ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise DeliverableAuditRejected(f"Audit for {spec.id!r} needs structured criteria.")
    indexed: dict[str, Mapping[object, object]] = {}
    for value in raw:
        if not isinstance(value, Mapping) or not isinstance(value.get("criterion"), str):
            raise DeliverableAuditRejected(f"Audit for {spec.id!r} has a malformed criterion.")
        if "verdict" not in value and "status" in value:
            value = {**value, "verdict": value.get("status")}
        criterion = value["criterion"]
        if criterion not in spec.acceptance_criteria or criterion in indexed:
            raise DeliverableAuditRejected(f"Audit for {spec.id!r} has an unknown or duplicate criterion.")
        indexed[criterion] = value
    if set(indexed) != set(spec.acceptance_criteria):
        raise DeliverableAuditRejected(f"Audit for {spec.id!r} is missing acceptance criteria.")

    checks: list[CriterionAudit] = []
    for criterion in spec.acceptance_criteria:
        value = indexed[criterion]
        try:
            verdict = DeliverableAuditVerdict(value.get("verdict"))
        except (TypeError, ValueError) as exc:
            raise DeliverableAuditRejected(f"Criterion {criterion!r} has an unknown verdict.") from exc
        checks.append(CriterionAudit(criterion=criterion, verdict=verdict, detail=_detail(value.get("detail"), field="criterion detail")))
    return tuple(checks)


def _parse_item(raw: object, *, spec: DeliverableSpec) -> DeliverableAuditItem:
    if not isinstance(raw, Mapping):
        raise DeliverableAuditRejected(f"Audit for {spec.id!r} must be an object.")
    raw_artifacts = raw.get("observed_artifacts", [])
    if isinstance(raw_artifacts, Sequence) and not isinstance(raw_artifacts, (str, bytes)):
        raw_artifacts = [({**item, "content_hash": item.get("sha256")} if isinstance(item, Mapping) and "content_hash" not in item and "sha256" in item else item) for item in raw_artifacts]
    try:
        observed = parse_artifact_evidence(raw_artifacts, expected_paths=spec.expected_paths)
    except BuildFulfillmentRejected as exc:
        raise DeliverableAuditRejected(str(exc)) from exc
    criteria = _criteria(raw.get("criteria", raw.get("acceptance_criteria")), spec=spec)
    observed_paths = {item.path for item in observed}
    if observed_paths != set(spec.expected_paths) or any(check.verdict is DeliverableAuditVerdict.FAIL for check in criteria):
        computed = DeliverableAuditVerdict.FAIL
    elif any(check.verdict is DeliverableAuditVerdict.NOT_TESTABLE for check in criteria):
        computed = DeliverableAuditVerdict.NOT_TESTABLE
    else:
        computed = DeliverableAuditVerdict.PASS
    try:
        declared = DeliverableAuditVerdict(raw.get("verdict"))
    except (TypeError, ValueError) as exc:
        raise DeliverableAuditRejected(f"Audit for {spec.id!r} has an unknown verdict.") from exc
    if declared is not computed:
        raise DeliverableAuditRejected(f"Audit verdict for {spec.id!r} contradicts its observed evidence.")
    return DeliverableAuditItem(
        deliverable_id=spec.id,
        expected_paths=spec.expected_paths,
        observed_artifacts=observed,
        criteria=criteria,
        verdict=computed,
        notes=_detail(raw.get("notes"), field=f"notes for {spec.id}"),
    )


def parse_deliverable_audit(raw: object, *, manifest: DeliverableManifest) -> DeliverableAudit:
    """Validate Test's typed audit and compute completeness from evidence."""

    if not isinstance(raw, Mapping) or raw.get("version") != 1:
        raise DeliverableAuditRejected("Deliverable audit must be a version 1 object.")
    raw_items = raw.get("items")
    if isinstance(raw_items, (str, bytes)) or not isinstance(raw_items, Sequence):
        raise DeliverableAuditRejected("Deliverable audit needs a structured items list.")
    specs = {item.id: item for item in manifest.deliverables}
    indexed: dict[str, Mapping[object, object]] = {}
    for item in raw_items:
        if not isinstance(item, Mapping):
            raise DeliverableAuditRejected("Every audit item needs a deliverable_id.")
        canonical_id = item.get("deliverable_id")
        alias_id = item.get("id")
        if canonical_id is not None and alias_id is not None and canonical_id != alias_id:
            raise DeliverableAuditRejected("Audit item id and deliverable_id disagree.")
        item_id = canonical_id if canonical_id is not None else alias_id
        if not isinstance(item_id, str):
            raise DeliverableAuditRejected("Every audit item needs a deliverable_id.")
        if item_id not in specs:
            raise DeliverableAuditRejected(f"Unknown deliverable id {item_id!r}.")
        if item_id in indexed:
            raise DeliverableAuditRejected(f"Duplicate deliverable id {item_id!r}.")
        indexed[item_id] = item
    missing = [item_id for item_id in specs if item_id not in indexed]
    if missing:
        raise DeliverableAuditRejected(f"Missing audit for: {', '.join(missing)}.")
    return DeliverableAudit(items=tuple(_parse_item(indexed[item.id], spec=item) for item in manifest.deliverables))
