"""Pure, content-addressable deliverable contracts for DBTL Design.

The manifest records products a person can inspect.  Runtime files such as the
machine rerun JSON belong to their existing contracts and are deliberately not
represented here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from deerflow.dbtl.cycle_state import CycleClass

MAX_DELIVERABLES = 10
MANIFEST_VERSION = 1
_STABLE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_NOTEBOOK_SUFFIXES = frozenset({".ipynb", ".rmd"})

DELIVERABLE_MANIFEST_CONTRACT = """Design deliverables (required in provenance.deliverable_manifest):
- Return version 1 and 1-10 human-facing products. The machine rerun JSON is infrastructure and is not one of these products.
- Each product needs a unique lowercase kebab-case id, title, kind, required boolean, project-relative expected_paths, acceptance_criteria, validation procedure, and required capabilities.
- Allowed kinds: figure, table, image, equation, software, notebook, report, dataset, other.
- For a computational cycle, include exactly one notebook deliverable whose expected path ends in .ipynb or .Rmd. It is the short human replay/playbook.
- Build must account for every item and Test will independently audit it, so promise only concrete products the cycle can attempt.
Shape:
{"version": 1, "cycle_class": "the cycle class from Project context", "deliverables": [
  {"id": "stable-id", "title": "Human-facing title", "kind": "figure|table|image|equation|software|notebook|report|dataset|other",
   "required": true, "expected_paths": ["outputs/example.ext"], "acceptance_criteria": ["observable criterion"],
   "validation": "how Test checks it", "capabilities": ["capability needed to build it"]}
]}"""


class DeliverableManifestRejected(ValueError):
    """The proposed Design deliverables do not form an executable contract."""


class DeliverableKind(StrEnum):
    FIGURE = "figure"
    TABLE = "table"
    IMAGE = "image"
    EQUATION = "equation"
    SOFTWARE = "software"
    NOTEBOOK = "notebook"
    REPORT = "report"
    DATASET = "dataset"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class DeliverableSpec:
    """One human-facing product promised by the Design meeting."""

    id: str
    title: str
    kind: DeliverableKind
    required: bool
    expected_paths: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    validation: str
    capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind.value,
            "required": self.required,
            "expected_paths": list(self.expected_paths),
            "acceptance_criteria": list(self.acceptance_criteria),
            "validation": self.validation,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True, slots=True)
class DeliverableManifest:
    """The bounded set of products Build must account for."""

    cycle_class: CycleClass
    deliverables: tuple[DeliverableSpec, ...]

    @property
    def notebook(self) -> DeliverableSpec | None:
        return next((item for item in self.deliverables if item.kind is DeliverableKind.NOTEBOOK), None)

    def as_dict(self) -> dict[str, object]:
        return {
            "version": MANIFEST_VERSION,
            "cycle_class": self.cycle_class.value,
            "deliverables": [item.as_dict() for item in self.deliverables],
        }

    @property
    def canonical_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()


def _required_text(raw: object, *, field: str, item_id: str) -> str:
    if not isinstance(raw, str) or not (value := raw.strip()):
        raise DeliverableManifestRejected(f"Deliverable {item_id!r} needs a nonempty {field}.")
    return value


def _required_texts(raw: object, *, field: str, item_id: str) -> tuple[str, ...]:
    if isinstance(raw, str) or not isinstance(raw, Sequence) or not raw:
        raise DeliverableManifestRejected(f"Deliverable {item_id!r} needs nonempty {field}.")
    values = tuple(_required_text(value, field=field, item_id=item_id) for value in raw)
    if len(set(values)) != len(values):
        raise DeliverableManifestRejected(f"Deliverable {item_id!r} has duplicate {field}.")
    return values


def _safe_path(raw: object, *, item_id: str) -> str:
    value = _required_text(raw, field="expected_paths", item_id=item_id)
    path = PurePosixPath(value)
    unsafe = value.startswith(("/", "~")) or "\\" in value or ":" in value or any(ord(character) < 32 for character in value) or any(part in {"", ".", ".."} for part in value.split("/")) or path.as_posix() != value
    if unsafe:
        raise DeliverableManifestRejected(f"Deliverable {item_id!r} expected_paths must contain only safe project-relative paths; got {value!r}.")
    return value


def _parse_item(raw: object, *, index: int) -> DeliverableSpec:
    if not isinstance(raw, Mapping):
        raise DeliverableManifestRejected(f"Deliverable {index} must be an object.")

    item_id = _required_text(raw.get("id"), field="id", item_id=f"#{index}")
    if len(item_id) > 64 or _STABLE_ID.fullmatch(item_id) is None:
        raise DeliverableManifestRejected(f"Deliverable id {item_id!r} must be a stable lowercase kebab-case identifier of at most 64 characters.")
    try:
        kind = DeliverableKind(_required_text(raw.get("kind"), field="kind", item_id=item_id))
    except ValueError as exc:
        allowed = ", ".join(kind.value for kind in DeliverableKind)
        raise DeliverableManifestRejected(f"Deliverable {item_id!r} has an unsupported kind; expected one of: {allowed}.") from exc
    required = raw.get("required")
    if not isinstance(required, bool):
        raise DeliverableManifestRejected(f"Deliverable {item_id!r} required must be a boolean.")

    raw_paths = raw.get("expected_paths")
    if isinstance(raw_paths, str) or not isinstance(raw_paths, Sequence) or not raw_paths:
        raise DeliverableManifestRejected(f"Deliverable {item_id!r} needs nonempty expected_paths.")
    expected_paths = tuple(_safe_path(value, item_id=item_id) for value in raw_paths)
    if len(set(expected_paths)) != len(expected_paths):
        raise DeliverableManifestRejected(f"Deliverable {item_id!r} has duplicate expected_paths.")
    if kind is DeliverableKind.NOTEBOOK:
        notebook_paths = [path for path in expected_paths if PurePosixPath(path).suffix.lower() in _NOTEBOOK_SUFFIXES]
        if len(notebook_paths) != 1:
            raise DeliverableManifestRejected(f"Notebook deliverable {item_id!r} must name exactly one .ipynb or .Rmd expected path.")

    return DeliverableSpec(
        id=item_id,
        title=_required_text(raw.get("title"), field="title", item_id=item_id),
        kind=kind,
        required=required,
        expected_paths=expected_paths,
        acceptance_criteria=_required_texts(
            raw.get("acceptance_criteria"),
            field="acceptance_criteria",
            item_id=item_id,
        ),
        validation=_required_text(raw.get("validation"), field="validation", item_id=item_id),
        capabilities=_required_texts(raw.get("capabilities"), field="capabilities", item_id=item_id),
    )


def parse_deliverable_manifest(
    raw: object,
    *,
    cycle_class: CycleClass | str,
) -> DeliverableManifest:
    """Parse and strictly validate a Design deliverable manifest."""

    if not isinstance(raw, Mapping):
        raise DeliverableManifestRejected("A deliverable manifest must be an object.")
    try:
        parsed_cycle_class = CycleClass(cycle_class)
    except (TypeError, ValueError) as exc:
        raise DeliverableManifestRejected(f"Unknown cycle_class {cycle_class!r}.") from exc
    if raw.get("version", MANIFEST_VERSION) != MANIFEST_VERSION:
        raise DeliverableManifestRejected(f"Unsupported deliverable manifest version {raw.get('version')!r}.")
    if "cycle_class" in raw and raw["cycle_class"] != parsed_cycle_class.value:
        raise DeliverableManifestRejected("Manifest cycle_class does not match the cycle being designed.")

    raw_items = raw.get("deliverables")
    if isinstance(raw_items, str) or not isinstance(raw_items, Sequence):
        raise DeliverableManifestRejected("A deliverable manifest needs a deliverables list between 1 and 10 items.")
    if not 1 <= len(raw_items) <= MAX_DELIVERABLES:
        raise DeliverableManifestRejected("A deliverable manifest must contain between 1 and 10 items.")
    deliverables = tuple(_parse_item(item, index=index) for index, item in enumerate(raw_items, start=1))
    ids = [item.id for item in deliverables]
    if len(set(ids)) != len(ids):
        duplicate = next(item_id for item_id in ids if ids.count(item_id) > 1)
        raise DeliverableManifestRejected(f"A deliverable manifest cannot contain duplicate id {duplicate!r}.")
    if parsed_cycle_class is CycleClass.COMPUTATIONAL:
        notebooks = [item for item in deliverables if item.kind is DeliverableKind.NOTEBOOK]
        if len(notebooks) != 1:
            raise DeliverableManifestRejected("A computational cycle requires exactly one notebook deliverable.")

    return DeliverableManifest(cycle_class=parsed_cycle_class, deliverables=deliverables)
