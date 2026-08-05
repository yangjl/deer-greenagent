"""Pure Build accounting for approved Design deliverables."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from deerflow.dbtl.deliverables import DeliverableManifest, DeliverableSpec

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_EVIDENCE_ITEMS = 20
MAX_EVIDENCE_CHARS = 1_000
MAX_NOTES_CHARS = 2_000

BUILD_FULFILLMENT_CONTRACT = """Build deliverable accounting (required in provenance.deliverable_fulfillment):
Return version 1 and one item for every id in build_input_bundle.deliverable_manifest. Each item has deliverable_id, status
(delivered, attempted_failed, blocked, not_attempted, or not_applicable), artifacts, attempt_evidence, and notes. Artifacts use
the manifest's project-relative expected path; the server replaces content_hash with the hash of bytes it actually publishes.
Failed or blocked items require concrete attempt evidence. Do not omit an item or turn a failed attempt into prose."""


class BuildFulfillmentRejected(ValueError):
    """Build did not return one evidence-backed result per promised product."""


class FulfillmentStatus(StrEnum):
    DELIVERED = "delivered"
    ATTEMPTED_FAILED = "attempted_failed"
    BLOCKED = "blocked"
    NOT_ATTEMPTED = "not_attempted"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class ArtifactEvidence:
    path: str
    content_hash: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class BuildFulfillmentItem:
    deliverable_id: str
    status: FulfillmentStatus
    artifacts: tuple[ArtifactEvidence, ...]
    attempt_evidence: tuple[str, ...]
    notes: str

    def as_dict(self) -> dict[str, object]:
        return {
            "deliverable_id": self.deliverable_id,
            "status": self.status.value,
            "artifacts": [item.as_dict() for item in self.artifacts],
            "attempt_evidence": list(self.attempt_evidence),
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class BuildFulfillment:
    items: tuple[BuildFulfillmentItem, ...]

    @property
    def all_delivered(self) -> bool:
        return all(item.status is FulfillmentStatus.DELIVERED for item in self.items)

    @property
    def reviewable(self) -> bool:
        return all(item.status is not FulfillmentStatus.NOT_ATTEMPTED for item in self.items)

    def as_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "items": [item.as_dict() for item in self.items],
            "all_delivered": self.all_delivered,
            "reviewable": self.reviewable,
        }


def _required_text(raw: object, *, field: str, limit: int) -> str:
    if not isinstance(raw, str) or not (value := raw.strip()) or len(value) > limit:
        raise BuildFulfillmentRejected(f"{field} must be a nonempty string of at most {limit} characters.")
    return value


def parse_artifact_evidence(raw: object, *, expected_paths: tuple[str, ...]) -> tuple[ArtifactEvidence, ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) > MAX_EVIDENCE_ITEMS:
        raise BuildFulfillmentRejected("artifacts must be a bounded list.")
    artifacts: list[ArtifactEvidence] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise BuildFulfillmentRejected("Every artifact must bind a path and SHA-256 hash.")
        path = _required_text(value.get("path"), field="artifact path", limit=1_024)
        content_hash = _required_text(value.get("content_hash"), field="artifact content_hash", limit=64).lower()
        if path not in expected_paths or _SHA256.fullmatch(content_hash) is None:
            raise BuildFulfillmentRejected(f"Artifact {path!r} is not a hash-bound expected path.")
        if any(item.path == path for item in artifacts):
            raise BuildFulfillmentRejected(f"Artifact path {path!r} is duplicated.")
        artifacts.append(ArtifactEvidence(path=path, content_hash=content_hash))
    return tuple(artifacts)


def _attempt_evidence(raw: object) -> tuple[str, ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) > MAX_EVIDENCE_ITEMS:
        raise BuildFulfillmentRejected("attempt_evidence must be a bounded list of strings.")
    evidence = tuple(_required_text(value, field="attempt evidence", limit=MAX_EVIDENCE_CHARS) for value in raw)
    if len(set(evidence)) != len(evidence):
        raise BuildFulfillmentRejected("attempt_evidence cannot contain duplicates.")
    return evidence


def _parse_item(raw: object, *, spec: DeliverableSpec) -> BuildFulfillmentItem:
    if not isinstance(raw, Mapping):
        raise BuildFulfillmentRejected(f"Fulfillment for {spec.id!r} must be an object.")
    try:
        status = FulfillmentStatus(raw.get("status"))
    except (TypeError, ValueError) as exc:
        raise BuildFulfillmentRejected(f"Fulfillment for {spec.id!r} has an unknown status.") from exc
    artifacts = parse_artifact_evidence(raw.get("artifacts", []), expected_paths=spec.expected_paths)
    attempt_evidence = _attempt_evidence(raw.get("attempt_evidence", []))
    if status is FulfillmentStatus.DELIVERED and not artifacts:
        raise BuildFulfillmentRejected(f"Delivered item {spec.id!r} needs hash-bound artifact evidence.")
    if status in {FulfillmentStatus.ATTEMPTED_FAILED, FulfillmentStatus.BLOCKED} and not attempt_evidence:
        raise BuildFulfillmentRejected(f"{status.value} item {spec.id!r} needs attempt evidence.")
    return BuildFulfillmentItem(
        deliverable_id=spec.id,
        status=status,
        artifacts=artifacts,
        attempt_evidence=attempt_evidence,
        notes=_required_text(raw.get("notes"), field=f"notes for {spec.id}", limit=MAX_NOTES_CHARS),
    )


def parse_build_fulfillment(raw: object, *, manifest: DeliverableManifest) -> BuildFulfillment:
    """Validate one and only one Build result for every approved item."""

    if not isinstance(raw, Mapping) or raw.get("version") != 1:
        raise BuildFulfillmentRejected("Build fulfillment must be a version 1 object.")
    raw_items = raw.get("items")
    if isinstance(raw_items, (str, bytes)) or not isinstance(raw_items, Sequence):
        raise BuildFulfillmentRejected("Build fulfillment needs a structured items list.")

    specs = {item.id: item for item in manifest.deliverables}
    indexed: dict[str, Mapping[object, object]] = {}
    for item in raw_items:
        if not isinstance(item, Mapping) or not isinstance(item.get("deliverable_id"), str):
            raise BuildFulfillmentRejected("Every fulfillment item needs a deliverable_id.")
        item_id = item["deliverable_id"]
        if item_id not in specs:
            raise BuildFulfillmentRejected(f"Unknown deliverable id {item_id!r}.")
        if item_id in indexed:
            raise BuildFulfillmentRejected(f"Duplicate deliverable id {item_id!r}.")
        indexed[item_id] = item
    missing = [item_id for item_id in specs if item_id not in indexed]
    if missing:
        raise BuildFulfillmentRejected(f"Missing fulfillment for: {', '.join(missing)}.")
    return BuildFulfillment(items=tuple(_parse_item(indexed[item.id], spec=item) for item in manifest.deliverables))


def derive_build_fulfillment(
    manifest: DeliverableManifest,
    *,
    published: Sequence[Mapping[str, object]],
    declarations: Sequence[Mapping[str, object]],
) -> BuildFulfillment:
    """Bind worker accounting to the output bytes the server actually copied."""
    published_by_source: dict[str, ArtifactEvidence] = {}
    for item in published:
        source_path = str(item.get("source_path") or "").strip()
        content_hash = str(item.get("content_hash") or "").strip().lower()
        if source_path and _SHA256.fullmatch(content_hash):
            published_by_source[source_path] = ArtifactEvidence(path=source_path, content_hash=content_hash)

    declared_by_id = {str(item.get("deliverable_id") or ""): item for item in declarations if isinstance(item.get("deliverable_id"), str)}
    normalized: list[dict[str, object]] = []
    for spec in manifest.deliverables:
        artifacts = [published_by_source[path].as_dict() for path in spec.expected_paths if path in published_by_source]
        missing = [path for path in spec.expected_paths if path not in published_by_source]
        if not missing:
            normalized.append(
                {
                    "deliverable_id": spec.id,
                    "status": FulfillmentStatus.DELIVERED.value,
                    "artifacts": artifacts,
                    "attempt_evidence": [],
                    "notes": "All expected paths were published and hash-bound by the server.",
                }
            )
            continue

        raw = declared_by_id.get(spec.id) or {}
        status = str(raw.get("status") or "")
        if status not in {
            FulfillmentStatus.ATTEMPTED_FAILED.value,
            FulfillmentStatus.BLOCKED.value,
            FulfillmentStatus.NOT_ATTEMPTED.value,
            FulfillmentStatus.NOT_APPLICABLE.value,
        }:
            status = FulfillmentStatus.ATTEMPTED_FAILED.value if artifacts else FulfillmentStatus.NOT_ATTEMPTED.value
        evidence = raw.get("attempt_evidence")
        if status in {FulfillmentStatus.ATTEMPTED_FAILED.value, FulfillmentStatus.BLOCKED.value} and not evidence:
            evidence = [f"Published {len(artifacts)} expected path(s); missing: {', '.join(missing)}"]
        notes = str(raw.get("notes") or "").strip() or f"Expected path(s) not published: {', '.join(missing)}."
        normalized.append(
            {
                "deliverable_id": spec.id,
                "status": status,
                "artifacts": artifacts,
                "attempt_evidence": evidence if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)) else [],
                "notes": notes,
            }
        )
    return parse_build_fulfillment({"version": 1, "items": normalized}, manifest=manifest)
