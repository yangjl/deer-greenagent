"""What Build starts from: the approved Design, resolved once by the server.

Before this, a Build worker was handed the cycle and told to find its own design
— so the first thing an expensive sandbox run did was re-derive, from prose,
which document it was implementing. A worker that guessed wrong produced
plausible work against the wrong plan, and nothing downstream could tell.

`load_design` answers that question deterministically instead, and this module is
the shape of its answer. Two rules make the bundle trustworthy:

**Only an approved Design travels, bound by content hash.** The approval bound a
specific document; a bundle naming only a path could silently carry a later
revision into work nobody agreed to. Absence is meaningful too — a Build with no
bundle is working before the gate, not merely without context.

**Provenance is typed, not prose.** An approved artifact, a workspace file, a
declared dataset, and a server policy are four different kinds of claim, and a
reviewer reading the record has to be able to tell them apart without parsing a
sentence.

The bundle owns no storage and reads no files. Resolving one is the step's job;
this is only what a resolved one *is*, so a test can build one in two lines and
the digest can be computed without a project on disk.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from deerflow.dbtl.build_workflow import BuildErrorCode

#: How much of the approved Design travels inline. The document is already
#: bound by hash and readable at its URI, so this is a convenience for the
#: worker's first model call rather than the authority.
MAX_DESIGN_EXCERPT_CHARS = 24_000

_SHA256 = re.compile(r"[0-9a-f]{64}")


class BundleInputKind(StrEnum):
    """The four kinds of claim a Build input can be.

    Kept distinct because they are believed for different reasons: an approved
    artifact by a human decision, a dataset by its declaration, a workspace file
    by its bytes, and a policy by the server asserting it.
    """

    APPROVED_ARTIFACT = "approved_artifact"
    WORKSPACE_INPUT = "workspace_input"
    DATASET = "dataset"
    SERVER_POLICY = "server_policy"


class BuildInputError(RuntimeError):
    """A Build that cannot resolve its inputs must not dispatch a worker.

    Carries a bounded `BuildErrorCode` so the failure is recorded as a step
    outcome rather than as an unclassified exception string.
    """

    def __init__(self, code: BuildErrorCode, summary: str) -> None:
        super().__init__(summary)
        self.code = code
        self.summary = summary


@dataclass(frozen=True, slots=True)
class BundleInput:
    """One typed input, with the hash that makes it checkable."""

    kind: BundleInputKind
    reference: str
    content_hash: str = ""
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reference": self.reference,
            "content_hash": self.content_hash,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class BuildInputBundle:
    """The resolved, hash-bound starting point for one Build attempt."""

    design: BundleInput
    design_revision: int
    design_text: str = ""
    design_truncated: bool = False
    inputs: tuple[BundleInput, ...] = ()
    manifest: tuple[Mapping[str, Any], ...] = ()
    policy: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validated here rather than at every call site: a bundle that exists is
        # a bundle whose design is approved and hash-bound, so no downstream
        # reader has to re-check it before trusting the binding.
        if self.design.kind is not BundleInputKind.APPROVED_ARTIFACT:
            raise ValueError("A Build input bundle's design must be an approved artifact.")
        if not self.design.reference.strip():
            raise ValueError("A Build input bundle's design must name its artifact.")
        if not _SHA256.fullmatch(self.design.content_hash or ""):
            raise ValueError("A Build input bundle's design must carry a lowercase SHA-256 content hash.")
        object.__setattr__(self, "policy", MappingProxyType(dict(self.policy)))

    @property
    def digest(self) -> str:
        """This bundle's identity, and so `load_design`'s output digest.

        The excerpt is deliberately excluded: it is a bounded projection of a
        document already bound by hash, and letting a truncation boundary move
        the digest would invalidate a perfectly good plan for a display change.
        """
        payload = {
            "design": self.design.as_dict(),
            "design_revision": self.design_revision,
            "inputs": [item.as_dict() for item in self.inputs],
            "manifest": [dict(entry) for entry in self.manifest],
            "policy": dict(self.policy),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "design": self.design.as_dict(),
            "design_revision": self.design_revision,
            "design_text": self.design_text,
            "design_truncated": self.design_truncated,
            "inputs": [item.as_dict() for item in self.inputs],
            "manifest": [dict(entry) for entry in self.manifest],
            "policy": dict(self.policy),
            "digest": self.digest,
        }


def bounded_excerpt(text: str, *, limit: int = MAX_DESIGN_EXCERPT_CHARS) -> tuple[str, bool]:
    """Return the excerpt and whether anything was dropped.

    Reported rather than silent: a worker reading a truncated design has to know
    the rest exists at the URI, or it will treat the tail it never saw as absent.
    """
    value = text or ""
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def dataset_inputs(datasets: Sequence[Mapping[str, Any]]) -> tuple[BundleInput, ...]:
    """Declared datasets as typed inputs, skipping any that cannot be checked.

    A declaration with no source key or no usable hash is not a binding; keeping
    it would put an uncheckable claim in a record whose whole value is that its
    claims are checkable.
    """
    resolved: list[BundleInput] = []
    for item in datasets:
        source_key = str(item.get("source_key") or "").strip()
        content_hash = str(item.get("content_hash") or "").strip().lower()
        if not source_key or not _SHA256.fullmatch(content_hash):
            continue
        resolved.append(
            BundleInput(
                kind=BundleInputKind.DATASET,
                reference=f"dataset:{source_key}",
                content_hash=content_hash,
                description=str(item.get("uri") or ""),
            )
        )
    return tuple(resolved)
